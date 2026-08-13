import logging
import re
import time
from dataclasses import dataclass
from typing import Callable, Mapping
from urllib.parse import urljoin

from selenium.common import StaleElementReferenceException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from auto_base import AutoBase, normalize_config


HOMEWORK_DISCOVERY_URL = (
    'https://teacher.ewt360.com/ewtbend/bend/index/index.html#/student/homework'
)
HOLIDAY_DISCOVERY_URL = (
    'https://teacher.ewt360.com/ewtbend/bend/index/index.html#/holiday/student/home'
)
TASK_DETAIL_RE = re.compile(r'student-task-overview', re.IGNORECASE)
TASK_ACTION_RE = re.compile(
    r'查看详情|去完成|继续完成|去学习|开始学习|继续学习'
)
QUIZ_TASK_RE = re.compile(
    r'试卷|测一测|测验|考试|答题|继续答|去答题|去考试|去练习|试题|题目|\d+\s*题'
)
TASK_META_RE = re.compile(r'布置人|开始时间|截止时间|任务|假期|学习计划')
TASK_CARD_CLASS_RE = re.compile(
    r'task|homework|course|lesson|plan|card|item',
    re.IGNORECASE,
)
TASK_STATUS_RE = re.compile(
    r'已完成|已提交|已批改|已截止|已过期|超时|未开始|进行中|未完成'
)
PAGE_NAVIGATION_RE = re.compile(
    r'学生端.{0,80}首页.{0,80}我的(?:错题本|任务|班级|测评)'
)
LIST_FOOTER_RE = re.compile(
    r'没有更多(?:内容|任务)?了[~～]?|暂无更多(?:内容|任务)?'
)
STATUS_FILTERS = ('进行中', '未开始', '已截止')
HOLIDAY_TABS = ('常规任务', '专项提升')


@dataclass(frozen=True)
class DiscoveredTask:
    id: str
    title: str
    url: str
    status: str = '未完成'
    start_time: str = ''
    deadline: str = ''
    teacher: str = ''
    source_filter: str = ''


def compact_text(value: str | None) -> str:
    return re.sub(r'\s+', ' ', value or '').strip()


def is_task_detail_url(url: str | None) -> bool:
    value = str(url or '')
    return bool(TASK_DETAIL_RE.search(value) and 'homeworkId=' in value)


def classify_task_status(text: str, source_filter: str = '') -> str:
    text = compact_text(text)
    if re.search(r'已完成|已提交|已批改', text):
        return '已完成'
    if re.search(r'已截止|已过期|超时', text):
        return '已截止（未完成）'
    if '未开始' in text:
        return '未开始'
    if '进行中' in text:
        return '进行中'
    return f'{source_filter}（未完成）' if source_filter else '未完成'


def task_title(text: str) -> str:
    text = compact_text(text)
    text = LIST_FOOTER_RE.split(text, maxsplit=1)[0]
    before_meta = re.split(r'布置人[:：]|开始时间[:：]|截止时间[:：]', text)[0]
    title = TASK_ACTION_RE.sub('', before_meta)
    title = re.sub(r'已完成|已提交|已批改|已截止|未开始|进行中', '', title)
    return compact_text(title)[:100]


def task_field(text: str, expression: str) -> str:
    match = re.search(expression, compact_text(text))
    if not match:
        return ''
    value = compact_text(TASK_ACTION_RE.sub('', match.group(1)))
    return compact_text(LIST_FOOTER_RE.split(value, maxsplit=1)[0])


class TaskDiscoverySession(AutoBase):
    """Open the homework page and collect selectable video-task URLs."""

    def __init__(
        self,
        config: Mapping,
        status_sink: Callable[[str], None] | None = None,
        stop_event=None,
        visible_handoff_event=None,
        foreground_request_event=None,
        manual_intervention_sink=None,
    ):
        discovery_config = normalize_config(config)
        discovery_config['list_url'] = HOMEWORK_DISCOVERY_URL
        self.status_sink = status_sink
        super().__init__(
            config=discovery_config,
            stop_event=stop_event,
            visible_handoff_event=visible_handoff_event,
            foreground_request_event=foreground_request_event,
            manual_intervention_sink=manual_intervention_sink,
        )

    def finish_a_day(self, day) -> None:
        raise NotImplementedError

    def discover(self) -> list[DiscoveredTask]:
        tasks: dict[str, DiscoveredTask] = {}
        self._discover_page(
            HOMEWORK_DISCOVERY_URL,
            STATUS_FILTERS,
            tasks,
        )
        self._discover_page(
            HOLIDAY_DISCOVERY_URL,
            HOLIDAY_TABS,
            tasks,
        )
        return list(tasks.values())

    def _discover_page(
        self,
        page_url: str,
        tabs: tuple[str, ...],
        tasks: dict[str, DiscoveredTask],
    ) -> None:
        self.check_control_requests()
        self._status('等待任务列表加载')
        self.driver.get(page_url)
        try:
            def page_ready(driver):
                self.check_control_requests()
                return any(fragment in driver.current_url for fragment in (
                    'student/homework',
                    'holiday/student/home',
                )) or self._find_task_cards(include_completed=True)

            WebDriverWait(self.driver, 25).until(
                page_ready
            )
        except TimeoutException:
            self._status('任务列表加载超时，继续扫描当前页面')
        self._dismiss_holiday_intro()

        scanned_filter = False
        # Always scan the initially selected page before switching tabs.
        self._collect_current_tasks(tasks, '')
        for label in tabs:
            self.check_control_requests()
            if self._click_filter(label):
                scanned_filter = True
                self._status(f'扫描{label}任务')
                time.sleep(max(1.0, 2.0 * self.config['delay_multiplier']))
                self._dismiss_holiday_intro()
                source_filter = label if label in STATUS_FILTERS else ''
                self._collect_current_tasks(tasks, source_filter)
        if not scanned_filter or not tasks:
            self._status('扫描当前任务列表')
            self._collect_current_tasks(tasks, '')

    def _dismiss_holiday_intro(self) -> None:
        buttons = self.driver.find_elements(
            By.XPATH,
            "//*[self::button or self::a or @role='button' or self::div or self::span]"
            "[normalize-space(.)='即刻开启']",
        )
        for button in buttons:
            try:
                if button.is_displayed():
                    self.click(button)
                    self._status('已关闭暑假任务引导')
                    time.sleep(0.5)
                    return
            except StaleElementReferenceException:
                continue

    def close(self) -> None:
        if self.driver:
            self.driver.quit()
            self.driver = None

    def _status(self, message: str) -> None:
        logging.info(message)
        if self.status_sink:
            self.status_sink(message)

    def _collect_current_tasks(
        self,
        tasks: dict[str, DiscoveredTask],
        source_filter: str,
    ) -> None:
        snapshots: list[tuple[str, str, str]] = []
        for card in self._find_task_cards(include_completed=False):
            try:
                text = compact_text(card.get_attribute('innerText') or card.text)
                title = task_title(text)
                if not title or QUIZ_TASK_RE.search(title):
                    continue
                snapshots.append((title, text, self._task_url_from_card(card)))
            except StaleElementReferenceException:
                continue

        for index, (title, text, direct_url) in enumerate(snapshots):
            try:
                url = direct_url
                if not url:
                    card = self._find_card_by_title(title)
                    if card is not None:
                        url = self._resolve_task_url_by_click(card)
                if not is_task_detail_url(url):
                    continue
                key = url or '|'.join((title, source_filter))
                tasks[key] = DiscoveredTask(
                    id=f'task-{len(tasks) + index + 1}',
                    title=title,
                    url=url,
                    status=classify_task_status(text, source_filter),
                    start_time=task_field(
                        text,
                        r'开始时间[:：]\s*(.+?)(?:截止时间[:：]|查看详情|去完成|继续完成|去学习|$)',
                    ),
                    deadline=task_field(
                        text,
                        r'截止时间[:：]\s*(.+?)(?:查看详情|去完成|继续完成|去学习|$)',
                    ),
                    teacher=task_field(
                        text,
                        r'布置人[:：]\s*(.+?)(?:开始时间[:：]|截止时间[:：]|查看详情|$)',
                    ),
                    source_filter=source_filter,
                )
            except StaleElementReferenceException:
                continue

    def _find_card_by_title(self, title: str):
        for card in self._find_task_cards(include_completed=False):
            try:
                text = compact_text(card.get_attribute('innerText') or card.text)
                if task_title(text) == title:
                    return card
            except StaleElementReferenceException:
                continue
        return None

    def _find_task_cards(self, include_completed: bool) -> list:
        cards = self.driver.find_elements(
            By.CSS_SELECTOR,
            'div[class], li[class], section[class], article[class], tr',
        )
        result = []
        for card in cards:
            try:
                if not card.is_displayed():
                    continue
                text = compact_text(card.get_attribute('innerText') or card.text)
                if len(text) < 8 or len(text) > 600:
                    continue
                if not TASK_ACTION_RE.search(text):
                    continue
                class_name = compact_text(card.get_attribute('class'))
                has_task_structure = bool(
                    TASK_META_RE.search(text)
                    or TASK_STATUS_RE.search(text)
                    or TASK_CARD_CLASS_RE.search(class_name)
                    or self._task_url_from_card(card)
                )
                if not has_task_structure:
                    continue
                if PAGE_NAVIGATION_RE.search(text):
                    continue
                completed = bool(re.search(r'已完成|已提交|已批改', text))
                if completed and not include_completed:
                    continue
                if not include_completed and QUIZ_TASK_RE.search(task_title(text)):
                    continue
                result.append(card)
            except StaleElementReferenceException:
                continue

        # A task element is often nested in several generic divs. Keep only the
        # innermost matching element so page/list wrappers cannot become tasks.
        leaves = []
        for card in result:
            try:
                descendants = card.find_elements(By.XPATH, './/*')
                if any(other != card and other in descendants for other in result):
                    continue
                leaves.append(card)
            except (AttributeError, StaleElementReferenceException):
                leaves.append(card)

        # Prefer the smallest matching containers to avoid page wrappers.
        result = leaves
        result.sort(key=lambda element: len(compact_text(element.get_attribute('innerText'))))
        unique = []
        seen = set()
        for card in result:
            try:
                text = compact_text(card.get_attribute('innerText'))
                signature = (task_title(text), classify_task_status(text))
                if signature in seen:
                    continue
                seen.add(signature)
                unique.append(card)
            except StaleElementReferenceException:
                continue
        return unique

    def _click_filter(self, label: str) -> bool:
        elements = self.driver.find_elements(
            By.XPATH,
            "//*[self::li or self::a or self::button or @role='button' or self::span or self::div]"
            f"[normalize-space(.)='{label}']",
        )
        visible = []
        for element in elements:
            try:
                if element.is_displayed():
                    visible.append(element)
            except StaleElementReferenceException:
                continue
        if not visible:
            return False
        visible.sort(key=lambda element: element.size.get('width', 0) * element.size.get('height', 0))
        self.click(visible[0])
        return True

    def _task_url_from_card(self, card) -> str:
        elements = [card] + card.find_elements(By.CSS_SELECTOR, 'a[href], [data-href], [data-url]')
        for element in elements:
            for attribute in ('href', 'data-href', 'data-url'):
                value = element.get_attribute(attribute)
                if not value:
                    continue
                url = urljoin(self.driver.current_url, value)
                if is_task_detail_url(url):
                    return url
        html = card.get_attribute('outerHTML') or ''
        match = re.search(
            r'https?://[^"\'\s<>]+student-task-overview[^"\'\s<>]*homeworkId=\d+',
            html,
            re.IGNORECASE,
        )
        return match.group(0).replace('&amp;', '&') if match else ''

    def _resolve_task_url_by_click(self, card) -> str:
        before_handles = set(self.driver.window_handles)
        before_url = self.driver.current_url
        actions = card.find_elements(By.CSS_SELECTOR, 'a, button, [role="button"], div, span')
        target = None
        for action in actions:
            try:
                text = compact_text(action.get_attribute('innerText') or action.text)
                if action.is_displayed() and len(text) <= 40 and TASK_ACTION_RE.search(text):
                    target = action
                    break
            except StaleElementReferenceException:
                continue
        if target is None:
            target = card
        self.click(target)
        try:
            WebDriverWait(self.driver, 10).until(
                lambda driver: is_task_detail_url(driver.current_url)
                or bool(set(driver.window_handles) - before_handles)
            )
        except TimeoutException:
            return ''
        new_handles = set(self.driver.window_handles) - before_handles
        opened_new = bool(new_handles)
        if opened_new:
            self.driver.switch_to.window(new_handles.pop())
        url = self.driver.current_url
        if opened_new:
            self.driver.close()
            self.driver.switch_to.window(next(iter(before_handles)))
        elif self.driver.current_url != before_url:
            self.driver.back()
            try:
                WebDriverWait(self.driver, 10).until(
                    lambda driver: driver.current_url == before_url
                )
            except TimeoutException:
                self.driver.get(before_url)
        return url if is_task_detail_url(url) else ''
