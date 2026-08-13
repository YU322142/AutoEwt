import sys
import unittest
import gc
from pathlib import Path
from unittest.mock import patch


SRC_DIR = Path(__file__).resolve().parents[1]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from progress import (  # noqa: E402
    BatchProgressModel,
    ProgressState,
    ProgressTqdm,
    reset_progress_sink,
    set_progress_sink,
)


class BatchProgressModelTests(unittest.TestCase):
    def test_same_scope_is_replaced_instead_of_double_counted(self):
        model = BatchProgressModel(['task-1'])

        model.update(
            'task-1',
            ProgressState('课程总数', 0, 3, '项', kind='courses', scope='day:1'),
        )
        model.update(
            'task-1',
            ProgressState('课程总数', 2, 3, '项', kind='courses', scope='day:1'),
        )

        summary = model.aggregate('courses')
        self.assertEqual((summary.current, summary.total), (2, 3))
        self.assertEqual(summary.scope_count, 1)

    def test_courses_sum_across_days_and_tasks(self):
        model = BatchProgressModel(['task-1', 'task-2'])
        model.update(
            'task-1',
            ProgressState('课程总数', 2, 3, '项', kind='courses', scope='day:1'),
        )
        model.update(
            'task-1',
            ProgressState(
                '课程总数', 1, 1, '项', True, kind='courses', scope='day:2'
            ),
        )
        model.update(
            'task-2',
            ProgressState('课程总数', 4, 5, '项', kind='courses', scope='day:1'),
        )

        summary = model.aggregate('courses')
        self.assertEqual((summary.current, summary.total), (7, 9))
        self.assertEqual(summary.known_tasks, 2)
        self.assertEqual(summary.expected_tasks, 2)
        self.assertEqual(summary.scope_count, 3)

    def test_unknown_tasks_are_not_faked_into_the_denominator(self):
        model = BatchProgressModel(['known', 'queued'])
        model.update(
            'known',
            ProgressState('日期遍历', 1, 4, '天', kind='days', scope='task'),
        )

        summary = model.aggregate('days')
        self.assertEqual((summary.current, summary.total), (1, 4))
        self.assertEqual(summary.known_tasks, 1)
        self.assertEqual(summary.expected_tasks, 2)

    def test_latest_current_course_tracks_activity_instead_of_summing_video_time(self):
        model = BatchProgressModel(['task-1', 'task-2'])
        model.update(
            'task-1',
            ProgressState('视频播放进度', 40, 100, '秒', kind='course'),
        )
        model.update(
            'task-2',
            ProgressState(
                '视频播放进度', 5, 20, '秒', kind='current_course', scope='active'
            ),
        )

        task_id, state = model.latest_current_course()
        self.assertEqual(task_id, 'task-2')
        self.assertEqual((state.current, state.total), (5, 20))
        self.assertEqual(state.kind, 'current_course')

    def test_submodel_isolates_one_accounts_tasks(self):
        model = BatchProgressModel(['a-1', 'a-2', 'b-1'])
        model.update(
            'a-1',
            ProgressState('日期遍历', 1, 2, '天', kind='days', scope='task'),
        )
        model.update(
            'b-1',
            ProgressState('日期遍历', 3, 4, '天', kind='days', scope='task'),
        )

        account_a = model.submodel(['a-1', 'a-2'])
        summary = account_a.aggregate('days')

        self.assertEqual((summary.current, summary.total), (1, 2))
        self.assertEqual((summary.known_tasks, summary.expected_tasks), (1, 2))

    def test_finished_task_requires_all_known_scopes_to_finish(self):
        model = BatchProgressModel(['task-1'])
        model.update(
            'task-1',
            ProgressState('课程总数', 1, 1, '项', True, 'courses', 'day:1'),
        )
        model.update(
            'task-1',
            ProgressState('课程总数', 0, 2, '项', False, 'courses', 'day:2'),
        )
        self.assertEqual(model.aggregate('courses').finished_tasks, 0)

        model.update(
            'task-1',
            ProgressState('课程总数', 2, 2, '项', True, 'courses', 'day:2'),
        )
        self.assertEqual(model.aggregate('courses').finished_tasks, 1)


class ProgressTqdmTests(unittest.TestCase):
    def test_close_emits_finished_state_only_once(self):
        events = []
        token = set_progress_sink(events.append)
        try:
            with patch('progress.tqdm.close', return_value=None):
                progress = ProgressTqdm(total=10, disable=True, title='视频')
                progress.close()
                progress.close()
        finally:
            reset_progress_sink(token)

        finished = [event for event in events if event.finished]
        self.assertEqual(len(finished), 1)
        self.assertEqual(finished[0].title, '视频')

    def test_context_exit_and_gc_do_not_emit_a_second_finished_state(self):
        events = []
        token = set_progress_sink(events.append)
        try:
            with patch('progress.tqdm.close', return_value=None):
                with ProgressTqdm(total=10, disable=True, title='视频') as progress:
                    progress.n = 4
                del progress
                gc.collect()
        finally:
            reset_progress_sink(token)

        finished = [event for event in events if event.finished]
        self.assertEqual(len(finished), 1)
        self.assertEqual((finished[0].current, finished[0].total), (4, 10))


if __name__ == '__main__':
    unittest.main()
