import os
import sys
import threading
import time
import unittest
from pathlib import Path
from types import MethodType


os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

SRC_DIR = Path(__file__).resolve().parents[1]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from account_store import AccountProfile  # noqa: E402
from gui_qfluent import DiscoveryWorker, WorkspaceTask  # noqa: E402


class DiscoveryWorkerTests(unittest.TestCase):
    def test_account_discovery_uses_configured_parallelism_and_stable_order(self):
        accounts = [
            AccountProfile(f'account-{index}', f'账户 {index}', f'user-{index}', 'pass')
            for index in range(4)
        ]
        worker = DiscoveryWorker({}, accounts, parallelism=2)
        active = 0
        maximum_active = 0
        lock = threading.Lock()
        completed = []
        failed = []

        def discover_account(_worker, account):
            nonlocal active, maximum_active
            with lock:
                active += 1
                maximum_active = max(maximum_active, active)
            try:
                # Finish later accounts first to prove that the GUI result order
                # follows the selected account order rather than thread timing.
                time.sleep(0.02 * (5 - int(account.id.rsplit('-', 1)[1])))
                return [WorkspaceTask(
                    id=f'{account.id}:task',
                    account_id=account.id,
                    account_name=account.name,
                    title=f'{account.name} 的任务',
                    url=f'https://example.test/{account.id}',
                )]
            finally:
                with lock:
                    active -= 1

        worker._discover_account = MethodType(discover_account, worker)
        worker.completed.connect(completed.append)
        worker.failed.connect(failed.append)

        worker.run()

        self.assertFalse(failed)
        self.assertEqual(maximum_active, 2)
        self.assertEqual(
            [task.account_id for task in completed[0]],
            [account.id for account in accounts],
        )

    def test_discovery_parallelism_one_stays_serial(self):
        accounts = [
            AccountProfile(f'account-{index}', f'账户 {index}', f'user-{index}', 'pass')
            for index in range(3)
        ]
        worker = DiscoveryWorker({}, accounts, parallelism=1)
        active = 0
        maximum_active = 0
        lock = threading.Lock()

        def discover_account(_worker, account):
            nonlocal active, maximum_active
            with lock:
                active += 1
                maximum_active = max(maximum_active, active)
            try:
                time.sleep(0.01)
                return []
            finally:
                with lock:
                    active -= 1

        worker._discover_account = MethodType(discover_account, worker)
        worker.run()

        self.assertEqual(maximum_active, 1)


if __name__ == '__main__':
    unittest.main()
