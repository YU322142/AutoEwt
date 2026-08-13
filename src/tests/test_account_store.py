import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SRC_DIR = Path(__file__).resolve().parents[1]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from account_store import (  # noqa: E402
    AccountProfile,
    apply_account,
    export_import_template,
    import_workspace_rows,
    merge_accounts,
    merge_imported_accounts,
    normalize_accounts,
)


class AccountStoreTests(unittest.TestCase):
    def test_legacy_credentials_become_default_account(self):
        accounts = normalize_accounts({'username': 'student', 'password': 123456})

        self.assertEqual(len(accounts), 1)
        self.assertEqual(accounts[0].username, 'student')
        self.assertEqual(accounts[0].password, '123456')

    def test_default_config_placeholders_are_not_imported_as_an_account(self):
        accounts = normalize_accounts({'username': '用户名', 'password': '密码'})

        self.assertEqual(accounts, [])

    def test_applying_account_does_not_mutate_base_config(self):
        base = {'username': 'old', 'options': '--headless'}
        account = AccountProfile('a-1', '甲', 'new', 'secret')

        result = apply_account(base, account)

        self.assertEqual(base['username'], 'old')
        self.assertEqual(result['username'], 'new')
        self.assertEqual(result['account_id'], 'a-1')

    def test_merge_updates_duplicate_username_without_losing_stable_id(self):
        existing = [AccountProfile('stable', '旧名称', 'student', 'old')]
        incoming = [AccountProfile('new-id', '新名称', 'STUDENT', 'new')]

        result = merge_accounts(existing, incoming)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].id, 'stable')
        self.assertEqual(result[0].name, '新名称')
        self.assertEqual(result[0].password, 'new')

    def test_csv_import_supports_account_and_bound_task(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'accounts.csv'
            path.write_text(
                '账户名称,用户名,密码,启用,任务名称,任务URL\n'
                '主账号,student,password,是,暑假任务,'
                'https://teacher.ewt360.com/ewtbend/bend/index/index.html#/'
                'holiday/student-task-overview?homeworkId=123\n',
                encoding='utf-8-sig',
            )

            rows = import_workspace_rows(path)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].account.name, '主账号')
        self.assertEqual(rows[0].task_title, '暑假任务')
        self.assertIn('homeworkId=123', rows[0].task_url)

    def test_csv_template_is_importable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'template.csv'
            export_import_template(path)
            rows = import_workspace_rows(path)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].account.username, 'student001')

    def test_repeated_account_rows_preserve_nonblank_profile_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'accounts.csv'
            path.write_text(
                '账户名称,用户名,密码,启用,任务名称,任务URL\n'
                '主账号,student,password,否,任务一,https://example.test/1\n'
                ',student,,,任务二,https://example.test/2\n',
                encoding='utf-8-sig',
            )

            rows = import_workspace_rows(path)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].account.id, rows[1].account.id)
        self.assertEqual(rows[1].account.name, '主账号')
        self.assertEqual(rows[1].account.password, 'password')
        self.assertFalse(rows[1].account.enabled)

    def test_blank_import_cells_do_not_overwrite_existing_profile(self):
        existing = [
            AccountProfile('stable', '原名称', 'student', 'old-password', False),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'accounts.csv'
            path.write_text(
                '账户名称,用户名,密码,启用\n'
                ',student,,\n',
                encoding='utf-8-sig',
            )
            rows = import_workspace_rows(path)

        self.assertEqual(rows[0].provided_fields, frozenset())
        self.assertEqual(merge_imported_accounts(existing, rows), existing)

    def test_nonblank_import_cells_explicitly_update_existing_profile(self):
        existing = [
            AccountProfile('stable', '原名称', 'student', 'old-password', True),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'accounts.csv'
            path.write_text(
                '账户名称,用户名,密码,启用\n'
                '新名称,STUDENT,new-password,否\n',
                encoding='utf-8-sig',
            )
            rows = import_workspace_rows(path)

        self.assertEqual(
            rows[0].provided_fields,
            frozenset({'name', 'password', 'enabled'}),
        )
        self.assertEqual(
            merge_imported_accounts(existing, rows),
            [AccountProfile('stable', '新名称', 'student', 'new-password', False)],
        )

    def test_boolean_false_cell_is_an_explicit_enabled_update(self):
        raw_rows = [{'用户名': 'student', '启用': False}]
        with patch('account_store._read_xlsx', return_value=raw_rows):
            rows = import_workspace_rows('accounts.xlsx')

        self.assertIn('enabled', rows[0].provided_fields)
        self.assertFalse(rows[0].account.enabled)


if __name__ == '__main__':
    unittest.main()
