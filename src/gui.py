import logging
import os
import sys
import threading
from pathlib import Path

import yaml
from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CheckBox,
    ComboBox,
    DoubleSpinBox,
    FluentIcon as FIF,
    FluentWindow,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PasswordLineEdit,
    PlainTextEdit,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    ScrollArea,
    SpinBox,
    SubtitleLabel,
    Theme,
    setTheme,
)

from auto_base import DEFAULT_CONFIG, clear_config_cache
from progress import ProgressState
from runner import AutoEwtRunner, setup_logging


APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / 'config.yml'


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


class RunPage(QWidget):
    start_requested = Signal()
    stop_requested = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName('runPage')

        self.title_label = SubtitleLabel('AutoEwt')
        self.status_label = BodyLabel('就绪')
        self.progress_label = BodyLabel('进度')
        self.progress_value_label = BodyLabel('0%')
        self.progress_bar = ProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(0)

        self.start_button = PrimaryPushButton('启动')
        self.start_button.setIcon(FIF.PLAY)
        self.stop_button = PushButton('停止')
        self.stop_button.setIcon(FIF.CANCEL)
        self.stop_button.setEnabled(False)

        self.console = PlainTextEdit()
        self.console.setReadOnly(True)
        self.console.document().setMaximumBlockCount(3000)
        self.console.setPlaceholderText('控制台')
        self.console.setMinimumHeight(360)
        self.console.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        header_layout = QHBoxLayout()
        header_layout.addWidget(self.title_label)
        header_layout.addStretch(1)
        header_layout.addWidget(self.status_label)

        command_layout = QHBoxLayout()
        command_layout.addWidget(self.start_button)
        command_layout.addWidget(self.stop_button)
        command_layout.addStretch(1)

        progress_header_layout = QHBoxLayout()
        progress_header_layout.addWidget(self.progress_label)
        progress_header_layout.addStretch(1)
        progress_header_layout.addWidget(self.progress_value_label)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)
        layout.addLayout(header_layout)
        layout.addLayout(command_layout)
        layout.addLayout(progress_header_layout)
        layout.addWidget(self.progress_bar)
        layout.addWidget(QLabel('控制台'))
        layout.addWidget(self.console, 1)

        self.start_button.clicked.connect(self.start_requested)
        self.stop_button.clicked.connect(self.stop_requested)

    def set_running(self, running: bool) -> None:
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        self.status_label.setText('运行中' if running else '就绪')

    def set_status(self, status: str) -> None:
        self.status_label.setText(status)

    def append_log(self, message: str) -> None:
        self.console.appendPlainText(message)
        cursor = self.console.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.console.setTextCursor(cursor)

    def clear_progress(self) -> None:
        self.progress_label.setText('进度')
        self.progress_value_label.setText('0%')
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(0)

    def update_progress(self, state: ProgressState) -> None:
        self.progress_label.setText(state.title)
        if state.total and state.total > 0:
            ratio = max(0.0, min(1.0, state.current / state.total))
            self.progress_bar.setRange(0, 1000)
            self.progress_bar.setValue(int(ratio * 1000))
            current = int(state.current)
            total = int(state.total)
            percent = int(ratio * 100)
            unit = state.unit or ''
            self.progress_value_label.setText(
                f'{percent}%  {current}/{total}{unit}'
            )
        else:
            self.progress_bar.setRange(0, 1000)
            self.progress_bar.setValue(0)
            self.progress_value_label.setText('等待进度')

        if state.finished:
            self.progress_bar.setValue(1000)
            self.progress_value_label.setText('完成')


class ConfigPage(QWidget):
    saved = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName('configPage')

        self.username_edit = LineEdit()
        self.password_edit = PasswordLineEdit()
        self.list_url_edit = LineEdit()
        self.report_id_edit = LineEdit()
        self.driver_path_edit = LineEdit()
        self.browser_binary_edit = LineEdit()
        self.options_edit = LineEdit()

        self.browser_combo = ComboBox()
        self.browser_combo.addItems(['Chrome', 'Edge', 'Firefox'])
        self.mode_combo = ComboBox()
        self.mode_combo.addItems(['video', 'paper'])

        self.choose_correctly_check = CheckBox('选择正确答案')
        self.day_spin = SpinBox()
        self.day_spin.setRange(1, 999)
        self.delay_spin = DoubleSpinBox()
        self.delay_spin.setRange(0.1, 20.0)
        self.delay_spin.setSingleStep(0.1)

        self.driver_browse_button = PushButton('浏览')
        self.driver_browse_button.setIcon(FIF.FOLDER)
        self.browser_browse_button = PushButton('浏览')
        self.browser_browse_button.setIcon(FIF.FOLDER)

        self.save_button = PrimaryPushButton('保存')
        self.save_button.setIcon(FIF.SAVE)
        self.reload_button = PushButton('重载')
        self.reload_button.setIcon(FIF.SYNC)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setFormAlignment(Qt.AlignTop)
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(14)
        form.addRow('账号', self.username_edit)
        form.addRow('密码', self.password_edit)
        form.addRow('课程列表 URL', self.list_url_edit)
        form.addRow('模式', self.mode_combo)
        form.addRow('浏览器', self.browser_combo)
        form.addRow('浏览器参数', self.options_edit)
        form.addRow('从第几天开始', self.day_spin)
        form.addRow('延迟倍率', self.delay_spin)
        form.addRow('', self.choose_correctly_check)
        form.addRow('report_id', self.report_id_edit)
        form.addRow('驱动路径', self._path_row(self.driver_path_edit, self.driver_browse_button))
        form.addRow('浏览器路径', self._path_row(self.browser_binary_edit, self.browser_browse_button))

        button_layout = QHBoxLayout()
        button_layout.addWidget(self.save_button)
        button_layout.addWidget(self.reload_button)
        button_layout.addStretch(1)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(28, 24, 28, 24)
        content_layout.setSpacing(18)
        content_layout.addWidget(SubtitleLabel('配置'))
        content_layout.addLayout(form)
        content_layout.addLayout(button_layout)
        content_layout.addStretch(1)

        scroll_area = ScrollArea()
        scroll_area.setWidget(content)
        scroll_area.setWidgetResizable(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(scroll_area)

        self.save_button.clicked.connect(self.save)
        self.reload_button.clicked.connect(self.load)
        self.driver_browse_button.clicked.connect(
            lambda: self._select_file(self.driver_path_edit, 'WebDriver (*.exe);;All files (*.*)')
        )
        self.browser_browse_button.clicked.connect(
            lambda: self._select_file(self.browser_binary_edit, 'Browser (*.exe);;All files (*.*)')
        )

        self.load()

    def _path_row(self, edit: LineEdit, button: PushButton) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(edit, 1)
        layout.addWidget(button)
        return row

    def _select_file(self, edit: LineEdit, file_filter: str) -> None:
        path, _ = QFileDialog.getOpenFileName(self, '选择文件', str(APP_DIR), file_filter)
        if path:
            edit.setText(path)

    def load(self) -> None:
        config = self._read_config()
        self.username_edit.setText(str(config.get('username', '')))
        self.password_edit.setText(str(config.get('password', '')))
        self.list_url_edit.setText(str(config.get('list_url', '')))
        self.report_id_edit.setText(str(config.get('report_id', '')))
        self.driver_path_edit.setText(str(config.get('driver_path', '')))
        self.browser_binary_edit.setText(str(config.get('browser_binary', '')))
        self.options_edit.setText(str(config.get('options', '')))
        self.browser_combo.setCurrentText(str(config.get('browser', 'Chrome')))
        self.mode_combo.setCurrentText(str(config.get('mode', 'video')))
        self.choose_correctly_check.setChecked(bool(config.get('choose_correctly', True)))
        self.day_spin.setValue(int(config.get('day_to_start_on', 1)))
        self.delay_spin.setValue(float(config.get('delay_multiplier', 1.0)))

    def save(self) -> None:
        config = {
            'username': self.username_edit.text().strip(),
            'password': self.password_edit.text(),
            'list_url': self.list_url_edit.text().strip(),
            'browser': self.browser_combo.currentText(),
            'driver_path': self.driver_path_edit.text().strip(),
            'browser_binary': self.browser_binary_edit.text().strip(),
            'options': self.options_edit.text().strip(),
            'mode': self.mode_combo.currentText(),
            'choose_correctly': self.choose_correctly_check.isChecked(),
            'report_id': self.report_id_edit.text().strip(),
            'day_to_start_on': self.day_spin.value(),
            'delay_multiplier': self.delay_spin.value(),
        }
        with CONFIG_PATH.open('w', encoding='utf-8') as f:
            yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)
        clear_config_cache()
        self.saved.emit()

    def _read_config(self) -> dict:
        if not CONFIG_PATH.exists():
            return DEFAULT_CONFIG.copy()
        with CONFIG_PATH.open(encoding='utf-8') as f:
            return DEFAULT_CONFIG | (yaml.safe_load(f) or {})


class MainWindow(FluentWindow):
    def __init__(self):
        super().__init__()
        self.thread: QThread | None = None
        self.worker: RunnerWorker | None = None

        self.run_page = RunPage()
        self.config_page = ConfigPage()
        self.addSubInterface(self.run_page, FIF.PLAY, '运行')
        self.addSubInterface(self.config_page, FIF.SETTING, '配置')

        self.setWindowTitle('AutoEwt')
        self.resize(980, 680)
        self.setMinimumSize(860, 600)

        self.run_page.start_requested.connect(self.start_runner)
        self.run_page.stop_requested.connect(self.stop_runner)
        self.config_page.saved.connect(lambda: self._toast('已保存', '配置已写入 config.yml'))

    def start_runner(self) -> None:
        if self.thread and self.thread.isRunning():
            return
        self.config_page.save()
        self.run_page.console.clear()
        self.run_page.clear_progress()
        self.run_page.set_running(True)

        self.thread = QThread(self)
        self.worker = RunnerWorker()
        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.run)
        self.worker.log_message.connect(self.run_page.append_log)
        self.worker.progress_changed.connect(self.run_page.update_progress)
        self.worker.status_changed.connect(self.run_page.set_status)
        self.worker.finished.connect(self._runner_finished)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.finished.connect(self._clear_thread_refs)
        self.thread.start()

    def stop_runner(self) -> None:
        if self.worker:
            self.worker.stop()
        self.run_page.set_status('正在停止')
        self.run_page.stop_button.setEnabled(False)

    def _runner_finished(self, code: int) -> None:
        running_stopped = code == 130
        self.run_page.set_running(False)
        self.run_page.set_status('已停止' if running_stopped else '已完成')
        if running_stopped:
            self._toast('已停止', '任务已结束')
        elif code == 0:
            self._toast('已完成', '任务执行完成')
        else:
            self._toast('已退出', f'退出码：{code}', error=True)

    def _clear_thread_refs(self) -> None:
        self.thread = None
        self.worker = None

    def _toast(self, title: str, content: str, error: bool = False) -> None:
        show = InfoBar.error if error else InfoBar.success
        show(
            title=title,
            content=content,
            duration=1800,
            position=InfoBarPosition.TOP_RIGHT,
            parent=self,
        )

    def closeEvent(self, event) -> None:
        if self.worker:
            self.worker.stop()
        if self.thread and self.thread.isRunning():
            self.thread.quit()
            self.thread.wait(3000)
        super().closeEvent(event)


def main() -> int:
    os.chdir(APP_DIR)
    app = QApplication(sys.argv)
    setTheme(Theme.AUTO)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
