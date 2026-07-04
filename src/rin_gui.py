import logging
import os
import sys
import threading
from pathlib import Path

import yaml
from PySide6.QtCore import QObject, Property, QThread, QUrl, Signal, Slot
from PySide6.QtWidgets import QApplication, QFileDialog
from PySide6.QtWebEngineQuick import QtWebEngineQuick
from RinUI import RinUIWindow, Theme

from auto_base import DEFAULT_CONFIG, clear_config_cache
from progress import ProgressState
from runner import AutoEwtRunner, setup_logging


APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / 'config.yml'
QML_PATH = APP_DIR / 'qml' / 'Main.qml'


class LogEmitter(QObject):
    message = Signal(str)


class QtLogHandler(logging.Handler):
    def __init__(self, emitter: LogEmitter):
        super().__init__()
        self.emitter = emitter

    def emit(self, record: logging.LogRecord) -> None:
        self.emitter.message.emit(self.format(record))


class RunnerWorker(QObject):
    log_message = Signal(str)
    progress_changed = Signal(object)
    status_changed = Signal(str)
    finished = Signal(int)

    def __init__(self):
        super().__init__()
        self.stop_event = threading.Event()
        self.runner: AutoEwtRunner | None = None
        self.log_emitter = LogEmitter()
        self.log_emitter.message.connect(self.log_message)

    @Slot()
    def run(self) -> None:
        os.chdir(APP_DIR)
        self.stop_event.clear()
        self.status_changed.emit('运行中')
        handler = QtLogHandler(self.log_emitter)
        log_path = setup_logging(
            APP_DIR / 'log',
            extra_handlers=[handler],
            use_tqdm_handler=False,
        )
        logging.info('日志文件：%s', log_path)
        self.runner = AutoEwtRunner(
            stop_event=self.stop_event,
            progress_sink=self.progress_changed.emit,
        )
        code = self.runner.run()
        self.runner = None
        self.finished.emit(code)

    def stop(self) -> None:
        self.status_changed.emit('正在停止')
        if self.runner:
            self.runner.stop()
        else:
            self.stop_event.set()


class GuiBackend(QObject):
    configChanged = Signal()
    runningChanged = Signal()
    statusChanged = Signal()
    logTextChanged = Signal()
    progressChanged = Signal()
    browserUrlChanged = Signal()
    toastRequested = Signal(str, str, str)

    def __init__(self):
        super().__init__()
        self._config = self._read_config()
        self._running = False
        self._status = '就绪'
        self._log_lines: list[str] = []
        self._progress_title = '进度'
        self._progress_text = '0%'
        self._progress_value = 0.0
        self._progress_indeterminate = False
        self.thread: QThread | None = None
        self.worker: RunnerWorker | None = None

    @Property('QVariantMap', notify=configChanged)
    def config(self):
        return self._config

    @Property(bool, notify=runningChanged)
    def running(self) -> bool:
        return self._running

    @Property(str, notify=statusChanged)
    def status(self) -> str:
        return self._status

    @Property(str, notify=logTextChanged)
    def logText(self) -> str:
        return '\n'.join(self._log_lines)

    @Property(str, notify=progressChanged)
    def progressTitle(self) -> str:
        return self._progress_title

    @Property(str, notify=progressChanged)
    def progressText(self) -> str:
        return self._progress_text

    @Property(float, notify=progressChanged)
    def progressValue(self) -> float:
        return self._progress_value

    @Property(bool, notify=progressChanged)
    def progressIndeterminate(self) -> bool:
        return self._progress_indeterminate

    @Property(str, notify=browserUrlChanged)
    def browserUrl(self) -> str:
        url = str(self._config.get('list_url') or '').strip()
        return url or 'about:blank'

    @Slot()
    def loadConfig(self) -> None:
        self._config = self._read_config()
        self.configChanged.emit()
        self.browserUrlChanged.emit()

    @Slot('QVariantMap', result=bool)
    def saveConfig(self, values) -> bool:
        config = DEFAULT_CONFIG | dict(values)
        config['username'] = str(config.get('username', '')).strip()
        config['password'] = str(config.get('password', ''))
        config['list_url'] = str(config.get('list_url', '')).strip()
        config['browser'] = str(config.get('browser', 'Chrome')).strip() or 'Chrome'
        config['driver_path'] = str(config.get('driver_path', '')).strip()
        config['browser_binary'] = str(config.get('browser_binary', '')).strip()
        config['options'] = str(config.get('options', '')).strip()
        config['mode'] = 'paper' if str(config.get('mode')) == 'paper' else 'video'
        config['choose_correctly'] = bool(config.get('choose_correctly', True))
        config['report_id'] = str(config.get('report_id', '')).strip()
        config['day_to_start_on'] = self._to_int(config.get('day_to_start_on'), 1, 1)
        config['delay_multiplier'] = self._to_float(config.get('delay_multiplier'), 1.0, 0.1)

        with CONFIG_PATH.open('w', encoding='utf-8') as f:
            yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)
        clear_config_cache()
        self._config = config
        self.configChanged.emit()
        self.browserUrlChanged.emit()
        self.toastRequested.emit('success', '已保存', '配置已写入 config.yml')
        return True

    @Slot(str, str, result=str)
    def selectFile(self, current_path: str, file_filter: str) -> str:
        start_dir = str(APP_DIR)
        current = Path(str(current_path).strip().strip('"'))
        if current.parent.exists():
            start_dir = str(current.parent)
        path, _ = QFileDialog.getOpenFileName(None, '选择文件', start_dir, file_filter)
        return path

    @Slot()
    def startRunner(self) -> None:
        if self._running:
            return
        self.clearLogs()
        self._reset_progress()
        self._set_running(True)
        self._set_status('运行中')

        self.thread = QThread()
        self.worker = RunnerWorker()
        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.run)
        self.worker.log_message.connect(self._append_log)
        self.worker.progress_changed.connect(self._update_progress)
        self.worker.status_changed.connect(self._set_status)
        self.worker.finished.connect(self._runner_finished)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.finished.connect(self._clear_thread_refs)
        self.thread.start()

    @Slot()
    def stopRunner(self) -> None:
        if self.worker:
            self.worker.stop()
        self._set_status('正在停止')

    @Slot()
    def clearLogs(self) -> None:
        self._log_lines = []
        self.logTextChanged.emit()

    @Slot()
    def reloadBrowser(self) -> None:
        self.browserUrlChanged.emit()

    def shutdown(self) -> None:
        if self.worker:
            self.worker.stop()
        if self.thread and self.thread.isRunning():
            self.thread.quit()
            self.thread.wait(3000)

    def _read_config(self) -> dict:
        if not CONFIG_PATH.exists():
            return DEFAULT_CONFIG.copy()
        with CONFIG_PATH.open(encoding='utf-8') as f:
            return DEFAULT_CONFIG | (yaml.safe_load(f) or {})

    def _append_log(self, message: str) -> None:
        self._log_lines.append(message)
        if len(self._log_lines) > 3000:
            self._log_lines = self._log_lines[-3000:]
        self.logTextChanged.emit()

    def _update_progress(self, state: ProgressState) -> None:
        self._progress_title = state.title
        if state.total and state.total > 0:
            ratio = max(0.0, min(1.0, state.current / state.total))
            self._progress_value = ratio
            current = int(state.current)
            total = int(state.total)
            unit = state.unit or ''
            self._progress_text = f'{int(ratio * 100)}%  {current}/{total}{unit}'
            self._progress_indeterminate = False
        else:
            self._progress_value = 0.0
            self._progress_text = '等待进度'
            self._progress_indeterminate = True

        if state.finished:
            self._progress_value = 1.0
            self._progress_text = '完成'
            self._progress_indeterminate = False
        self.progressChanged.emit()

    def _runner_finished(self, code: int) -> None:
        stopped = code == 130
        self._set_running(False)
        self._set_status('已停止' if stopped else '已完成')
        if stopped:
            self.toastRequested.emit('info', '已停止', '任务已结束')
        elif code == 0:
            self.toastRequested.emit('success', '已完成', '任务执行完成')
        else:
            self.toastRequested.emit('error', '已退出', f'退出码：{code}')

    def _clear_thread_refs(self) -> None:
        self.thread = None
        self.worker = None

    def _set_running(self, running: bool) -> None:
        if self._running == running:
            return
        self._running = running
        self.runningChanged.emit()

    def _set_status(self, status: str) -> None:
        self._status = status
        self.statusChanged.emit()

    def _reset_progress(self) -> None:
        self._progress_title = '进度'
        self._progress_text = '0%'
        self._progress_value = 0.0
        self._progress_indeterminate = False
        self.progressChanged.emit()

    def _to_int(self, value, default: int, minimum: int) -> int:
        try:
            return max(minimum, int(value))
        except (TypeError, ValueError):
            return default

    def _to_float(self, value, default: float, minimum: float) -> float:
        try:
            return max(minimum, float(value))
        except (TypeError, ValueError):
            return default


def main() -> int:
    os.chdir(APP_DIR)
    QtWebEngineQuick.initialize()
    app = QApplication(sys.argv)
    backend = GuiBackend()
    window = RinUIWindow()
    window.engine.rootContext().setContextProperty('Backend', backend)
    window.load(QML_PATH)
    window.setTheme(Theme.Auto)
    app.aboutToQuit.connect(backend.shutdown)
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
