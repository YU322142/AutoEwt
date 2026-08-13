import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

SRC_DIR = Path(__file__).resolve().parents[1]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from selenium.common import TimeoutException  # noqa: E402

from auto_base import AutoBase, normalize_config  # noqa: E402
from manual_intervention import HumanVerificationRequiredError  # noqa: E402


class LoginDriver:
    def __init__(self, cookie_scans):
        self.cookie_scans = iter(cookie_scans)

    def get_cookies(self):
        return next(self.cookie_scans)


class LoginHarness(AutoBase):
    def finish_a_day(self, day):
        raise NotImplementedError

    def bring_browser_to_front(self, reason=''):
        self.foreground_calls.append(reason)
        return True

    def _manual_login_verification_visible(self):
        return self.manual_visible


class WindowSwitchDriver:
    def __init__(self):
        self.handles = ['main']
        self.current_window_handle = 'main'
        self.current_url = 'https://example.test/list'
        self.switch_to = self

    @property
    def window_handles(self):
        return list(self.handles)

    def window(self, handle):
        self.current_window_handle = handle
        self.current_url = (
            'https://example.test/lesson'
            if handle == 'lesson'
            else 'https://example.test/list'
        )

    def close(self):
        self.handles.remove(self.current_window_handle)


class WindowSwitchHarness(AutoBase):
    def __init__(self):
        self.driver = WindowSwitchDriver()
        self.config = {
            'delay_multiplier': 0,
            '_auto_hide_after_manual': True,
        }
        self.hide_refreshes = []

    def finish_a_day(self, day):
        raise NotImplementedError

    def click(self, button):
        del button
        self.driver.handles.append('lesson')

    def hide_browser_window(self, *, refresh_current=False):
        self.hide_refreshes.append(refresh_current)
        return True


def make_login_harness(cookie_scans, *, manual_visible=True, headless=False):
    auto = object.__new__(LoginHarness)
    auto.driver = LoginDriver(cookie_scans)
    auto.config = {
        'login_wait_timeout': 10.0,
        'foreground_on_manual': True,
        'options': '--headless=new' if headless else '',
    }
    auto.stop_event = threading.Event()
    auto.manual_visible = manual_visible
    auto.foreground_calls = []
    auto.manual_intervention_sink = None
    auto.hide_calls = 0
    auto.hide_browser_window = lambda: setattr(
        auto,
        'hide_calls',
        auto.hide_calls + 1,
    ) or True
    return auto


class LoginWaitTests(unittest.TestCase):
    def test_legacy_day_start_is_forced_to_first_day(self):
        self.assertEqual(normalize_config({'day_to_start_on': 99})['day_to_start_on'], 1)

    def test_waits_for_a_real_token_and_foregrounds_verification_once(self):
        auto = make_login_harness([
            [],
            [],
            [{'name': 'token', 'value': 'ready'}],
        ])

        with (
            patch('auto_base.time.monotonic', side_effect=[0.0, 0.0, 1.0]),
            patch('auto_base.time.sleep'),
        ):
            token = auto._wait_for_login_token()

        self.assertEqual(token, 'ready')
        self.assertEqual(len(auto.foreground_calls), 1)

    def test_stop_event_interrupts_login_wait(self):
        auto = make_login_harness([[]])
        auto.stop_event.set()

        with self.assertRaisesRegex(RuntimeError, '用户停止'):
            auto._wait_for_login_token()

    def test_login_timeout_never_returns_none_as_success(self):
        auto = make_login_harness([[]])

        with patch('auto_base.time.monotonic', side_effect=[0.0, 11.0]):
            with self.assertRaises(TimeoutException):
                auto._wait_for_login_token()

    def test_headless_login_verification_requests_visible_handoff(self):
        auto = make_login_harness([[]], headless=True)

        with patch('auto_base.time.monotonic', side_effect=[0.0, 0.0]):
            with self.assertRaises(HumanVerificationRequiredError) as caught:
                auto._wait_for_login_token()

        self.assertEqual(caught.exception.kind, 'login_verification')
        self.assertEqual(auto.foreground_calls, [])

    def test_background_manual_login_keeps_same_session_until_token(self):
        auto = make_login_harness([
            [],
            [],
            [{'name': 'token', 'value': 'ready'}],
        ])
        auto.config.update({
            '_logical_headless': True,
            '_background_manual_session': True,
            '_auto_hide_after_manual': True,
        })

        with (
            patch('auto_base.time.monotonic', side_effect=[0.0, 0.0, 1.0]),
            patch('auto_base.time.sleep'),
        ):
            token = auto._wait_for_login_token()

        self.assertEqual(token, 'ready')
        self.assertEqual(len(auto.foreground_calls), 1)
        self.assertEqual(auto.hide_calls, 1)

    def test_course_window_switches_back_to_background(self):
        auto = WindowSwitchHarness()

        with patch('auto_base.time.sleep'):
            auto.click_and_switch(object())
            self.assertEqual(auto.driver.current_window_handle, 'lesson')
            auto.close_and_switch()

        self.assertEqual(auto.driver.current_window_handle, 'main')
        self.assertEqual(auto.hide_refreshes, [True, True])


if __name__ == '__main__':
    unittest.main()
