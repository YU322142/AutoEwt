import ast
import logging
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT / 'src'
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


class GuiContractTests(unittest.TestCase):
    def test_desktop_gui_has_no_embedded_webengine(self):
        source = (SRC_DIR / 'gui_qfluent.py').read_text(encoding='utf-8')

        self.assertNotIn('QWebEngine', source)
        self.assertNotIn('BrowserPage', source)
        self.assertIn('QDesktopServices.openUrl', source)
        self.assertIn('configure_application_font(app)', source)
        self.assertFalse((SRC_DIR / 'rin_gui.py').exists())
        self.assertFalse((SRC_DIR / 'qml').exists())

    def test_build_workflow_explicitly_targets_gui_entry(self):
        workflow = (ROOT / '.github' / 'workflows' / 'build.yml').read_text(
            encoding='utf-8'
        )

        self.assertIn('script-name: src/gui.py', workflow)
        self.assertIn('enable-plugins: pyside6', workflow)
        self.assertIn('windows-console-mode: disable', workflow)
        self.assertIn('build/gui.dist/*', workflow)

        gui_requirements = (ROOT / 'requirements-gui.txt').read_text(
            encoding='utf-8'
        )
        self.assertIn('pywin32', gui_requirements)

    def test_account_combo_stores_ids_as_user_data(self):
        source = (SRC_DIR / 'gui_qfluent.py').read_text(encoding='utf-8')

        self.assertIn(
            "self.account_filter.addItem('全部启用账户', userData='')",
            source,
        )
        self.assertIn(
            'self.account_filter.addItem(account.name, userData=account.id)',
            source,
        )

    def test_headless_mode_is_a_checkbox_and_day_skip_is_removed(self):
        source = (SRC_DIR / 'gui_qfluent.py').read_text(encoding='utf-8')

        self.assertIn("CheckBox('无头模式（后台运行）')", source)
        self.assertIn('headless_browser_options(raw_options, browser)', source)
        self.assertNotIn("common.addRow('从第几天开始'", source)

    def test_batch_worker_defers_preview_until_runner_is_attached(self):
        """The GUI startup race must be covered without launching Qt or Selenium."""
        source = (SRC_DIR / 'gui_qfluent.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        batch_worker = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == 'BatchWorker'
        )
        methods = {
            node.name: node
            for node in batch_worker.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        run_source = ast.unparse(methods['run'])
        harness_tree = ast.Module(
            body=[
                ast.ImportFrom(
                    module='__future__',
                    names=[ast.alias(name='annotations')],
                    level=0,
                ),
                ast.ClassDef(
                    name='BatchWorkerHarness',
                    bases=[],
                    keywords=[],
                    decorator_list=[],
                    body=[
                        methods['promote_task'],
                        methods['_attach_runner'],
                        methods['stop'],
                    ],
                ),
            ],
            type_ignores=[],
        )
        namespace = {'logging': logging}
        exec(compile(ast.fix_missing_locations(harness_tree), '<gui-contract>', 'exec'), namespace)
        worker = object.__new__(namespace['BatchWorkerHarness'])
        worker.tasks = [SimpleNamespace(id='queued-task')]
        worker.runner = None
        worker._preview_lock = threading.Lock()
        worker._pending_preview_task_ids = set()
        worker._finished = False
        worker._stop_pending = False
        worker.stop_event = threading.Event()

        class RecordingRunner:
            def __init__(self):
                self.promoted = []
                self.stop_calls = 0

            def promote_task(self, task_id):
                self.promoted.append(task_id)
                return True

            def stop(self):
                self.stop_calls += 1

        runner = RecordingRunner()

        self.assertTrue(worker.promote_task('queued-task'))
        self.assertEqual(worker._pending_preview_task_ids, {'queued-task'})
        worker._attach_runner(runner)
        self.assertIs(worker.runner, runner)
        self.assertEqual(runner.promoted, ['queued-task'])
        self.assertFalse(worker._pending_preview_task_ids)
        worker.runner = None
        worker._finished = True
        self.assertFalse(worker.promote_task('queued-task'))

        worker._finished = False
        worker.stop()
        self.assertTrue(worker.stop_event.is_set())
        self.assertTrue(worker._stop_pending)
        stopped_runner = RecordingRunner()
        worker._attach_runner(stopped_runner)
        self.assertEqual(stopped_runner.stop_calls, 1)
        self.assertLess(
            run_source.index('self._attach_runner(runner)'),
            run_source.index('results = runner.run()'),
        )

    def test_run_page_keeps_structured_logs_and_account_task_filters(self):
        source = (SRC_DIR / 'gui_qfluent.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        classes = {
            node.name: node for node in tree.body if isinstance(node, ast.ClassDef)
        }

        log_record = classes['GuiLogRecord']
        field_names = {
            node.target.id
            for node in log_record.body
            if isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
        }
        self.assertTrue({
            'message', 'account_id', 'account_name', 'task_id',
            'task_label', 'thread_name',
        }.issubset(field_names))

        run_page_source = ast.unparse(classes['RunPage'])
        self.assertIn("self.log_account_filter = ComboBox()", run_page_source)
        self.assertIn("self.log_task_filter = ComboBox()", run_page_source)
        self.assertIn("self.log_thread_filter = ComboBox()", run_page_source)
        self.assertIn('self.log_records.append(record)', run_page_source)
        self.assertIn('record.account_id == account_id', run_page_source)
        self.assertIn('record.task_id == task_id', run_page_source)
        self.assertIn('record.thread_name == thread_name', run_page_source)
        self.assertIn('setMaximumBlockCount(0)', run_page_source)

    def test_run_page_embeds_three_progress_metrics_per_account(self):
        source = (SRC_DIR / 'gui_qfluent.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        run_page = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == 'RunPage'
        )
        run_page_source = ast.unparse(run_page)

        self.assertIn("('courses', '课程')", run_page_source)
        self.assertIn("('days', '天数')", run_page_source)
        self.assertIn("('current_course', '当前')", run_page_source)
        self.assertIn('self.progress_model.update(task_id, state)', run_page_source)
        self.assertIn('self.account_progress_models.setdefault', run_page_source)
        self.assertIn('self._create_account_progress_widget(account_id)', run_page_source)
        self.assertIn('self._refresh_account_progress(account_id)', run_page_source)

    def test_run_page_groups_rows_by_account(self):
        source = (SRC_DIR / 'gui_qfluent.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        run_page = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == 'RunPage'
        )
        run_page_source = ast.unparse(run_page)

        self.assertIn('self.row_by_account', run_page_source)
        self.assertIn('grouped.setdefault(account_id, []).append(task)', run_page_source)
        self.assertIn('self.table.setRowCount(len(grouped))', run_page_source)
        self.assertIn("['账户', '任务队列', '状态', '进度', '预览']", run_page_source)

    def test_task_page_can_select_latest_course_per_account(self):
        source = (SRC_DIR / 'gui_qfluent.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        task_page = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == 'TaskPage'
        )
        task_page_source = ast.unparse(task_page)

        self.assertIn("PushButton('选择每个账号最新一个课程')", task_page_source)
        self.assertIn('def select_latest_per_account(self)', task_page_source)
        self.assertIn('latest_by_account', task_page_source)
        self.assertIn('account.id for account in self.accounts if account.enabled', task_page_source)
        self.assertLess(
            task_page_source.index('commands.addWidget(self.select_all_button)'),
            task_page_source.index('commands.addWidget(self.select_latest_button)'),
        )

    def test_latest_course_selection_handles_dates_ties_and_disabled_accounts(self):
        source = (SRC_DIR / 'gui_qfluent.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        task_page = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == 'TaskPage'
        )
        methods = {
            node.name: node
            for node in task_page.body
            if isinstance(node, ast.FunctionDef)
        }
        harness_tree = ast.Module(
            body=[
                ast.ImportFrom(
                    module='__future__',
                    names=[ast.alias(name='annotations')],
                    level=0,
                ),
                ast.ClassDef(
                    name='TaskPage',
                    bases=[],
                    keywords=[],
                    decorator_list=[],
                    body=[
                        methods['select_latest_per_account'],
                        methods['_latest_task_key'],
                        methods['_sortable_datetime'],
                    ],
                ),
            ],
            type_ignores=[],
        )

        class CheckItem:
            def __init__(self):
                self.state = None

            def setCheckState(self, state):
                self.state = state

        class Table:
            def __init__(self, rows):
                self.items = [CheckItem() for _ in range(rows)]
                self.blocked = False

            def blockSignals(self, blocked):
                previous = self.blocked
                self.blocked = blocked
                return previous

            def item(self, row, column):
                self.assert_checkbox_column(column)
                return self.items[row]

            @staticmethod
            def assert_checkbox_column(column):
                if column != 0:
                    raise AssertionError(f'unexpected column: {column}')

        namespace = {
            'Qt': SimpleNamespace(Checked=2, Unchecked=0),
            're': __import__('re'),
        }
        exec(
            compile(ast.fix_missing_locations(harness_tree), '<gui-contract>', 'exec'),
            namespace,
        )
        page = object.__new__(namespace['TaskPage'])
        page.accounts = [
            SimpleNamespace(id='a', enabled=True),
            SimpleNamespace(id='b', enabled=True),
            SimpleNamespace(id='c', enabled=False),
            SimpleNamespace(id='d', enabled=True),
            SimpleNamespace(id='e', enabled=True),
        ]
        page.tasks = [
            SimpleNamespace(id='a-old', account_id='a', start_time='2026年8月3日 0:00', deadline='2026-09-30'),
            SimpleNamespace(id='a-new', account_id='a', start_time='2026-8-12 08:05', deadline='2026-08-20'),
            SimpleNamespace(id='b-first', account_id='b', start_time='', deadline='2026-08-20'),
            SimpleNamespace(id='b-last', account_id='b', start_time='', deadline='2026-08-31'),
            SimpleNamespace(id='c-disabled', account_id='c', start_time='2026-12-31', deadline=''),
            SimpleNamespace(id='d-short', account_id='d', start_time='2026-08-10', deadline='2026-08-20'),
            SimpleNamespace(id='d-long', account_id='d', start_time='2026-08-10', deadline='2026-08-22'),
            SimpleNamespace(id='e-a', account_id='e', start_time='2026-08-11', deadline='2026-08-22'),
            SimpleNamespace(id='e-z', account_id='e', start_time='2026-08-11', deadline='2026-08-22'),
            SimpleNamespace(id='orphan', account_id='missing', start_time='2026-12-31', deadline=''),
        ]
        page.table = Table(len(page.tasks))
        page.checked_task_ids = {task.id for task in page.tasks}

        page.select_latest_per_account()

        self.assertEqual(
            page.checked_task_ids,
            {'a-new', 'b-last', 'd-long', 'e-z'},
        )
        checked_from_table = {
            task.id
            for task, item in zip(page.tasks, page.table.items)
            if item.state == namespace['Qt'].Checked
        }
        self.assertEqual(checked_from_table, page.checked_task_ids)
        self.assertFalse(page.table.blocked)
        self.assertGreater(
            page._sortable_datetime('2026年8月12日 8:05'),
            page._sortable_datetime('2026-08-03 00:00'),
        )

    def test_gui_log_handler_forwards_context_without_parsing_text(self):
        source = (SRC_DIR / 'gui_qfluent.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        handler = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == 'QtLogHandler'
        )
        emit_method = next(
            node for node in handler.body
            if isinstance(node, ast.FunctionDef) and node.name == 'emit'
        )
        emit_source = ast.unparse(emit_method)

        self.assertIn("getattr(record, 'account_id', '')", emit_source)
        self.assertIn("getattr(record, 'task_id', '')", emit_source)
        self.assertIn("getattr(record, 'thread_name', '')", emit_source)
        self.assertIn('record.threadName', emit_source)

    def test_discovery_status_is_scoped_to_the_current_account(self):
        source = (SRC_DIR / 'gui_qfluent.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        classes = {
            node.name: node for node in tree.body if isinstance(node, ast.ClassDef)
        }
        discovery_source = ast.unparse(classes['DiscoveryWorker'])
        main_window_source = ast.unparse(classes['MainWindow'])

        self.assertIn("status_changed = Signal(str, str)", discovery_source)
        self.assertIn(
            'self.status_changed.emit(account.id, status)',
            discovery_source,
        )
        self.assertIn(
            'def discovery_status_changed(self, account_id: str, status: str)',
            main_window_source,
        )

    def test_oobe_is_versioned_transactional_and_multi_account(self):
        source = (SRC_DIR / 'gui_qfluent.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        classes = {
            node.name: node for node in tree.body if isinstance(node, ast.ClassDef)
        }
        oobe_source = ast.unparse(classes['OobeDialog'])
        main_source = ast.unparse(classes['MainWindow'])

        self.assertIn('self.config = deepcopy(config)', oobe_source)
        self.assertIn('self.accounts = normalize_accounts(self.config)', oobe_source)
        self.assertIn('self._merge_import_rows(rows)', oobe_source)
        self.assertIn("'oobe_version': OOBE_VERSION", oobe_source)
        self.assertIn('self._update_finish_summary()', oobe_source)
        self.assertIn('self._apply_config()', oobe_source)
        self.assertIn('int(config.get(\'oobe_version\', 0)) < OOBE_VERSION', main_source)
        self.assertIn('QTimer.singleShot(0, self.discover_tasks)', main_source)

    def test_config_writes_are_atomic(self):
        source = (SRC_DIR / 'gui_qfluent.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        writer = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == 'write_config_file'
        )
        writer_source = ast.unparse(writer)

        self.assertIn('os.fsync(file.fileno())', writer_source)
        self.assertIn('os.replace(temporary, CONFIG_PATH)', writer_source)


if __name__ == '__main__':
    unittest.main()
