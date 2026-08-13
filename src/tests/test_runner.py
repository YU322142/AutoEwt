import logging
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch


SRC_DIR = Path(__file__).resolve().parents[1]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from runner import (  # noqa: E402
    AutoEwtRunner,
    BatchTask,
    ParallelBatchRunner,
    RestartPolicy,
    TaskContextFilter,
)
from auto_video.auto_video import LessonProcessingError  # noqa: E402
from auto_video.auto_video import MissedCheckpointReplayError  # noqa: E402
from manual_intervention import (  # noqa: E402
    HumanVerificationRequiredError,
    ManualInterventionRequest,
    is_headless_options,
)
from account_store import AccountProfile  # noqa: E402


class FakeRunner:
    active = 0
    maximum_active = 0
    expected_active = 0
    lock = threading.Lock()
    all_started = threading.Event()

    def __init__(self, *, stop_event, config, task_label, **kwargs):
        self.stop_event = stop_event
        self.config = config
        self.task_label = task_label
        self.preview_requested = False

    def run(self):
        with self.lock:
            type(self).active += 1
            type(self).maximum_active = max(
                type(self).maximum_active,
                type(self).active,
            )
            if type(self).active >= type(self).expected_active:
                type(self).all_started.set()
        try:
            type(self).all_started.wait(0.5)
            return 130 if self.stop_event.is_set() else 0
        finally:
            with self.lock:
                type(self).active -= 1

    def stop(self):
        self.stop_event.set()

    def request_visible_handoff(self):
        self.preview_requested = True
        return True


class AccountConcurrencyRunner(FakeRunner):
    active_by_account = {}
    maximum_by_account = {}
    active_accounts = set()
    maximum_distinct_accounts = 0
    start_order = []

    @classmethod
    def reset(cls):
        cls.active_by_account = {}
        cls.maximum_by_account = {}
        cls.active_accounts = set()
        cls.maximum_distinct_accounts = 0
        cls.start_order = []

    def __init__(self, *, account_id='', account_name='', **kwargs):
        super().__init__(**kwargs)
        self.account_id = account_id or '__default_account__'
        self.account_name = account_name

    def run(self):
        with self.lock:
            cls = type(self)
            active = cls.active_by_account.get(self.account_id, 0) + 1
            cls.active_by_account[self.account_id] = active
            cls.maximum_by_account[self.account_id] = max(
                cls.maximum_by_account.get(self.account_id, 0),
                active,
            )
            cls.active_accounts.add(self.account_id)
            cls.maximum_distinct_accounts = max(
                cls.maximum_distinct_accounts,
                len(cls.active_accounts),
            )
            cls.start_order.append(self.task_label)
        try:
            time.sleep(0.05)
            return 0
        finally:
            with self.lock:
                cls.active_by_account[self.account_id] -= 1
                if not cls.active_by_account[self.account_id]:
                    cls.active_accounts.discard(self.account_id)


class ParallelBatchRunnerTests(unittest.TestCase):
    def setUp(self):
        FakeRunner.active = 0
        FakeRunner.maximum_active = 0
        FakeRunner.expected_active = 0
        FakeRunner.all_started.clear()
        AccountConcurrencyRunner.reset()
        self.tasks = [
            BatchTask(str(index), f'任务{index}', f'https://example.test/{index}')
            for index in range(4)
        ]

    def test_unbound_tasks_share_one_default_account_browser(self):
        FakeRunner.expected_active = 1
        runner = ParallelBatchRunner(
            self.tasks,
            {'list_url': 'https://example.test'},
            parallelism=999,
            runner_factory=FakeRunner,
        )

        results = runner.run()

        self.assertEqual([result.code for result in results], [0, 0, 0, 0])
        self.assertEqual(FakeRunner.maximum_active, 1)

    def test_each_task_gets_an_independent_url(self):
        seen = {}

        class RecordingRunner(FakeRunner):
            def run(self):
                seen[self.task_label] = self.config['list_url']
                return 0

        runner = ParallelBatchRunner(
            self.tasks,
            {'list_url': 'https://example.test/base'},
            parallelism=2,
            runner_factory=RecordingRunner,
        )
        runner.run()

        self.assertEqual(
            seen,
            {f'默认账号｜{task.title}': task.url for task in self.tasks},
        )

    def test_each_task_uses_its_bound_account_without_mutating_base_config(self):
        seen = {}
        tasks = [
            BatchTask('one', '任务一', 'https://example.test/1', 'account-a'),
            BatchTask('two', '任务二', 'https://example.test/2', 'account-b'),
        ]

        class RecordingRunner(FakeRunner):
            def run(self):
                seen[self.task_label] = (
                    self.config['username'],
                    self.config['password'],
                )
                return 0

        base = {'username': 'base', 'password': 'base-secret'}
        runner = ParallelBatchRunner(
            tasks,
            base,
            parallelism=2,
            account_configs={
                'account-a': AccountProfile('account-a', '甲', 'alice', 'a-secret'),
                'account-b': AccountProfile('account-b', '乙', 'bob', 'b-secret'),
            },
            runner_factory=RecordingRunner,
        )

        results = runner.run()

        self.assertEqual([result.code for result in results], [0, 0])
        self.assertEqual(seen['甲｜任务一'], ('alice', 'a-secret'))
        self.assertEqual(seen['乙｜任务二'], ('bob', 'b-secret'))
        self.assertEqual(base['username'], 'base')

    def test_same_account_is_serial_while_different_accounts_parallel(self):
        tasks = [
            BatchTask('a1', 'A-1', 'https://example.test/a1', 'account-a'),
            BatchTask('a2', 'A-2', 'https://example.test/a2', 'account-a'),
            BatchTask('b1', 'B-1', 'https://example.test/b1', 'account-b'),
            BatchTask('b2', 'B-2', 'https://example.test/b2', 'account-b'),
        ]
        runner = ParallelBatchRunner(
            tasks,
            {'list_url': 'https://example.test/base'},
            parallelism=8,
            account_configs={
                'account-a': AccountProfile('account-a', '甲', 'alice', 'a-secret'),
                'account-b': AccountProfile('account-b', '乙', 'bob', 'b-secret'),
            },
            runner_factory=AccountConcurrencyRunner,
        )

        results = runner.run()

        self.assertEqual([result.code for result in results], [0, 0, 0, 0])
        self.assertEqual(AccountConcurrencyRunner.maximum_by_account['account-a'], 1)
        self.assertEqual(AccountConcurrencyRunner.maximum_by_account['account-b'], 1)
        self.assertEqual(AccountConcurrencyRunner.maximum_distinct_accounts, 2)
        self.assertLess(
            AccountConcurrencyRunner.start_order.index('甲｜A-1'),
            AccountConcurrencyRunner.start_order.index('甲｜A-2'),
        )

    def test_results_preserve_original_cross_account_task_order(self):
        tasks = [
            BatchTask('a1', 'A-1', 'https://example.test/a1', 'account-a'),
            BatchTask('b1', 'B-1', 'https://example.test/b1', 'account-b'),
            BatchTask('a2', 'A-2', 'https://example.test/a2', 'account-a'),
            BatchTask('b2', 'B-2', 'https://example.test/b2', 'account-b'),
        ]

        class StaggeredRunner(FakeRunner):
            def run(self):
                if self.task_label.endswith('A-1'):
                    time.sleep(0.04)
                return 0

        runner = ParallelBatchRunner(
            tasks,
            {'list_url': 'https://example.test/base'},
            parallelism=2,
            account_configs={
                'account-a': AccountProfile('account-a', 'Account A', 'alice', 'a-secret'),
                'account-b': AccountProfile('account-b', 'Account B', 'bob', 'b-secret'),
            },
            runner_factory=StaggeredRunner,
        )

        results = runner.run()

        self.assertEqual([result.task.id for result in results], [
            'a1', 'b1', 'a2', 'b2',
        ])

    def test_runner_emits_stable_account_and_task_log_context(self):
        records = []

        class RecordingHandler(logging.Handler):
            def emit(self, record):
                records.append(record)

        handler = RecordingHandler()
        handler.addFilter(TaskContextFilter())
        logger = logging.getLogger('autoewt-context-test')
        previous_handlers = list(logger.handlers)
        previous_propagate = logger.propagate
        previous_level = logger.level
        logger.handlers = [handler]
        logger.propagate = False
        logger.setLevel(logging.INFO)

        class LoggingRunner(AutoEwtRunner):
            def _run_loop(self):
                logger.info('context')
                return 0

        task = BatchTask(
            'task-1',
            'Task One',
            'https://example.test/1',
            'account-a',
        )
        runner = ParallelBatchRunner(
            [task],
            {'list_url': 'https://example.test/base'},
            parallelism=1,
            account_configs={
                'account-a': AccountProfile(
                    'account-a',
                    'Account A',
                    'alice',
                    'secret',
                ),
            },
            runner_factory=LoggingRunner,
        )
        try:
            runner.run()
        finally:
            logger.handlers = previous_handlers
            logger.propagate = previous_propagate
            logger.setLevel(previous_level)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].task_id, 'task-1')
        self.assertEqual(records[0].account_id, 'account-a')
        self.assertEqual(records[0].account_name, 'Account A')
        self.assertEqual(records[0].task_label, 'Account A｜Task One')
        self.assertTrue(records[0].thread_name.startswith('autoewt-account'))

    def test_queued_preview_request_is_applied_when_task_starts(self):
        task = self.tasks[0]
        seen = []

        class PreviewRunner(FakeRunner):
            def run(self):
                seen.append(self.preview_requested)
                return 0

        runner = ParallelBatchRunner(
            [task],
            {'list_url': 'https://example.test/base'},
            parallelism=1,
            runner_factory=PreviewRunner,
        )

        self.assertTrue(runner.promote_task(task.id))
        self.assertEqual(runner.run()[0].code, 0)
        self.assertEqual(seen, [True])


class WorkerRestartTests(unittest.TestCase):
    def test_background_manual_session_never_rebuilds_on_verification(self):
        configs = []
        events = []
        notifications = []

        class Driver:
            def __init__(self):
                self.quit_calls = 0

            def quit(self):
                self.quit_calls += 1

        first_driver = Driver()

        class BackgroundAuto:
            driver = first_driver

            def run(self):
                return None

        def build(mode, **kwargs):
            configs.append(dict(kwargs['config']))
            return BackgroundAuto()

        runner = AutoEwtRunner(
            config={
                'mode': 'video',
                'options': '--mute-audio --headless=new --disable-gpu',
                'manual_handoff_enabled': True,
                'system_notifications': True,
            },
            restart_policy=RestartPolicy(0, 0, 0),
            task_label='暑假任务',
            manual_intervention_sink=events.append,
            notification_sender=lambda title, message: notifications.append(
                (title, message)
            ) or True,
        )

        with patch('runner.build_auto', side_effect=build):
            self.assertEqual(runner.run(), 0)

        self.assertEqual(len(configs), 1)
        self.assertFalse(is_headless_options(configs[0]['options']))
        self.assertTrue(configs[0]['_logical_headless'])
        self.assertTrue(configs[0]['_background_manual_session'])
        self.assertNotIn('disable-gpu', configs[0]['options'])
        self.assertEqual(first_driver.quit_calls, 1)
        self.assertEqual(notifications, [])
        self.assertEqual(events, [])

    def test_notification_failure_does_not_abort_manual_event(self):
        events = []
        runner = AutoEwtRunner(
            config={'mode': 'video', 'options': '--headless'},
            manual_intervention_sink=events.append,
            notification_sender=lambda *_: (_ for _ in ()).throw(RuntimeError('toast failed')),
        )

        runner._publish_manual_request(ManualInterventionRequest(
            kind='login_verification',
            reason='验证码',
            headless=True,
        ))

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].kind, 'login_verification')

    def test_logical_headless_manual_event_holds_shared_window_slot(self):
        manual_lock = threading.Lock()
        events = []
        runner = AutoEwtRunner(
            config={
                'mode': 'video',
                'options': '--headless=new',
                'system_notifications': False,
            },
            manual_intervention_sink=events.append,
            manual_session_lock=manual_lock,
        )

        runner._publish_manual_request(ManualInterventionRequest(
            kind='attention_checkpoint',
            reason='认真度验证',
            headless=True,
        ))

        self.assertTrue(manual_lock.locked())
        self.assertTrue(runner._manual_session_lock_held)
        self.assertEqual(events[-1].phase, 'required')

        runner._publish_manual_request(ManualInterventionRequest(
            kind='attention_checkpoint',
            reason='认真度验证已完成',
            phase='resolved',
            headless=True,
        ))

        self.assertFalse(manual_lock.locked())
        self.assertFalse(runner._manual_session_lock_held)

    def test_preview_request_is_deferred_to_the_selenium_owner_thread(self):
        class Driver:
            def __init__(self):
                self.quit_calls = 0

            def quit(self):
                self.quit_calls += 1

        driver = Driver()
        auto = type('HeadlessAuto', (), {'driver': driver, 'is_headless': True})()
        runner = AutoEwtRunner(config={'mode': 'video', 'options': '--headless'})
        runner._set_active(auto)

        self.assertTrue(runner.request_visible_handoff())

        self.assertEqual(driver.quit_calls, 0)
        self.assertTrue(runner._visible_handoff_requested.is_set())
        self.assertTrue(runner._persistent_preview_requested.is_set())

    def test_lesson_processing_failure_restarts_the_whole_worker(self):
        runner = AutoEwtRunner(
            config={'mode': 'video'},
            restart_policy=RestartPolicy(
                initial_interval=0,
                max_interval=0,
                max_retries=1,
            ),
        )
        attempts = 0

        class FailingAuto:
            driver = None

            def run(self):
                nonlocal attempts
                attempts += 1
                raise LessonProcessingError('课程仍未完成')

        with patch('runner.build_auto', return_value=FailingAuto()):
            self.assertEqual(runner.run(), 1)

        self.assertEqual(attempts, 2)

    def test_persistent_missed_checkpoint_replay_failure_restarts_with_limit(self):
        runner = AutoEwtRunner(
            config={'mode': 'video'},
            restart_policy=RestartPolicy(
                initial_interval=0,
                max_interval=0,
                max_retries=3,
            ),
        )
        attempts = 0

        class FailingAuto:
            driver = None

            def run(self):
                nonlocal attempts
                attempts += 1
                raise MissedCheckpointReplayError('no reliable replay entry')

        with patch('runner.build_auto', return_value=FailingAuto()):
            self.assertEqual(runner.run(), 1)

        self.assertEqual(attempts, 4)


if __name__ == '__main__':
    unittest.main()
