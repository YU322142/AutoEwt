import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SRC_DIR = Path(__file__).resolve().parents[1]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import system_notification  # noqa: E402


class SystemNotificationTests(unittest.TestCase):
    def test_non_windows_is_a_safe_noop(self):
        with patch.object(system_notification.os, 'name', 'posix'):
            self.assertFalse(system_notification.send_system_notification('title', 'body'))

    def test_windows_notification_uses_environment_not_script_interpolation(self):
        with (
            patch.object(system_notification.os, 'name', 'nt'),
            patch('system_notification.subprocess.Popen') as popen,
        ):
            self.assertTrue(system_notification.send_system_notification(
                "title'; exit 9; #",
                'body $env:PATH',
                duration_ms=1,
            ))

        args, kwargs = popen.call_args
        command = args[0]
        self.assertIn('-WindowStyle', command)
        self.assertNotIn("title'; exit 9; #", command[-1])
        self.assertEqual(kwargs['env']['AUTOEWT_NOTIFICATION_TITLE'], "title'; exit 9; #")
        self.assertEqual(kwargs['env']['AUTOEWT_NOTIFICATION_DURATION'], '3000')


if __name__ == '__main__':
    unittest.main()
