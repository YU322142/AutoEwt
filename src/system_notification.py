from __future__ import annotations

import logging
import os
import subprocess


_POWERSHELL_NOTIFY_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$notify = New-Object System.Windows.Forms.NotifyIcon
try {
    $notify.Icon = [System.Drawing.SystemIcons]::Warning
    $notify.Text = 'AutoEwt'
    $notify.BalloonTipIcon = [System.Windows.Forms.ToolTipIcon]::Warning
    $notify.BalloonTipTitle = $env:AUTOEWT_NOTIFICATION_TITLE
    $notify.BalloonTipText = $env:AUTOEWT_NOTIFICATION_MESSAGE
    $notify.Visible = $true
    $notify.ShowBalloonTip([int]$env:AUTOEWT_NOTIFICATION_DURATION)
    [System.Windows.Forms.Application]::DoEvents()
    Start-Sleep -Milliseconds ([int]$env:AUTOEWT_NOTIFICATION_DURATION + 1000)
} finally {
    $notify.Dispose()
}
"""


def send_system_notification(
    title: str,
    message: str,
    duration_ms: int = 10000,
) -> bool:
    """Show a non-blocking Windows notification without extra dependencies."""
    if os.name != 'nt':
        logging.info('系统通知仅在 Windows 桌面会话中可用：%s', title)
        return False

    env = os.environ.copy()
    env['AUTOEWT_NOTIFICATION_TITLE'] = str(title).strip()[:63] or 'AutoEwt'
    env['AUTOEWT_NOTIFICATION_MESSAGE'] = str(message).strip()[:255]
    env['AUTOEWT_NOTIFICATION_DURATION'] = str(
        max(3000, min(30000, int(duration_ms)))
    )
    try:
        subprocess.Popen(
            [
                'powershell.exe',
                '-NoProfile',
                '-NonInteractive',
                '-STA',
                '-WindowStyle',
                'Hidden',
                '-Command',
                _POWERSHELL_NOTIFY_SCRIPT,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        return True
    except (OSError, subprocess.SubprocessError) as exc:
        logging.warning('系统通知发送失败：%s', exc)
        return False
