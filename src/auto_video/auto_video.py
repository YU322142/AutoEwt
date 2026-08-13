#
# Created by 着火的冰块nya (zhdbk3) on 2025/1/23
#

import logging
import re
import time
import traceback
import warnings
from dataclasses import dataclass

from selenium.common import (
    InvalidSessionIdException,
    InvalidSelectorException,
    NoSuchElementException,
    NoSuchWindowException,
    StaleElementReferenceException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webelement import WebElement
from tqdm import TqdmWarning

from auto_base import AutoBase
from manual_intervention import (
    BrowserControlRequested,
    HumanVerificationRequiredError,
)
from progress import ProgressTqdm, emit_progress

warnings.filterwarnings('ignore', category=TqdmWarning)


MISSED_CHECKPOINT_RE = re.compile(
    r'(?:你)?错过(?:了)?所有\s*(?:看课)?(?:检测|检查)点|再认真观看一次'
)
MISSED_CHECKPOINT_WARNING_XPATH = (
    "//*[((contains(normalize-space(.), '错过了所有') "
    "and contains(normalize-space(.), '看课检测点')) "
    "or contains(normalize-space(.), '再认真观看一次')) "
    "and not(.//*[(contains(normalize-space(.), '错过了所有') "
    "and contains(normalize-space(.), '看课检测点')) "
    "or contains(normalize-space(.), '再认真观看一次')])]"
)
MISSED_CHECKPOINT_REPLAY_LIMIT = 3
MISSED_CHECKPOINT_SCAN_RETRIES = 3
MISSED_CHECKPOINT_STALE_RETRIES = 5
MISSED_CHECKPOINT_SETTLE_SCANS = 1
KNOWN_MISSED_CHECKPOINT_SETTLE_SCANS = 5
MISSED_CHECKPOINT_OPERATION_LIMIT = 100
LESSON_FAILURE_LIMIT = 3
DAY_LESSON_OPERATION_LIMIT = 100
PLAYBACK_BLOCKED_RE = re.compile(
    r'检测到网络不稳定或开启了第三方辅助工具|学习数据无法被记录'
)
ATTENTION_CHECKPOINT_RE = re.compile(
    r'认真度检测|近期看课操作异常|将图形拖动至正确位置|'
    r'向右拖动滑块填充拼图|后将错过当前检测'
)
UNKNOWN_OVERLAY_WAIT_LIMIT = 25.0
PAUSED_CHECKPOINT_GRACE_DELAYS = (0.1, 0.4, 0.8)
MISSED_CHECKPOINT_DIALOG_RE = re.compile(
    r'错过(?:了)?(?:本次|当前|所有)?\s*(?:看课)?(?:检测|检查)点|'
    r'未通过(?:本次|当前)?\s*(?:看课)?(?:检测|检查)点|'
    r'(?:^|[:：\s])(?:我)?知道了$'
)
CHECKPOINT_ACTION_LABELS = (
    '点击通过检查',
    '通过检查',
    '继续播放',
    '跳过',
)
CHECKPOINT_RESULT_SETTLE_SECONDS = 0.8


def compact_text(value: str | None) -> str:
    """Collapse DOM text so status checks are not affected by layout whitespace."""
    return re.sub(r'\s+', ' ', value or '').strip()


def has_missed_checkpoint_warning(value: str | None) -> bool:
    """Return whether the course status says all watch checkpoints were missed."""
    return bool(MISSED_CHECKPOINT_RE.search(compact_text(value)))


def requires_checkpoint_replay(status_text: str | None, action_text: str | None) -> bool:
    """A missed-checkpoint warning overrides an otherwise completed action."""
    return has_missed_checkpoint_warning(status_text) and '已学完' in compact_text(action_text)


def has_playback_block_warning(value: str | None) -> bool:
    return bool(PLAYBACK_BLOCKED_RE.search(compact_text(value)))


def has_missed_checkpoint_dialog(value: str | None) -> bool:
    return bool(MISSED_CHECKPOINT_DIALOG_RE.search(compact_text(value)))


@dataclass(frozen=True)
class MissedCheckpointCandidate:
    button: WebElement
    signature: str
    label: str


class MissedCheckpointReplayError(RuntimeError):
    """Raised when a missed-checkpoint course cannot be cleared by replaying."""


class PlaybackBlockedError(RuntimeError):
    """Raised when the site pauses playback because it detected a risk."""


class CheckpointMissedError(RuntimeError):
    """Raised when the site confirms that this lesson missed its checkpoint."""


class CheckpointInteractionRequiredError(RuntimeError):
    """Raised when a checkpoint state cannot be handled safely."""


class LessonProcessingError(RuntimeError):
    """Raised when one or more lessons cannot be processed reliably."""


class AutoVideo(AutoBase):
    def __init__(
        self,
        config=None,
        stop_event=None,
        visible_handoff_event=None,
        foreground_request_event=None,
        manual_intervention_sink=None,
    ):
        super().__init__(
            config=config,
            stop_event=stop_event,
            visible_handoff_event=visible_handoff_event,
            foreground_request_event=foreground_request_event,
            manual_intervention_sink=manual_intervention_sink,
        )
        self._attention_checkpoint_started_at: float | None = None
        self._attention_checkpoint_logged = False
        self._last_checkpoint_kind = ''
        self._unknown_overlay_started_at: float | None = None
        self._last_checkpoint_action_signature = ''
        self._last_checkpoint_action_at = 0.0
        self._missed_checkpoint_seen = False
        self._passed_missed_checkpoint_signatures: set[str] = set()
        self._passed_missed_checkpoint_labels: set[str] = set()
        self._missed_replay_completed_courses = 0

    def finish_a_day(self, day: WebElement) -> None:
        """
        完成一天的任务
        :param day: 该天在网页上的标签
        :return: None
        """
        self.click(day)
        self._passed_missed_checkpoint_signatures = set()
        self._passed_missed_checkpoint_labels = set()
        self._missed_replay_completed_courses = 0
        time.sleep(2 * self.config.get('delay_multiplier'))
        self._finish_missed_checkpoint_lessons(
            settle_scans=MISSED_CHECKPOINT_SETTLE_SCANS,
        )

        unit = '节课'
        lesson_index = 0
        failed_attempts: dict[str, int] = {}
        failed_lessons: dict[str, str] = {}
        stale_attempts: dict[str, int] = {}
        checkpoint_miss_attempts: dict[str, int] = {}
        identity_stale_attempts = 0
        lesson_operations = 0
        course_scope = getattr(self, '_progress_day_scope', 'day:unknown')
        course_total = None
        ordinary_course_count = None
        completed_courses = self._missed_replay_completed_courses
        while True:
            self.check_control_requests()
            lesson_operations += 1
            if lesson_operations > DAY_LESSON_OPERATION_LIMIT:
                raise LessonProcessingError(
                    '当天课程列表状态反复变化，已交由刷课线程整体重启'
                )
            btns = self._find_video_lesson_buttons()
            if course_total is None:
                # Missed-checkpoint replays are already completed when this
                # day's ordinary lesson list is scanned, so include them in
                # the denominator instead of resetting the bar to zero.
                ordinary_course_count = len(btns)
                course_total = self._missed_replay_completed_courses + ordinary_course_count
                emit_progress(
                    '课程总数',
                    completed_courses,
                    course_total,
                    '项',
                    finished=course_total == 0,
                    kind='courses',
                    scope=course_scope,
                )
            if not btns:
                break
            candidates: list[tuple[WebElement, str, str]] = []
            identity_stale_count = 0
            for candidate_index, candidate in enumerate(btns):
                try:
                    signature, label = self._lesson_button_identity(candidate)
                except StaleElementReferenceException:
                    identity_stale_count += 1
                    continue
                signature = f'{signature}|index:{candidate_index}'
                if signature not in failed_lessons:
                    candidates.append((candidate, signature, label))
            if not candidates:
                if identity_stale_count:
                    identity_stale_attempts += 1
                    if identity_stale_attempts >= LESSON_FAILURE_LIMIT:
                        raise LessonProcessingError(
                            '课程列表元素持续刷新，无法可靠定位未完成课程'
                        )
                    time.sleep(0.5 * self.config.get('delay_multiplier'))
                    continue
                break
            identity_stale_attempts = 0
            btn, signature, label = candidates[0]
            lesson_index += 1
            logging.info(f'该天还剩 {len(btns)} {unit}')
            logging.info(f'第 {lesson_index} 个未完成{unit}')
            logging.info(
                '准备进入未完成课程“%s”；已完成课程不会进入，漏检重看课程会在本轮前优先处理',
                label,
            )
            try:
                self._finish_open_lesson(btn)
                failed_attempts.pop(signature, None)
                stale_attempts.pop(signature, None)
                completed_courses += 1
                emit_progress(
                    '课程总数',
                    completed_courses,
                    course_total,
                    '项',
                    finished=completed_courses >= (course_total or 0),
                    kind='courses',
                    scope=course_scope,
                )
            except BrowserControlRequested:
                raise
            except PlaybackBlockedError:
                raise
            except CheckpointInteractionRequiredError:
                raise
            except CheckpointMissedError:
                miss_attempt = checkpoint_miss_attempts.get(signature, 0) + 1
                checkpoint_miss_attempts[signature] = miss_attempt
                if miss_attempt >= LESSON_FAILURE_LIMIT:
                    failed_lessons[signature] = label
                    logging.error(
                        '课程“%s”连续确认漏检 %s 次，交由刷课线程整体重启',
                        label,
                        miss_attempt,
                    )
                    continue
                logging.warning('本节课漏检，立即重新扫描列表并获取新的检查点')
                replay_before = self._missed_replay_completed_courses
                self._finish_missed_checkpoint_lessons(
                    settle_scans=KNOWN_MISSED_CHECKPOINT_SETTLE_SCANS,
                    require_warning=True,
                )
                checkpoint_miss_attempts.pop(signature, None)
                replay_delta = self._missed_replay_completed_courses - replay_before
                if replay_delta > 0:
                    # One replay belongs to the ordinary course already present
                    # in course_total. Any additional confirmed warning rows are
                    # completed-looking courses discovered only after refresh.
                    completed_courses += replay_delta
                    course_total = (course_total or 0) + max(0, replay_delta - 1)
                emit_progress(
                    '课程总数',
                    completed_courses,
                    course_total,
                    '项',
                    finished=completed_courses >= (course_total or 0),
                    kind='courses',
                    scope=course_scope,
                )
            except StaleElementReferenceException:
                attempts = stale_attempts.get(signature, 0) + 1
                stale_attempts[signature] = attempts
                if attempts >= LESSON_FAILURE_LIMIT:
                    failed_lessons[signature] = label
                    logging.error(
                        '课程“%s”的页面元素连续失效 %s 次，交由刷课线程整体重启',
                        label,
                        attempts,
                    )
                else:
                    logging.warning(
                        '课程列表已刷新，重新定位后重试“%s”（%s/%s）',
                        label,
                        attempts,
                        LESSON_FAILURE_LIMIT,
                    )

            except Exception:
                logging.error(traceback.format_exc())
                attempts = failed_attempts.get(signature, 0) + 1
                failed_attempts[signature] = attempts
                if attempts >= LESSON_FAILURE_LIMIT:
                    failed_lessons[signature] = label
                    logging.error(
                        '课程“%s”连续处理失败 %s 次，停止重试但不会标记为完成',
                        label,
                        attempts,
                    )
                else:
                    logging.warning(
                        '课程“%s”处理失败，重新定位后重试（%s/%s）',
                        label,
                        attempts,
                        LESSON_FAILURE_LIMIT,
                    )

        if lesson_index:
            time.sleep(2 * self.config.get('delay_multiplier'))
        replay_before = self._missed_replay_completed_courses
        self._finish_missed_checkpoint_lessons(
            settle_scans=MISSED_CHECKPOINT_SETTLE_SCANS,
        )
        replay_delta = self._missed_replay_completed_courses - replay_before
        if replay_delta > 0:
            # Warnings that appear only after ordinary playback belong to
            # courses already included in this day's inventory. Passing one
            # checkpoint confirms their completion; it must not create a
            # duplicate course or advance the aggregate a second time.
            if not ordinary_course_count:
                course_total = (course_total or 0) + replay_delta
                completed_courses += replay_delta
            emit_progress(
                '课程总数',
                completed_courses,
                course_total,
                '项',
                finished=completed_courses >= course_total,
                kind='courses',
                scope=course_scope,
            )

        # Re-query after any replay because the course list may have re-rendered.
        btns_one_click = self._find_one_click_buttons()
        course_total = (course_total or 0) + len(btns_one_click)
        emit_progress(
            '课程总数',
            completed_courses,
            course_total,
            '项',
            finished=completed_courses >= course_total,
            kind='courses',
            scope=course_scope,
        )
        unit_one_click = "个"
        failed_one_click: list[str] = []
        logging.info(f'该天还剩 {len(btns_one_click)} {unit_one_click} 非视频课程')
        for i in range(len(btns_one_click)):
            self.check_control_requests()
            logging.info(f'第 {i + 1} / {len(btns_one_click)} {unit_one_click} 非视频课程')
            try:
                self.finish_a_click(btns_one_click[i])
                completed_courses += 1
                emit_progress(
                    '课程总数',
                    completed_courses,
                    course_total,
                    '项',
                    finished=completed_courses >= (course_total or 0),
                    kind='courses',
                    scope=course_scope,
                )
            except BrowserControlRequested:
                raise
            except Exception:
                logging.error(traceback.format_exc())
                failed_one_click.append(f'非视频课程 {i + 1}')
                logging.error('非视频课程处理失败，不会标记为完成')
                self._return_to_course_list_if_open(force=False)

        failed_labels = list(dict.fromkeys(failed_lessons.values()))
        failed_labels.extend(failed_one_click)
        if failed_labels:
            raise LessonProcessingError(
                f'以下课程未能可靠完成：{"、".join(failed_labels)}'
            )
        if course_total is not None:
            emit_progress(
                '课程总数',
                course_total,
                course_total,
                '项',
                finished=True,
                kind='courses',
                scope=course_scope,
            )

    def _find_video_lesson_buttons(self) -> list[WebElement]:
        return self.driver.find_elements(
            By.XPATH,
            "//div[contains(@class, 'btn-AoqsA') "
            "and .//text()[contains(., '学')] "
            "and not(.//text()[contains(., '已学完')])]")

    def _find_one_click_buttons(self) -> list[WebElement]:
        return self.driver.find_elements(
            By.XPATH,
            "//div[contains(@class, 'btn-AoqsA') "
            "and (.//text()[contains(., '去收听')] "
            " or .//text()[contains(., '去查看')]) "
            "and not(.//text()[contains(., '已学完')])]")

    def _lesson_button_identity(self, button: WebElement) -> tuple[str, str]:
        """Build a stable identity from the nearest useful course-card text."""
        current = button
        best = compact_text(self._element_text(button))
        for _ in range(6):
            try:
                if compact_text(getattr(current, 'tag_name', '')).lower() in {
                    'body', 'html',
                }:
                    break
                current = current.find_element(By.XPATH, '..')
                text = compact_text(self._element_text(current))
                if len(best) < len(text) <= 500:
                    best = text
            except (
                NoSuchElementException,
                StaleElementReferenceException,
                InvalidSelectorException,
                WebDriverException,
            ):
                break
        label = self._course_label(best) or '未命名课程'
        stable_id = ''
        current = button
        for _ in range(6):
            try:
                if compact_text(getattr(current, 'tag_name', '')).lower() in {
                    'body', 'html',
                }:
                    break
                stable_id = next((
                    compact_text(current.get_attribute(name))
                    for name in (
                        'data-id', 'data-course-id', 'data-homework-id',
                        'data-task-id', 'href', 'id',
                    )
                    if compact_text(current.get_attribute(name))
                ), '')
                if stable_id:
                    break
                current = current.find_element(By.XPATH, '..')
            except (
                NoSuchElementException,
                StaleElementReferenceException,
                InvalidSelectorException,
                WebDriverException,
            ):
                break
        signature = '|'.join((best[:300] or label, stable_id))
        return signature, label

    def _finish_missed_checkpoint_lessons(
        self,
        settle_scans: int = 0,
        require_warning: bool = False,
    ) -> int:
        """Replay completed-looking courses until one checkpoint is confirmed.

        A confirmed checkpoint click is authoritative for that replay course.
        The list can keep an old warning row visible briefly, so completion is
        remembered by its stable course signature or fallback card fingerprint.
        """
        attempts: dict[str, int] = {}
        stale_retries: dict[str, int] = {}
        replayed = 0
        warning_without_action_scans = 0
        quiet_scans_remaining = max(0, settle_scans)
        operations = 0
        warning_seen = False

        while True:
            self.check_control_requests()
            operations += 1
            if operations > MISSED_CHECKPOINT_OPERATION_LIMIT:
                raise MissedCheckpointReplayError(
                    '检查点重看状态反复变化，已停止以避免无限重播'
                )
            candidates, warning_found = self._find_missed_checkpoint_candidates()
            warning_seen = warning_seen or warning_found
            passed_signatures = getattr(
                self,
                '_passed_missed_checkpoint_signatures',
                set(),
            )
            pending_candidates = [
                candidate for candidate in candidates
                if not self._missed_checkpoint_candidate_passed(
                    candidate,
                    passed_signatures,
                )
            ]
            if candidates and not pending_candidates:
                # The site can leave the old warning in the list briefly. A
                # confirmed checkpoint action is the authoritative completion
                # signal for an already-completed-looking replay course.
                return replayed
            candidates = pending_candidates
            if not candidates:
                if not warning_found:
                    if quiet_scans_remaining:
                        quiet_scans_remaining -= 1
                        time.sleep(1 * self.config.get('delay_multiplier'))
                        continue
                    if require_warning and not warning_seen and replayed == 0:
                        raise MissedCheckpointReplayError(
                            '播放器已确认漏检，但课程列表未出现可靠的重看状态'
                        )
                    return replayed
                if replayed and self._all_visible_missed_warnings_passed(
                    passed_signatures,
                ):
                    # The warning can outlive the course row's action while
                    # the list is being re-rendered. Only suppress it when
                    # every visible warning can be tied to a course that has
                    # already passed a checkpoint in this run.
                    return replayed
                warning_without_action_scans += 1
                if warning_without_action_scans >= MISSED_CHECKPOINT_SCAN_RETRIES:
                    raise MissedCheckpointReplayError(
                        '检测到“错过所有看课检测点”，但未找到同课程的“已学完”重看入口'
                    )
                logging.warning('检查点漏看提示仍在加载，等待重看入口出现')
                time.sleep(1 * self.config.get('delay_multiplier'))
                continue

            warning_without_action_scans = 0
            quiet_scans_remaining = 0
            candidate = candidates[0]
            completed_attempts = attempts.get(candidate.signature, 0)
            if completed_attempts >= MISSED_CHECKPOINT_REPLAY_LIMIT:
                raise MissedCheckpointReplayError(
                    f'课程“{candidate.label}”重看后仍提示错过所有看课检测点'
                )
            attempt = completed_attempts + 1

            logging.warning(
                '课程“%s”虽显示已学完，但错过了所有看课检测点；按未完成重看（第 %s 次）',
                candidate.label,
                attempt,
            )
            try:
                checkpoint_passed = self._finish_open_lesson(
                    candidate.button,
                    stop_after_checkpoint=True,
                )
            except CheckpointMissedError:
                attempts[candidate.signature] = attempt
                stale_retries.pop(candidate.signature, None)
                logging.warning(
                    '课程“%s”本次仍漏检，已返回列表并重新获取检查点（第 %s 次）',
                    candidate.label,
                    attempt,
                )
                time.sleep(1 * self.config.get('delay_multiplier'))
                continue
            except CheckpointInteractionRequiredError:
                raise
            except StaleElementReferenceException:
                # The list can refresh between status detection and clicking.
                # Re-scan instead of treating the completed-looking row as done.
                stale_count = stale_retries.get(candidate.signature, 0) + 1
                stale_retries[candidate.signature] = stale_count
                if stale_count >= MISSED_CHECKPOINT_STALE_RETRIES:
                    raise MissedCheckpointReplayError(
                        f'课程“{candidate.label}”的重看入口反复失效，无法确认完成状态'
                    )
                logging.warning('课程列表已刷新，重新定位需要重看的课程')
                time.sleep(1 * self.config.get('delay_multiplier'))
                continue

            attempts[candidate.signature] = attempt
            stale_retries.pop(candidate.signature, None)
            replayed += 1
            if checkpoint_passed:
                passed_signatures.add(candidate.signature)
                self._passed_missed_checkpoint_signatures = passed_signatures
                self._missed_replay_completed_courses = (
                    getattr(self, '_missed_replay_completed_courses', 0) + 1
                )
                logging.info(
                    '课程“%s”已顺利通过一个看课检查点，按已学完处理',
                    candidate.label,
                )
            time.sleep(2 * self.config.get('delay_multiplier'))

    @staticmethod
    def _missed_checkpoint_candidate_passed(
        candidate: MissedCheckpointCandidate,
        passed_signatures: set[str],
    ) -> bool:
        """Match a passed replay across stale list renders.

        Stable IDs and fallback course fingerprints are exact identities. The
        display label is deliberately excluded because two rows may share it.
        """
        return candidate.signature in passed_signatures

    def _all_visible_missed_warnings_passed(
        self,
        passed_signatures: set[str],
    ) -> bool:
        """Return true only when every warning maps to an exact passed identity."""
        if not passed_signatures:
            return False
        try:
            warnings = self._find_missed_checkpoint_warning_elements()
        except Exception:
            return False
        signatures: set[str] = set()
        for warning in warnings:
            try:
                if not warning.is_displayed():
                    continue
                candidate = self._missed_checkpoint_candidate_for_warning(
                    warning,
                )
                possible_signatures = self._warning_course_signatures(warning)
                if candidate is not None:
                    possible_signatures.add(candidate.signature)
                matched = possible_signatures.intersection(passed_signatures)
                if not matched:
                    return False
                signatures.update(matched)
            except StaleElementReferenceException:
                return False
        return bool(signatures) and signatures.issubset(passed_signatures)

    def _warning_course_signatures(self, warning: WebElement) -> set[str]:
        """Recover a course identity even if its stale replay action vanished."""
        signatures: set[str] = set()
        current = warning
        for _ in range(12):
            try:
                if compact_text(current.tag_name).lower() in {'body', 'html'}:
                    break
                text = self._element_text(current)
                if has_missed_checkpoint_warning(text):
                    stable_id = self._stable_course_id(current)
                    if stable_id:
                        signatures.add(f'id:{stable_id}')
                    fingerprint = self._course_card_fingerprint(text)
                    if fingerprint:
                        signatures.add(f'card:{fingerprint}')
                current = current.find_element(By.XPATH, '..')
            except (
                NoSuchElementException,
                InvalidSelectorException,
                WebDriverException,
            ):
                break
            except StaleElementReferenceException:
                return set()
        return signatures

    def _find_missed_checkpoint_candidates(
        self,
    ) -> tuple[list[MissedCheckpointCandidate], bool]:
        implicit_wait_changed = False
        try:
            self.driver.implicitly_wait(0)
            implicit_wait_changed = True
        except AttributeError:
            pass
        try:
            warnings = self._find_missed_checkpoint_warning_elements()
            visible_warnings: list[tuple[int, WebElement]] = []
            warning_found = False
            for warning in warnings:
                try:
                    text = self._element_text(warning)
                    if warning.is_displayed() and has_missed_checkpoint_warning(text):
                        warning_found = True
                        visible_warnings.append((len(text), warning))
                except StaleElementReferenceException:
                    # A matched warning going stale means the list is refreshing;
                    # retry instead of accepting the day as complete.
                    warning_found = True
                    continue

            # The DOM query already returns document order. Preserve it so formal
            # playback starts from the first affected course shown on the page.
            raw_candidates: list[MissedCheckpointCandidate] = []
            for _, warning in visible_warnings:
                candidate = self._missed_checkpoint_candidate_for_warning(warning)
                if candidate:
                    raw_candidates.append(candidate)

            # Test doubles and fallback XPath queries can return both a warning
            # node and its ancestors. Keep one row per actionable element before
            # disambiguating genuinely identical course cards.
            unique_candidates: list[MissedCheckpointCandidate] = []
            seen_buttons: set[tuple[str, object]] = set()
            for candidate in raw_candidates:
                remote_id = compact_text(getattr(candidate.button, 'id', ''))
                button_key = (
                    ('webdriver', remote_id)
                    if remote_id
                    else ('object', id(candidate.button))
                )
                if button_key in seen_buttons:
                    continue
                seen_buttons.add(button_key)
                unique_candidates.append(candidate)
            raw_candidates = unique_candidates

            signature_counts: dict[str, int] = {}
            for candidate in raw_candidates:
                signature_counts[candidate.signature] = (
                    signature_counts.get(candidate.signature, 0) + 1
                )
            signature_occurrences: dict[str, int] = {}
            candidates: list[MissedCheckpointCandidate] = []
            seen_stable_signatures: set[str] = set()
            for candidate in raw_candidates:
                if candidate.signature.startswith('id:'):
                    if candidate.signature in seen_stable_signatures:
                        continue
                    seen_stable_signatures.add(candidate.signature)
                    candidates.append(candidate)
                    continue
                if signature_counts[candidate.signature] == 1:
                    candidates.append(candidate)
                    continue
                occurrence = signature_occurrences.get(candidate.signature, 0)
                signature_occurrences[candidate.signature] = occurrence + 1
                candidates.append(MissedCheckpointCandidate(
                    button=candidate.button,
                    signature=f'{candidate.signature}|occurrence:{occurrence}',
                    label=candidate.label,
                ))
            return candidates, warning_found
        finally:
            if implicit_wait_changed:
                self.driver.implicitly_wait(3)

    def _find_missed_checkpoint_warning_elements(self) -> list[WebElement]:
        """Return only the deepest visible DOM nodes that contain the warning."""
        try:
            warnings = self.driver.execute_script(
                """
                const result = [];
                const seen = new Set();
                const walker = document.createTreeWalker(
                    document.body,
                    NodeFilter.SHOW_TEXT
                );
                let node;
                while ((node = walker.nextNode())) {
                    const ownText = (node.nodeValue || '')
                        .replace(/\\s+/g, ' ')
                        .trim();
                    if (
                        !ownText.includes('错过')
                        && !ownText.includes('再认真观看一次')
                    ) {
                        continue;
                    }
                    let element = node.parentElement;
                    for (let depth = 0; element && depth < 5; depth += 1) {
                        const text = (element.textContent || '')
                            .replace(/\\s+/g, ' ')
                            .trim();
                        const matched = (
                            (text.includes('错过')
                                && text.includes('所有')
                                && (text.includes('检测点') || text.includes('检查点')))
                            || text.includes('再认真观看一次')
                        );
                        if (matched) {
                            if (!seen.has(element)) {
                                seen.add(element);
                                result.push(element);
                            }
                            break;
                        }
                        element = element.parentElement;
                    }
                }
                return result;
                """
            )
            if warnings is not None:
                return list(warnings)
        except (AttributeError, InvalidSessionIdException, NoSuchWindowException):
            pass
        return self.driver.find_elements(By.XPATH, MISSED_CHECKPOINT_WARNING_XPATH)

    def _missed_checkpoint_candidate_for_warning(
        self,
        warning: WebElement,
    ) -> MissedCheckpointCandidate | None:
        current = warning
        for _ in range(12):
            try:
                if compact_text(current.tag_name).lower() in {'body', 'html'}:
                    return None
                status_text = self._element_text(current)
                if has_missed_checkpoint_warning(status_text):
                    replay_buttons = current.find_elements(
                        By.XPATH,
                        ".//*[contains(normalize-space(.), '已学完')]",
                    )
                    replay_buttons = [
                        button for button in replay_buttons
                        if self._is_visible_replay_button(button, status_text)
                    ]
                    replay_buttons.sort(key=self._replay_button_score)
                    if replay_buttons:
                        button = self._actionable_replay_element(
                            replay_buttons[0],
                            current,
                        )
                        label = self._course_label(status_text)
                        return MissedCheckpointCandidate(
                            button=button,
                            signature=self._course_signature(current, button),
                            label=label,
                        )
                current = current.find_element(By.XPATH, '..')
            except (
                NoSuchElementException,
                StaleElementReferenceException,
                InvalidSelectorException,
                WebDriverException,
            ):
                return None
        return None

    def _is_visible_replay_button(
        self,
        button: WebElement,
        status_text: str,
    ) -> bool:
        try:
            action_text = self._element_text(button)
            return (
                button.is_displayed()
                and len(compact_text(action_text)) <= 48
                and requires_checkpoint_replay(status_text, action_text)
            )
        except StaleElementReferenceException:
            return False

    def _actionable_replay_element(
        self,
        element: WebElement,
        container: WebElement | None,
    ) -> WebElement:
        current = element
        for _ in range(5):
            try:
                tag_name = compact_text(current.tag_name).lower()
                role = compact_text(current.get_attribute('role')).lower()
                class_name = compact_text(current.get_attribute('class')).lower()
                if (
                    tag_name in {'button', 'a'}
                    or role == 'button'
                    or 'btn' in class_name
                    or 'button' in class_name
                ):
                    return current
                if container is not None and current == container:
                    break
                current = current.find_element(By.XPATH, '..')
            except (
                NoSuchElementException,
                StaleElementReferenceException,
                InvalidSelectorException,
                WebDriverException,
            ):
                break
        return element

    def _return_to_course_list_if_open(self, force: bool = False) -> None:
        try:
            if force or len(self.driver.window_handles) > 1:
                self.close_and_switch()
        except (
            IndexError,
            InvalidSessionIdException,
            NoSuchElementException,
            NoSuchWindowException,
        ):
            return

    def _finish_open_lesson(
        self,
        btn: WebElement,
        *,
        stop_after_checkpoint: bool = False,
    ) -> bool:
        """Finish one lesson and always restore the course list on failure."""
        original_handle = self.driver.current_window_handle
        original_handles = set(self.driver.window_handles)
        original_url = self.driver.current_url
        try:
            return self.finish_a_lesson(
                btn,
                stop_after_checkpoint=stop_after_checkpoint,
            )
        except BrowserControlRequested:
            raise
        except (CheckpointInteractionRequiredError, HumanVerificationRequiredError):
            # Leave the browser on the visible slider so the user can inspect
            # or finish it; the runner stops and will not claim completion.
            raise
        except Exception:
            try:
                opened = (
                    bool(set(self.driver.window_handles) - original_handles)
                    or self.driver.current_window_handle != original_handle
                    or self.driver.current_url != original_url
                )
            except (InvalidSessionIdException, NoSuchWindowException):
                opened = False
            self._return_to_course_list_if_open(force=opened)
            raise

    def _replay_button_score(self, element: WebElement) -> tuple[int, int, float]:
        try:
            text = compact_text(self._element_text(element))
            tag_name = compact_text(element.tag_name).lower()
            role = compact_text(element.get_attribute('role')).lower()
            class_name = compact_text(element.get_attribute('class')).lower()
            action_like = (
                tag_name in {'button', 'a'}
                or role == 'button'
                or 'btn' in class_name
                or 'button' in class_name
            )
            rect = element.rect or {}
            area = max(1.0, float(rect.get('width', 0)) * float(rect.get('height', 0)))
            return (0 if text == '已学完' else 1, 0 if action_like else 1, area)
        except (StaleElementReferenceException, TypeError, ValueError):
            return (2, 2, float('inf'))

    def _course_signature(self, container: WebElement, button: WebElement) -> str:
        container_text = self._element_text(container)
        label = self._course_label(container_text)
        stable_id = self._stable_course_id(button)
        if stable_id:
            return f'id:{stable_id}'
        return f'card:{self._course_card_fingerprint(container_text) or label}'

    def _stable_course_id(self, element: WebElement) -> str:
        ancestors: list[WebElement] = []
        current = element
        for _ in range(8):
            try:
                if compact_text(getattr(current, 'tag_name', '')).lower() in {
                    'body', 'html',
                }:
                    ancestors.append(current)
                    break
                ancestors.append(current)
                current = current.find_element(By.XPATH, '..')
            except (
                NoSuchElementException,
                StaleElementReferenceException,
                InvalidSelectorException,
                WebDriverException,
            ):
                break
        for name in (
            'data-task-id', 'data-homework-id', 'data-lesson-id',
            'data-course-id', 'data-id', 'href', 'id',
        ):
            for ancestor in ancestors:
                try:
                    value = compact_text(ancestor.get_attribute(name))
                except StaleElementReferenceException:
                    return ''
                if value:
                    return f'{name}:{value[:240]}'
        return ''

    @staticmethod
    def _course_card_fingerprint(status_text: str) -> str:
        """Normalize a course card without layout coordinates or stale state."""
        text = MISSED_CHECKPOINT_RE.sub(' ', compact_text(status_text))
        text = re.sub(r'吧(?=[！!。.]|$)', ' ', text)
        text = re.sub(
            r'(?:已学完|去学习|开始学习|继续学习|重新学习|播放|重看)',
            ' ',
            text,
        )
        text = re.sub(
            r'(?:学|已看)\s*\d+(?:\.\d+)?\s*[%％]|完成\s*\d+\s*/\s*\d+',
            ' ',
            text,
        )
        return compact_text(text).strip(' ，。！？!')[:300]

    @staticmethod
    def _course_label(status_text: str) -> str:
        text = compact_text(status_text)
        warning = MISSED_CHECKPOINT_RE.search(text)
        if warning:
            text = text[:warning.start()]
        text = text.replace('已学完', '').strip(' ，。！？!')
        return text[:80] or '未命名课程'

    @staticmethod
    def _element_text(element: WebElement) -> str:
        text = element.get_attribute('innerText')
        if not text:
            text = element.get_attribute('textContent')
        return compact_text(text)

    def _get_duration(self):
        """获取视频总时长（秒），获取失败返回 None"""
        try:
            duration_text = self.driver.find_element(
                By.CSS_SELECTOR, '.vjs-duration-display'
            ).get_attribute('textContent')
            parts = duration_text.split(':')
            duration = int(parts[0]) * 60 + int(parts[1])
            return duration
        except Exception:
            return None

    def _create_pbar(self, duration):
        return ProgressTqdm(
            total=duration,
            title='视频播放进度',
            unit_label='秒',
            desc='播放进度',
            ncols=100,
            unit_scale=True,
            bar_format='{l_bar}{bar}| {n_fmt}秒/{total_fmt}秒',
        )

    def finish_a_lesson(
        self,
        btn: WebElement,
        *,
        stop_after_checkpoint: bool = False,
    ) -> bool:
        """
        完成一节课，应对各种突发情况
        :param btn: “学”按钮
        :param stop_after_checkpoint: 漏检重看课程通过一个检查点后立即结束
        :return: 是否因顺利通过检查点而提前完成
        """
        self.click_and_switch(btn)
        video = self.driver.find_element(By.TAG_NAME, 'video')
        self._attention_checkpoint_started_at = None
        self._attention_checkpoint_logged = False
        self._unknown_overlay_started_at = None
        self._missed_checkpoint_seen = False
        time.sleep(3 * self.config.get('delay_multiplier'))
        self._raise_if_playback_blocked()
        initial_state = self._handle_checkpoint_state(video)
        if initial_state == 'missed':
            raise CheckpointMissedError(
                '视频进入时已显示“我知道了”漏检提示，停止本节课'
            )
        if initial_state == 'handled' and stop_after_checkpoint:
            logging.info('漏检重看课程已顺利通过检查点，结束本次播放')
            self._return_to_course_list_after_checkpoint()
            return True
        initial_overlay = self._has_unknown_visible_overlay()
        try:
            if initial_state == 'none' and not initial_overlay:
                self.driver.find_element(By.CLASS_NAME, 'vjs-big-play-button').click()
        except InvalidSessionIdException:
            pass
        except NoSuchElementException:
            pass
        logging.info("播放视频")
        duration = self._get_duration()
        checkpoint_passed = False
        with self._create_pbar(duration) as pbar:
            while True:
                self.check_control_requests()
                self._raise_if_playback_blocked()
                checkpoint_state = self._handle_checkpoint_state(video)
                if checkpoint_state == 'missed':
                    raise CheckpointMissedError(
                        '视频弹出“我知道了”漏检提示，本节课未通过检查点，已停止并返回重看流程'
                    )
                if checkpoint_state in {'manual', 'pending'}:
                    time.sleep(0.25)
                    continue
                if checkpoint_state == 'handled':
                    if stop_after_checkpoint:
                        checkpoint_passed = True
                        break
                    time.sleep(max(0.2, self.config.get('delay_multiplier', 1.0)))
                    continue
                ended = bool(self.driver.execute_script(
                    'return Boolean(arguments[0] && arguments[0].ended);',
                    video,
                ))
                if ended:
                    break

                # 只有确认不存在弹窗/检查点后，才尝试恢复意外暂停。
                self._resume_if_paused(video, pbar)

                #从页面上已显示的时长文本解析（最简单可靠）
                current_time_text = self.driver.find_element(
                    By.CSS_SELECTOR, ".vjs-current-time-display"
                ).get_attribute("textContent")
                # 转换为秒数
                parts_current = current_time_text.split(":")
                current_time = int(parts_current[0]) * 60 + int(parts_current[1])
                pbar.n = current_time
                pbar.refresh()
                time.sleep(1 * self.config.get('delay_multiplier'))

            if duration is not None and not checkpoint_passed:
                pbar.n = duration
                pbar.refresh()
        if checkpoint_passed:
            # ProgressTqdm.close() emits its final video-time snapshot. Publish
            # the authoritative checkpoint completion only after leaving the
            # context so the GUI cannot overwrite 100% with stale seconds.
            logging.info('漏检重看课程已顺利通过检查点，结束本次播放')
            self._return_to_course_list_after_checkpoint()
            return True
        logging.info('好诶~完成啦~')

        self.close_and_switch()
        return False

    def _return_to_course_list_after_checkpoint(self) -> None:
        """Return from a replay lesson without ever quitting the browser session."""
        emit_progress(
            '检查点已通过',
            1,
            1,
            '个',
            finished=True,
            kind='current_course',
            scope='active',
        )
        logging.info('检查点已确认，返回课程列表；保持当前浏览器会话，不退出浏览器')
        self.close_and_switch()

    def _handle_checkpoint_state(self, video: WebElement) -> str:
        """Handle a real checkpoint or a failed-checkpoint dialog.

        Returns ``handled`` after clicking a real action, ``missed`` for the
        post-failure acknowledgement, and ``none`` when no checkpoint is shown.
        """
        try:
            try:
                video_paused = bool(self.driver.execute_script(
                    'return Boolean(arguments[0] && arguments[0].paused);',
                    video,
                ))
            except Exception:
                video_paused = True
            elements = self.driver.find_elements(
                By.XPATH,
                "//*[contains(normalize-space(.), '我知道了') or "
                "contains(normalize-space(.), '知道了')]",
            )
            for element in elements:
                if element.is_displayed() and compact_text(self._element_text(element)) in {
                    '我知道了',
                    '知道了',
                }:
                    class_name = compact_text(element.get_attribute('class')).lower()
                    if not (
                        self._element_has_missed_context(element)
                        or (video_paused and 'btn-ug8kt' in class_name)
                    ):
                        continue
                    logging.error('检测到“我知道了”漏检结果，停止本节课')
                    self.release_browser_topmost()
                    self._missed_checkpoint_seen = True
                    try:
                        self.driver.execute_script(
                            'arguments[0].pause();',
                            video,
                        )
                    except Exception:
                        pass
                    return 'missed'

            if self._attention_checkpoint_visible():
                self._last_checkpoint_kind = 'puzzle'
                now = time.monotonic()
                if self._attention_checkpoint_started_at is None:
                    self._attention_checkpoint_started_at = now
                try:
                    self.driver.execute_script(
                        'if (arguments[0]) arguments[0].pause();',
                        video,
                    )
                except Exception:
                    pass
                if self.is_headless and not self.config.get('_background_manual_session'):
                    raise HumanVerificationRequiredError(
                        '看课页面出现认真度拼图验证',
                        kind='attention_checkpoint',
                    )
                if not self._attention_checkpoint_logged:
                    logging.warning(
                        '检测到认真度拼图检查点，请在浏览器中手动拖动滑块；'
                        '程序不会自动破解或点击该验证'
                    )
                    self.emit_manual_intervention(
                        'attention_checkpoint',
                        '看课页面出现认真度拼图验证',
                    )
                    if self.config.get('foreground_on_manual'):
                        self.bring_browser_to_front(
                            '需要人工完成认真度验证',
                            keep_topmost=True,
                        )
                    self._attention_checkpoint_logged = True
                return 'manual'

            manual_was_active = self._attention_checkpoint_logged
            if manual_was_active:
                # The slider disappearing is not enough by itself: the site can
                # mount a delayed missed-checkpoint acknowledgement. Settle the
                # result first, then hide the same browser session again.
                manual_result = self._settle_checkpoint_result(video)
                if manual_result in {'manual', 'pending'}:
                    return manual_result
                self._attention_checkpoint_started_at = None
                self._attention_checkpoint_logged = False
                self.release_browser_topmost()
                self.hide_browser_window()
                self.emit_manual_intervention(
                    'attention_checkpoint',
                    '认真度拼图验证已完成',
                    phase='resolved',
                )
                logging.info(
                    '认真度拼图检查点结果已确认：%s；保持原浏览器会话继续运行',
                    manual_result,
                )
                return manual_result

            self._attention_checkpoint_started_at = None
            self._attention_checkpoint_logged = False
            self.release_browser_topmost()

            actions = self.driver.find_elements(
                By.XPATH,
                "//*[contains(normalize-space(.), '点击通过检查') or "
                "contains(normalize-space(.), '通过检查') or "
                "contains(normalize-space(.), '跳过') or "
                "contains(normalize-space(.), '继续播放')]",
            )
            actions = [
                item for item in actions
                if item.is_displayed()
                and compact_text(self._element_text(item)) in CHECKPOINT_ACTION_LABELS
                and self._is_checkpoint_action_context(item)
            ]
            if not actions:
                return 'none'
            action = min(actions, key=lambda item: self._action_area(item))
            before = compact_text(self._element_text(action))
            signature = self._checkpoint_action_signature(action, before)
            now = time.monotonic()
            if (
                signature == getattr(self, '_last_checkpoint_action_signature', '')
                and now - getattr(self, '_last_checkpoint_action_at', 0.0) < 1.5
            ):
                return 'pending'
            self._last_checkpoint_action_signature = signature
            self._last_checkpoint_action_at = now
            if getattr(self, '_last_checkpoint_kind', '') == 'puzzle':
                self.emit_manual_intervention(
                    'checkpoint_notice',
                    '上一次检查点为拼图验证；本次检测到普通检查点，程序将自动处理。',
                    phase='notice',
                )
            self._last_checkpoint_kind = 'automatic'
            self.click(self._actionable_replay_element(action, None))
            logging.info('点击了检查点或答题点：%s', before)
            deadline = time.monotonic() + max(2.0, 5.0 * self.config.get('delay_multiplier', 1.0))
            while time.monotonic() < deadline:
                try:
                    if not action.is_displayed() or compact_text(self._element_text(action)) != before:
                        return self._settle_checkpoint_result(video)
                except StaleElementReferenceException:
                    return self._settle_checkpoint_result(video)
                time.sleep(0.2)
            raise CheckpointInteractionRequiredError(
                '检查点按钮点击后仍可见，无法确认已通过；已停止且不会恢复播放'
            )
        except StaleElementReferenceException:
            return 'missed' if self._missed_checkpoint_seen else 'pending'

    def _settle_checkpoint_result(self, video: WebElement) -> str:
        """Let a delayed failure/manual result mount before declaring success."""
        deadline = time.monotonic() + max(
            CHECKPOINT_RESULT_SETTLE_SECONDS,
            CHECKPOINT_RESULT_SETTLE_SECONDS
            * self.config.get('delay_multiplier', 1.0),
        )
        while time.monotonic() < deadline:
            state = self._checkpoint_result_state(video)
            if state != 'none':
                return state
            time.sleep(0.1)
        return 'handled'

    def _checkpoint_result_state(self, video: WebElement) -> str:
        """Inspect only terminal states; never click another checkpoint action."""
        self._raise_if_playback_blocked()
        try:
            video_paused = bool(self.driver.execute_script(
                'return Boolean(arguments[0] && arguments[0].paused);',
                video,
            ))
        except Exception:
            video_paused = True
        try:
            elements = self.driver.find_elements(
                By.XPATH,
                "//*[contains(normalize-space(.), '我知道了') or "
                "contains(normalize-space(.), '知道了')]",
            )
            for element in elements:
                text = compact_text(self._element_text(element))
                if not element.is_displayed() or text not in {'我知道了', '知道了'}:
                    continue
                class_name = compact_text(element.get_attribute('class')).lower()
                if (
                    self._element_has_missed_context(element)
                    or (video_paused and 'btn-ug8kt' in class_name)
                ):
                    self._missed_checkpoint_seen = True
                    return 'missed'
        except StaleElementReferenceException:
            return 'pending'
        if self._attention_checkpoint_visible():
            return 'manual'
        if self._has_unknown_visible_overlay():
            return 'pending'
        return 'none'

    def _element_has_missed_context(self, element: WebElement) -> bool:
        """Require a paused/checkpoint context before treating an exact ack as failure."""
        current = element
        for _ in range(6):
            try:
                if compact_text(getattr(current, 'tag_name', '')).lower() in {
                    'body',
                    'html',
                }:
                    break
                text = self._element_text(current)
                if any(token in text for token in (
                    '错过', '未通过', '漏检', '认真观看一次', '重新观看'
                )):
                    return True
                current = current.find_element(By.XPATH, '..')
            except (
                NoSuchElementException,
                StaleElementReferenceException,
                InvalidSessionIdException,
                InvalidSelectorException,
                WebDriverException,
            ):
                break
        return False

    @staticmethod
    def _action_area(element: WebElement) -> float:
        try:
            rect = element.rect or {}
            return max(1.0, float(rect.get('width', 0)) * float(rect.get('height', 0)))
        except (TypeError, ValueError, StaleElementReferenceException):
            return float('inf')

    @staticmethod
    def _checkpoint_action_signature(element: WebElement, text: str) -> str:
        try:
            rect = element.rect or {}
            return '|'.join((
                text,
                str(round(float(rect.get('x', 0)) / 8)),
                str(round(float(rect.get('y', 0)) / 8)),
            ))
        except (TypeError, ValueError, StaleElementReferenceException):
            return text

    def _is_checkpoint_action_context(self, element: WebElement) -> bool:
        """Only click actions tied to a checkpoint/answer surface."""
        text = compact_text(self._element_text(element))
        current = element
        for _ in range(6):
            try:
                if compact_text(getattr(current, 'tag_name', '')).lower() in {
                    'body',
                    'html',
                }:
                    break
                ancestor_text = self._element_text(current)
                class_name = compact_text(current.get_attribute('class')).lower()
                if (
                    any(token in ancestor_text for token in (
                        '检测点', '检查点', '认真度', '认真观看', '答题点'
                    ))
                    or 'checkpoint' in class_name
                    or 'check_point' in class_name
                    or 'earnest_check' in class_name
                ):
                    return True
                current = current.find_element(By.XPATH, '..')
            except (
                NoSuchElementException,
                StaleElementReferenceException,
                InvalidSelectorException,
                WebDriverException,
            ):
                break
        return False

    def _raise_if_playback_blocked(self) -> None:
        try:
            blocked = self.driver.execute_script(
                """
                const text = (document.body && document.body.innerText) || '';
                return text.includes('检测到网络不稳定或开启了第三方辅助工具')
                    || text.includes('学习数据无法被记录');
                """
            )
        except (InvalidSessionIdException, NoSuchWindowException):
            return
        if not blocked:
            return
        try:
            self.driver.execute_script(
                "const video = document.querySelector('video'); if (video) video.pause();"
            )
        except (InvalidSessionIdException, NoSuchWindowException):
            pass
        raise PlaybackBlockedError(
            '站点已暂停视频：检测到网络不稳定或第三方辅助工具；'
            '当前任务已停止且不会自动重试'
        )

    def finish_a_click(self, btn: WebElement):
        self.click_and_switch(btn)
        logging.info('好诶~完成啦~')
        self.close_and_switch()

    def _resume_if_paused(self, video: WebElement, pbar):
        """
        检查视频是否被暂停，如果暂停则恢复播放
        https://github.com/zhdbk3/AutoEwt/pull/12/changes/46ac6a7e68f0724df552be6081b241e2f09173c7#diff-7e8f19105f96cbaa19843e7461b4b68b16d6cfedf1629ca0fc37772a4bb3936dR193-R203
        """
        ended = bool(self.driver.execute_script(
            'return Boolean(arguments[0] && arguments[0].ended);',
            video,
        ))
        if ended:
            return

        paused = self.driver.execute_script('return arguments[0].paused;', video)
        if paused:
            # A pause event can arrive before its modal DOM. Observe the whole
            # mounting window instead of issuing play() after one short sleep.
            for delay in PAUSED_CHECKPOINT_GRACE_DELAYS:
                time.sleep(delay)
                state = self._handle_checkpoint_state(video)
                if state != 'none' or self._has_unknown_visible_overlay():
                    now = time.monotonic()
                    if self._unknown_overlay_started_at is None:
                        self._unknown_overlay_started_at = now
                    elif now - self._unknown_overlay_started_at > UNKNOWN_OVERLAY_WAIT_LIMIT:
                        raise CheckpointInteractionRequiredError(
                            '视频暂停弹层长时间未消失，无法确认检查点状态；已停止且不会恢复播放'
                        )
                    logging.warning('检测到暂停检查点或弹层，暂不强制恢复视频')
                    return
            self._unknown_overlay_started_at = None
            self.driver.execute_script('arguments[0].play();', video)
            logging.info('正在尝试以方式3重新播放视频')
            pbar.refresh()
        else:
            self._unknown_overlay_started_at = None

    def _has_unknown_visible_overlay(self) -> bool:
        try:
            return bool(self.driver.execute_script(
                """
                const selector = "[role='dialog'], [aria-modal='true'], .ant-modal, "
                    + ".ant-modal-mask, [class*='modal'], [class*='dialog'], "
                    + "[class*='overlay'], [class*='mask'], "
                    + "[class*='captcha'], #captcha, "
                    + "[class*='spc_video_earnest_check_box'], [class*='earnest_check']";
                return Array.from(document.querySelectorAll(selector)).some(function(element) {
                    var style = getComputedStyle(element);
                    var rect = element.getBoundingClientRect();
                    var zIndex = Number(style.zIndex || 0);
                    var largeFloating = ['fixed', 'absolute'].includes(style.position)
                        && rect.width * rect.height >= innerWidth * innerHeight * 0.08
                        && zIndex >= 10;
                    var explicitModal = element.getAttribute('aria-modal') === 'true'
                        || element.getAttribute('role') === 'dialog'
                        || element.classList.contains('ant-modal')
                        || element.classList.contains('ant-modal-mask')
                        || element.id === 'captcha'
                        || /captcha|earnest_check/.test(String(element.className || ''));
                    var playerControl = Boolean(element.closest('.video-js, .vjs-control-bar'))
                        && !/captcha|earnest_check/.test(String(element.className || ''));
                    return style.display !== 'none' && style.visibility !== 'hidden'
                        && rect.width > 20 && rect.height > 20
                        && !playerControl
                        && (largeFloating || explicitModal);
                });
                """
            ))
        except Exception:
            return True

    def _attention_checkpoint_visible(self) -> bool:
        """Detect the site's graphical attention slider without interacting with it."""
        try:
            return bool(self.driver.execute_script(
                """
                const selector = "[class*='spc_video_earnest_check_box'], "
                    + "[class*='earnest_check'], #captcha, .ecaptcha_wrapper_content";
                return Array.from(document.querySelectorAll(selector)).some(function(element) {
                    const style = getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    const text = (element.innerText || element.textContent || '')
                        .replace(/\\s+/g, ' ').trim();
                    const semantic = text.includes('认真度检测')
                        || text.includes('近期看课操作异常')
                        || text.includes('将图形拖动至正确位置')
                        || text.includes('向右拖动滑块填充拼图')
                        || text.includes('后将错过当前检测');
                    return semantic && style.display !== 'none'
                        && style.visibility !== 'hidden'
                        && rect.width > 20 && rect.height > 20;
                });
                """
            ))
        except (InvalidSessionIdException, NoSuchWindowException):
            return False
        except Exception:
            # Fail closed if the browser cannot be inspected while paused.
            return True
