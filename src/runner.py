import datetime
import logging
import os
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from selenium.common.exceptions import StaleElementReferenceException
from tqdm import tqdm

from auto_base import clear_config_cache, read_config
from auto_paper.auto_paper import AutoPaper
from auto_video.auto_video import AutoVideo
from progress import ProgressSink, reset_progress_sink, set_progress_sink


class TqdmLoggingHandler(logging.StreamHandler):
    """Write CLI logs through tqdm so progress bars stay readable."""

    def emit(self, record):
        msg = self.format(record)
        tqdm.write(msg, file=sys.stderr)
        self.flush()


@dataclass(frozen=True)
class RestartPolicy:
    initial_interval: int = 3
    max_interval: int = 300


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

    logging.basicConfig(
        level=logging.INFO,
        format='[%(asctime)s %(levelname)s] %(message)s',
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


def build_auto(mode: str):
    match mode:
        case 'video':
            return AutoVideo()
        case 'paper':
            return AutoPaper()
        case _:
            raise ValueError('mode 只能是 video 或 paper，请检查配置文件')


class AutoEwtRunner:
    def __init__(
        self,
        stop_event: threading.Event | None = None,
        restart_policy: RestartPolicy = RestartPolicy(),
        progress_sink: ProgressSink | None = None,
    ):
        self.stop_event = stop_event or threading.Event()
        self.restart_policy = restart_policy
        self.progress_sink = progress_sink
        self._active_auto = None
        self._active_lock = threading.Lock()

    def stop(self) -> None:
        self.stop_event.set()
        with self._active_lock:
            auto = self._active_auto
        driver = getattr(auto, 'driver', None)
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    def run(self) -> int:
        token = set_progress_sink(self.progress_sink)
        try:
            return self._run_loop()
        finally:
            reset_progress_sink(token)

    def _run_loop(self) -> int:
        retry_count = 0
        retry_interval = self.restart_policy.initial_interval

        while not self.stop_event.is_set():
            auto = None
            try:
                clear_config_cache()
                log_startup_notice()
                config = read_config()
                auto = build_auto(config['mode'])
                self._set_active(auto)
                auto.run()
                logging.info('任务执行完成')
                return 0
            except StaleElementReferenceException:
                if self.stop_event.is_set():
                    logging.info('任务已停止')
                    return 130
                logging.error(traceback.format_exc())
                logging.info('页面刷新导致元素失效，准备自动重启')
            except Exception as exc:
                if self.stop_event.is_set():
                    logging.info('任务已停止')
                    return 130
                logging.critical(traceback.format_exc())
                logging.critical('程序异常崩溃：%s，准备自动重启', exc)
            finally:
                self._close_auto(auto)
                self._set_active(None)

            if self.stop_event.is_set():
                logging.info('任务已停止')
                return 130

            retry_count += 1
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


def run_cli(work_dir: str | Path | None = None) -> int:
    if work_dir:
        os.chdir(work_dir)
    setup_logging('log')
    return AutoEwtRunner().run()
