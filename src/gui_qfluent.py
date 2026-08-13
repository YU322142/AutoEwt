from __future__ import annotations

import logging
import os
import re
import shutil
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

import yaml
from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QDesktopServices, QFont, QFontDatabase
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QStackedWidget,
    QStyle,
    QSystemTrayIcon,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
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
    StrongBodyLabel,
    SubtitleLabel,
    TableWidget,
    Theme,
    ToolButton,
    setTheme,
)

from account_store import (
    AccountProfile,
    apply_account,
    export_import_template,
    import_workspace_rows,
    merge_accounts,
    merge_imported_accounts,
    normalize_accounts,
)
from auto_base import clear_config_cache, normalize_config
from manual_intervention import (
    ManualInterventionEvent,
    ManualInterventionRequest,
    browser_options_without_mode,
    headless_browser_options,
    is_headless_options,
    visible_browser_options,
)
from progress import BatchProgressModel, ProgressState, canonical_progress_kind
from runner import BatchTask, BatchTaskResult, ParallelBatchRunner, setup_logging
from system_notification import send_system_notification
from task_discovery import DiscoveredTask, TaskDiscoverySession, is_task_detail_url


APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / 'config.yml'
OOBE_VERSION = 3


def configure_application_font(app: QApplication) -> str:
    """Prefer a CJK-capable UI font, including Qt offscreen/minimal runs."""
    candidates: list[tuple[str, str]] = []
    if os.name == 'nt':
        candidates.extend((
            ('Microsoft YaHei UI', r'C:\Windows\Fonts\msyh.ttc'),
            ('Microsoft YaHei', r'C:\Windows\Fonts\msyh.ttc'),
            ('DengXian', r'C:\Windows\Fonts\Deng.ttf'),
            ('SimHei', r'C:\Windows\Fonts\simhei.ttf'),
        ))

    installed = set(QFontDatabase.families())
    for family, font_path in candidates:
        if family not in installed and Path(font_path).exists():
            font_id = QFontDatabase.addApplicationFont(font_path)
            if font_id >= 0:
                installed.update(QFontDatabase.applicationFontFamilies(font_id))
        if family in installed:
            app.setFont(QFont(family, 10))
            return family

    return app.font().family()


@dataclass(frozen=True)
class WorkspaceTask:
    id: str
    account_id: str
    account_name: str
    title: str
    url: str
    status: str = '待运行'
    start_time: str = ''
    deadline: str = ''
    teacher: str = ''
    source_filter: str = ''
    origin: str = 'manual'


def read_config_file() -> dict:
    if not CONFIG_PATH.exists():
        return normalize_config()
    try:
        with CONFIG_PATH.open(encoding='utf-8') as file:
            raw = yaml.safe_load(file) or {}
    except (OSError, yaml.YAMLError, UnicodeError) as exc:
        backup = CONFIG_PATH.with_name(f'{CONFIG_PATH.name}.invalid.bak')
        try:
            if not backup.exists():
                shutil.copy2(CONFIG_PATH, backup)
        except OSError:
            logging.exception('备份损坏配置失败')
        logging.warning('配置文件无法读取，将进入恢复向导：%s', exc)
        return normalize_config({'oobe_completed': False, 'oobe_version': 0})
    if not isinstance(raw, dict):
        backup = CONFIG_PATH.with_name(f'{CONFIG_PATH.name}.invalid.bak')
        try:
            if not backup.exists():
                shutil.copy2(CONFIG_PATH, backup)
        except OSError:
            logging.exception('备份非字典配置失败')
        logging.warning('配置文件根节点不是对象，将进入恢复向导')
        return normalize_config({'oobe_completed': False, 'oobe_version': 0})
    config = normalize_config(raw)
    config['batch_tasks'] = raw.get('batch_tasks', [])
    return config


def write_config_file(config: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = CONFIG_PATH.with_name(f'.{CONFIG_PATH.name}.{uuid.uuid4().hex}.tmp')
    try:
        with temporary.open('w', encoding='utf-8', newline='\n') as file:
            yaml.safe_dump(config, file, allow_unicode=True, sort_keys=False)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, CONFIG_PATH)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    clear_config_cache()


@dataclass(frozen=True)
class GuiLogRecord:
    message: str
    account_id: str = ''
    account_name: str = ''
    task_id: str = ''
    task_label: str = ''
    thread_name: str = ''


class LogEmitter(QObject):
    message = Signal(object)


class QtLogHandler(logging.Handler):
    def __init__(self, emitter: LogEmitter):
        super().__init__()
        self.emitter = emitter

    def emit(self, record: logging.LogRecord) -> None:
        self.emitter.message.emit(GuiLogRecord(
            message=self.format(record),
            account_id=str(getattr(record, 'account_id', '') or ''),
            account_name=str(getattr(record, 'account_name', '') or ''),
            task_id=str(getattr(record, 'task_id', '') or ''),
            task_label=str(
                getattr(record, 'task_label', '')
                or getattr(record, 'task', '')
                or ''
            ).strip(' []'),
            thread_name=str(
                getattr(record, 'thread_name', '')
                or record.threadName
            ),
        ))


class DiscoveryWorker(QObject):
    status_changed = Signal(str, str)
    manual_intervention = Signal(str, object)
    completed = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        config: dict,
        accounts: list[AccountProfile],
        parallelism: int,
    ):
        super().__init__()
        self.config = config
        self.accounts = [account for account in accounts if account.enabled]
        self.parallelism = max(1, int(parallelism))
        self.stop_event = threading.Event()
        self._session_lock = threading.Lock()
        self._sessions: dict[str, TaskDiscoverySession] = {}

    @Slot()
    def run(self) -> None:
        os.chdir(APP_DIR)
        tasks: list[WorkspaceTask] = []
        try:
            worker_count = min(self.parallelism, max(1, len(self.accounts)))
            logging.info(
                '并发获取 %s 个账号的任务信息，账号并发数 %s',
                len(self.accounts),
                worker_count,
            )
            by_account: dict[str, list[WorkspaceTask]] = {}
            errors: list[tuple[AccountProfile, Exception]] = []
            with ThreadPoolExecutor(
                max_workers=worker_count,
                thread_name_prefix='autoewt-discovery',
            ) as executor:
                future_accounts = {
                    executor.submit(self._discover_account, account): account
                    for account in self.accounts
                }
                for future in as_completed(future_accounts):
                    account = future_accounts[future]
                    if self.stop_event.is_set():
                        continue
                    try:
                        by_account[account.id] = future.result()
                    except Exception as exc:
                        errors.append((account, exc))
                        logging.exception(
                            '账号“%s”自动发现任务失败',
                            account.name,
                            extra={
                                'account_id': account.id,
                                'account_name': account.name,
                            },
                        )
                        self.status_changed.emit(account.id, f'获取失败：{exc}')
            for account in self.accounts:
                tasks.extend(by_account.get(account.id, []))
            if errors and not by_account and not self.stop_event.is_set():
                failed_account, error = errors[0]
                raise RuntimeError(
                    f'所有账号的任务信息均获取失败；{failed_account.name}：{error}'
                ) from error
            self.completed.emit(tasks)
        except Exception as exc:
            if self.stop_event.is_set():
                self.completed.emit(tasks)
            else:
                logging.exception('自动发现任务失败')
                self.failed.emit(str(exc))
        finally:
            self.stop_event.set()
            self._close_session()

    def _discover_account(self, account: AccountProfile) -> list[WorkspaceTask]:
        if self.stop_event.is_set():
            return []
        self.status_changed.emit(account.id, f'正在获取 {account.name}')
        config = apply_account(self.config, account)
        session = TaskDiscoverySession(
            config,
            status_sink=(
                lambda status: self.status_changed.emit(account.id, status)
            ),
            stop_event=self.stop_event,
            manual_intervention_sink=(
                lambda request: self._emit_manual(account, request)
            ),
        )
        with self._session_lock:
            self._sessions[account.id] = session
        try:
            discovered = session.discover()
            self.status_changed.emit(account.id, f'获取完成，共 {len(discovered)} 个任务')
            return [self._workspace_task(account, task) for task in discovered]
        finally:
            with self._session_lock:
                self._sessions.pop(account.id, None)
            session.close()

    def _workspace_task(
        self,
        account: AccountProfile,
        task: DiscoveredTask,
    ) -> WorkspaceTask:
        return WorkspaceTask(
            id=f'{account.id}:{task.id}',
            account_id=account.id,
            account_name=account.name,
            title=task.title,
            url=task.url,
            status=task.status,
            start_time=task.start_time,
            deadline=task.deadline,
            teacher=task.teacher,
            source_filter=task.source_filter,
            origin='discovered',
        )

    def _emit_manual(
        self,
        account: AccountProfile,
        request: ManualInterventionRequest,
    ) -> None:
        self.manual_intervention.emit(account.id, ManualInterventionEvent(
            task_label=f'{account.name} 的任务发现',
            kind=request.kind,
            reason=request.reason,
            phase=request.phase,
            headless=request.headless,
        ))

    def close(self) -> None:
        self.stop_event.set()

    def _close_session(self) -> None:
        with self._session_lock:
            sessions = tuple(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            try:
                session.close()
            except Exception:
                logging.exception('关闭任务发现浏览器失败')


class BatchWorker(QObject):
    log_message = Signal(object)
    status_changed = Signal(str, str)
    progress_changed = Signal(str, object)
    result_changed = Signal(object)
    manual_intervention = Signal(str, object)
    completed = Signal(object)

    def __init__(
        self,
        tasks: list[BatchTask],
        config: dict,
        parallelism: int,
        accounts: list[AccountProfile],
    ):
        super().__init__()
        self.tasks = tasks
        self.config = config
        self.parallelism = parallelism
        self.accounts = {account.id: account for account in accounts}
        self.stop_event = threading.Event()
        self.runner: ParallelBatchRunner | None = None
        self._preview_lock = threading.Lock()
        self._pending_preview_task_ids: set[str] = set()
        self._finished = False
        self._stop_pending = False
        self.log_emitter = LogEmitter()
        self.log_emitter.message.connect(self.log_message)

    @Slot()
    def run(self) -> None:
        results: list[BatchTaskResult] = []
        try:
            os.chdir(APP_DIR)
            handler = QtLogHandler(self.log_emitter)
            log_path = setup_logging(
                APP_DIR / 'log',
                extra_handlers=[handler],
                use_tqdm_handler=False,
            )
            logging.info('日志文件：%s', log_path)
            runner = ParallelBatchRunner(
                tasks=self.tasks,
                base_config=self.config,
                parallelism=self.parallelism,
                stop_event=self.stop_event,
                status_sink=self.status_changed.emit,
                progress_sink=self.progress_changed.emit,
                result_sink=self.result_changed.emit,
                manual_intervention_sink=self.manual_intervention.emit,
                notification_sender=None,
                account_configs=self.accounts,
            )
            self._attach_runner(runner)
            results = runner.run()
        except Exception as exc:
            logging.exception('批量任务工作线程异常退出')
            results = [
                BatchTaskResult(task=task, code=1, error=str(exc))
                for task in self.tasks
            ]
            for result in results:
                self.result_changed.emit(result)
        finally:
            with self._preview_lock:
                self._finished = True
                self.runner = None
                self._pending_preview_task_ids.clear()
            self.completed.emit(results)

    def promote_task(self, task_id: str) -> bool:
        with self._preview_lock:
            runner = self.runner
            if runner is None:
                if self._finished:
                    return False
                known_ids = {task.id for task in self.tasks}
                if task_id not in known_ids:
                    return False
                self._pending_preview_task_ids.add(task_id)
                return True
        return bool(runner.promote_task(task_id))

    def _attach_runner(self, runner: ParallelBatchRunner) -> None:
        """Publish a runner and forward previews requested during startup."""
        with self._preview_lock:
            self.runner = runner
            pending = tuple(self._pending_preview_task_ids)
            self._pending_preview_task_ids.clear()
            stop_pending = self._stop_pending
        if stop_pending:
            runner.stop()
        for task_id in pending:
            if not runner.promote_task(task_id):
                logging.debug('预览请求对应的任务已不在批量队列：%s', task_id)

    def stop(self) -> None:
        with self._preview_lock:
            self._stop_pending = True
            runner = self.runner
        if runner:
            runner.stop()
        else:
            self.stop_event.set()


class AccountPage(QWidget):
    accounts_changed = Signal(object)
    tasks_imported = Signal(object)

    def __init__(self):
        super().__init__()
        self.setObjectName('accountPage')
        self.accounts: list[AccountProfile] = []
        self.default_account_id = ''
        self.editing_id = ''

        self.name_edit = LineEdit()
        self.name_edit.setPlaceholderText('账户名称')
        self.username_edit = LineEdit()
        self.username_edit.setPlaceholderText('用户名')
        self.password_edit = PasswordLineEdit()
        self.password_edit.setPlaceholderText('密码')
        self.enabled_check = CheckBox('启用')
        self.enabled_check.setChecked(True)
        self.save_account_button = PrimaryPushButton('添加账户')
        self.save_account_button.setIcon(FIF.ADD)
        self.clear_button = PushButton('取消编辑')
        self.clear_button.setIcon(FIF.CANCEL)
        self.default_account_combo = ComboBox()
        self.default_account_combo.setPlaceholderText('选择默认账号')

        self.import_button = PushButton('导入表格')
        self.import_button.setIcon(FIF.DOWNLOAD)
        self.template_button = PushButton('导出模板')
        self.template_button.setIcon(FIF.SAVE)
        self.delete_button = PushButton('删除选中')
        self.delete_button.setIcon(FIF.DELETE)

        self.table = TableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(['启用', '账户名称', '用户名', '密码'])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)

        form = QHBoxLayout()
        form.addWidget(self.name_edit, 2)
        form.addWidget(self.username_edit, 2)
        form.addWidget(self.password_edit, 2)
        form.addWidget(self.enabled_check)
        form.addWidget(self.save_account_button)
        form.addWidget(self.clear_button)

        commands = QHBoxLayout()
        commands.addWidget(self.import_button)
        commands.addWidget(self.template_button)
        commands.addWidget(self.delete_button)
        commands.addWidget(BodyLabel('默认账号'))
        commands.addWidget(self.default_account_combo)
        commands.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)
        layout.addWidget(SubtitleLabel('账户库'))
        layout.addWidget(CaptionLabel('账户只保存在本机配置中；运行任务时每个账户使用独立浏览器会话。'))
        layout.addLayout(form)
        layout.addLayout(commands)
        layout.addWidget(self.table, 1)

        self.save_account_button.clicked.connect(self.save_account)
        self.clear_button.clicked.connect(self.clear_editor)
        self.import_button.clicked.connect(self.import_table)
        self.template_button.clicked.connect(self.export_template)
        self.delete_button.clicked.connect(self.delete_selected)
        self.default_account_combo.currentIndexChanged.connect(
            self._default_account_changed
        )
        self.table.cellDoubleClicked.connect(self.edit_row)

    def load(self, config: dict) -> None:
        self.accounts = normalize_accounts(config)
        self.default_account_id = str(config.get('default_account_id', '') or '')
        self.refresh()

    def save_into(self, config: dict) -> dict:
        selected_id = str(
            self.default_account_combo.currentData()
            or self.default_account_id
            or ''
        )
        config['accounts'] = [account.to_dict() for account in self.accounts]
        enabled = next(
            (
                item for item in self.accounts
                if item.enabled and item.id == selected_id
            ),
            None,
        ) or next((item for item in self.accounts if item.enabled), None)
        if enabled:
            self.default_account_id = enabled.id
            config['default_account_id'] = enabled.id
            config['username'] = enabled.username
            config['password'] = enabled.password
        else:
            config['default_account_id'] = ''
            config['username'] = ''
            config['password'] = ''
        return config

    def refresh(self) -> None:
        selected_id = self.default_account_id
        self.table.setRowCount(len(self.accounts))
        for row, account in enumerate(self.accounts):
            self.table.setItem(row, 0, QTableWidgetItem('是' if account.enabled else '否'))
            self.table.setItem(row, 1, QTableWidgetItem(account.name))
            self.table.setItem(row, 2, QTableWidgetItem(account.username))
            self.table.setItem(row, 3, QTableWidgetItem('●' * min(10, len(account.password))))
        self.default_account_combo.blockSignals(True)
        self.default_account_combo.clear()
        for account in self.accounts:
            if account.enabled:
                self.default_account_combo.addItem(account.name, userData=account.id)
        valid_ids = {account.id for account in self.accounts if account.enabled}
        if selected_id not in valid_ids:
            selected_id = next(
                (account.id for account in self.accounts if account.enabled),
                '',
            )
        self.default_account_id = selected_id
        index = self.default_account_combo.findData(selected_id)
        if index >= 0:
            self.default_account_combo.setCurrentIndex(index)
        self.default_account_combo.blockSignals(False)
        self.accounts_changed.emit(list(self.accounts))

    def _default_account_changed(self, index: int) -> None:
        self.default_account_id = str(
            self.default_account_combo.itemData(index) or ''
        )

    def save_account(self) -> None:
        username = self.username_edit.text().strip()
        if not username:
            return
        profile = AccountProfile(
            id=self.editing_id or f'account-{uuid.uuid4().hex}',
            name=self.name_edit.text().strip() or username,
            username=username,
            password=self.password_edit.text(),
            enabled=self.enabled_check.isChecked(),
        )
        if self.editing_id:
            self.accounts = [
                profile if item.id == self.editing_id else item
                for item in self.accounts
            ]
        else:
            self.accounts = merge_accounts(self.accounts, [profile])
        self.clear_editor()
        self.refresh()

    def edit_row(self, row: int, column: int) -> None:
        if not 0 <= row < len(self.accounts):
            return
        account = self.accounts[row]
        self.editing_id = account.id
        self.name_edit.setText(account.name)
        self.username_edit.setText(account.username)
        self.password_edit.setText(account.password)
        self.enabled_check.setChecked(account.enabled)
        self.save_account_button.setText('保存账户')

    def clear_editor(self) -> None:
        self.editing_id = ''
        self.name_edit.clear()
        self.username_edit.clear()
        self.password_edit.clear()
        self.enabled_check.setChecked(True)
        self.save_account_button.setText('添加账户')

    def delete_selected(self) -> None:
        rows = {index.row() for index in self.table.selectionModel().selectedRows()}
        self.accounts = [
            account for row, account in enumerate(self.accounts) if row not in rows
        ]
        self.clear_editor()
        self.refresh()

    def import_table(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            '导入账号与任务',
            str(APP_DIR),
            '表格 (*.xlsx *.csv *.tsv)',
        )
        if not path:
            return
        try:
            rows = import_workspace_rows(path)
        except Exception as exc:
            InfoBar.error(
                title='导入失败',
                content=str(exc),
                position=InfoBarPosition.TOP_RIGHT,
                parent=self,
            )
            return
        self.accounts = merge_imported_accounts(self.accounts, rows)
        self.refresh()
        imported_tasks = []
        account_by_username = {
            account.username.casefold(): account for account in self.accounts
        }
        for row in rows:
            if not is_task_detail_url(row.task_url):
                continue
            account = account_by_username[row.account.username.casefold()]
            imported_tasks.append(WorkspaceTask(
                id=f'imported-{uuid.uuid4().hex}',
                account_id=account.id,
                account_name=account.name,
                title=row.task_title or '导入任务',
                url=row.task_url,
                origin='imported',
            ))
        if imported_tasks:
            self.tasks_imported.emit(imported_tasks)

    def export_template(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            '导出导入模板',
            str(APP_DIR / 'AutoEwt账号任务模板.xlsx'),
            'Excel (*.xlsx);;CSV (*.csv)',
        )
        if not path:
            return
        try:
            export_import_template(path)
        except Exception as exc:
            InfoBar.error(
                title='导出失败',
                content=str(exc),
                position=InfoBarPosition.TOP_RIGHT,
                parent=self,
            )


class TaskPage(QWidget):
    discover_requested = Signal()
    start_requested = Signal(object, int)
    preview_requested = Signal(str, str)

    def __init__(self):
        super().__init__()
        self.setObjectName('taskPage')
        self.tasks: list[WorkspaceTask] = []
        self.accounts: list[AccountProfile] = []
        self.checked_task_ids: set[str] = set()

        self.discovery_status = BodyLabel('就绪')
        self.account_filter = ComboBox()
        self.account_filter.setMinimumWidth(170)
        self.discover_button = PrimaryPushButton('批量获取任务')
        self.discover_button.setIcon(FIF.SYNC)
        self.select_all_button = PushButton('全选')
        self.select_all_button.setIcon(FIF.ACCEPT)
        self.select_latest_button = PushButton('选择每个账号最新一个课程')
        self.select_latest_button.setIcon(FIF.SYNC)
        self.select_latest_button.setToolTip('每个启用账号只选择开始时间最新的一门课程')
        self.remove_button = ToolButton(FIF.DELETE)
        self.remove_button.setToolTip('删除选中任务')
        self.manual_url_edit = LineEdit()
        self.manual_url_edit.setPlaceholderText('课程任务 URL')
        self.add_url_button = ToolButton(FIF.ADD)
        self.add_url_button.setToolTip('为当前账户添加任务 URL')
        self.parallel_spin = SpinBox()
        self.parallel_spin.setRange(1, 2_147_483_647)
        self.parallel_spin.setMinimumWidth(100)
        self.start_button = PrimaryPushButton('批量启动')
        self.start_button.setIcon(FIF.PLAY)

        self.table = TableWidget()
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels(
            ['选择', '账户', '任务', '状态', '开始时间', '截止时间', 'URL', '预览']
        )
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        header = self.table.horizontalHeader()
        for column in (0, 1, 3, 4, 5, 7):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(6, QHeaderView.Stretch)

        commands = QHBoxLayout()
        commands.addWidget(self.account_filter)
        commands.addWidget(self.discover_button)
        commands.addWidget(self.select_all_button)
        commands.addWidget(self.select_latest_button)
        commands.addWidget(self.remove_button)
        commands.addStretch(1)
        commands.addWidget(BodyLabel('并发账号'))
        commands.addWidget(self.parallel_spin)
        commands.addWidget(self.start_button)
        manual = QHBoxLayout()
        manual.addWidget(self.manual_url_edit, 1)
        manual.addWidget(self.add_url_button)
        heading = QHBoxLayout()
        heading.addWidget(SubtitleLabel('任务工作台'))
        heading.addStretch(1)
        heading.addWidget(self.discovery_status)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)
        layout.addLayout(heading)
        layout.addLayout(commands)
        layout.addLayout(manual)
        layout.addWidget(self.table, 1)

        self.discover_button.clicked.connect(self.discover_requested)
        self.select_all_button.clicked.connect(self.select_all)
        self.select_latest_button.clicked.connect(self.select_latest_per_account)
        self.remove_button.clicked.connect(self.remove_selected_rows)
        self.add_url_button.clicked.connect(self.add_manual_url)
        self.manual_url_edit.returnPressed.connect(self.add_manual_url)
        self.start_button.clicked.connect(self._emit_start)
        self.table.itemChanged.connect(self._remember_check_state)

    def set_accounts(self, accounts: list[AccountProfile]) -> None:
        current_id = self.account_filter.currentData()
        self.accounts = list(accounts)
        self.account_filter.clear()
        self.account_filter.addItem('全部启用账户', userData='')
        for account in self.accounts:
            if account.enabled:
                self.account_filter.addItem(account.name, userData=account.id)
        index = self.account_filter.findData(current_id)
        self.account_filter.setCurrentIndex(max(0, index))

    def selected_account_ids(self) -> set[str]:
        account_id = str(self.account_filter.currentData() or '')
        return {account_id} if account_id else {
            account.id for account in self.accounts if account.enabled
        }

    def load(self, config: dict) -> None:
        self.parallel_spin.setValue(max(1, int(config.get('parallelism', 2))))
        tasks = []
        account_names = {item.id: item.name for item in normalize_accounts(config)}
        for entry in config.get('batch_tasks', []) or []:
            if not isinstance(entry, dict) or not entry.get('url'):
                continue
            account_id = str(entry.get('account_id', ''))
            task_id = str(entry.get('id') or uuid.uuid4())
            source_filter = str(entry.get('source_filter') or '')
            origin = str(entry.get('origin') or '').strip().lower()
            if origin not in {'discovered', 'imported', 'manual'}:
                if task_id.startswith('imported-'):
                    origin = 'imported'
                elif task_id.startswith('manual-'):
                    origin = 'manual'
                elif ':' in task_id or source_filter:
                    origin = 'discovered'
                else:
                    origin = 'manual'
            tasks.append(WorkspaceTask(
                id=task_id,
                account_id=account_id,
                account_name=str(entry.get('account_name') or account_names.get(account_id, '')),
                title=str(entry.get('title') or '手工任务'),
                url=str(entry['url']),
                status=str(entry.get('status') or '待运行'),
                start_time=str(entry.get('start_time') or ''),
                deadline=str(entry.get('deadline') or ''),
                teacher=str(entry.get('teacher') or ''),
                source_filter=source_filter,
                origin=origin,
            ))
        self.set_tasks(tasks, replace=True)

    def save_into(self, config: dict) -> dict:
        config['parallelism'] = self.parallel_spin.value()
        config['task_urls'] = [task.url for task in self.tasks]
        config['batch_tasks'] = [task.__dict__.copy() for task in self.tasks]
        return config

    def set_discovering(self, discovering: bool, status: str = '') -> None:
        self.discover_button.setEnabled(not discovering)
        self.discovery_status.setText(status or ('正在获取' if discovering else '就绪'))

    def set_tasks(self, tasks: list[WorkspaceTask], replace: bool = False) -> None:
        merged = [] if replace else list(self.tasks)
        by_key = {(task.account_id, task.url): index for index, task in enumerate(merged)}
        for task in tasks:
            key = (task.account_id, task.url)
            if key in by_key:
                merged[by_key[key]] = task
            else:
                by_key[key] = len(merged)
                merged.append(task)
        self._install_tasks(merged)

    def replace_discovered_tasks(
        self,
        account_ids: set[str],
        tasks: list[WorkspaceTask],
    ) -> None:
        merged = [
            task for task in self.tasks
            if not (task.origin == 'discovered' and task.account_id in account_ids)
        ]
        by_key = {(task.account_id, task.url): index for index, task in enumerate(merged)}
        for task in tasks:
            key = (task.account_id, task.url)
            if key in by_key:
                # A manually added or imported task owns its row even if discovery
                # later reports the same URL.
                continue
            by_key[key] = len(merged)
            merged.append(task)
        self._install_tasks(merged)

    def _install_tasks(self, tasks: list[WorkspaceTask]) -> None:
        old_ids = {task.id for task in self.tasks}
        old_state_by_key = {
            (task.account_id, task.url): task.id in self.checked_task_ids
            for task in self.tasks
        }
        checked = set()
        for task in tasks:
            if task.id in old_ids:
                is_checked = task.id in self.checked_task_ids
            else:
                is_checked = old_state_by_key.get((task.account_id, task.url), True)
            if is_checked:
                checked.add(task.id)
        self.tasks = list(tasks)
        self.checked_task_ids = checked
        self.refresh()

    def refresh(self) -> None:
        was_blocked = self.table.blockSignals(True)
        try:
            self.table.setRowCount(len(self.tasks))
            for row, task in enumerate(self.tasks):
                check_item = QTableWidgetItem()
                check_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
                check_item.setCheckState(
                    Qt.Checked if task.id in self.checked_task_ids else Qt.Unchecked
                )
                self.table.setItem(row, 0, check_item)
                self.table.setItem(row, 1, QTableWidgetItem(task.account_name))
                self.table.setItem(row, 2, QTableWidgetItem(task.title))
                self.table.setItem(row, 3, QTableWidgetItem(task.status))
                self.table.setItem(row, 4, QTableWidgetItem(task.start_time))
                self.table.setItem(row, 5, QTableWidgetItem(task.deadline))
                self.table.setItem(row, 6, QTableWidgetItem(task.url))
                preview = PushButton('预览')
                preview.setIcon(FIF.GLOBE)
                preview.clicked.connect(
                    lambda checked=False, item=task: self.preview_requested.emit(
                        item.id,
                        item.url,
                    )
                )
                self.table.setCellWidget(row, 7, preview)
        finally:
            self.table.blockSignals(was_blocked)

    def _remember_check_state(self, item: QTableWidgetItem) -> None:
        if item.column() != 0 or not 0 <= item.row() < len(self.tasks):
            return
        task_id = self.tasks[item.row()].id
        if item.checkState() == Qt.Checked:
            self.checked_task_ids.add(task_id)
        else:
            self.checked_task_ids.discard(task_id)

    def add_manual_url(self) -> None:
        url = self.manual_url_edit.text().strip()
        account_id = str(self.account_filter.currentData() or '')
        enabled = [item for item in self.accounts if item.enabled]
        account = next((item for item in enabled if item.id == account_id), None)
        account = account or (enabled[0] if len(enabled) == 1 else None)
        if not url or account is None:
            return
        self.set_tasks([WorkspaceTask(
            id=f'manual-{uuid.uuid4().hex}',
            account_id=account.id,
            account_name=account.name,
            title=f'手工任务 {len(self.tasks) + 1}',
            url=url,
            origin='manual',
        )])
        self.manual_url_edit.clear()

    def select_all(self) -> None:
        all_checked = self.table.rowCount() > 0 and all(
            self.table.item(row, 0).checkState() == Qt.Checked
            for row in range(self.table.rowCount())
        )
        state = Qt.Unchecked if all_checked else Qt.Checked
        for row in range(self.table.rowCount()):
            self.table.item(row, 0).setCheckState(state)

    def select_latest_per_account(self) -> None:
        enabled_account_ids = {
            account.id for account in self.accounts if account.enabled
        }
        latest_by_account: dict[str, tuple[tuple[str, str, str], str]] = {}
        for task in self.tasks:
            if task.account_id not in enabled_account_ids:
                continue
            key = self._latest_task_key(task)
            current = latest_by_account.get(task.account_id)
            if current is None or key > current[0]:
                latest_by_account[task.account_id] = (key, task.id)
        selected_ids = {item[1] for item in latest_by_account.values()}
        was_blocked = self.table.blockSignals(True)
        try:
            for row, task in enumerate(self.tasks):
                checked = task.id in selected_ids
                self.table.item(row, 0).setCheckState(
                    Qt.Checked if checked else Qt.Unchecked
                )
            self.checked_task_ids = selected_ids
        finally:
            self.table.blockSignals(was_blocked)

    @staticmethod
    def _latest_task_key(task: WorkspaceTask) -> tuple[str, str, str]:
        # Normalize non-zero-padded Chinese/ISO dates before lexical comparison.
        # Start time is the primary definition of “latest”; deadline and id are
        # deterministic fallbacks for imported/manual rows.
        return (
            TaskPage._sortable_datetime(task.start_time),
            TaskPage._sortable_datetime(task.deadline),
            task.id,
        )

    @staticmethod
    def _sortable_datetime(value: str) -> str:
        text = ' '.join(str(value or '').split())
        numbers = [int(item) for item in re.findall(r'\d+', text)]
        if not numbers:
            return ''
        padded = (numbers + [0, 0, 0, 0, 0, 0])[:6]
        return ''.join(f'{item:04d}' if index == 0 else f'{item:02d}' for index, item in enumerate(padded))

    def remove_selected_rows(self) -> None:
        selected = {index.row() for index in self.table.selectionModel().selectedRows()}
        if not selected:
            selected = {
                row for row in range(self.table.rowCount())
                if self.table.item(row, 0).checkState() == Qt.Checked
            }
        self._install_tasks([
            task for row, task in enumerate(self.tasks) if row not in selected
        ])

    def selected_tasks(self) -> list[BatchTask]:
        result = []
        for row, task in enumerate(self.tasks):
            if self.table.item(row, 0).checkState() == Qt.Checked:
                result.append(BatchTask(
                    id=task.id,
                    title=task.title,
                    url=task.url,
                    account_id=task.account_id,
                ))
        return result

    def _emit_start(self) -> None:
        tasks = self.selected_tasks()
        parallelism = min(self.parallel_spin.value(), max(1, len(tasks)))
        self.start_requested.emit(tasks, parallelism)


class RunPage(QWidget):
    stop_requested = Signal()
    preview_requested = Signal(str, str)

    def __init__(self):
        super().__init__()
        self.setObjectName('runPage')
        self.row_by_task: dict[str, int] = {}
        self.row_by_account: dict[str, int] = {}
        self.task_ids_by_account: dict[str, list[str]] = {}
        self.account_progress_models: dict[str, BatchProgressModel] = {}
        self.task_by_id: dict[str, BatchTask] = {}
        self.account_names: dict[str, str] = {}
        self.log_records: list[GuiLogRecord] = []
        self.completed_task_ids: set[str] = set()
        self.progress_model = BatchProgressModel()
        self.status_label = StrongBodyLabel('就绪')
        self.stop_button = PushButton('停止全部')
        self.stop_button.setIcon(FIF.CANCEL)
        self.stop_button.setEnabled(False)
        self.table = TableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(['账户', '任务队列', '状态', '进度', '预览'])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.table.setMaximumHeight(300)
        self.log_account_filter = ComboBox()
        self.log_account_filter.setMinimumWidth(160)
        self.log_task_filter = ComboBox()
        self.log_task_filter.setMinimumWidth(220)
        self.log_thread_filter = ComboBox()
        self.log_thread_filter.setMinimumWidth(180)
        self.console = PlainTextEdit()
        self.console.setReadOnly(True)
        self.console.document().setMaximumBlockCount(0)
        self.console.setPlaceholderText('运行日志')
        self.console.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        heading = QHBoxLayout()
        heading.addWidget(SubtitleLabel('运行中心'))
        heading.addStretch(1)
        heading.addWidget(self.status_label)
        heading.addWidget(self.stop_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)
        layout.addLayout(heading)
        layout.addWidget(self.table)
        log_heading = QHBoxLayout()
        log_heading.addWidget(CaptionLabel('完整日志'))
        log_heading.addStretch(1)
        log_heading.addWidget(CaptionLabel('账户'))
        log_heading.addWidget(self.log_account_filter)
        log_heading.addWidget(CaptionLabel('任务'))
        log_heading.addWidget(self.log_task_filter)
        log_heading.addWidget(CaptionLabel('线程'))
        log_heading.addWidget(self.log_thread_filter)
        layout.addLayout(log_heading)
        layout.addWidget(self.console, 1)
        self.stop_button.clicked.connect(self.stop_requested)
        self.log_account_filter.currentIndexChanged.connect(self._account_filter_changed)
        self.log_task_filter.currentIndexChanged.connect(self._render_logs)
        self.log_thread_filter.currentIndexChanged.connect(self._render_logs)

    def prepare(self, tasks: list[BatchTask], account_names: dict[str, str]) -> None:
        self.row_by_task.clear()
        self.row_by_account.clear()
        self.task_ids_by_account.clear()
        self.account_progress_models.clear()
        self.task_by_id = {task.id: task for task in tasks}
        self.account_names = dict(account_names)
        self.log_records.clear()
        self.completed_task_ids.clear()
        self.progress_model.reset(task.id for task in tasks)
        self.console.clear()
        self.log_account_filter.blockSignals(True)
        self.log_account_filter.clear()
        self.log_account_filter.addItem('全部账户', userData='')
        used_account_ids = []
        for task in tasks:
            if task.account_id and task.account_id not in used_account_ids:
                used_account_ids.append(task.account_id)
        for account_id in used_account_ids:
            self.log_account_filter.addItem(
                account_names.get(account_id, account_id),
                userData=account_id,
            )
        self.log_account_filter.blockSignals(False)
        self._rebuild_task_filter()
        self.log_thread_filter.blockSignals(True)
        self.log_thread_filter.clear()
        self.log_thread_filter.addItem('全部线程', userData='')
        self.log_thread_filter.blockSignals(False)
        grouped: dict[str, list[BatchTask]] = {}
        for task in tasks:
            account_id = str(task.account_id or '__default_account__')
            grouped.setdefault(account_id, []).append(task)
            self.task_ids_by_account.setdefault(account_id, []).append(task.id)
        self.table.setRowCount(len(grouped))
        for row, (account_id, account_tasks) in enumerate(grouped.items()):
            self.row_by_account[account_id] = row
            for task in account_tasks:
                self.row_by_task[task.id] = row
            account_name = account_names.get(account_id, '默认账号' if account_id == '__default_account__' else account_id)
            self.table.setItem(row, 0, QTableWidgetItem(account_name))
            queue_text = ' → '.join(task.title for task in account_tasks)
            self.table.setItem(row, 1, QTableWidgetItem(queue_text))
            self.table.setItem(row, 2, QTableWidgetItem('排队中'))
            self.table.setCellWidget(row, 3, self._create_account_progress_widget(account_id))
            self.table.setRowHeight(row, 78)
            preview = PushButton('预览')
            preview.setIcon(FIF.GLOBE)
            preview.clicked.connect(
                lambda checked=False, item=account_tasks[0]: self.preview_requested.emit(
                    item.id,
                    item.url,
                )
            )
            self.table.setCellWidget(row, 4, preview)
        self._update_total_progress()
        self.status_label.setText('运行中')
        self.stop_button.setEnabled(True)

    def set_task_status(self, task_id: str, status: str) -> None:
        row = self.row_by_task.get(task_id)
        if row is not None:
            task = self.task_by_id.get(task_id)
            prefix = task.title if task else task_id
            self.table.setItem(row, 2, QTableWidgetItem(f'{prefix}：{status}'))
            if task and status in {'等待启动', '运行中', '等待人工验证', '人工会话运行中'}:
                self._set_account_preview_target(task)

    def set_task_progress(self, task_id: str, state: ProgressState) -> None:
        row = self.row_by_task.get(task_id)
        task = self.task_by_id.get(task_id)
        if row is None or task is None:
            return
        state = self.progress_model.update(task_id, state)
        account_id = str(task.account_id or '__default_account__')
        account_model = self.account_progress_models.setdefault(
            account_id,
            BatchProgressModel(self.task_ids_by_account.get(account_id, ())),
        )
        account_model.update(task_id, state)
        self._refresh_account_progress(account_id)

    def record_result(self, result: BatchTaskResult) -> None:
        self.completed_task_ids.add(result.task.id)
        self.set_task_status(
            result.task.id,
            '已完成' if result.code == 0 else ('已停止' if result.code == 130 else '失败'),
        )
        self._refresh_account_progress(str(result.task.account_id or '__default_account__'))

    def finish(self, results: list[BatchTaskResult]) -> None:
        failed = sum(result.code not in (0, 130) for result in results)
        stopped = any(result.code == 130 for result in results)
        self.status_label.setText(
            '已停止' if stopped else (f'完成，{failed} 个失败' if failed else '全部完成')
        )
        self.stop_button.setEnabled(False)
        self._update_total_progress()

    def append_log(self, record: GuiLogRecord | str) -> None:
        if isinstance(record, str):
            record = GuiLogRecord(record)
        self.log_records.append(record)
        if (
            record.thread_name
            and self.log_thread_filter.findData(record.thread_name) < 0
        ):
            self.log_thread_filter.blockSignals(True)
            self.log_thread_filter.addItem(
                record.thread_name,
                userData=record.thread_name,
            )
            self.log_thread_filter.blockSignals(False)
        if self._record_matches_filters(record):
            self.console.appendPlainText(self._display_log(record))
            cursor = self.console.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            self.console.setTextCursor(cursor)

    def _account_filter_changed(self) -> None:
        self._rebuild_task_filter()
        self._render_logs()

    def _rebuild_task_filter(self) -> None:
        account_id = str(self.log_account_filter.currentData() or '')
        current_task_id = str(self.log_task_filter.currentData() or '')
        self.log_task_filter.blockSignals(True)
        self.log_task_filter.clear()
        self.log_task_filter.addItem('全部任务', userData='')
        for task in self.task_by_id.values():
            if account_id and task.account_id != account_id:
                continue
            self.log_task_filter.addItem(task.title, userData=task.id)
        index = self.log_task_filter.findData(current_task_id)
        self.log_task_filter.setCurrentIndex(max(0, index))
        self.log_task_filter.blockSignals(False)

    def _record_matches_filters(self, record: GuiLogRecord) -> bool:
        account_id = str(self.log_account_filter.currentData() or '')
        task_id = str(self.log_task_filter.currentData() or '')
        thread_name = str(self.log_thread_filter.currentData() or '')
        return (
            (not account_id or record.account_id == account_id)
            and (not task_id or record.task_id == task_id)
            and (not thread_name or record.thread_name == thread_name)
        )

    @staticmethod
    def _display_log(record: GuiLogRecord) -> str:
        if not record.thread_name:
            return record.message
        return f'[{record.thread_name}] {record.message}'

    def _render_logs(self) -> None:
        self.console.setPlainText('\n'.join(
            self._display_log(record)
            for record in self.log_records
            if self._record_matches_filters(record)
        ))
        cursor = self.console.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.console.setTextCursor(cursor)

    def _update_total_progress(self) -> None:
        for account_id in self.row_by_account:
            self._refresh_account_progress(account_id)

    def _create_account_progress_widget(self, account_id: str) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(2)
        metrics = (
            ('courses', '课程'),
            ('days', '天数'),
            ('current_course', '当前'),
        )
        bars = {}
        labels = {}
        for kind, title in metrics:
            line = QHBoxLayout()
            line.setSpacing(5)
            name = CaptionLabel(title)
            name.setMinimumWidth(32)
            bar = ProgressBar()
            bar.setRange(0, 1000)
            label = CaptionLabel('等待统计')
            label.setMinimumWidth(150)
            label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            line.addWidget(name)
            line.addWidget(bar, 1)
            line.addWidget(label)
            layout.addLayout(line)
            bars[kind] = bar
            labels[kind] = label
        container._progress_bars = bars
        container._progress_labels = labels
        return container

    def _set_account_preview_target(self, task: BatchTask) -> None:
        account_id = str(task.account_id or '__default_account__')
        row = self.row_by_account.get(account_id)
        if row is None:
            return
        preview = PushButton('预览')
        preview.setIcon(FIF.GLOBE)
        preview.clicked.connect(
            lambda checked=False, item=task: self.preview_requested.emit(
                item.id,
                item.url,
            )
        )
        self.table.setCellWidget(row, 4, preview)

    def _refresh_account_progress(self, account_id: str) -> None:
        row = self.row_by_account.get(account_id)
        if row is None:
            return
        widget = self.table.cellWidget(row, 3)
        if widget is None:
            return
        model = self.account_progress_models.setdefault(
            account_id,
            BatchProgressModel(self.task_ids_by_account.get(account_id, ())),
        )
        for kind, unit in (('courses', '项'), ('days', '天')):
            summary = model.aggregate(kind)
            if summary.total is None:
                self._set_account_metric(widget, kind, 0, '等待统计')
                continue
            ratio = self._progress_ratio(summary.current, summary.total, False)
            text = f'{int(summary.current)}/{int(summary.total)}{unit}'
            if summary.known_tasks < summary.expected_tasks:
                text += f' · 已统计 {summary.known_tasks}/{summary.expected_tasks}'
            self._set_account_metric(widget, kind, int(ratio * 1000), text)
        latest = model.latest_current_course()
        if latest is None:
            self._set_account_metric(widget, 'current_course', 0, '等待课程播放')
        else:
            task_id, state = latest
            task = self.task_by_id.get(task_id)
            title = self._short_progress_title(task.title if task else state.title, 20)
            if state.total is None or state.total < 0:
                self._set_account_metric(widget, 'current_course', 0, f'{title} · 获取时长')
            else:
                ratio = self._progress_ratio(state.current, state.total, state.finished)
                self._set_account_metric(
                    widget,
                    'current_course',
                    int(ratio * 1000),
                    f'{title} · {int(state.current)}/{int(state.total)}{state.unit}',
                )

    @staticmethod
    def _set_account_metric(widget: QWidget, kind: str, value: int, text: str) -> None:
        widget._progress_bars[kind].setValue(max(0, min(1000, value)))
        widget._progress_labels[kind].setText(text)

    @staticmethod
    def _progress_ratio(current: float, total: float, finished: bool) -> float:
        if total > 0:
            return max(0.0, min(1.0, current / total))
        return 1.0 if finished else 0.0

    @staticmethod
    def _short_progress_title(title: str, limit: int = 28) -> str:
        compact = ' '.join(str(title or '当前任务').split())
        return compact if len(compact) <= limit else f'{compact[:limit - 1]}…'


class SettingsPage(QWidget):
    saved = Signal(object)
    oobe_requested = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName('settingsPage')
        self.mode_combo = ComboBox()
        self.mode_combo.addItems(['video', 'paper'])
        self.delay_spin = DoubleSpinBox()
        self.delay_spin.setRange(0.1, 20.0)
        self.delay_spin.setSingleStep(0.1)
        self.headless_check = CheckBox('无头模式（后台运行）')
        self.notification_check = CheckBox('启用系统通知')
        self.handoff_check = CheckBox('需要人工验证时显示浏览器窗口')
        self.choose_correctly_check = CheckBox('做题时优先选择正确答案')

        self.browser_combo = ComboBox()
        self.browser_combo.addItems(['Chrome', 'Edge', 'Firefox'])
        self.options_edit = LineEdit()
        self.driver_path_edit = LineEdit()
        self.browser_binary_edit = LineEdit()
        self.login_timeout_spin = SpinBox()
        self.login_timeout_spin.setRange(10, 3600)
        self.report_id_edit = LineEdit()
        self.driver_browse_button = ToolButton(FIF.FOLDER)
        self.browser_browse_button = ToolButton(FIF.FOLDER)
        self.advanced_button = PushButton('显示高级设置')
        self.advanced_button.setIcon(FIF.SETTING)
        self.save_button = PrimaryPushButton('保存设置')
        self.save_button.setIcon(FIF.SAVE)
        self.reload_button = PushButton('重载')
        self.reload_button.setIcon(FIF.SYNC)
        self.oobe_button = PushButton('重新运行首次设置')

        common = QFormLayout()
        common.setHorizontalSpacing(16)
        common.setVerticalSpacing(14)
        common.addRow('运行模式', self.mode_combo)
        common.addRow('操作延迟倍率', self.delay_spin)
        common.addRow('', self.headless_check)
        common.addRow('', self.notification_check)
        common.addRow('', self.handoff_check)
        common.addRow('', self.choose_correctly_check)

        self.advanced_widget = QWidget()
        advanced = QFormLayout(self.advanced_widget)
        advanced.setHorizontalSpacing(16)
        advanced.setVerticalSpacing(14)
        advanced.addRow('浏览器', self.browser_combo)
        advanced.addRow('其他浏览器参数', self.options_edit)
        advanced.addRow('登录等待秒数', self.login_timeout_spin)
        advanced.addRow('WebDriver', self._path_row(self.driver_path_edit, self.driver_browse_button))
        advanced.addRow('浏览器程序', self._path_row(self.browser_binary_edit, self.browser_browse_button))
        advanced.addRow('report_id', self.report_id_edit)
        self.advanced_widget.setVisible(False)

        buttons = QHBoxLayout()
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.reload_button)
        buttons.addWidget(self.oobe_button)
        buttons.addStretch(1)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(28, 24, 28, 24)
        content_layout.setSpacing(18)
        content_layout.addWidget(SubtitleLabel('设置'))
        content_layout.addLayout(common)
        content_layout.addWidget(self.advanced_button)
        content_layout.addWidget(self.advanced_widget)
        content_layout.addLayout(buttons)
        content_layout.addStretch(1)
        scroll = ScrollArea()
        scroll.setWidget(content)
        scroll.setWidgetResizable(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(scroll)

        self.advanced_button.clicked.connect(self.toggle_advanced)
        self.save_button.clicked.connect(self.save)
        self.reload_button.clicked.connect(self.load)
        self.oobe_button.clicked.connect(self.oobe_requested)
        self.driver_browse_button.clicked.connect(
            lambda: self._select_file(self.driver_path_edit, 'WebDriver (*.exe);;All files (*.*)')
        )
        self.browser_browse_button.clicked.connect(
            lambda: self._select_file(self.browser_binary_edit, 'Browser (*.exe);;All files (*.*)')
        )
        self.load()

    def _path_row(self, edit: LineEdit, button: ToolButton) -> QWidget:
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

    def toggle_advanced(self) -> None:
        visible = not self.advanced_widget.isVisible()
        self.advanced_widget.setVisible(visible)
        self.advanced_button.setText('隐藏高级设置' if visible else '显示高级设置')

    def load(self) -> None:
        config = read_config_file()
        self.mode_combo.setCurrentText(str(config.get('mode', 'video')))
        self.delay_spin.setValue(float(config.get('delay_multiplier', 1.0)))
        raw_options = str(config.get('options', ''))
        browser = str(config.get('browser', 'Chrome'))
        self.headless_check.setChecked(is_headless_options(raw_options))
        self.notification_check.setChecked(bool(config.get('system_notifications', True)))
        self.handoff_check.setChecked(bool(config.get('manual_handoff_enabled', True)))
        self.choose_correctly_check.setChecked(bool(config.get('choose_correctly', True)))
        self.browser_combo.setCurrentText(str(config.get('browser', 'Chrome')))
        self.options_edit.setText(browser_options_without_mode(raw_options))
        self.driver_path_edit.setText(str(config.get('driver_path', '')))
        self.browser_binary_edit.setText(str(config.get('browser_binary', '')))
        self.login_timeout_spin.setValue(int(config.get('login_wait_timeout', 300)))
        self.report_id_edit.setText(str(config.get('report_id', '')))

    def update_config(self, config: dict) -> dict:
        browser = self.browser_combo.currentText()
        raw_options = self.options_edit.text().strip()
        options = (
            headless_browser_options(raw_options, browser)
            if self.headless_check.isChecked()
            else visible_browser_options(raw_options, browser)
        )
        config.update({
            'mode': self.mode_combo.currentText(),
            'day_to_start_on': 1,
            'delay_multiplier': self.delay_spin.value(),
            'system_notifications': self.notification_check.isChecked(),
            'manual_handoff_enabled': self.handoff_check.isChecked(),
            'foreground_on_manual': True,
            'choose_correctly': self.choose_correctly_check.isChecked(),
            'browser': browser,
            'options': options,
            'driver_path': self.driver_path_edit.text().strip(),
            'browser_binary': self.browser_binary_edit.text().strip(),
            'login_wait_timeout': self.login_timeout_spin.value(),
            'report_id': self.report_id_edit.text().strip(),
        })
        return config

    def save(self) -> dict:
        config = self.update_config(read_config_file())
        write_config_file(config)
        self.saved.emit(config)
        return config


class OobeDialog(QDialog):
    """Transactional first-run setup for accounts and desktop automation."""

    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self.config = normalize_config()
        self.config.update(deepcopy(config))
        self.accounts = normalize_accounts(self.config)
        # Keep the configured default account until the user explicitly picks
        # another one.  Older builds silently replaced it with the first row
        # whenever the OOBE dialog was opened.
        self.default_account_id = str(
            self.config.get('default_account_id', '') or ''
        ).strip()
        self.imported_tasks: list[WorkspaceTask] = []
        self.editing_account_id = ''
        self.discover_after_accept = False
        self._first_run = not bool(config.get('oobe_completed', False))

        self.setWindowTitle('AutoEwt 首次设置')
        self.setModal(True)
        self.resize(780, 600)
        self.setMinimumSize(700, 540)
        self.stack = QStackedWidget()
        self.step_label = CaptionLabel()
        self.error_label = CaptionLabel()
        self.error_label.setStyleSheet('color: #c42b1c;')
        self.error_label.setWordWrap(True)
        self.back_button = PushButton('上一步')
        self.next_button = PrimaryPushButton('下一步')
        self.cancel_button = PushButton('稍后设置')

        self._build_welcome_page()
        self._build_account_page()
        self._build_finish_page()

        buttons = QHBoxLayout()
        buttons.addWidget(self.step_label)
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.back_button)
        buttons.addWidget(self.next_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 22)
        layout.setSpacing(12)
        layout.addWidget(self.stack, 1)
        layout.addWidget(self.error_label)
        layout.addLayout(buttons)
        self.back_button.clicked.connect(self.back)
        self.next_button.clicked.connect(self.next)
        self.cancel_button.clicked.connect(self.reject)
        self._refresh_account_table()
        self.update_buttons()

    def _build_welcome_page(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        title = SubtitleLabel('欢迎使用 AutoEwt')
        intro = BodyLabel(
            '桌面端用于集中管理多个账号及其任务。同一账号只会运行一个浏览器，'
            '不同账号可按并发设置同时执行。'
        )
        intro.setWordWrap(True)
        verification = BodyLabel(
            '后台模式遇到真人验证时会发送系统通知并显示原浏览器会话；'
            '程序不会自动破解、拖动或绕过验证。'
        )
        verification.setWordWrap(True)
        layout.addWidget(title)
        layout.addSpacing(12)
        layout.addWidget(intro)
        layout.addWidget(verification)
        layout.addStretch(1)
        self.stack.addWidget(page)

    def _build_account_page(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)
        layout.addWidget(SubtitleLabel('账户库'))

        self.account_name_edit = LineEdit()
        self.account_name_edit.setPlaceholderText('账户名称')
        self.account_username_edit = LineEdit()
        self.account_username_edit.setPlaceholderText('用户名')
        self.account_password_edit = PasswordLineEdit()
        self.account_password_edit.setPlaceholderText('密码')
        self.account_enabled_check = CheckBox('启用')
        self.account_enabled_check.setChecked(True)
        self.account_save_button = PrimaryPushButton('添加账户')
        self.account_save_button.setIcon(FIF.ADD)
        self.account_clear_button = PushButton('取消编辑')
        self.account_clear_button.setIcon(FIF.CANCEL)
        self.default_account_combo = ComboBox()
        self.default_account_combo.setPlaceholderText('选择默认账号')
        editor = QGridLayout()
        editor.addWidget(self.account_name_edit, 0, 0)
        editor.addWidget(self.account_username_edit, 0, 1)
        editor.addWidget(self.account_password_edit, 0, 2)
        editor.addWidget(self.account_enabled_check, 0, 3)
        editor.addWidget(self.account_save_button, 1, 0)
        editor.addWidget(self.account_clear_button, 1, 1)
        editor.addWidget(BodyLabel('默认账号'), 1, 2)
        editor.addWidget(self.default_account_combo, 1, 3)

        self.account_import_button = PushButton('导入表格')
        self.account_import_button.setIcon(FIF.DOWNLOAD)
        self.account_template_button = PushButton('导出模板')
        self.account_template_button.setIcon(FIF.SAVE)
        self.account_delete_button = PushButton('删除选中')
        self.account_delete_button.setIcon(FIF.DELETE)
        commands = QHBoxLayout()
        commands.addWidget(self.account_import_button)
        commands.addWidget(self.account_template_button)
        commands.addWidget(self.account_delete_button)
        commands.addStretch(1)

        self.account_table = TableWidget()
        self.account_table.setColumnCount(4)
        self.account_table.setHorizontalHeaderLabels(['启用', '账户名称', '用户名', '密码'])
        self.account_table.verticalHeader().hide()
        self.account_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.account_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.account_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        header = self.account_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)

        layout.addLayout(editor)
        layout.addLayout(commands)
        layout.addWidget(self.account_table, 1)
        self.account_save_button.clicked.connect(self._save_account)
        self.account_clear_button.clicked.connect(self._clear_account_editor)
        self.account_import_button.clicked.connect(self._import_accounts)
        self.account_template_button.clicked.connect(self._export_account_template)
        self.account_delete_button.clicked.connect(self._delete_account)
        self.account_table.cellDoubleClicked.connect(self._edit_account)
        self.default_account_combo.currentIndexChanged.connect(
            self._default_account_changed
        )
        self.stack.addWidget(page)

    def _build_finish_page(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(SubtitleLabel('确认设置'))
        self.finish_summary = BodyLabel()
        self.finish_summary.setWordWrap(True)
        layout.addWidget(self.finish_summary)
        note = CaptionLabel(
            '点击“完成”后才会写入账户与导入任务。浏览器、人工验证和运行设置保持不变，'
            '可随后在“设置”页管理；任务信息请在任务页主动获取。'
        )
        note.setWordWrap(True)
        layout.addSpacing(12)
        layout.addWidget(note)
        layout.addStretch(1)
        self.stack.addWidget(page)

    def _show_error(self, message: str) -> None:
        self.error_label.setText(message)

    def _clear_error(self) -> None:
        self.error_label.clear()

    def _refresh_account_table(self) -> None:
        selected_id = str(getattr(self, 'default_account_id', '') or '')
        self.account_table.setRowCount(len(self.accounts))
        for row, account in enumerate(self.accounts):
            self.account_table.setItem(row, 0, QTableWidgetItem('是' if account.enabled else '否'))
            self.account_table.setItem(row, 1, QTableWidgetItem(account.name))
            self.account_table.setItem(row, 2, QTableWidgetItem(account.username))
            self.account_table.setItem(
                row,
                3,
                QTableWidgetItem('●' * min(10, len(account.password))),
            )
        self.default_account_combo.blockSignals(True)
        self.default_account_combo.clear()
        for account in self.accounts:
            if account.enabled:
                self.default_account_combo.addItem(account.name, userData=account.id)
        valid_ids = {account.id for account in self.accounts if account.enabled}
        if selected_id not in valid_ids:
            selected_id = next(
                (account.id for account in self.accounts if account.enabled),
                '',
            )
        self.default_account_id = selected_id
        index = self.default_account_combo.findData(selected_id)
        if index >= 0:
            self.default_account_combo.setCurrentIndex(index)
        self.default_account_combo.blockSignals(False)

    def _default_account_changed(self, index: int) -> None:
        value = self.default_account_combo.itemData(index)
        self.default_account_id = str(value or '').strip()

    def _save_account(self) -> bool:
        username = self.account_username_edit.text().strip()
        password = self.account_password_edit.text()
        if not username:
            self._show_error('请输入用户名。')
            return False
        if not password and self.account_enabled_check.isChecked():
            self._show_error('启用的账户必须填写密码。')
            return False
        duplicate = next((
            item for item in self.accounts
            if item.username.casefold() == username.casefold()
            and item.id != self.editing_account_id
        ), None)
        if duplicate:
            self._show_error(f'用户名“{username}”已经存在。')
            return False
        profile = AccountProfile(
            id=self.editing_account_id or f'account-{uuid.uuid4().hex}',
            name=self.account_name_edit.text().strip() or username,
            username=username,
            password=password,
            enabled=self.account_enabled_check.isChecked(),
        )
        if self.editing_account_id:
            self.accounts = [
                profile if item.id == self.editing_account_id else item
                for item in self.accounts
            ]
        else:
            self.accounts.append(profile)
        self._clear_account_editor()
        self._refresh_account_table()
        self._clear_error()
        return True

    def _edit_account(self, row: int, column: int) -> None:
        del column
        if not 0 <= row < len(self.accounts):
            return
        account = self.accounts[row]
        self.editing_account_id = account.id
        self.account_name_edit.setText(account.name)
        self.account_username_edit.setText(account.username)
        self.account_password_edit.setText(account.password)
        self.account_enabled_check.setChecked(account.enabled)
        self.account_save_button.setText('保存账户')

    def _clear_account_editor(self) -> None:
        self.editing_account_id = ''
        self.account_name_edit.clear()
        self.account_username_edit.clear()
        self.account_password_edit.clear()
        self.account_enabled_check.setChecked(True)
        self.account_save_button.setText('添加账户')

    def _delete_account(self) -> None:
        rows = {index.row() for index in self.account_table.selectionModel().selectedRows()}
        removed_ids = {
            account.id for row, account in enumerate(self.accounts) if row in rows
        }
        if not removed_ids:
            return
        self.accounts = [
            account for account in self.accounts if account.id not in removed_ids
        ]
        self.imported_tasks = [
            task for task in self.imported_tasks if task.account_id not in removed_ids
        ]
        self._clear_account_editor()
        self._refresh_account_table()

    def _import_accounts(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            '导入账号与任务',
            str(APP_DIR),
            '表格 (*.xlsx *.csv *.tsv)',
        )
        if not path:
            return
        try:
            rows = import_workspace_rows(path)
            self._merge_import_rows(rows)
        except Exception as exc:
            self._show_error(f'导入失败：{exc}')
            return
        self._clear_error()

    def _merge_import_rows(self, rows) -> None:
        self.accounts = merge_imported_accounts(self.accounts, rows)
        account_by_username = {
            account.username.casefold(): account for account in self.accounts
        }
        existing_keys = {
            (task.account_id, task.url) for task in self.imported_tasks
        }
        for row in rows:
            if not is_task_detail_url(row.task_url):
                continue
            account = account_by_username[row.account.username.casefold()]
            key = (account.id, row.task_url)
            if key in existing_keys:
                continue
            existing_keys.add(key)
            self.imported_tasks.append(WorkspaceTask(
                id=f'imported-{uuid.uuid4().hex}',
                account_id=account.id,
                account_name=account.name,
                title=row.task_title or '导入任务',
                url=row.task_url,
                origin='imported',
            ))
        self._refresh_account_table()

    def _export_account_template(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            '导出导入模板',
            str(APP_DIR / 'AutoEwt账号任务模板.xlsx'),
            'Excel (*.xlsx);;CSV (*.csv)',
        )
        if not path:
            return
        try:
            export_import_template(path)
        except Exception as exc:
            self._show_error(f'导出失败：{exc}')

    def _validate_accounts(self) -> bool:
        editor_has_values = any((
            self.account_name_edit.text().strip(),
            self.account_username_edit.text().strip(),
            self.account_password_edit.text(),
        ))
        if editor_has_values and not self._save_account():
            return False
        enabled = [account for account in self.accounts if account.enabled]
        if not enabled:
            self._show_error('至少需要一个启用的账户。')
            return False
        missing_password = next((account for account in enabled if not account.password), None)
        if missing_password:
            self._show_error(f'启用的账户“{missing_password.name}”尚未填写密码。')
            return False
        return True

    def _update_finish_summary(self) -> None:
        enabled = sum(1 for account in self.accounts if account.enabled)
        self.finish_summary.setText(
            f'账户：{len(self.accounts)} 个（启用 {enabled} 个）\n'
            f'导入任务：{len(self.imported_tasks)} 个'
        )

    def _legacy_task_urls(self) -> list[str]:
        """Return URLs from the legacy ``task_urls`` field, de-duplicated."""
        raw_urls = self.config.get('task_urls', [])
        if isinstance(raw_urls, str):
            raw_urls = raw_urls.splitlines()
        if not isinstance(raw_urls, (list, tuple)):
            raw_urls = []

        urls: list[str] = []
        seen: set[str] = set()
        for value in raw_urls:
            url = str(value or '').strip()
            if not url or url in seen:
                continue
            seen.add(url)
            urls.append(url)
        return urls

    @staticmethod
    def _legacy_task_title(url: str) -> str:
        match = re.search(r'[?&]homeworkId=([^&#]+)', url, re.IGNORECASE)
        homework_id = match.group(1) if match else ''
        return f'旧配置任务 {homework_id}' if homework_id else '旧配置任务'

    def _apply_config(self) -> None:
        self.config.update({
            'oobe_completed': True,
            'oobe_version': OOBE_VERSION,
            'accounts': [account.to_dict() for account in self.accounts],
        })
        selected_id = str(
            self.default_account_combo.currentData()
            or self.default_account_id
            or ''
        ).strip()
        enabled = next(
            (
                account for account in self.accounts
                if account.enabled and account.id == selected_id
            ),
            None,
        ) or next(account for account in self.accounts if account.enabled)
        self.default_account_id = enabled.id
        self.config['default_account_id'] = enabled.id
        self.config['username'] = enabled.username
        self.config['password'] = enabled.password

        # Keep every legacy batch-task payload, including tasks whose account
        # was disabled or removed in this OOBE pass.  The task page can report
        # the missing account later; silently deleting it makes old
        # configurations impossible to recover.
        tasks = [
            dict(task) for task in self.config.get('batch_tasks', []) or []
            if isinstance(task, dict)
        ]
        by_key = {
            (str(task.get('account_id', '')), str(task.get('url', ''))): index
            for index, task in enumerate(tasks)
            if str(task.get('url', '')).strip()
        }
        represented_urls = {
            str(task.get('url', '')).strip()
            for task in tasks
            if str(task.get('url', '')).strip()
        }

        # Pre-version OOBE stored detail URLs only in ``task_urls`` (and some
        # users put a detail URL in ``list_url``).  Materialize those URLs as
        # normal tasks while retaining all raw URLs for compatibility.
        legacy_urls = self._legacy_task_urls()
        migration_urls = list(legacy_urls)
        legacy_list_url = str(self.config.get('list_url', '') or '').strip()
        if is_task_detail_url(legacy_list_url):
            migration_urls.append(legacy_list_url)
        for url in migration_urls:
            key = (enabled.id, url)
            if (
                not is_task_detail_url(url)
                or key in by_key
                or url in represented_urls
            ):
                continue
            stable_id = f'legacy-{uuid.uuid5(uuid.NAMESPACE_URL, url).hex}'
            migrated = WorkspaceTask(
                id=stable_id,
                account_id=enabled.id,
                account_name=enabled.name,
                title=self._legacy_task_title(url),
                url=url,
                origin='manual',
            ).__dict__.copy()
            by_key[key] = len(tasks)
            represented_urls.add(url)
            tasks.append(migrated)

        for task in self.imported_tasks:
            key = (task.account_id, task.url)
            if key in by_key:
                tasks[by_key[key]] = task.__dict__.copy()
            else:
                by_key[key] = len(tasks)
                tasks.append(task.__dict__.copy())
        self.config['batch_tasks'] = tasks
        task_urls: list[str] = []
        seen_urls: set[str] = set()
        for url in legacy_urls + [str(task.get('url', '')) for task in tasks]:
            url = str(url or '').strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            task_urls.append(url)
        self.config['task_urls'] = task_urls
        self.discover_after_accept = False

    def back(self) -> None:
        self._clear_error()
        self.stack.setCurrentIndex(max(0, self.stack.currentIndex() - 1))
        self.update_buttons()

    def next(self) -> None:
        self._clear_error()
        index = self.stack.currentIndex()
        if index == 1 and not self._validate_accounts():
            return
        if index < self.stack.count() - 1:
            next_index = index + 1
            if next_index == self.stack.count() - 1:
                self._update_finish_summary()
            self.stack.setCurrentIndex(next_index)
            self.update_buttons()
            return
        if not self._validate_accounts():
            return
        self._apply_config()
        self.accept()

    def update_buttons(self) -> None:
        index = self.stack.currentIndex()
        self.step_label.setText(f'{index + 1} / {self.stack.count()}')
        self.back_button.setEnabled(index > 0)
        self.next_button.setText('完成' if index == self.stack.count() - 1 else '下一步')


class MainWindow(FluentWindow):
    def __init__(self):
        super().__init__()
        self.discovery_thread: QThread | None = None
        self.discovery_worker: DiscoveryWorker | None = None
        self.discovery_account_ids: set[str] = set()
        self.batch_thread: QThread | None = None
        self.batch_worker: BatchWorker | None = None
        self._close_requested = False
        self._close_ready = False

        self.config_page = SettingsPage()
        self.account_page = AccountPage()
        self.task_page = TaskPage()
        self.run_page = RunPage()
        config = read_config_file()
        self.account_page.load(config)
        self.task_page.set_accounts(self.account_page.accounts)
        self.task_page.load(config)

        self.addSubInterface(self.task_page, FIF.DOCUMENT, '任务')
        self.addSubInterface(self.account_page, FIF.PEOPLE, '账户')
        self.addSubInterface(self.run_page, FIF.PLAY, '运行')
        self.addSubInterface(self.config_page, FIF.SETTING, '设置')
        self.setWindowTitle('AutoEwt')
        self.resize(1240, 800)
        self.setMinimumSize(1024, 680)
        try:
            self.setMicaEffectEnabled(False)
        except (AttributeError, RuntimeError):
            pass

        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(
            QApplication.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning)
        )
        self.tray.setToolTip('AutoEwt')
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

        self.task_page.discover_requested.connect(self.discover_tasks)
        self.task_page.start_requested.connect(self.start_batch)
        self.task_page.preview_requested.connect(self.preview_task)
        self.run_page.preview_requested.connect(self.preview_task)
        self.run_page.stop_requested.connect(self.stop_batch)
        self.account_page.accounts_changed.connect(self.accounts_changed)
        self.account_page.tasks_imported.connect(self.import_tasks)
        self.config_page.saved.connect(
            lambda _: self._toast('已保存', '设置已写入 config.yml')
        )
        self.config_page.oobe_requested.connect(self.show_oobe)
        if (
            not config.get('oobe_completed', False)
            or int(config.get('oobe_version', 0)) < OOBE_VERSION
        ):
            QTimer.singleShot(0, self.show_oobe)

    def _save_all(self) -> dict:
        config = self.config_page.update_config(read_config_file())
        self.account_page.save_into(config)
        self.task_page.save_into(config)
        write_config_file(config)
        return config

    def accounts_changed(self, accounts: list[AccountProfile]) -> None:
        if self._worker_threads_running():
            return
        self.task_page.set_accounts(accounts)
        self._save_all()

    def import_tasks(self, tasks: list[WorkspaceTask]) -> None:
        if self._worker_threads_running():
            return
        self.task_page.set_tasks(tasks)
        self._save_all()
        self._toast('导入完成', f'已导入 {len(tasks)} 个任务')

    def discover_tasks(self) -> None:
        if self.batch_thread and self.batch_thread.isRunning():
            self._toast('任务正在运行', '请等待批量任务结束后再获取任务', error=True)
            return
        if self.discovery_thread and self.discovery_thread.isRunning():
            return
        selected_ids = self.task_page.selected_account_ids()
        accounts = [
            item for item in self.account_page.accounts
            if item.enabled and item.id in selected_ids
        ]
        if not accounts:
            self._toast('没有可用账户', '请先在账户库添加并启用账户', error=True)
            return
        config = self._save_all()
        parallelism = self.task_page.parallel_spin.value()
        self.discovery_account_ids = {account.id for account in accounts}
        self.task_page.set_discovering(
            True,
            f'准备并发登录（最多 {min(parallelism, len(accounts))} 个账号）',
        )
        self.discovery_thread = QThread(self)
        self.discovery_worker = DiscoveryWorker(config, accounts, parallelism)
        self.discovery_worker.moveToThread(self.discovery_thread)
        self.discovery_thread.started.connect(self.discovery_worker.run)
        self.discovery_worker.status_changed.connect(
            self.discovery_status_changed
        )
        self.discovery_worker.manual_intervention.connect(self.handle_manual_event)
        self.discovery_worker.completed.connect(self.discovery_completed)
        self.discovery_worker.failed.connect(self.discovery_failed)
        self.discovery_worker.completed.connect(self.discovery_worker.deleteLater)
        self.discovery_worker.failed.connect(self.discovery_worker.deleteLater)
        self.discovery_worker.completed.connect(self.discovery_thread.quit)
        self.discovery_worker.failed.connect(self.discovery_thread.quit)
        self.discovery_thread.finished.connect(self.discovery_thread.deleteLater)
        self.discovery_thread.finished.connect(self.clear_discovery_refs)
        self.discovery_thread.start()

    def discovery_status_changed(self, account_id: str, status: str) -> None:
        account_name = next(
            (
                account.name
                for account in self.account_page.accounts
                if account.id == account_id
            ),
            account_id,
        )
        self.task_page.set_discovering(
            True,
            f'{account_name}：{status}' if account_name else status,
        )

    def discovery_completed(self, tasks: list[WorkspaceTask]) -> None:
        self.task_page.set_discovering(False, f'找到 {len(tasks)} 个任务')
        self.task_page.replace_discovered_tasks(self.discovery_account_ids, tasks)
        if self._close_requested:
            return
        self._save_all()
        self._toast(
            '任务已更新' if tasks else '未找到任务',
            f'找到 {len(tasks)} 个未完成任务' if tasks else '当前账户没有可运行的视频任务',
            error=not bool(tasks),
        )

    def discovery_failed(self, message: str) -> None:
        self.task_page.set_discovering(False, '获取失败')
        if self._close_requested:
            return
        self._toast('自动获取失败', message or '请检查登录状态', error=True)

    def clear_discovery_refs(self) -> None:
        self.discovery_thread = None
        self.discovery_worker = None
        self.discovery_account_ids.clear()
        self._finish_close_if_ready()

    def start_batch(self, tasks: list[BatchTask], parallelism: int) -> None:
        if self.discovery_thread and self.discovery_thread.isRunning():
            self._toast('正在获取任务', '请等待任务获取结束后再开始刷课', error=True)
            return
        if self.batch_thread and self.batch_thread.isRunning():
            return
        if not tasks:
            self._toast('没有任务', '请先选择至少一个课程任务', error=True)
            return
        invalid = [task.title for task in tasks if not is_task_detail_url(task.url)]
        accounts = {item.id: item for item in self.account_page.accounts if item.enabled}
        missing = [task.title for task in tasks if task.account_id not in accounts]
        if invalid or missing:
            name = (invalid or missing)[0]
            self._toast('任务无法启动', f'请检查任务 URL 或绑定账户：{name}', error=True)
            return
        config = self._save_all()
        account_names = {key: value.name for key, value in accounts.items()}
        self.run_page.prepare(tasks, account_names)
        self.switchTo(self.run_page)
        self.batch_thread = QThread(self)
        self.batch_worker = BatchWorker(
            tasks,
            config,
            parallelism,
            list(accounts.values()),
        )
        self.batch_worker.moveToThread(self.batch_thread)
        self.batch_thread.started.connect(self.batch_worker.run)
        self.batch_worker.log_message.connect(self.run_page.append_log)
        self.batch_worker.status_changed.connect(self.run_page.set_task_status)
        self.batch_worker.progress_changed.connect(self.run_page.set_task_progress)
        self.batch_worker.result_changed.connect(self.run_page.record_result)
        self.batch_worker.manual_intervention.connect(self.handle_manual_event)
        self.batch_worker.completed.connect(self.batch_completed)
        self.batch_worker.completed.connect(self.batch_worker.deleteLater)
        self.batch_worker.completed.connect(self.batch_thread.quit)
        self.batch_thread.finished.connect(self.batch_thread.deleteLater)
        self.batch_thread.finished.connect(self.clear_batch_refs)
        self.batch_thread.start()

    def preview_task(self, task_id: str, url: str) -> None:
        if self.batch_worker and self.batch_worker.promote_task(task_id):
            self._toast('正在打开预览', '该任务将打开或置前自己的可见浏览器')
            return
        if not QDesktopServices.openUrl(QUrl.fromUserInput(url)):
            self._toast('无法打开预览', '系统默认浏览器未能打开该链接', error=True)

    def stop_batch(self) -> None:
        if self.batch_worker:
            self.batch_worker.stop()
            self.run_page.status_label.setText('正在停止')
            self.run_page.stop_button.setEnabled(False)

    def batch_completed(self, results: list[BatchTaskResult]) -> None:
        self.run_page.finish(results)
        if self._close_requested:
            return
        failed = sum(result.code not in (0, 130) for result in results)
        if failed:
            self._toast('批量任务结束', f'{failed} 个任务失败，请查看日志', error=True)
        elif any(result.code == 130 for result in results):
            self._toast('已停止', '批量任务已结束')
        else:
            self._toast('全部完成', f'{len(results)} 个任务执行完成')

    def clear_batch_refs(self) -> None:
        self.batch_thread = None
        self.batch_worker = None
        self._finish_close_if_ready()

    def handle_manual_event(self, task_id: str, event: ManualInterventionEvent) -> None:
        notification_phase = event.phase in {
            'required',
            'waiting_slot',
            'visible_started',
            'notice',
        }
        notifications_enabled = bool(
            read_config_file().get('system_notifications', True)
        )
        if notification_phase and notifications_enabled:
            if self.tray.isVisible():
                self.tray.showMessage(
                    event.title,
                    event.message,
                    QSystemTrayIcon.MessageIcon.Warning,
                    10000,
                )
            else:
                send_system_notification(event.title, event.message)
            QApplication.alert(self, 3000)
        if event.phase not in {'resolved', 'notice'}:
            self._toast(event.title, event.message, error=True)

    def show_oobe(self) -> None:
        if self._worker_threads_running():
            self._toast('任务正在运行', '运行期间不能重新打开 OOBE 或修改账户设置', error=True)
            return
        dialog = OobeDialog(read_config_file(), self)
        while True:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            try:
                write_config_file(dialog.config)
            except Exception as exc:
                logging.exception('OOBE 配置写入失败，保留向导状态等待重试')
                dialog._show_error(
                    f'配置写入失败：{exc}。请检查磁盘空间或文件权限后再次点击“完成”。'
                )
                self._toast('设置尚未保存', '向导内容已保留，可以修复问题后重试', error=True)
                continue
            break
        self.account_page.blockSignals(True)
        self.account_page.load(dialog.config)
        self.account_page.blockSignals(False)
        self.config_page.load()
        self.task_page.set_accounts(self.account_page.accounts)
        self.task_page.load(dialog.config)
        self._toast('首次设置完成', '现在可以获取任务或导入账号表格')

    def _toast(self, title: str, content: str, error: bool = False) -> None:
        show = InfoBar.error if error else InfoBar.success
        show(
            title=title,
            content=content,
            duration=3000,
            position=InfoBarPosition.TOP_RIGHT,
            parent=self,
        )

    def closeEvent(self, event) -> None:
        if self._close_ready or not self._worker_threads_running():
            self.tray.hide()
            super().closeEvent(event)
            return

        event.ignore()
        if self._close_requested:
            return
        self._close_requested = True
        self.setEnabled(False)
        self.run_page.status_label.setText('正在安全退出')
        self.stop_batch()
        if self.discovery_worker:
            try:
                self.discovery_worker.close()
            except Exception:
                logging.exception('停止任务发现线程失败')
        self._toast('正在退出', '等待浏览器任务和工作线程安全结束')

    def _worker_threads_running(self) -> bool:
        return bool(
            (self.discovery_thread and self.discovery_thread.isRunning())
            or (self.batch_thread and self.batch_thread.isRunning())
        )

    def _finish_close_if_ready(self) -> None:
        if not self._close_requested or self._worker_threads_running():
            return
        self._close_ready = True
        self.tray.hide()
        QTimer.singleShot(0, self.close)


def main() -> int:
    os.chdir(APP_DIR)
    app = QApplication(sys.argv)
    app.setApplicationName('AutoEwt')
    configure_application_font(app)
    setTheme(Theme.AUTO)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
