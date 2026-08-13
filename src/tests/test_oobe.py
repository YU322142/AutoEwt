import os
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

SRC_DIR = Path(__file__).resolve().parents[1]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from account_store import AccountProfile, ImportedWorkspaceRow  # noqa: E402
from auto_base import normalize_config  # noqa: E402
from gui_qfluent import (  # noqa: E402
    MainWindow,
    OOBE_VERSION,
    OobeDialog,
    read_config_file,
)
class OobeDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_config(self):
        return {
            'oobe_completed': True,
            'oobe_version': 1,
            'browser': 'Chrome',
            'options': '--mute-audio --disable-features=Example --headless=new',
            'driver_path': '',
            'browser_binary': '',
            'manual_handoff_enabled': True,
            'system_notifications': True,
            'login_wait_timeout': 300,
            'mode': 'video',
            'parallelism': 3,
            'delay_multiplier': 1.25,
            'choose_correctly': True,
            'accounts': [
                AccountProfile('a-1', '甲', 'user-a', 'pass-a').to_dict(),
                AccountProfile('a-2', '乙', 'user-b', 'pass-b').to_dict(),
            ],
            'default_account_id': 'a-1',
            'username': 'user-a',
            'password': 'pass-a',
            'batch_tasks': [{
                'id': 'task-existing',
                'account_id': 'a-2',
                'account_name': '乙',
                'title': '已有任务',
                'url': 'https://teacher.ewt360.com/ewtbend/bend/index/index.html#/'
                       'holiday/student-task-overview?homeworkId=100',
                'origin': 'manual',
            }],
        }

    def test_dialog_does_not_mutate_source_before_accept(self):
        config = self.make_config()
        original = deepcopy(config)
        dialog = OobeDialog(config)
        try:
            dialog.accounts.pop()
            dialog.config['options'] = '--changed'
            dialog.config['batch_tasks'][0]['title'] = 'changed'

            self.assertEqual(config, original)
        finally:
            dialog.close()

    def test_apply_preserves_accounts_tasks_and_custom_browser_options(self):
        config = self.make_config()
        hidden_settings = {
            'browser': 'Firefox',
            'options': '  --mute-audio --custom-flag --headless  ',
            'driver_path': 'drivers/custom-driver.exe',
            'browser_binary': 'browsers/custom-browser.exe',
            'foreground_browser': True,
            'foreground_on_manual': False,
            'manual_handoff_enabled': False,
            'system_notifications': False,
            'login_wait_timeout': 456.5,
            'mode': 'paper',
            'parallelism': 9,
            'delay_multiplier': 2.75,
            'choose_correctly': False,
            'report_id': 'custom-report',
            'day_to_start_on': 7,
        }
        config.update(hidden_settings)
        dialog = OobeDialog(config)
        try:
            dialog._apply_config()

            self.assertEqual(
                [item['id'] for item in dialog.config['accounts']],
                ['a-1', 'a-2'],
            )
            self.assertEqual(
                [item['id'] for item in dialog.config['batch_tasks']],
                ['task-existing'],
            )
            self.assertEqual(
                {key: dialog.config[key] for key in hidden_settings},
                hidden_settings,
            )
            self.assertEqual(dialog.config['oobe_version'], OOBE_VERSION)
            self.assertEqual(dialog.config['parallelism'], hidden_settings['parallelism'])
            self.assertEqual(dialog.config['day_to_start_on'], hidden_settings['day_to_start_on'])
            self.assertEqual(dialog.config['default_account_id'], 'a-1')
            self.assertEqual(dialog.config['username'], 'user-a')
        finally:
            dialog.close()

    def test_oobe_has_only_welcome_accounts_and_confirmation_pages(self):
        dialog = OobeDialog(self.make_config())
        try:
            self.assertEqual(dialog.stack.count(), 3)
            self.assertEqual(dialog.step_label.text(), '1 / 3')
            dialog.next()
            self.assertEqual(dialog.stack.currentIndex(), 1)
            dialog.next()
            self.assertEqual(dialog.stack.currentIndex(), 2)
            self.assertEqual(dialog.step_label.text(), '3 / 3')
            self.assertNotIn('浏览器：', dialog.finish_summary.text())
            self.assertNotIn('账号并发：', dialog.finish_summary.text())
            for attribute in (
                'browser_combo',
                'options_edit',
                'driver_edit',
                'binary_edit',
                'headless_check',
                'notify_check',
                'handoff_check',
                'login_timeout_spin',
                'mode_combo',
                'parallel_spin',
                'delay_spin',
                'choose_correctly_check',
                'report_id_edit',
            ):
                self.assertFalse(hasattr(dialog, attribute), attribute)
        finally:
            dialog.close()

    def test_missing_hidden_settings_use_normalize_config_defaults(self):
        account = AccountProfile('a-1', '甲', 'user-a', 'pass-a')
        dialog = OobeDialog({
            'accounts': [account.to_dict()],
            'default_account_id': account.id,
        })
        hidden_keys = (
            'browser',
            'options',
            'driver_path',
            'browser_binary',
            'foreground_browser',
            'foreground_on_manual',
            'manual_handoff_enabled',
            'system_notifications',
            'login_wait_timeout',
            'mode',
            'parallelism',
            'delay_multiplier',
            'choose_correctly',
            'report_id',
            'day_to_start_on',
        )
        try:
            dialog._apply_config()
            defaults = normalize_config()

            self.assertEqual(
                {key: dialog.config[key] for key in hidden_keys},
                {key: defaults[key] for key in hidden_keys},
            )
        finally:
            dialog.close()

    def test_apply_preserves_non_first_default_account(self):
        config = self.make_config()
        config['default_account_id'] = 'a-2'
        config['username'] = 'user-b'
        config['password'] = 'pass-b'
        dialog = OobeDialog(config)
        try:
            self.assertEqual(dialog.default_account_combo.currentData(), 'a-2')

            dialog._apply_config()

            self.assertEqual(dialog.config['default_account_id'], 'a-2')
            self.assertEqual(dialog.config['username'], 'user-b')
            self.assertEqual(dialog.config['password'], 'pass-b')
        finally:
            dialog.close()

    def test_default_account_selection_is_saved(self):
        dialog = OobeDialog(self.make_config())
        try:
            dialog.default_account_combo.setCurrentIndex(
                dialog.default_account_combo.findData('a-2')
            )

            dialog._apply_config()

            self.assertEqual(dialog.config['default_account_id'], 'a-2')
            self.assertEqual(dialog.config['username'], 'user-b')
        finally:
            dialog.close()

    def test_apply_migrates_legacy_urls_without_losing_existing_tasks(self):
        config = self.make_config()
        task_url = (
            'https://teacher.ewt360.com/ewtbend/bend/index/index.html#/'
            'student-task-overview?homeworkId=201'
        )
        list_url = (
            'https://teacher.ewt360.com/ewtbend/bend/index/index.html#/'
            'holiday/student-task-overview?homeworkId=202'
        )
        non_task_url = 'https://teacher.ewt360.com/ewtbend/bend/index/index.html#/student/homework'
        config['task_urls'] = [task_url, non_task_url, task_url]
        config['list_url'] = list_url
        dialog = OobeDialog(config)
        try:
            dialog._apply_config()

            self.assertEqual(dialog.config['list_url'], list_url)
            self.assertEqual(
                [task['id'] for task in dialog.config['batch_tasks']][0],
                'task-existing',
            )
            migrated = {
                task['url']: task
                for task in dialog.config['batch_tasks']
                if str(task.get('id', '')).startswith('legacy-')
            }
            self.assertEqual(set(migrated), {task_url, list_url})
            self.assertTrue(all(task['account_id'] == 'a-1' for task in migrated.values()))
            self.assertEqual(
                dialog.config['task_urls'],
                [task_url, non_task_url, self.make_config()['batch_tasks'][0]['url'], list_url],
            )
        finally:
            dialog.close()

    def test_apply_keeps_unknown_batch_task_payload(self):
        config = self.make_config()
        config['batch_tasks'].append({
            'id': 'legacy-placeholder',
            'account_id': 'removed-account',
            'title': '待修复任务',
            'url': '',
            'custom': {'keep': True},
        })
        dialog = OobeDialog(config)
        try:
            dialog._apply_config()

            preserved = next(
                task for task in dialog.config['batch_tasks']
                if task.get('id') == 'legacy-placeholder'
            )
            self.assertEqual(preserved['custom'], {'keep': True})
        finally:
            dialog.close()

    def test_imported_task_binds_to_existing_account_id(self):
        dialog = OobeDialog(self.make_config())
        try:
            dialog._merge_import_rows([
                ImportedWorkspaceRow(
                    account=AccountProfile(
                        'unstable-import-id',
                        '乙更新',
                        'USER-B',
                        'new-pass',
                    ),
                    task_title='导入任务',
                    task_url=(
                        'https://teacher.ewt360.com/ewtbend/bend/index/index.html#/'
                        'student-task-overview?homeworkId=200'
                    ),
                ),
            ])
            dialog._apply_config()

            imported = next(
                item for item in dialog.config['batch_tasks']
                if item['title'] == '导入任务'
            )
            self.assertEqual(imported['account_id'], 'a-2')
            self.assertEqual(len(dialog.config['accounts']), 2)
            updated = next(
                item for item in dialog.config['accounts'] if item['id'] == 'a-2'
            )
            self.assertEqual(updated['password'], 'new-pass')
        finally:
            dialog.close()

    def test_blank_import_cells_do_not_overwrite_oobe_account(self):
        dialog = OobeDialog(self.make_config())
        try:
            dialog._merge_import_rows([
                ImportedWorkspaceRow(
                    account=AccountProfile(
                        'unstable-import-id',
                        'USER-B',
                        'USER-B',
                        '',
                        False,
                    ),
                    provided_fields=frozenset(),
                ),
            ])

            account = next(item for item in dialog.accounts if item.id == 'a-2')
            self.assertEqual(account.name, '乙')
            self.assertEqual(account.password, 'pass-b')
            self.assertTrue(account.enabled)
        finally:
            dialog.close()

    def test_invalid_yaml_is_backed_up_and_enters_recovery_oobe(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / 'config.yml'
            config_path.write_text('accounts: [broken', encoding='utf-8')
            with patch('gui_qfluent.CONFIG_PATH', config_path):
                config = read_config_file()

            self.assertFalse(config['oobe_completed'])
            self.assertEqual(config['oobe_version'], 0)
            self.assertTrue(
                config_path.with_name('config.yml.invalid.bak').exists()
            )

    def test_oobe_write_failure_keeps_same_dialog_for_retry(self):
        dialog = unittest.mock.Mock()
        dialog.config = self.make_config()
        dialog.discover_after_accept = False
        dialog.exec.side_effect = [
            QDialog.DialogCode.Accepted,
            QDialog.DialogCode.Accepted,
        ]
        account_page = unittest.mock.Mock()
        account_page.accounts = []
        window = SimpleNamespace(
            _worker_threads_running=unittest.mock.Mock(return_value=False),
            _toast=unittest.mock.Mock(),
            account_page=account_page,
            config_page=unittest.mock.Mock(),
            task_page=unittest.mock.Mock(),
        )

        with (
            patch('gui_qfluent.read_config_file', return_value=self.make_config()),
            patch('gui_qfluent.OobeDialog', return_value=dialog) as dialog_factory,
            patch(
                'gui_qfluent.write_config_file',
                side_effect=[OSError('permission denied'), None],
            ) as writer,
        ):
            MainWindow.show_oobe(window)

        dialog_factory.assert_called_once()
        self.assertEqual(dialog.exec.call_count, 2)
        self.assertEqual(writer.call_count, 2)
        dialog._show_error.assert_called_once()
        account_page.load.assert_called_once_with(dialog.config)


if __name__ == '__main__':
    unittest.main()
