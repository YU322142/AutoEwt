from __future__ import annotations

import os
import shlex
import subprocess
from copy import deepcopy
from dataclasses import dataclass
from typing import Mapping


_HEADLESS_ARGUMENTS = ('--headless', '-headless')
_VISIBLE_RENDERING_ARGUMENTS = (
    '--disable-gpu',
    '--disable-gpu-compositing',
    '--disable-gpu-rasterization',
    '--disable-software-rasterizer',
    '--hide-scrollbars',
    '--use-angle',
    '--use-gl',
    '--window-size',
)
_VISIBLE_ONLY_ARGUMENTS = ('--start-maximized', '--start-minimized')


@dataclass(frozen=True)
class ManualInterventionRequest:
    kind: str
    reason: str
    phase: str = 'required'
    headless: bool = False


@dataclass(frozen=True)
class ManualInterventionEvent:
    task_label: str
    kind: str
    reason: str
    phase: str
    headless: bool

    @property
    def title(self) -> str:
        label = self.task_label or '当前任务'
        if self.phase == 'notice':
            return f'AutoEwt：{label} 检查点提示'
        if self.phase == 'resolved':
            return f'AutoEwt：{label} 已恢复'
        if self.phase == 'waiting_slot':
            return f'AutoEwt：{label} 等待人工窗口'
        if self.phase == 'visible_started':
            return f'AutoEwt：{label} 人工会话已打开'
        return f'AutoEwt：{label} 需要人工验证'

    @property
    def message(self) -> str:
        if self.phase == 'notice':
            return self.reason
        if self.phase == 'resolved':
            return '验证已完成，任务将继续运行。'
        if self.phase == 'waiting_slot':
            return '另一条任务正在人工验证；本任务已安全暂停，稍后会自动打开可见窗口。'
        if self.phase == 'visible_started':
            return '该任务已切换为可见浏览器，会继续运行并在需要时等待人工操作。'
        if self.headless:
            return f'{self.reason}。正在显示这一条任务原有的浏览器窗口，验证页面会保持不变。'
        return f'{self.reason}。请在已打开的浏览器窗口中完成验证。'


class HumanVerificationRequiredError(RuntimeError):
    """A confirmed human verification cannot be completed in headless mode."""

    def __init__(self, reason: str, kind: str = 'captcha'):
        super().__init__(reason)
        self.reason = reason
        self.kind = kind


class BrowserControlRequested(RuntimeError):
    """Internal control flow raised by the Selenium owner thread."""


class TaskStopRequested(BrowserControlRequested):
    """The owning worker has observed a stop request."""


class VisibleHandoffRequested(BrowserControlRequested):
    """The owning worker must rebuild this headless session as visible."""


def split_browser_options(value: str | None) -> list[str]:
    raw = str(value or '')
    if not raw:
        return []
    if os.name != 'nt':
        return shlex.split(raw)
    try:
        import ctypes
        from ctypes import wintypes

        argc = ctypes.c_int()
        shell32 = ctypes.WinDLL('shell32', use_last_error=True)
        shell32.CommandLineToArgvW.argtypes = [
            wintypes.LPCWSTR,
            ctypes.POINTER(ctypes.c_int),
        ]
        shell32.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
        argv = shell32.CommandLineToArgvW(
            f'autoewt-options.exe {raw}',
            ctypes.byref(argc),
        )
        if not argv:
            raise OSError(ctypes.get_last_error(), 'CommandLineToArgvW failed')
        try:
            return [argv[index] for index in range(1, argc.value)]
        finally:
            ctypes.windll.kernel32.LocalFree(argv)
    except (AttributeError, OSError, TypeError, ValueError):
        return shlex.split(raw)


def join_browser_options(arguments: list[str]) -> str:
    if os.name == 'nt':
        return subprocess.list2cmdline(arguments)
    return shlex.join(arguments)


def _matches_argument(argument: str, option: str) -> bool:
    value = argument.strip().lower()
    return value == option or value.startswith(f'{option}=')


def is_headless_options(value: str | None) -> bool:
    return any(
        any(_matches_argument(argument, option) for option in _HEADLESS_ARGUMENTS)
        for argument in split_browser_options(value)
    )


def browser_options_without_mode(value: str | None) -> str:
    """Remove arguments managed by the GUI's headless-mode checkbox."""
    filtered = []
    arguments = split_browser_options(value)
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if any(_matches_argument(argument, option) for option in _HEADLESS_ARGUMENTS):
            index += 1
            continue
        if any(
            _matches_argument(argument, option)
            for option in _VISIBLE_RENDERING_ARGUMENTS
        ):
            if (
                '=' not in argument
                and index + 1 < len(arguments)
                and not arguments[index + 1].startswith('-')
            ):
                index += 1
            index += 1
            continue
        if not any(
            _matches_argument(argument, option)
            for option in _VISIBLE_ONLY_ARGUMENTS
        ):
            filtered.append(argument)
        index += 1

    return join_browser_options(filtered)


def visible_browser_options(value: str | None, browser: str = 'Chrome') -> str:
    """Build a stable visible-browser option string."""
    filtered = split_browser_options(browser_options_without_mode(value))

    if browser.strip().lower() in {'chrome', 'edge'}:
        if not any(_matches_argument(item, '--start-maximized') for item in filtered):
            filtered.append('--start-maximized')
    return join_browser_options(filtered)


def headless_browser_options(value: str | None, browser: str = 'Chrome') -> str:
    """Enable headless mode without making users edit raw browser arguments."""
    arguments = split_browser_options(browser_options_without_mode(value))
    if browser.strip().lower() == 'firefox':
        arguments.append('-headless')
    else:
        arguments.append('--headless=new')
    return join_browser_options(arguments)


def visible_handoff_config(
    values: Mapping,
    *,
    auto_hide_after_manual: bool = False,
    handoff_kind: str = '',
) -> dict:
    config = deepcopy(dict(values))
    browser = str(config.get('browser', 'Chrome'))
    if auto_hide_after_manual:
        arguments = split_browser_options(
            browser_options_without_mode(str(config.get('options', '')))
        )
        if browser.strip().lower() in {'chrome', 'edge'}:
            arguments.append('--start-minimized')
        config['options'] = join_browser_options(arguments)
    else:
        config['options'] = visible_browser_options(
            str(config.get('options', '')),
            browser,
        )
    config['foreground_browser'] = not auto_hide_after_manual
    config['foreground_on_manual'] = True
    config['_manual_handoff_active'] = True
    config['_auto_hide_after_manual'] = bool(auto_hide_after_manual)
    config['_launch_hidden'] = bool(auto_hide_after_manual)
    config['_manual_handoff_kind'] = str(handoff_kind)
    return config


def background_manual_config(values: Mapping) -> dict:
    """Keep a manual-capable browser session hidden instead of rebuilding it.

    A real Chrome/Firefox headless session cannot be made interactive after a
    captcha appears.  For configurations that allow human handoff we start a
    normal browser minimized, keep it hidden during automation, and only
    foreground this same session when verification is detected.  The logical
    headless intent is retained separately for UI/status reporting.
    """
    config = deepcopy(dict(values))
    if not is_headless_options(str(config.get('options', ''))):
        return config
    if not bool(config.get('manual_handoff_enabled', True)):
        return config
    config = visible_handoff_config(
        config,
        auto_hide_after_manual=True,
        handoff_kind='background_manual',
    )
    config['_logical_headless'] = True
    config['_background_manual_session'] = True
    return config
