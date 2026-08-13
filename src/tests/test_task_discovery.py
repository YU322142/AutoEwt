import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SRC_DIR = Path(__file__).resolve().parents[1]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from task_discovery import (  # noqa: E402
    QUIZ_TASK_RE,
    TaskDiscoverySession,
    classify_task_status,
    is_task_detail_url,
    task_field,
    task_title,
)


class FakeCard:
    def __init__(self, text, class_name='', children=None):
        self.text = text
        self.class_name = class_name
        self.children = list(children or [])

    def is_displayed(self):
        return True

    def get_attribute(self, name):
        if name == 'innerText':
            return self.text
        if name == 'class':
            return self.class_name
        return ''

    def find_elements(self, by, value):
        descendants = []
        for child in self.children:
            descendants.append(child)
            descendants.extend(child.find_elements(by, value))
        return descendants


class FakeDriver:
    def __init__(self, cards):
        self.cards = cards

    def find_elements(self, by, value):
        return list(self.cards)


class TaskDiscoveryTextTests(unittest.TestCase):
    def test_holiday_task_detail_url_is_supported(self):
        url = (
            'https://teacher.ewt360.com/ewtbend/bend/index/index.html'
            '#/holiday/student-task-overview?homeworkId=12345678'
        )
        self.assertTrue(is_task_detail_url(url))

    def test_holiday_home_route_is_distinct_from_regular_homework(self):
        from task_discovery import HOLIDAY_DISCOVERY_URL, HOMEWORK_DISCOVERY_URL

        self.assertIn('#/holiday/student/home', HOLIDAY_DISCOVERY_URL)
        self.assertIn('#/student/homework', HOMEWORK_DISCOVERY_URL)

    def test_summer_task_is_not_quiz_just_because_card_lists_a_quiz(self):
        text = (
            '2026暑假学习任务 布置人：老师 开始时间：2026-07-01 '
            '截止时间：2026-08-31 视频课任务 试卷1份 查看详情 进行中'
        )
        title = task_title(text)
        self.assertEqual(title, '2026暑假学习任务')
        self.assertIsNone(QUIZ_TASK_RE.search(title))

    def test_quiz_only_task_title_is_filtered(self):
        title = task_title('暑假数学测验 布置人：老师 查看详情')
        self.assertIsNotNone(QUIZ_TASK_RE.search(title))

    def test_status_and_metadata_are_extracted(self):
        text = (
            '暑假任务 布置人：李老师 开始时间：2026-07-01 '
            '截止时间：2026-08-31 去学习 已截止'
        )
        self.assertEqual(classify_task_status(text), '已截止（未完成）')
        self.assertEqual(
            task_field(text, r'布置人[:：]\s*(.+?)(?:开始时间[:：]|$)'),
            '李老师',
        )

    def test_deadline_does_not_include_list_footer(self):
        text = '截止时间：2026年8月22日 23:59 没有更多内容了～'
        self.assertEqual(
            task_field(text, r'截止时间[:：]\s*(.+?)(?:查看详情|去完成|继续完成|去学习|$)'),
            '2026年8月22日 23:59',
        )

    def test_task_with_attached_list_footer_is_still_detected(self):
        card = FakeCard(
            '暑假物理专题 42分钟 进行中 30% 去学习 没有更多内容了～',
            class_name='holiday-course-card',
        )
        session = object.__new__(TaskDiscoverySession)
        session.driver = FakeDriver([card])

        with patch.object(session, '_task_url_from_card', return_value=''):
            cards = session._find_task_cards(include_completed=False)

        self.assertEqual(cards, [card])
        self.assertEqual(task_title(card.text), '暑假物理专题 42分钟 30%')

    def test_page_wrapper_is_not_misidentified_as_task_card(self):
        task = FakeCard(
            '王梓恺的任务 布置人：王老师 开始时间：2026年8月3日 00:00 '
            '截止时间：2026年8月22日 23:59 去学习',
            class_name='task-row',
        )
        wrapper = FakeCard(
            '学生端 首页 我的错题本 我的任务 我的班级 我的测评 '
            f'{task.text} 没有更多内容了～',
            class_name='homework-page',
            children=[task],
        )
        session = object.__new__(TaskDiscoverySession)
        session.driver = FakeDriver([wrapper, task])

        with patch.object(session, '_task_url_from_card', return_value=''):
            cards = session._find_task_cards(include_completed=False)

        self.assertEqual(cards, [task])

    def test_innermost_task_container_is_preferred(self):
        task = FakeCard(
            '暑假物理专题 42分钟 进行中 30% 去学习',
            class_name='holiday-course-card',
        )
        container = FakeCard(
            task.text,
            class_name='holiday-task-list-item',
            children=[task],
        )
        session = object.__new__(TaskDiscoverySession)
        session.driver = FakeDriver([container, task])

        with patch.object(session, '_task_url_from_card', return_value=''):
            cards = session._find_task_cards(include_completed=False)

        self.assertEqual(cards, [task])

    def test_holiday_card_without_teacher_or_deadline_is_detected(self):
        card = FakeCard(
            '暑假物理专题 42分钟 进行中 30% 去学习',
            class_name='holiday-course-card',
        )
        session = object.__new__(TaskDiscoverySession)
        session.driver = FakeDriver([card])

        with patch.object(session, '_task_url_from_card', return_value=''):
            cards = session._find_task_cards(include_completed=False)

        self.assertEqual(cards, [card])

    def test_submitted_holiday_card_is_not_returned_as_unfinished(self):
        card = FakeCard(
            '暑假物理专题 已提交 去学习',
            class_name='holiday-course-card',
        )
        session = object.__new__(TaskDiscoverySession)
        session.driver = FakeDriver([card])

        with patch.object(session, '_task_url_from_card', return_value=''):
            cards = session._find_task_cards(include_completed=False)

        self.assertEqual(cards, [])


if __name__ == '__main__':
    unittest.main()
