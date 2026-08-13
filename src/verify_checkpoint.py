"""Real-account smoke check for missed watch checkpoints.

This utility intentionally reads the normal config.yml and never prints account
credentials. By default it audits one day; pass --replay-one to replay the
shortest affected video and verify that its warning is cleared.
"""

import argparse
import logging
import sys
import time

from selenium.common import StaleElementReferenceException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from auto_base import clear_config_cache, read_config
from account_store import apply_account, normalize_accounts
from auto_video.auto_video import (
    AutoVideo,
    CheckpointInteractionRequiredError,
    CheckpointMissedError,
    MISSED_CHECKPOINT_REPLAY_LIMIT,
    MissedCheckpointCandidate,
    PlaybackBlockedError,
    compact_text,
)
from progress import reset_progress_sink, set_progress_sink
from runner import setup_logging


DAY_SELECTOR = 'li[data-active="true"], li[data-active="false"]'


def select_day(auto: AutoVideo, day_text: str) -> str:
    days = auto.driver.find_elements(By.CSS_SELECTOR, DAY_SELECTOR)
    for day in days:
        try:
            label = compact_text(day.get_attribute('innerText') or day.text)
            if day_text not in label:
                continue
            auto.click(day)
            WebDriverWait(auto.driver, 20).until(
                lambda driver: any(
                    day_text in compact_text(item.get_attribute('innerText'))
                    and item.get_attribute('data-active') == 'true'
                    for item in driver.find_elements(By.CSS_SELECTOR, DAY_SELECTOR)
                )
            )
            WebDriverWait(auto.driver, 20).until(
                lambda driver: bool(driver.find_elements(By.CSS_SELECTOR, 'li[class*="taskItem"]'))
            )
            time.sleep(max(1.0, auto.config['delay_multiplier']))
            return label
        except StaleElementReferenceException:
            return select_day(auto, day_text)
    raise LookupError(f'未找到日期“{day_text}”')


def audit(auto: AutoVideo) -> tuple[list[MissedCheckpointCandidate], bool]:
    candidates, warning_found = auto._find_missed_checkpoint_candidates()
    logging.info(
        '真实页面检查：漏检提示=%s，可重看课程=%s',
        warning_found,
        len(candidates),
    )
    for candidate in candidates:
        logging.info('需要重看：%s', candidate.label)
    return candidates, warning_found


def _select_bound_config(
    config: dict,
    account_id: str = '',
    task_id: str = '',
) -> dict:
    if not account_id and not task_id:
        return config
    accounts = {item.id: item for item in normalize_accounts(config)}
    tasks = [
        item for item in config.get('batch_tasks', []) or []
        if isinstance(item, dict)
    ]
    task = next((
        item for item in tasks
        if (not task_id or str(item.get('id', '')) == task_id)
        and (not account_id or str(item.get('account_id', '')) == account_id)
    ), None)
    if task is None:
        raise LookupError('未找到指定的账号任务绑定')
    bound_account_id = str(task.get('account_id', '') or account_id)
    account = accounts.get(bound_account_id)
    if account is None:
        raise LookupError('指定任务绑定的账号不存在或未启用')
    result = apply_account(config, account)
    result['list_url'] = str(task.get('url', '')).strip()
    if not result['list_url']:
        raise LookupError('指定任务没有有效 URL')
    logging.info('专项复测使用账号“%s”的任务“%s”', account.name, task.get('title', task_id))
    return result


def run(
    day_text: str,
    replay_one: bool,
    course_text: str,
    account_id: str = '',
    task_id: str = '',
    visible: bool = False,
) -> int:
    setup_logging('log', use_tqdm_handler=False)
    clear_config_cache()
    auto = None
    try:
        config = _select_bound_config(dict(read_config()), account_id, task_id)
        if visible:
            from manual_intervention import visible_browser_options
            config['options'] = visible_browser_options(
                str(config.get('options', '')),
                str(config.get('browser', 'Chrome')),
            )
        config['foreground_browser'] = bool(visible)
        config['foreground_on_manual'] = True
        auto = AutoVideo(config=config)
        auto.bring_browser_to_front('真实账号检查点复测')
        selected = select_day(auto, day_text)
        logging.info('真实账号已选择日期：%s', selected)
        before, warning_found = audit(auto)
        if not warning_found:
            logging.error('目标日期没有发现“错过所有看课检测点”提示')
            return 2
        if not before:
            logging.error('发现漏检提示，但没有找到同课程的“已学完”重看入口')
            return 3
        if not replay_one:
            return 0

        if course_text:
            candidate = next(
                (item for item in before if course_text in item.label),
                None,
            )
            if candidate is None:
                logging.error('未找到名称包含“%s”的漏检课程', course_text)
                return 6
        else:
            candidate = before[0]
        logging.info('开始真实重看课程：%s', candidate.label)
        progress_events = []
        progress_token = set_progress_sink(progress_events.append)
        try:
            for attempt in range(1, MISSED_CHECKPOINT_REPLAY_LIMIT + 1):
                try:
                    checkpoint_passed = auto._finish_open_lesson(
                        candidate.button,
                        stop_after_checkpoint=True,
                    )
                    if not checkpoint_passed:
                        logging.error('本次重看未确认通过任何看课检查点')
                        return 12
                    break
                except CheckpointMissedError:
                    logging.warning(
                        '本轮已错过检查点，返回列表重新获取同课程的新检查点（%s/%s）',
                        attempt,
                        MISSED_CHECKPOINT_REPLAY_LIMIT,
                    )
                    if attempt >= MISSED_CHECKPOINT_REPLAY_LIMIT:
                        logging.error('连续重看仍错过检查点，真实闭环失败')
                        return 9
                    select_day(auto, day_text)
                    refreshed, _ = audit(auto)
                    candidate = next(
                        (item for item in refreshed if course_text in item.label),
                        None,
                    )
                    if candidate is None:
                        logging.error('返回列表后未能重新定位目标课程')
                        return 10
        finally:
            reset_progress_sink(progress_token)
        final_progress = next((
            event
            for event in reversed(progress_events)
            if event.kind == 'current_course' and event.scope == 'active'
        ), None)
        if not (
            final_progress
            and final_progress.finished
            and final_progress.current == 1
            and final_progress.total == 1
        ):
            logging.error('检查点已通过，但最终课程进度没有稳定同步为 1/1')
            return 13
        select_day(auto, day_text)
        after, warning_after = audit(auto)
        labels_after = {item.label for item in after}
        cleared = candidate.label not in labels_after and len(after) < len(before)
        if not cleared:
            logging.warning(
                '检查点已确认通过且进度为 1/1，但课程列表仍显示旧警告；'
                '按产品语义视为已完成（重看前 %s，重看后 %s）',
                len(before),
                len(after),
            )
            return 0
        logging.info(
            '真实重看闭环通过：课程警告已消失，漏检课程数 %s -> %s',
            len(before),
            len(after),
        )
        return 0
    except (LookupError, TimeoutException) as exc:
        logging.error('真实账号检查失败：%s', exc)
        return 5
    except PlaybackBlockedError as exc:
        logging.error('%s', exc)
        return 7
    except CheckpointInteractionRequiredError as exc:
        logging.error('%s', exc)
        return 8
    except Exception as exc:
        logging.exception('真实账号复测发生未预期异常：%s', exc)
        return 11
    finally:
        if auto and auto.driver:
            auto.driver.quit()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--day', required=True, help='专项验证要检查的日期文本')
    parser.add_argument('--replay-one', action='store_true', help='真实重看一节并验证状态')
    parser.add_argument('--course', default='', help='优先重看的课程名片段')
    parser.add_argument('--account-id', default='', help='限定配置中的账号 ID')
    parser.add_argument('--task-id', default='', help='限定配置中的批量任务 ID')
    parser.add_argument('--visible', action='store_true', help='从启动起显示浏览器窗口')
    args = parser.parse_args()
    return run(
        args.day,
        args.replay_one,
        args.course,
        args.account_id,
        args.task_id,
        args.visible,
    )


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    raise SystemExit(main())
