import sys
import tempfile
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from checkpoint_probe import (  # noqa: E402
    OBSERVER_SCRIPT,
    capture_reason,
    save_capture,
)


class FakeCaptureDriver:
    page_source = '<html><body>checkpoint</body></html>'

    def __init__(self):
        self.scripts = []

    def execute_script(self, script):
        self.scripts.append(script)

    def save_screenshot(self, path):
        Path(path).write_bytes(b'png')
        return True


class CheckpointProbeStateTests(unittest.TestCase):
    def test_started_video_pause_is_captured(self):
        reason = capture_reason(
            {'video': {'paused': True, 'ended': False}, 'events': []},
            playback_started=True,
            seconds_after_click=2,
        )
        self.assertEqual(reason, 'video-paused')

    def test_new_modal_is_captured_before_any_resume(self):
        reason = capture_reason(
            {
                'video': {'paused': True, 'ended': False},
                'events': [{'modalLike': True, 'text': '我知道了'}],
            },
            playback_started=False,
            seconds_after_click=1,
        )
        self.assertEqual(reason, 'new-modal-or-overlay')

    def test_risk_warning_has_priority_over_generic_modal(self):
        reason = capture_reason(
            {
                'riskTextVisible': True,
                'video': {'paused': True},
                'events': [{'modalLike': True}],
            },
            playback_started=True,
            seconds_after_click=10,
        )
        self.assertEqual(reason, 'playback-risk-warning')

    def test_initial_paused_state_gets_a_grace_period(self):
        snapshot = {'video': {'paused': True, 'ended': False}, 'events': []}
        self.assertIsNone(capture_reason(
            snapshot,
            playback_started=False,
            seconds_after_click=2,
        ))
        self.assertEqual(capture_reason(
            snapshot,
            playback_started=False,
            seconds_after_click=10,
        ), 'playback-never-started')

    def test_capture_writes_json_html_png_and_only_pauses(self):
        driver = FakeCaptureDriver()
        with tempfile.TemporaryDirectory() as temp_dir:
            stem = save_capture(
                driver,
                Path(temp_dir),
                {'reason': 'video-paused', 'events': []},
            )
            self.assertTrue(stem.with_suffix('.json').exists())
            self.assertTrue(stem.with_suffix('.html').exists())
            self.assertTrue(stem.with_suffix('.png').exists())
        self.assertEqual(len(driver.scripts), 1)
        self.assertIn('video.pause()', driver.scripts[0])
        self.assertNotIn('video.play()', driver.scripts[0])

    def test_observer_treats_known_action_text_as_capture_signal(self):
        self.assertIn('我知道了', OBSERVER_SCRIPT)
        self.assertIn('actionText', OBSERVER_SCRIPT)
        self.assertNotIn("|| Number(style.zIndex) >= 10", OBSERVER_SCRIPT)


if __name__ == '__main__':
    unittest.main()
