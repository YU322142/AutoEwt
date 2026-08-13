import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SRC_DIR = Path(__file__).resolve().parents[1]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from selenium.common import (  # noqa: E402
    InvalidSelectorException,
    NoSuchElementException,
    StaleElementReferenceException,
)

from auto_video.auto_video import (  # noqa: E402
    DAY_LESSON_OPERATION_LIMIT,
    LESSON_FAILURE_LIMIT,
    MISSED_CHECKPOINT_REPLAY_LIMIT,
    MISSED_CHECKPOINT_STALE_RETRIES,
    AutoVideo,
    CheckpointMissedError,
    CheckpointInteractionRequiredError,
    HumanVerificationRequiredError,
    LessonProcessingError,
    MissedCheckpointCandidate,
    MissedCheckpointReplayError,
    has_missed_checkpoint_warning,
    has_missed_checkpoint_dialog,
    has_playback_block_warning,
    requires_checkpoint_replay,
)
from progress import emit_progress, set_progress_sink, reset_progress_sink  # noqa: E402


class FakeReplayButton:
    pass


class FakeElement:
    def __init__(
        self,
        text='',
        *,
        tag_name='div',
        class_name='',
        role='',
        displayed=True,
        x=0,
        y=0,
        width=100,
        height=30,
        attributes=None,
    ):
        self.own_text = text
        self.tag_name = tag_name
        self.class_name = class_name
        self.role = role
        self.displayed = displayed
        self.location = {'x': x, 'y': y}
        self.rect = {'x': x, 'y': y, 'width': width, 'height': height}
        self.attributes = dict(attributes or {})
        self.parent = None
        self.children = []

    def add(self, *children):
        for child in children:
            child.parent = self
            self.children.append(child)
        return self

    def is_displayed(self):
        return self.displayed

    def click(self):
        return None

    def get_attribute(self, name):
        if name in {'innerText', 'textContent'}:
            return ' '.join(filter(None, [
                self.own_text,
                *(child.get_attribute(name) for child in self.children),
            ]))
        if name == 'class':
            return self.class_name
        if name == 'role':
            return self.role
        return self.attributes.get(name, '')

    def find_element(self, by, value):
        if value == '..' and self.parent is not None:
            return self.parent
        raise NoSuchElementException(value)

    def find_elements(self, by, value):
        descendants = []
        for child in self.children:
            descendants.append(child)
            descendants.extend(child.find_elements(by, value))
        if '已学完' in value:
            return [
                element for element in descendants
                if '已学完' in element.get_attribute('innerText')
            ]
        return []


class FakeDriver:
    def __init__(self, warnings, *, attention=False, paused=True, overlay=False):
        self.warnings = warnings
        self.attention = attention
        self.paused = paused
        self.overlay = overlay
        self.script_calls = []

    def execute_script(self, script, *args):
        self.script_calls.append(script)
        if 'Boolean(arguments[0] && arguments[0].paused)' in script:
            return self.paused
        if "spc_video_earnest_check_box" in script and 'semantic' in script:
            return self.attention
        if "[role='dialog']" in script:
            return self.overlay
        return list(self.warnings)

    def find_elements(self, by, value):
        return list(self.warnings)


class CheckpointHarness(AutoVideo):
    def __init__(self, elements):
        self.elements = elements
        self.clicked = []
        self.paused = False
        self._attention_checkpoint_started_at = None
        self._attention_checkpoint_logged = False
        self._last_checkpoint_kind = ''
        self._unknown_overlay_started_at = None
        self._last_checkpoint_action_signature = ''
        self._last_checkpoint_action_at = 0.0
        self._missed_checkpoint_seen = False
        self.config = {'delay_multiplier': 0}
        self.foreground_calls = []
        self.release_calls = 0
        self.hide_calls = 0
        self.manual_events = []
        self.driver_identity = object()

    def _element_text(self, element):
        return element.get_attribute('innerText')

    def click(self, element):
        self.clicked.append(element)

    def bring_browser_to_front(self, reason='', keep_topmost=False):
        self.foreground_calls.append((reason, keep_topmost))
        return True

    def release_browser_topmost(self):
        self.release_calls += 1

    def hide_browser_window(self):
        self.hide_calls += 1
        return True

    def emit_manual_intervention(self, kind, reason, phase='required'):
        self.manual_events.append((kind, reason, phase))

    def _actionable_replay_element(self, element, container):
        return super()._actionable_replay_element(element, container)


class CheckpointElement(FakeElement):
    def __init__(self, text, **kwargs):
        super().__init__(text, tag_name='span', **kwargs)

    def is_displayed(self):
        return self.displayed


class InvalidParentElement(FakeElement):
    def find_element(self, by, value):
        if value == '..':
            raise InvalidSelectorException('cannot traverse above document')
        return super().find_element(by, value)


class ReplayHarness(AutoVideo):
    def __init__(self, scans, *, checkpoint_passed=False):
        self.config = {'delay_multiplier': 0}
        self._scans = iter(scans)
        self.replayed = []
        self.replay_modes = []
        self.checkpoint_passed = checkpoint_passed
        self._passed_missed_checkpoint_signatures = set()
        self._passed_missed_checkpoint_labels = set()
        self._missed_replay_completed_courses = 0

    def _find_missed_checkpoint_candidates(self):
        return next(self._scans)

    def finish_a_lesson(self, button):
        self.replayed.append(button)

    def _finish_open_lesson(self, button, *, stop_after_checkpoint=False):
        self.replay_modes.append(stop_after_checkpoint)
        try:
            self.finish_a_lesson(button)
        except Exception:
            self._return_to_course_list_if_open(force=True)
            raise
        return self.checkpoint_passed


class ImmediateCheckpointLessonHarness(AutoVideo):
    def __init__(self):
        self.config = {'delay_multiplier': 0}
        self.video = object()
        self.closed = 0
        self.driver = type(
            'Driver',
            (),
            {
                'quit_calls': 0,
                'find_element': lambda _self, _by, _value: self.video,
                'quit': lambda _self: setattr(
                    _self,
                    'quit_calls',
                    _self.quit_calls + 1,
                ),
            },
        )()

    def click_and_switch(self, button):
        return None

    def _raise_if_playback_blocked(self):
        return None

    def _handle_checkpoint_state(self, video):
        return 'handled'

    def _has_unknown_visible_overlay(self):
        raise AssertionError('checkpoint-only replay should already be complete')

    def _settle_checkpoint_result(self, video):
        return 'handled'

    def close_and_switch(self):
        self.closed += 1


class LoopCheckpointLessonHarness(ImmediateCheckpointLessonHarness):
    def __init__(self):
        super().__init__()
        self.states = iter(['none', 'handled', 'none', 'none'])
        self.ended_scans = iter([False, True])
        self.driver.execute_script = self._execute_script

    def _handle_checkpoint_state(self, video):
        return next(self.states)

    def _execute_script(self, script, *args):
        if 'ended' in script:
            return next(self.ended_scans)
        return False

    def _has_unknown_visible_overlay(self):
        return False

    def _resume_if_paused(self, video, pbar):
        return None


class StaleReplayHarness(ReplayHarness):
    def __init__(self, scans):
        super().__init__(scans)
        self.return_attempts = 0

    def finish_a_lesson(self, button):
        raise StaleElementReferenceException('refreshed')

    def _return_to_course_list_if_open(self, force=False):
        self.return_attempts += 1


class DelayedMissedResultHarness(CheckpointHarness):
    def __init__(self, action, missed_ack):
        super().__init__([action])
        self.action = action
        self.missed_ack = missed_ack
        self.driver = FakeDriver([action], paused=True)
        self.result_scans = 0

    def click(self, element):
        self.clicked.append(element)
        self.action.displayed = False

    def _checkpoint_result_state(self, video):
        self.result_scans += 1
        return 'none' if self.result_scans == 1 else 'missed'


class FinishDayHarness(AutoVideo):
    def __init__(self):
        self.config = {'delay_multiplier': 0}
        self.events = []
        self.video_button = object()
        self.video_done = False

    def click(self, day):
        self.events.append('day')

    def _find_video_lesson_buttons(self):
        return [] if self.video_done else [self.video_button]

    def _find_one_click_buttons(self):
        self.events.append('find-one-click')
        return []

    def _lesson_button_identity(self, button):
        return 'video-1', '视频课'

    def _finish_missed_checkpoint_lessons(
        self,
        settle_scans=0,
        require_warning=False,
    ):
        self.events.append('scan-missed')
        return 0

    def finish_a_lesson(self, button):
        self.events.append('video')
        self.video_done = True

    def _finish_open_lesson(self, button):
        self.finish_a_lesson(button)


class FailingFinishDayHarness(FinishDayHarness):
    def __init__(self):
        super().__init__()
        self.second_button = object()
        self.second_done = False
        self.fail_calls = 0

    def _find_video_lesson_buttons(self):
        buttons = []
        if not self.video_done:
            buttons.append(self.video_button)
        if not self.second_done:
            buttons.append(self.second_button)
        return buttons

    def _lesson_button_identity(self, button):
        if button is self.video_button:
            return 'broken', '异常课程'
        return 'working', '正常课程'

    def _finish_open_lesson(self, button):
        if button is self.video_button:
            self.fail_calls += 1
            raise RuntimeError('cannot open')
        self.events.append('second-video')
        self.second_done = True


class StaleFinishDayHarness(FinishDayHarness):
    def __init__(self):
        super().__init__()
        self.stale_calls = 0

    def _finish_open_lesson(self, button):
        self.stale_calls += 1
        raise StaleElementReferenceException('refreshing')


class IdentityStaleFinishDayHarness(FinishDayHarness):
    def __init__(self):
        super().__init__()
        self.identity_calls = 0

    def _lesson_button_identity(self, button):
        self.identity_calls += 1
        raise StaleElementReferenceException('identity refresh')


class OneClickFailureHarness(FinishDayHarness):
    def __init__(self):
        super().__init__()
        self.video_done = True
        self.one_click = object()

    def _find_one_click_buttons(self):
        return [self.one_click]

    def finish_a_click(self, button):
        raise RuntimeError('cannot open one-click lesson')

    def _return_to_course_list_if_open(self, force=False):
        self.events.append(('return', force))


class InitialMissedReplayProgressHarness(FinishDayHarness):
    def __init__(self):
        super().__init__()
        self.video_done = True
        self.replay_scans = 0

    def _finish_missed_checkpoint_lessons(
        self,
        settle_scans=0,
        require_warning=False,
    ):
        self.replay_scans += 1
        if self.replay_scans == 1:
            self._missed_replay_completed_courses += 1
            return 1
        return 0


class PlaybackMissedReplayProgressHarness(FinishDayHarness):
    def _finish_open_lesson(self, button):
        raise CheckpointMissedError('missed')

    def _finish_missed_checkpoint_lessons(
        self,
        settle_scans=0,
        require_warning=False,
    ):
        if require_warning:
            self._missed_replay_completed_courses += 1
            self.video_done = True
            return 1
        return 0


class MultiplePlaybackMissedReplayProgressHarness(
    PlaybackMissedReplayProgressHarness
):
    def _finish_missed_checkpoint_lessons(
        self,
        settle_scans=0,
        require_warning=False,
    ):
        if require_warning:
            self._missed_replay_completed_courses += 2
            self.video_done = True
            return 2
        return 0


class DelayedOnlyMissedReplayProgressHarness(FinishDayHarness):
    def __init__(self):
        super().__init__()
        self.video_done = True
        self.scan_count = 0

    def _finish_missed_checkpoint_lessons(
        self,
        settle_scans=0,
        require_warning=False,
    ):
        self.scan_count += 1
        if self.scan_count == 2:
            self._missed_replay_completed_courses += 1
            return 1
        return 0


class MissedCheckpointStatusTests(unittest.TestCase):
    def test_warning_overrides_completed_action(self):
        status = '物理课 已学完 错过了所有看课检测点，再认真观看一次吧！'

        self.assertTrue(has_missed_checkpoint_warning(status))
        self.assertTrue(requires_checkpoint_replay(status, '已学完'))

    def test_warning_tolerates_layout_whitespace(self):
        self.assertTrue(has_missed_checkpoint_warning('错过了所有\n看课检测点'))

    def test_warning_accepts_site_prefix_and_check_wording(self):
        self.assertTrue(has_missed_checkpoint_warning('你错过所有看课检查点'))

    def test_completed_action_without_warning_is_not_replayed(self):
        self.assertFalse(requires_checkpoint_replay('物理课 已学完', '已学完'))

    def test_warning_with_unfinished_action_is_not_completed_replay_case(self):
        self.assertFalse(
            requires_checkpoint_replay('错过了所有看课检测点', '去学习')
        )

    def test_playback_risk_dialog_is_recognized(self):
        self.assertTrue(
            has_playback_block_warning(
                '检测到网络不稳定或开启了第三方辅助工具，学习数据无法被记录'
            )
        )

    def test_missed_checkpoint_ack_is_not_a_success_action(self):
        self.assertTrue(has_missed_checkpoint_dialog('视频已暂停：我知道了'))

    def test_missed_checkpoint_ack_is_not_part_of_success_message(self):
        self.assertFalse(has_missed_checkpoint_dialog('点击通过检查'))

    def test_missed_checkpoint_ack_does_not_get_clicked(self):
        element = CheckpointElement('我知道了')
        FakeElement('视频已暂停，错过当前检测点').add(element)
        auto = CheckpointHarness([element])
        auto.driver = FakeDriver([element])
        video = object()

        state = auto._handle_checkpoint_state(video)

        self.assertEqual(state, 'missed')
        self.assertEqual(auto.clicked, [])

    def test_unrelated_ack_is_not_treated_as_missed(self):
        element = CheckpointElement('我知道了')
        auto = CheckpointHarness([element])
        auto.driver = FakeDriver([element], paused=False)

        state = auto._handle_checkpoint_state(object())

        self.assertEqual(state, 'none')
        self.assertEqual(auto.clicked, [])

    def test_real_missed_ack_class_is_failure_while_video_is_paused(self):
        element = CheckpointElement('我知道了', class_name='btn-Ug8Kt')
        auto = CheckpointHarness([element])
        auto.driver = FakeDriver([element], paused=True)

        state = auto._handle_checkpoint_state(object())

        self.assertEqual(state, 'missed')
        self.assertEqual(auto.clicked, [])

    def test_real_slider_checkpoint_waits_without_clicking_or_playing(self):
        auto = CheckpointHarness([])
        auto.driver = FakeDriver([], attention=True, paused=True)

        state = auto._handle_checkpoint_state(object())

        self.assertEqual(state, 'manual')
        self.assertEqual(auto.clicked, [])
        self.assertFalse(any('.play()' in call for call in auto.driver.script_calls))

    def test_headless_slider_requests_visible_handoff_without_clicking(self):
        auto = CheckpointHarness([])
        auto.config['options'] = '--headless=new'
        auto.driver = FakeDriver([], attention=True, paused=True)

        with self.assertRaises(HumanVerificationRequiredError) as caught:
            auto._handle_checkpoint_state(object())

        self.assertEqual(caught.exception.kind, 'attention_checkpoint')
        self.assertEqual(auto.clicked, [])
        self.assertTrue(any('.pause()' in call for call in auto.driver.script_calls))
        self.assertFalse(any('.play()' in call for call in auto.driver.script_calls))

    def test_background_manual_slider_preserves_same_driver_and_foregrounds(self):
        auto = CheckpointHarness([])
        auto.config.update({
            'options': '--start-minimized',
            '_logical_headless': True,
            '_background_manual_session': True,
            '_auto_hide_after_manual': True,
            'foreground_on_manual': True,
        })
        auto.driver = FakeDriver([], attention=True, paused=True)

        state = auto._handle_checkpoint_state(object())

        self.assertEqual(state, 'manual')
        self.assertEqual(len(auto.foreground_calls), 1)
        self.assertEqual(auto.clicked, [])
        self.assertFalse(any('.play()' in call for call in auto.driver.script_calls))

    def test_slider_keeps_waiting_past_the_previous_timeout(self):
        auto = CheckpointHarness([])
        auto.driver = FakeDriver([], attention=True, paused=True)
        auto.config['foreground_on_manual'] = True
        auto._attention_checkpoint_started_at = 10.0

        with patch('auto_video.auto_video.time.monotonic', return_value=10_000.0):
            first = auto._handle_checkpoint_state(object())
            second = auto._handle_checkpoint_state(object())

        self.assertEqual(first, 'manual')
        self.assertEqual(second, 'manual')
        self.assertEqual(auto.clicked, [])
        self.assertEqual(len(auto.foreground_calls), 1)
        self.assertTrue(auto.foreground_calls[0][1])
        self.assertFalse(any('.play()' in call for call in auto.driver.script_calls))

    def test_slider_disappearing_resets_manual_state(self):
        auto = CheckpointHarness([])
        auto.driver = FakeDriver([], attention=True, paused=True)

        self.assertEqual(auto._handle_checkpoint_state(object()), 'manual')
        auto.driver.attention = False

        with patch.object(
            auto,
            '_settle_checkpoint_result',
            return_value='handled',
        ):
            state = auto._handle_checkpoint_state(object())

        self.assertEqual(state, 'handled')
        self.assertIsNone(auto._attention_checkpoint_started_at)
        self.assertFalse(auto._attention_checkpoint_logged)
        self.assertEqual(auto.release_calls, 1)
        self.assertEqual(auto.hide_calls, 1)
        self.assertEqual(
            auto.manual_events[-1],
            ('attention_checkpoint', '认真度拼图验证已完成', 'resolved'),
        )
        self.assertIsNotNone(auto.driver)

    def test_slider_disappearing_delayed_missed_result_wins(self):
        auto = CheckpointHarness([])
        auto.driver = FakeDriver([], attention=True, paused=True)

        self.assertEqual(auto._handle_checkpoint_state(object()), 'manual')
        auto.driver.attention = False

        with patch.object(
            auto,
            '_settle_checkpoint_result',
            return_value='missed',
        ):
            state = auto._handle_checkpoint_state(object())

        self.assertEqual(state, 'missed')
        self.assertIsNone(auto._attention_checkpoint_started_at)
        self.assertFalse(auto._attention_checkpoint_logged)
        self.assertEqual(auto.hide_calls, 1)
        self.assertEqual(auto.manual_events[-1][2], 'resolved')

    def test_slider_disappearing_pending_keeps_manual_window_active(self):
        auto = CheckpointHarness([])
        auto.driver = FakeDriver([], attention=True, paused=True)

        self.assertEqual(auto._handle_checkpoint_state(object()), 'manual')
        auto.driver.attention = False

        with patch.object(
            auto,
            '_settle_checkpoint_result',
            return_value='pending',
        ):
            state = auto._handle_checkpoint_state(object())

        self.assertEqual(state, 'pending')
        self.assertTrue(auto._attention_checkpoint_logged)
        self.assertIsNotNone(auto._attention_checkpoint_started_at)
        self.assertEqual(auto.release_calls, 0)
        self.assertEqual(auto.hide_calls, 0)
        self.assertFalse(any(event[2] == 'resolved' for event in auto.manual_events))

    def test_checkpoint_parent_scan_stops_on_invalid_document_parent(self):
        auto = CheckpointHarness([])
        element = InvalidParentElement('继续播放')

        self.assertFalse(auto._element_has_missed_context(element))
        self.assertFalse(auto._is_checkpoint_action_context(element))

    def test_checkpoint_span_clicks_actionable_ancestor(self):
        action = CheckpointElement('点击通过检查', displayed=True)
        button = FakeElement(
            tag_name='button',
            class_name='checkpoint-button',
            displayed=True,
        ).add(action)
        container = FakeElement('点击通过检查 视频检查点').add(button)
        auto = CheckpointHarness([container, action])
        auto.driver = FakeDriver([action], paused=True)

        def click_and_hide(element):
            auto.clicked.append(element)
            action.displayed = False

        auto.click = click_and_hide
        with patch.object(auto, '_settle_checkpoint_result', return_value='handled'):
            state = auto._handle_checkpoint_state(object())

        self.assertEqual(state, 'handled')
        self.assertEqual(auto.clicked, [button])

    def test_automatic_checkpoint_after_previous_puzzle_notifies_once(self):
        first = CheckpointElement('点击通过检查', displayed=True)
        first_button = FakeElement(
            tag_name='button',
            class_name='checkpoint-button',
            displayed=True,
        ).add(first)
        FakeElement('点击通过检查 视频检查点').add(first_button)
        auto = CheckpointHarness([first])
        auto.driver = FakeDriver([first], paused=True)
        auto._last_checkpoint_kind = 'puzzle'

        def click_and_hide(element):
            auto.clicked.append(element)
            first.displayed = False

        auto.click = click_and_hide
        with patch.object(auto, '_settle_checkpoint_result', return_value='handled'):
            self.assertEqual(auto._handle_checkpoint_state(object()), 'handled')

        notices = [event for event in auto.manual_events if event[2] == 'notice']
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0][0], 'checkpoint_notice')
        self.assertEqual(auto._last_checkpoint_kind, 'automatic')
        self.assertEqual(auto.clicked, [first_button])

        second = CheckpointElement('通过检查', displayed=True)
        second_button = FakeElement(
            tag_name='button',
            class_name='checkpoint-button',
            displayed=True,
        ).add(second)
        FakeElement('通过检查 视频检查点').add(second_button)
        auto.driver = FakeDriver([second], paused=True)
        auto._last_checkpoint_action_signature = ''
        auto.click = lambda element: (
            auto.clicked.append(element),
            setattr(second, 'displayed', False),
        )
        with patch.object(auto, '_settle_checkpoint_result', return_value='handled'):
            self.assertEqual(auto._handle_checkpoint_state(object()), 'handled')

        self.assertEqual(
            len([event for event in auto.manual_events if event[2] == 'notice']),
            1,
        )

    def test_previous_puzzle_kind_survives_course_boundary(self):
        auto = CheckpointHarness([])
        auto._last_checkpoint_kind = 'puzzle'

        # Course/day setup resets replay bookkeeping but must not reset the
        # last checkpoint kind for this account browser session.
        auto._passed_missed_checkpoint_signatures = {'old'}
        auto._passed_missed_checkpoint_labels = {'old'}
        auto._missed_replay_completed_courses = 4
        self.assertEqual(auto._last_checkpoint_kind, 'puzzle')

        action = CheckpointElement('通过检查', displayed=True)
        button = FakeElement(
            tag_name='button',
            class_name='checkpoint-button',
            displayed=True,
        ).add(action)
        FakeElement('通过检查 视频检查点').add(button)
        auto.driver = FakeDriver([action], paused=True)
        auto.click = lambda element: (
            auto.clicked.append(element),
            setattr(action, 'displayed', False),
        )

        with patch.object(auto, '_settle_checkpoint_result', return_value='handled'):
            self.assertEqual(auto._handle_checkpoint_state(object()), 'handled')

        notices = [event for event in auto.manual_events if event[2] == 'notice']
        self.assertEqual(len(notices), 1)
        self.assertEqual(auto._last_checkpoint_kind, 'automatic')

    def test_checkpoint_action_that_stays_visible_fails_closed(self):
        action = CheckpointElement('点击通过检查', displayed=True)
        button = FakeElement(
            tag_name='button',
            class_name='checkpoint-button',
            displayed=True,
        ).add(action)
        FakeElement('点击通过检查 视频检查点').add(button)
        auto = CheckpointHarness([action])
        auto.driver = FakeDriver([action], paused=True)

        with patch('auto_video.auto_video.time.monotonic', side_effect=[10.0, 10.0, 13.0]):
            with self.assertRaises(CheckpointInteractionRequiredError):
                auto._handle_checkpoint_state(object())

        self.assertEqual(auto.clicked, [button])

    def test_delayed_missed_result_wins_after_action_disappears(self):
        action = CheckpointElement('点击通过检查', displayed=True)
        button = FakeElement(
            tag_name='button',
            class_name='checkpoint-button',
            displayed=True,
        ).add(action)
        FakeElement('点击通过检查 视频检查点').add(button)
        missed_ack = CheckpointElement('我知道了', displayed=True)
        auto = DelayedMissedResultHarness(action, missed_ack)

        times = iter([10.0, 10.0, 10.1, 10.2, 10.3])
        with patch(
            'auto_video.auto_video.time.monotonic',
            side_effect=lambda: next(times, 10.3),
        ), patch('auto_video.auto_video.time.sleep'):
            state = auto._handle_checkpoint_state(object())

        self.assertEqual(state, 'missed')
        self.assertEqual(auto.clicked, [button])

    def test_fast_mode_keeps_the_full_checkpoint_result_window(self):
        auto = CheckpointHarness([])
        auto.config['delay_multiplier'] = 0.1
        scans = iter(['none', 'none', 'missed'])
        auto._checkpoint_result_state = lambda _video: next(scans)
        times = iter([10.0, 10.0, 10.1, 10.5, 10.6])

        with patch(
            'auto_video.auto_video.time.monotonic',
            side_effect=lambda: next(times, 10.6),
        ), patch('auto_video.auto_video.time.sleep'):
            state = auto._settle_checkpoint_result(object())

        self.assertEqual(state, 'missed')

    def test_unknown_modal_action_is_never_clicked(self):
        action = CheckpointElement('继续播放')
        auto = CheckpointHarness([action])
        auto.driver = FakeDriver([action], paused=True, overlay=True)

        state = auto._handle_checkpoint_state(object())

        self.assertEqual(state, 'none')
        self.assertEqual(auto.clicked, [])

    def test_generic_role_dialog_action_is_never_clicked(self):
        action = CheckpointElement('继续播放')
        FakeElement('普通提示', role='dialog').add(action)
        auto = CheckpointHarness([action])
        auto.driver = FakeDriver([action], paused=True, overlay=True)

        state = auto._handle_checkpoint_state(object())

        self.assertEqual(state, 'none')
        self.assertEqual(auto.clicked, [])

    def test_unknown_overlay_blocks_resume(self):
        auto = CheckpointHarness([])
        auto.driver = FakeDriver([], paused=True, overlay=True)
        video = FakeElement()
        pbar = type('Pbar', (), {'refresh': lambda self: None})()

        with patch('auto_video.auto_video.time.sleep'):
            auto._resume_if_paused(video, pbar)

        self.assertFalse(any('.play()' in call for call in auto.driver.script_calls))


class MissedCheckpointReplayTests(unittest.TestCase):
    def setUp(self):
        self.button = FakeReplayButton()
        self.candidate = MissedCheckpointCandidate(
            button=self.button,
            signature='course-1',
            label='物理课',
        )

    def test_replays_until_warning_disappears(self):
        auto = ReplayHarness([
            ([self.candidate], True),
            ([], False),
        ])

        replayed = auto._finish_missed_checkpoint_lessons()

        self.assertEqual(replayed, 1)
        self.assertEqual(auto.replayed, [self.button])
        self.assertEqual(auto.replay_modes, [True])

    def test_replay_finishes_immediately_after_one_confirmed_checkpoint(self):
        auto = ImmediateCheckpointLessonHarness()

        completed_by_checkpoint = auto.finish_a_lesson(
            self.button,
            stop_after_checkpoint=True,
        )

        self.assertTrue(completed_by_checkpoint)
        self.assertEqual(auto.closed, 1)
        self.assertEqual(auto.driver.quit_calls, 0)

    def test_confirmed_checkpoint_emits_finished_progress_before_returning(self):
        auto = ImmediateCheckpointLessonHarness()
        events = []
        token = set_progress_sink(events.append)
        try:
            completed_by_checkpoint = auto.finish_a_lesson(
                self.button,
                stop_after_checkpoint=True,
            )
        finally:
            reset_progress_sink(token)

        self.assertTrue(completed_by_checkpoint)
        self.assertEqual(auto.closed, 1)
        self.assertEqual(auto.driver.quit_calls, 0)
        self.assertEqual(events[-1].title, '检查点已通过')
        self.assertTrue(events[-1].finished)
        self.assertEqual(events[-1].kind, 'current_course')

    def test_confirmed_checkpoint_overrides_stale_list_warning(self):
        auto = ReplayHarness(
            [
                ([self.candidate], True),
                ([self.candidate], True),
            ],
            checkpoint_passed=True,
        )

        replayed = auto._finish_missed_checkpoint_lessons()

        self.assertEqual(replayed, 1)
        self.assertEqual(auto.replayed, [self.button])

    def test_warning_only_stale_row_matches_confirmed_course_id(self):
        warning = FakeElement('错过了所有看课检测点，再认真观看一次吧！')
        card = FakeElement(
            '物理课 已学完',
            attributes={'data-course-id': 'course-42'},
        ).add(warning)
        FakeElement(tag_name='body').add(card)
        auto = object.__new__(AutoVideo)
        auto.driver = FakeDriver([warning])
        passed = {'id:data-course-id:course-42'}

        self.assertTrue(auto._all_visible_missed_warnings_passed(passed))

    def test_warning_only_stale_row_for_other_course_fails_closed(self):
        warning = FakeElement('错过了所有看课检测点，再认真观看一次吧！')
        card = FakeElement(
            '物理课 已学完',
            attributes={'data-course-id': 'course-99'},
        ).add(warning)
        FakeElement(tag_name='body').add(card)
        auto = object.__new__(AutoVideo)
        auto.driver = FakeDriver([warning])
        passed = {'id:data-course-id:course-42'}

        self.assertFalse(auto._all_visible_missed_warnings_passed(passed))

    def test_course_signature_uses_stable_id_across_layout_changes(self):
        button = FakeElement(
            '已学完',
            tag_name='button',
            x=20,
            y=100,
        )
        button.attributes['data-course-id'] = 'course-42'
        container = FakeElement('物理课 已学完').add(button)
        auto = object.__new__(AutoVideo)

        first = auto._course_signature(container, button)
        button.location = {'x': 780, 'y': 520}
        second = auto._course_signature(container, button)

        self.assertEqual(first, 'id:data-course-id:course-42')
        self.assertEqual(second, first)

    def test_parent_course_id_wins_over_inner_button_id(self):
        button = FakeElement(
            '已学完',
            tag_name='button',
            attributes={'id': 'replay-button'},
        )
        container = FakeElement(
            '物理课 已学完',
            attributes={'data-course-id': 'course-42'},
        ).add(button)
        auto = object.__new__(AutoVideo)

        signature = auto._course_signature(container, button)

        self.assertEqual(signature, 'id:data-course-id:course-42')

    def test_identifier_attribute_namespaces_do_not_collide(self):
        auto = object.__new__(AutoVideo)
        data_button = FakeElement(
            '已学完',
            attributes={'data-course-id': 'shared-value'},
        )
        html_button = FakeElement(
            '已学完',
            attributes={'id': 'shared-value'},
        )

        self.assertNotEqual(
            auto._stable_course_id(data_button),
            auto._stable_course_id(html_button),
        )

    def test_card_fingerprint_survives_layout_changes(self):
        button = FakeElement('已学完', tag_name='button', x=20, y=100)
        container = FakeElement(
            '物理课 第3讲 已学完 错过了所有看课检测点',
        ).add(button)
        auto = object.__new__(AutoVideo)

        first = auto._course_signature(container, button)
        button.location = {'x': 780, 'y': 520}
        second = auto._course_signature(container, button)

        self.assertEqual(first, 'card:物理课 第3讲')
        self.assertEqual(second, first)

    def test_same_label_different_card_details_do_not_collide(self):
        auto = object.__new__(AutoVideo)
        first_button = FakeElement('已学完')
        second_button = FakeElement('已学完')
        first_card = FakeElement('物理课 第1讲 已学完').add(first_button)
        second_card = FakeElement('物理课 第2讲 已学完').add(second_button)

        self.assertNotEqual(
            auto._course_signature(first_card, first_button),
            auto._course_signature(second_card, second_button),
        )

    def test_duplicate_fallback_cards_are_kept_as_distinct_courses(self):
        first_button = FakeElement('已学完', tag_name='button')
        first_warning = FakeElement('错过了所有看课检测点，再认真观看一次吧！')
        first_card = FakeElement('物理课 已学完').add(first_warning, first_button)
        second_button = FakeElement('已学完', tag_name='button')
        second_warning = FakeElement('错过了所有看课检测点，再认真观看一次吧！')
        second_card = FakeElement('物理课 已学完').add(second_warning, second_button)
        body = FakeElement(tag_name='body').add(first_card, second_card)
        auto = object.__new__(AutoVideo)
        auto.driver = FakeDriver([first_warning, second_warning])

        candidates, warning_found = auto._find_missed_checkpoint_candidates()

        self.assertTrue(warning_found)
        self.assertEqual(len(candidates), 2)
        self.assertNotEqual(candidates[0].signature, candidates[1].signature)
        self.assertTrue(candidates[0].signature.endswith('|occurrence:0'))
        self.assertTrue(candidates[1].signature.endswith('|occurrence:1'))
        self.assertIs(body.children[0], first_card)

    def test_dynamic_progress_does_not_change_card_fingerprint(self):
        auto = object.__new__(AutoVideo)

        self.assertEqual(
            auto._course_card_fingerprint('物理课 学 20% 已学完'),
            auto._course_card_fingerprint('物理课 学 80% 已学完'),
        )

    def test_same_course_with_a_moved_layout_stays_completed(self):
        original_candidate = MissedCheckpointCandidate(
            button=self.button,
            signature='label:物理课',
            label='物理课',
        )
        moved_candidate = MissedCheckpointCandidate(
            button=self.button,
            signature='label:物理课',
            label='物理课',
        )
        auto = ReplayHarness(
            [
                ([original_candidate], True),
                ([moved_candidate], True),
            ],
            checkpoint_passed=True,
        )

        replayed = auto._finish_missed_checkpoint_lessons()

        self.assertEqual(replayed, 1)
        self.assertEqual(auto.replayed, [self.button])

    def test_stable_id_does_not_use_label_fallback_for_a_different_course(self):
        first = MissedCheckpointCandidate(
            button=self.button,
            signature='id:course-1',
            label='物理课',
        )
        second = MissedCheckpointCandidate(
            button=self.button,
            signature='id:course-2',
            label='物理课',
        )
        auto = ReplayHarness(
            [
                ([first], True),
                ([second], True),
                ([], False),
            ],
            checkpoint_passed=True,
        )

        replayed = auto._finish_missed_checkpoint_lessons()

        self.assertEqual(replayed, 2)
        self.assertEqual(auto.replayed, [self.button, self.button])

    def test_normal_course_does_not_stop_after_a_confirmed_checkpoint(self):
        auto = ImmediateCheckpointLessonHarness()
        auto._handle_checkpoint_state = lambda _video: 'handled'
        auto._has_unknown_visible_overlay = lambda: False

        states = iter(['handled', 'none'])
        auto._handle_checkpoint_state = lambda _video: next(states)
        auto.driver.execute_script = lambda *_args: True
        auto._get_duration = lambda: 0

        class EmptyProgress:
            n = 0

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def refresh(self):
                return None

        auto._create_pbar = lambda _duration: EmptyProgress()

        completed_by_checkpoint = auto.finish_a_lesson(
            self.button,
            stop_after_checkpoint=False,
        )

        self.assertFalse(completed_by_checkpoint)
        self.assertEqual(auto.closed, 1)

    def test_normal_course_loop_checkpoint_does_not_close_early(self):
        auto = LoopCheckpointLessonHarness()
        auto._get_duration = lambda: 0
        auto.driver.find_element = lambda _by, value: (
            auto.video
            if value == 'video'
            else FakeElement()
            if value == 'vjs-big-play-button'
            else FakeElement('00:00')
        )

        class EmptyProgress:
            n = 0

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def refresh(self):
                return None

        auto._create_pbar = lambda _duration: EmptyProgress()

        completed_by_checkpoint = auto.finish_a_lesson(
            self.button,
            stop_after_checkpoint=False,
        )

        self.assertFalse(completed_by_checkpoint)
        self.assertEqual(auto.closed, 1)

    def test_loop_checkpoint_completion_is_last_progress_event(self):
        auto = LoopCheckpointLessonHarness()
        auto._get_duration = lambda: 1394
        auto.driver.find_element = lambda _by, value: (
            auto.video
            if value == 'video'
            else FakeElement()
            if value == 'vjs-big-play-button'
            else FakeElement('00:19')
        )

        class ClosingProgress:
            n = 19

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                emit_progress(
                    '视频播放进度',
                    19,
                    1394,
                    '秒',
                    finished=True,
                    kind='current_course',
                    scope='active',
                )
                return False

            def refresh(self):
                return None

        auto._create_pbar = lambda _duration: ClosingProgress()
        events = []
        token = set_progress_sink(events.append)
        try:
            completed_by_checkpoint = auto.finish_a_lesson(
                self.button,
                stop_after_checkpoint=True,
            )
        finally:
            reset_progress_sink(token)

        self.assertTrue(completed_by_checkpoint)
        self.assertEqual(auto.closed, 1)
        self.assertEqual(auto.driver.quit_calls, 0)
        self.assertEqual(events[-1].title, '检查点已通过')
        self.assertEqual((events[-1].current, events[-1].total), (1, 1))
        self.assertTrue(events[-1].finished)

    def test_settle_scan_catches_delayed_warning(self):
        auto = ReplayHarness([
            ([], False),
            ([self.candidate], True),
            ([], False),
        ])

        replayed = auto._finish_missed_checkpoint_lessons(settle_scans=1)

        self.assertEqual(replayed, 1)
        self.assertEqual(auto.replayed, [self.button])

    def test_persistent_warning_is_never_treated_as_complete(self):
        scans = [
            ([self.candidate], True)
            for _ in range(MISSED_CHECKPOINT_REPLAY_LIMIT + 1)
        ]
        auto = ReplayHarness(scans)

        with self.assertRaises(MissedCheckpointReplayError):
            auto._finish_missed_checkpoint_lessons()

        self.assertEqual(len(auto.replayed), MISSED_CHECKPOINT_REPLAY_LIMIT)

    def test_warning_without_replay_button_fails_closed(self):
        auto = ReplayHarness([
            ([], True),
            ([], True),
            ([], True),
        ])

        with self.assertRaises(MissedCheckpointReplayError):
            auto._finish_missed_checkpoint_lessons()

        self.assertEqual(auto.replayed, [])

    def test_known_missed_checkpoint_requires_a_reliable_list_state(self):
        auto = ReplayHarness([([], False) for _ in range(3)])

        with self.assertRaises(MissedCheckpointReplayError):
            auto._finish_missed_checkpoint_lessons(
                settle_scans=2,
                require_warning=True,
            )

        self.assertEqual(auto.replayed, [])

    def test_repeated_stale_replay_button_has_a_retry_limit(self):
        scans = [
            ([self.candidate], True)
            for _ in range(MISSED_CHECKPOINT_STALE_RETRIES)
        ]
        auto = StaleReplayHarness(scans)

        with self.assertRaises(MissedCheckpointReplayError):
            auto._finish_missed_checkpoint_lessons()

        self.assertEqual(auto.return_attempts, MISSED_CHECKPOINT_STALE_RETRIES)

    def test_changing_dom_signatures_cannot_replay_forever(self):
        candidates = [
            MissedCheckpointCandidate(
                button=self.button,
                signature=f'course-{index}',
                label='物理课',
            )
            for index in range(4)
        ]
        auto = ReplayHarness([([candidate], True) for candidate in candidates])

        with patch(
            'auto_video.auto_video.MISSED_CHECKPOINT_OPERATION_LIMIT',
            3,
        ):
            with self.assertRaises(MissedCheckpointReplayError):
                auto._finish_missed_checkpoint_lessons()

        self.assertEqual(len(auto.replayed), 3)

    def test_warning_is_paired_with_completed_button_in_same_course(self):
        normal_button = FakeElement('已学完', tag_name='button', x=20, y=20)
        normal_card = FakeElement('普通已完成课程').add(normal_button)
        replay_button = FakeElement('已学完', tag_name='button', x=20, y=120)
        warning = FakeElement('错过了所有看课检测点，再认真观看一次吧！')
        warning_card = FakeElement('物理课').add(warning, replay_button)
        body = FakeElement(tag_name='body').add(normal_card, warning_card)
        auto = object.__new__(AutoVideo)
        auto.driver = FakeDriver([body, warning_card, warning])

        candidates, warning_found = auto._find_missed_checkpoint_candidates()

        self.assertTrue(warning_found)
        self.assertEqual(len(candidates), 1)
        self.assertIs(candidates[0].button, replay_button)
        self.assertEqual(candidates[0].label, '物理课')

    def test_day_scan_prioritizes_and_rechecks_missed_courses(self):
        auto = FinishDayHarness()

        auto.finish_a_day(object())

        self.assertEqual(
            auto.events,
            [
                'day',
                'scan-missed',
                'video',
                'scan-missed',
                'find-one-click',
            ],
        )

    def test_initial_completed_missed_replay_reports_one_of_one(self):
        auto = InitialMissedReplayProgressHarness()
        events = []
        token = set_progress_sink(events.append)
        try:
            auto.finish_a_day(object())
        finally:
            reset_progress_sink(token)

        courses = [event for event in events if event.kind == 'courses']
        self.assertTrue(courses)
        self.assertEqual(
            [(event.current, event.total) for event in courses],
            [(1, 1), (1, 1), (1, 1)],
        )

    def test_playback_missed_replay_does_not_duplicate_course_total(self):
        auto = PlaybackMissedReplayProgressHarness()
        events = []
        token = set_progress_sink(events.append)
        try:
            auto.finish_a_day(object())
        finally:
            reset_progress_sink(token)

        courses = [event for event in events if event.kind == 'courses']
        self.assertTrue(courses)
        self.assertEqual(courses[0].current, 0)
        self.assertEqual(courses[-1].current, 1)
        self.assertTrue(all(event.total == 1 for event in courses))
        self.assertFalse(any(
            event.current > (event.total or 0)
            for event in courses
        ))

    def test_playback_missed_scan_counts_additional_warning_courses_once(self):
        auto = MultiplePlaybackMissedReplayProgressHarness()
        events = []
        token = set_progress_sink(events.append)
        try:
            auto.finish_a_day(object())
        finally:
            reset_progress_sink(token)

        courses = [event for event in events if event.kind == 'courses']
        self.assertEqual((courses[-1].current, courses[-1].total), (2, 2))
        self.assertFalse(any(
            event.current > (event.total or 0)
            for event in courses
        ))

    def test_delayed_missed_replay_without_ordinary_courses_reports_one_of_one(self):
        auto = DelayedOnlyMissedReplayProgressHarness()
        events = []
        token = set_progress_sink(events.append)
        try:
            auto.finish_a_day(object())
        finally:
            reset_progress_sink(token)

        courses = [event for event in events if event.kind == 'courses']
        self.assertEqual((courses[-1].current, courses[-1].total), (1, 1))

    def test_day_scan_bounds_failures_processes_other_lessons_and_reports_failure(self):
        auto = FailingFinishDayHarness()

        with self.assertRaises(LessonProcessingError):
            auto.finish_a_day(object())

        self.assertEqual(auto.fail_calls, LESSON_FAILURE_LIMIT)
        self.assertIn('second-video', auto.events)

    def test_day_scan_bounds_repeated_stale_elements(self):
        auto = StaleFinishDayHarness()

        with self.assertRaises(LessonProcessingError):
            auto.finish_a_day(object())

        self.assertEqual(auto.stale_calls, LESSON_FAILURE_LIMIT)

    def test_day_scan_has_a_total_operation_limit(self):
        auto = StaleFinishDayHarness()

        with patch('auto_video.auto_video.LESSON_FAILURE_LIMIT', 10_000), patch(
            'auto_video.auto_video.DAY_LESSON_OPERATION_LIMIT',
            3,
        ):
            with self.assertRaises(LessonProcessingError):
                auto.finish_a_day(object())

        self.assertEqual(auto.stale_calls, 3)

    def test_day_scan_does_not_accept_all_identity_elements_stale_as_complete(self):
        auto = IdentityStaleFinishDayHarness()

        with patch('auto_video.auto_video.LESSON_FAILURE_LIMIT', 3):
            with self.assertRaises(LessonProcessingError):
                auto.finish_a_day(object())

        self.assertEqual(auto.identity_calls, LESSON_FAILURE_LIMIT)

    def test_one_click_failure_is_not_reported_as_success(self):
        auto = OneClickFailureHarness()

        with self.assertRaises(LessonProcessingError):
            auto.finish_a_day(object())

        self.assertIn(('return', False), auto.events)


if __name__ == '__main__':
    unittest.main()
