import sys
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parents[1]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from manual_intervention import (  # noqa: E402
    background_manual_config,
    headless_browser_options,
    is_headless_options,
    split_browser_options,
    visible_browser_options,
    visible_handoff_config,
)


class ManualInterventionOptionsTests(unittest.TestCase):
    def test_recognizes_chrome_and_firefox_headless_forms(self):
        for value in (
            '--headless',
            '--headless=new --mute-audio',
            '--headless=chrome',
            '-headless',
        ):
            with self.subTest(value=value):
                self.assertTrue(is_headless_options(value))
        self.assertFalse(is_headless_options('--not-headless --mute-audio'))

    def test_visible_options_remove_rendering_hazards_and_keep_other_flags(self):
        result = visible_browser_options(
            '--headless=new --mute-audio --disable-gpu '
            '--window-size 800,600 --use-angle=d3d11 --lang=zh-CN',
            'Chrome',
        )

        self.assertNotIn('headless', result)
        self.assertNotIn('disable-gpu', result)
        self.assertNotIn('window-size', result)
        self.assertNotIn('use-angle', result)
        self.assertIn('--mute-audio', result)
        self.assertIn('--lang=zh-CN', result)
        self.assertIn('--start-maximized', result)

    def test_visible_handoff_only_changes_runtime_copy(self):
        original = {
            'browser': 'Chrome',
            'options': '--mute-audio --headless=new',
            'foreground_browser': False,
        }

        result = visible_handoff_config(original)

        self.assertTrue(is_headless_options(original['options']))
        self.assertFalse(is_headless_options(result['options']))
        self.assertTrue(result['foreground_browser'])
        self.assertTrue(result['_manual_handoff_active'])

    def test_automatic_manual_handoff_starts_hidden_until_verification(self):
        result = visible_handoff_config(
            {'browser': 'Chrome', 'options': '--mute-audio --headless=new'},
            auto_hide_after_manual=True,
            handoff_kind='attention_checkpoint',
        )

        self.assertFalse(is_headless_options(result['options']))
        self.assertIn('--start-minimized', result['options'])
        self.assertFalse(result['foreground_browser'])
        self.assertTrue(result['_launch_hidden'])
        self.assertTrue(result['_auto_hide_after_manual'])
        self.assertEqual(result['_manual_handoff_kind'], 'attention_checkpoint')

    def test_headless_handoff_uses_one_hidden_manual_capable_session(self):
        original = {
            'browser': 'Chrome',
            'options': '--mute-audio --headless=new',
            'manual_handoff_enabled': True,
        }

        result = background_manual_config(original)

        self.assertTrue(is_headless_options(original['options']))
        self.assertFalse(is_headless_options(result['options']))
        self.assertIn('--start-minimized', result['options'])
        self.assertTrue(result['_logical_headless'])
        self.assertTrue(result['_background_manual_session'])
        self.assertTrue(result['_auto_hide_after_manual'])

    def test_disabled_manual_handoff_keeps_true_headless_mode(self):
        result = background_manual_config({
            'browser': 'Chrome',
            'options': '--headless=new',
            'manual_handoff_enabled': False,
        })

        self.assertTrue(is_headless_options(result['options']))
        self.assertNotIn('_background_manual_session', result)

    def test_headless_checkbox_builder_preserves_other_flags(self):
        result = headless_browser_options(
            '--mute-audio --lang=zh-CN --start-maximized',
            'Chrome',
        )

        self.assertTrue(is_headless_options(result))
        self.assertIn('--headless=new', result)
        self.assertIn('--mute-audio', result)
        self.assertIn('--lang=zh-CN', result)
        self.assertNotIn('--start-maximized', result)

    def test_firefox_headless_checkbox_uses_firefox_flag(self):
        result = headless_browser_options('--mute-audio', 'Firefox')

        self.assertTrue(is_headless_options(result))
        self.assertIn('-headless', result)
        self.assertNotIn('--headless=new', result)

    def test_windows_style_paths_keep_backslashes(self):
        result = split_browser_options(
            '--user-data-dir="C:\\Chrome Profiles\\Student 1" --mute-audio'
        )

        self.assertIn(r'--user-data-dir=C:\Chrome Profiles\Student 1', result)


if __name__ == '__main__':
    unittest.main()
