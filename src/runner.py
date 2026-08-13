from __future__ import annotations

import datetime
import logging
import os
import sys
import threading
import time
import traceback
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

from selenium.common.exceptions import StaleElementReferenceException
from tqdm import tqdm

from auto_base import clear_config_cache, normalize_config, read_config
from manual_intervention import (
    BrowserControlRequested,
    HumanVerificationRequiredError,
    ManualInterventionEvent,
    ManualInterventionRequest,
    background_manual_config,
    is_headless_options,
    visible_handoff_config,
)
from auto_paper.auto_paper import AutoPaper
from auto_video.auto_video import (
    AutoVideo,
    CheckpointInteractionRequiredError,
    LessonProcessingError,
    MissedCheckpointReplayError,
    PlaybackBlockedError,
)
from progress import ProgressSink, reset_progress_sink, set_progress_sink
from system_notification import send_system_notification
from account_store import AccountProfile, apply_account


class TqdmLoggingHandler(logging.StreamHandler):
    """Write CLI logs through tqdm so progress bars stay readable."""

    def emit(self, record):
        msg = self.format(record)
        tqdm.write(msg, file=sys.stderr)
        self.flush()


_task_log_label: ContextVar[str] = ContextVar('task_log_label', default='')
_task_log_id: ContextVar[str] = ContextVar('task_log_id', default='')
_account_log_id: ContextVar[str] = ContextVar('account_log_id', default='')
_account_log_name: ContextVar[str] = ContextVar('account_log_name', default='')


class TaskContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        label = _task_log_label.get()
        record.task = f' [{label}]' if label else ''
        record.task_label = label
        record.task_id = _task_log_id.get()
        record.account_id = _account_log_id.get()
        record.account_name = _account_log_name.get()
        record.thread_name = threading.current_thread().name
        return True


@dataclass(frozen=True)
class RestartPolicy:
    initial_interval: int = 3
    max_interval: int = 300
    max_retries: int | None = 3


@dataclass(frozen=True)
class BatchTask:
    id: str
    title: str
    url: str
    account_id: str = ''


@dataclass(frozen=True)
class BatchTaskResult:
    task: BatchTask
    code: int
    error: str = ''


BatchStatusSink = Callable[[str, str], None]
BatchProgressSink = Callable[[str, object], None]
BatchResultSink = Callable[[BatchTaskResult], None]
ManualInterventionSink = Callable[[ManualInterventionEvent], None]
BatchManualInterventionSink = Callable[[str, ManualInterventionEvent], None]
NotificationSender = Callable[[str, str], bool]


def setup_logging(
    log_dir: str | Path = 'log',
    extra_handlers: Iterable[logging.Handler] = (),
    use_tqdm_handler: bool = True,
) -> Path:
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    now = datetime.datetime.now().strftime('%Y-%m-%d_%H.%M.%S')
    log_path = Path(log_dir) / f'log_{now}.txt'

    handlers: list[logging.Handler] = []
    if use_tqdm_handler:
        handlers.append(TqdmLoggingHandler())
    handlers.extend(extra_handlers)
    handlers.append(logging.FileHandler(log_path, encoding='utf-8'))
    task_filter = TaskContextFilter()
    for handler in handlers:
        handler.addFilter(task_filter)

    logging.basicConfig(
        level=logging.INFO,
        format='[%(asctime)s %(levelname)s]%(task)s %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=handlers,
        force=True,
    )
    return log_path


def log_startup_notice() -> None:
    logging.info('启动')
    logging.info('开源项目：https://github.com/zhdbk3/AutoEwt')
    logging.info('如通过购买方式获得本软件，请退款并举报卖家')
    logging.info('使用即视为同意项目 README.md 中的相关条款')


def build_auto(
    mode: str,
    config: Mapping | None = None,
    stop_event: threading.Event | None = None,
    visible_handoff_event: threading.Event | None = None,
    foreground_request_event: threading.Event | None = None,
    manual_intervention_sink: Callable[[ManualInterventionRequest], None]
    | None = None,
):
    match mode:
        case 'video':
            return AutoVideo(
                config=config,
                stop_event=stop_event,
                visible_handoff_event=visible_handoff_event,
                foreground_request_event=foreground_request_event,
                manual_intervention_sink=manual_intervention_sink,
            )
        case 'paper':
            return AutoPaper(
                config=config,
                stop_event=stop_event,
                visible_handoff_event=visible_handoff_event,
                foreground_request_event=foreground_request_event,
                manual_intervention_sink=manual_intervention_sink,
            )
        case _:
            raise ValueError('mode 只能是 video 或 paper，请检查配置文件')


class AutoEwtRunner:
    def __init__(
        self,
        stop_event: threading.Event | None = None,
        restart_policy: RestartPolicy = RestartPolicy(),
        progress_sink: ProgressSink | None = None,
        config: Mapping | None = None,
        task_label: str = '',
        task_id: str = '',
        account_id: str = '',
        account_name: str = '',
        manual_intervention_sink: ManualInterventionSink | None = None,
        notification_sender: NotificationSender | None = send_system_notification,
        account_configs: Mapping[str, AccountProfile] | None = None,
        manual_session_lock: threading.Lock | None = None,
    ):
        self.stop_event = stop_event or threading.Event()
        self.restart_policy = restart_policy
        self.progress_sink = progress_sink
        self.config = normalize_config(config) if config is not None else None
        self.task_label = task_label
        self.task_id = task_id
        self.account_id = account_id
        self.account_name = account_name
        self.manual_intervention_sink = manual_intervention_sink
        self.notification_sender = notification_sender
        self.account_configs = dict(account_configs or {})
        self.manual_session_lock = manual_session_lock
        self._manual_session_lock_held = False
        self._visible_handoff_requested = threading.Event()
        self._foreground_requested = threading.Event()
        self._persistent_preview_requested = threading.Event()
        self._active_auto = None
        self._active_lock = threading.Lock()

    def stop(self) -> None:
        self.stop_event.set()

    def request_visible_handoff(self) -> bool:
        """Promote only this running task from headless to a visible session."""
        self._persistent_preview_requested.set()
        with self._active_lock:
            auto = self._active_auto
        if auto is None:
            self._visible_handoff_requested.set()
            return True
        if getattr(auto, 'config', {}).get('_background_manual_session'):
            # The session is already a normal browser hidden in the
            # background. Promote this exact window; never rebuild it.
            self._foreground_requested.set()
            return True
        if not getattr(auto, 'is_headless', False):
            self._foreground_requested.set()
            return True
        self._visible_handoff_requested.set()
        return True

    def run(self) -> int:
        token = set_progress_sink(self.progress_sink)
        task_token = _task_log_label.set(self.task_label)
        task_id_token = _task_log_id.set(self.task_id)
        account_id_token = _account_log_id.set(self.account_id)
        account_name_token = _account_log_name.set(self.account_name)
        try:
            return self._run_loop()
        finally:
            self._release_manual_session()
            _account_log_name.reset(account_name_token)
            _account_log_id.reset(account_id_token)
            _task_log_id.reset(task_id_token)
            _task_log_label.reset(task_token)
            reset_progress_sink(token)

    def _run_loop(self) -> int:
        retry_count = 0
        retry_interval = self.restart_policy.initial_interval
        runtime_config = normalize_config(self.config or read_config())
        # A true headless Chromium session cannot be made interactive after a
        # captcha appears.  When handoff is enabled, start a minimized normal
        # browser once and keep that same session alive; only foreground it
        # when a human action is actually required.
        runtime_config = background_manual_config(runtime_config)
        visible_handoff_used = not is_headless_options(
            runtime_config.get('options', '')
        )
        visible_started_emitted = False
        visible_session_reason = ''

        while not self.stop_event.is_set():
            auto = None
            handoff_requested = False
            try:
                if self._persistent_preview_requested.is_set():
                    runtime_config = visible_handoff_config(runtime_config)
                    visible_session_reason = 'preview'
                    self._persistent_preview_requested.clear()
                if self._visible_handoff_requested.is_set() and not visible_handoff_used:
                    runtime_config = visible_handoff_config(runtime_config)
                    visible_handoff_used = True
                    visible_session_reason = 'preview'
                    handoff_requested = True
                    self._visible_handoff_requested.clear()
                    logging.info('用户请求预览，正在将当前任务重建为可见浏览器')
                if handoff_requested:
                    continue
                log_startup_notice()
                config = runtime_config
                auto = build_auto(
                    config['mode'],
                    config=config,
                    stop_event=self.stop_event,
                    visible_handoff_event=self._visible_handoff_requested,
                    foreground_request_event=self._foreground_requested,
                    manual_intervention_sink=self._publish_manual_request,
                )
                self._set_active(auto)
                if (
                    visible_session_reason == 'preview'
                    and not visible_started_emitted
                ):
                    self._publish_manual_request(ManualInterventionRequest(
                        kind='visible_handoff',
                        reason='可见人工会话已启动',
                        phase='visible_started',
                        headless=False,
                    ))
                    visible_started_emitted = True
                auto.run()
                logging.info('任务执行完成')
                return 0
            except BrowserControlRequested:
                if self.stop_event.is_set():
                    logging.info('任务已停止')
                    return 130
                if self._visible_handoff_requested.is_set() and not visible_handoff_used:
                    runtime_config = visible_handoff_config(runtime_config)
                    visible_handoff_used = True
                    visible_session_reason = 'preview'
                    handoff_requested = True
                    self._visible_handoff_requested.clear()
                    logging.info('headless 会话已停止，正在打开可见预览会话')
                else:
                    logging.warning('收到无法执行的浏览器控制请求')
            except StaleElementReferenceException:
                if self.stop_event.is_set():
                    logging.info('任务已停止')
                    return 130
                logging.error(traceback.format_exc())
                logging.info('页面刷新导致元素失效，准备自动重启')
            except PlaybackBlockedError as exc:
                logging.error('%s', exc)
                logging.error('为避免风控重复触发，本任务不会自动重试')
                return 2
            except CheckpointInteractionRequiredError as exc:
                logging.error('%s', exc)
                logging.error('检查点需要人工处理，本任务不会自动重试')
                return 3
            except HumanVerificationRequiredError as exc:
                if self.stop_event.is_set():
                    logging.info('任务已停止')
                    return 130
                if (
                    visible_handoff_used
                    or not runtime_config.get('manual_handoff_enabled', True)
                    or not is_headless_options(runtime_config.get('options', ''))
                ):
                    self._publish_manual_request(ManualInterventionRequest(
                        kind=exc.kind,
                        reason=exc.reason,
                        headless=is_headless_options(
                            runtime_config.get('options', '')
                        ),
                    ))
                    logging.error('无法再次重建人工验证会话：%s', exc)
                    return 3
                if not self._acquire_manual_session(exc):
                    return 130
                self._publish_manual_request(ManualInterventionRequest(
                    kind=exc.kind,
                    reason=exc.reason,
                    headless=True,
                ))
                runtime_config = visible_handoff_config(
                    runtime_config,
                    auto_hide_after_manual=True,
                    handoff_kind=exc.kind,
                )
                visible_handoff_used = True
                visible_session_reason = 'manual'
                handoff_requested = True
                logging.warning(
                    'headless 会话检测到人工验证，正在仅为当前任务重建可见浏览器'
                )
            except MissedCheckpointReplayError as exc:
                logging.error('%s', exc)
                logging.warning('漏检课程无法取得可靠重看入口，准备整体重启当前刷课线程')
            except LessonProcessingError as exc:
                logging.error('%s', exc)
                logging.warning('存在未可靠完成的课程，准备整体重启当前刷课线程')
            except Exception as exc:
                if self.stop_event.is_set():
                    logging.info('任务已停止')
                    return 130
                if self._visible_handoff_requested.is_set() and not visible_handoff_used:
                    runtime_config = visible_handoff_config(runtime_config)
                    visible_handoff_used = True
                    visible_session_reason = 'preview'
                    handoff_requested = True
                    self._visible_handoff_requested.clear()
                    logging.info('headless 会话已停止，正在打开可见预览会话')
                else:
                    logging.critical(traceback.format_exc())
                    logging.critical('程序异常崩溃：%s，准备自动重启', exc)
            finally:
                self._close_auto(auto)
                self._set_active(None)

            if self.stop_event.is_set():
                logging.info('任务已停止')
                return 130

            if handoff_requested:
                continue

            retry_count += 1
            if (
                self.restart_policy.max_retries is not None
                and retry_count > self.restart_policy.max_retries
            ):
                logging.error('已达到最大重试次数，任务退出')
                return 1
            logging.info('第 %s 次重启，将在 %s 秒后进行', retry_count, retry_interval)
            if not self._sleep(retry_interval):
                logging.info('任务已停止')
                return 130
            retry_interval = min(
                retry_interval * 2,
                self.restart_policy.max_interval,
            )

        logging.info('任务已停止')
        return 130

    def _publish_manual_request(
        self,
        request: ManualInterventionRequest,
    ) -> None:
        if (
            request.phase == 'required'
            and request.kind not in {'visible_handoff', 'user_preview'}
        ):
            # Logical headless tasks use a normal browser that stays hidden so
            # the exact CAPTCHA DOM can be shown later. They still need the
            # shared manual-window slot; otherwise two accounts could surface
            # verification windows at the same time.
            error = HumanVerificationRequiredError(request.reason, request.kind)
            if not self._acquire_manual_session(error):
                return
        event = ManualInterventionEvent(
            task_label=self.task_label,
            kind=request.kind,
            reason=request.reason,
            phase=request.phase,
            headless=request.headless,
        )
        config = self.config or {}
        notifications_enabled = config.get('system_notifications', True)
        if (
            notifications_enabled
            and self.notification_sender
            and request.phase in {'required', 'waiting_slot', 'notice'}
        ):
            try:
                self.notification_sender(event.title, event.message)
            except Exception:
                logging.exception('系统通知发送失败')
        if self.manual_intervention_sink:
            try:
                self.manual_intervention_sink(event)
            except Exception:
                logging.exception('人工介入事件回调失败')
        if request.phase == 'resolved':
            self._release_manual_session()

    def _acquire_manual_session(
        self,
        error: HumanVerificationRequiredError,
    ) -> bool:
        if self.manual_session_lock is None or self._manual_session_lock_held:
            return True
        if not self.manual_session_lock.acquire(blocking=False):
            self._publish_manual_request(ManualInterventionRequest(
                kind=error.kind,
                reason=error.reason,
                phase='waiting_slot',
                headless=True,
            ))
            while not self.stop_event.is_set():
                if self.manual_session_lock.acquire(timeout=0.25):
                    break
            else:
                return False
        self._manual_session_lock_held = True
        return True

    def _release_manual_session(self) -> None:
        if not self._manual_session_lock_held or self.manual_session_lock is None:
            return
        self._manual_session_lock_held = False
        try:
            self.manual_session_lock.release()
        except RuntimeError:
            pass

    def _set_active(self, auto) -> None:
        with self._active_lock:
            self._active_auto = auto

    def _close_auto(self, auto) -> None:
        driver = getattr(auto, 'driver', None)
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    def _sleep(self, seconds: int) -> bool:
        end_time = time.monotonic() + seconds
        while not self.stop_event.is_set():
            remaining = end_time - time.monotonic()
            if remaining <= 0:
                return True
            time.sleep(min(0.2, remaining))
        return False


class ParallelBatchRunner:
    """Run one serial Selenium queue per account, with accounts in parallel."""

    def __init__(
        self,
        tasks: Sequence[BatchTask],
        base_config: Mapping,
        parallelism: int,
        stop_event: threading.Event | None = None,
        status_sink: BatchStatusSink | None = None,
        progress_sink: BatchProgressSink | None = None,
        result_sink: BatchResultSink | None = None,
        manual_intervention_sink: BatchManualInterventionSink | None = None,
        notification_sender: NotificationSender | None = send_system_notification,
        account_configs: Mapping[str, AccountProfile] | None = None,
        restart_policy: RestartPolicy = RestartPolicy(max_retries=3),
        runner_factory: Callable[..., AutoEwtRunner] = AutoEwtRunner,
    ):
        self.tasks = list(tasks)
        self.base_config = normalize_config(base_config)
        self.parallelism = max(1, int(parallelism))
        self.stop_event = stop_event or threading.Event()
        self.status_sink = status_sink
        self.progress_sink = progress_sink
        self.result_sink = result_sink
        self.manual_intervention_sink = manual_intervention_sink
        self.notification_sender = notification_sender
        self.account_configs = dict(account_configs or {})
        self.restart_policy = restart_policy
        self.runner_factory = runner_factory
        self._active_runners: dict[str, AutoEwtRunner] = {}
        self._pending_task_ids = {task.id for task in self.tasks}
        self._preview_requested_ids: set[str] = set()
        self._lock = threading.Lock()
        self._manual_session_lock = threading.Lock()

    def stop(self) -> None:
        self.stop_event.set()
        with self._lock:
            runners = list(self._active_runners.values())
        for runner in runners:
            runner.stop()

    def promote_task(self, task_id: str) -> bool:
        with self._lock:
            runner = self._active_runners.get(task_id)
            if runner is None and task_id in self._pending_task_ids:
                self._preview_requested_ids.add(task_id)
                return True
        return bool(runner and runner.request_visible_handoff())

    def run(self) -> list[BatchTaskResult]:
        if not self.tasks:
            return []
        account_queues = self._account_task_queues()
        worker_count = min(self.parallelism, len(account_queues))
        logging.info(
            '批量运行 %s 个任务，涉及 %s 个账号，并发账号数 %s；同一账号严格串行',
            len(self.tasks),
            len(account_queues),
            worker_count,
        )
        results: list[BatchTaskResult] = []
        with ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix='autoewt-account',
        ) as executor:
            futures: dict[Future, str] = {
                executor.submit(
                    self._run_account_queue,
                    account_key,
                    tasks,
                ): account_key
                for account_key, tasks in account_queues.items()
            }
            while futures:
                done, _ = wait(
                    tuple(futures),
                    timeout=0.25,
                    return_when=FIRST_COMPLETED,
                )
                if not done:
                    continue
                for future in done:
                    account_key = futures.pop(future)
                    try:
                        results.extend(future.result())
                    except Exception as exc:
                        logging.exception(
                            '账号队列“%s”调度失败',
                            self._account_label(account_key),
                        )
                        for task in account_queues[account_key]:
                            if not self._is_pending(task.id):
                                continue
                            result = BatchTaskResult(
                                task=task,
                                code=1,
                                error=str(exc),
                            )
                            results.append(result)
                            self._finish_task(result)
        return sorted(results, key=lambda result: self.tasks.index(result.task))

    def _account_task_queues(self) -> dict[str, list[BatchTask]]:
        queues: dict[str, list[BatchTask]] = {}
        for task in self.tasks:
            queues.setdefault(self._account_key(task), []).append(task)
        return queues

    @staticmethod
    def _account_key(task: BatchTask) -> str:
        account_id = str(task.account_id or '').strip()
        # Legacy/CLI tasks without a binding share the base credentials and
        # therefore must also share one browser slot.
        return account_id or '__default_account__'

    def _account_label(self, account_key: str) -> str:
        account = self.account_configs.get(account_key)
        if account:
            return account.name
        return '默认账号' if account_key == '__default_account__' else account_key

    def _run_account_queue(
        self,
        account_key: str,
        tasks: Sequence[BatchTask],
    ) -> list[BatchTaskResult]:
        results: list[BatchTaskResult] = []
        for task in tasks:
            if self.stop_event.is_set():
                result = BatchTaskResult(task=task, code=130)
            else:
                result = self._run_task(task, account_key)
            results.append(result)
            self._finish_task(result)
        return results

    def _finish_task(self, result: BatchTaskResult) -> None:
        with self._lock:
            self._pending_task_ids.discard(result.task.id)
            self._preview_requested_ids.discard(result.task.id)
        if self.result_sink:
            self.result_sink(result)

    def _is_pending(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self._pending_task_ids

    def _run_task(
        self,
        task: BatchTask,
        account_key: str | None = None,
    ) -> BatchTaskResult:
        if self.stop_event.is_set():
            return BatchTaskResult(task=task, code=130)
        config = dict(self.base_config)
        config['list_url'] = task.url
        account = self.account_configs.get(task.account_id)
        if account:
            config = apply_account(config, account)
        account_key = account_key or self._account_key(task)
        account_name = account.name if account else self._account_label(account_key)
        task_label = f'{account_name}｜{task.title}'
        if self.status_sink:
            self.status_sink(task.id, '等待启动')
        runner = self.runner_factory(
            stop_event=self.stop_event,
            restart_policy=self.restart_policy,
            progress_sink=(
                (lambda state: self.progress_sink(task.id, state))
                if self.progress_sink else None
            ),
            config=config,
            task_label=task_label,
            task_id=task.id,
            account_id=task.account_id,
            account_name=account_name,
            manual_intervention_sink=(
                (lambda event: self._handle_manual_event(task, event))
                if self.manual_intervention_sink or self.status_sink else None
            ),
            notification_sender=self.notification_sender,
            manual_session_lock=self._manual_session_lock,
        )
        with self._lock:
            self._active_runners[task.id] = runner
            promote_on_start = task.id in self._preview_requested_ids
            self._preview_requested_ids.discard(task.id)
        if promote_on_start:
            runner.request_visible_handoff()
        if self.status_sink:
            self.status_sink(task.id, '运行中')
        try:
            code = runner.run()
            status = '已完成' if code == 0 else ('已停止' if code == 130 else '失败')
            if self.status_sink:
                self.status_sink(task.id, status)
            return BatchTaskResult(task=task, code=code)
        except Exception as exc:
            if self.status_sink:
                self.status_sink(task.id, '失败')
            logging.exception('任务“%s”执行失败', task.title)
            return BatchTaskResult(task=task, code=1, error=str(exc))
        finally:
            with self._lock:
                self._active_runners.pop(task.id, None)

    def _handle_manual_event(
        self,
        task: BatchTask,
        event: ManualInterventionEvent,
    ) -> None:
        status = {
            'waiting_slot': '等待人工窗口',
            'required': '等待人工验证' if not event.headless else '正在打开人工窗口',
            'visible_started': '人工会话运行中',
            'resolved': '运行中',
        }.get(event.phase, '等待人工验证')
        if self.status_sink:
            self.status_sink(task.id, status)
        if self.manual_intervention_sink:
            self.manual_intervention_sink(task.id, event)


def run_cli(work_dir: str | Path | None = None) -> int:
    if work_dir:
        os.chdir(work_dir)
    setup_logging('log')
    return AutoEwtRunner().run()
