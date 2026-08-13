#
# 此模块用于存储通用的工具函数
#

import os
import logging
import time
import subprocess
import threading
import uuid
from abc import ABC, abstractmethod
from copy import deepcopy
from functools import cache
from typing import Callable, Mapping

import yaml
from selenium import webdriver
from selenium.common import TimeoutException, WebDriverException
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from progress import emit_progress

from manual_intervention import (
    BrowserControlRequested,
    HumanVerificationRequiredError,
    ManualInterventionRequest,
    TaskStopRequested,
    VisibleHandoffRequested,
    background_manual_config,
    is_headless_options,
    join_browser_options,
    split_browser_options,
    visible_browser_options,
)


DEFAULT_CONFIG = {
    'browser': 'Chrome',
    'driver_path': '',
    'browser_binary': '',
    'mode': 'video',
    'options': '--mute-audio --headless',
    'foreground_browser': False,
    'foreground_on_manual': True,
    'manual_handoff_enabled': True,
    'system_notifications': True,
    'login_wait_timeout': 300.0,
    'choose_correctly': True,
    'report_id': '',
    'delay_multiplier': 1.0,
    'parallelism': 2,
    'task_urls': [],
    'accounts': [],
    'default_account_id': '',
    'oobe_completed': False,
    'oobe_version': 0,
}


def _to_bool(value, default=False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {'1', 'true', 'yes', 'y', 'on'}


def normalize_config(values: Mapping | None = None) -> dict:
    config = deepcopy(DEFAULT_CONFIG)
    if values:
        config.update(dict(values))

    config['password'] = str(config.get('password', ''))
    config['username'] = str(config.get('username', ''))
    config['list_url'] = str(config.get('list_url', '')).strip()
    config['report_id'] = str(config.get('report_id', ''))
    config['choose_correctly'] = _to_bool(config.get('choose_correctly'), True)
    config['foreground_browser'] = _to_bool(
        config.get('foreground_browser'),
        False,
    )
    config['foreground_on_manual'] = _to_bool(
        config.get('foreground_on_manual'),
        True,
    )
    config['manual_handoff_enabled'] = _to_bool(
        config.get('manual_handoff_enabled'),
        True,
    )
    config['system_notifications'] = _to_bool(
        config.get('system_notifications'),
        True,
    )
    config['oobe_completed'] = _to_bool(
        config.get('oobe_completed'),
        False,
    )
    try:
        config['oobe_version'] = max(0, int(config.get('oobe_version', 0)))
    except (TypeError, ValueError):
        config['oobe_version'] = 0
    if not isinstance(config.get('accounts'), list):
        config['accounts'] = []

    try:
        config['delay_multiplier'] = max(0.1, float(config.get('delay_multiplier', 1.0)))
    except (TypeError, ValueError):
        logging.warning('配置文件中 delay_multiplier 不合法，将使用默认值 1.0')
        config['delay_multiplier'] = 1.0

    # Always scan from the first visible day. This intentionally ignores the
    # legacy setting so an older completed-looking course with a missed
    # checkpoint warning can never be skipped.
    config['day_to_start_on'] = 1

    try:
        config['parallelism'] = max(1, int(config.get('parallelism', 2)))
    except (TypeError, ValueError):
        logging.warning('配置文件中 parallelism 不合法，将使用默认值 2')
        config['parallelism'] = 2

    try:
        config['login_wait_timeout'] = max(
            10.0,
            float(config.get('login_wait_timeout', 300.0)),
        )
    except (TypeError, ValueError):
        logging.warning('配置文件中 login_wait_timeout 不合法，将使用默认值 300 秒')
        config['login_wait_timeout'] = 300.0

    raw_task_urls = config.get('task_urls', [])
    if isinstance(raw_task_urls, str):
        raw_task_urls = raw_task_urls.splitlines()
    if not isinstance(raw_task_urls, (list, tuple)):
        raw_task_urls = []
    config['task_urls'] = [
        str(url).strip() for url in raw_task_urls if str(url).strip()
    ]
    return config


@cache
def read_config() -> dict:
    """
    配置文件样例：
    # 修改时不要删掉冒号后的空格
    browser: 浏览器名称（首字母大写），如 Chrome, Edge 等
    driver_path: 浏览器驱动路径
    username: 用户名
    password: 密码
    list_url: 课程列表页面的链接
    # 浏览器启动时的参数，这里给了个静音
    options: --mute-audio
    # AutoEwt 模式，选填 watch（看课）/ test（做试卷）
    mode: watch
    """
    if not os.path.exists('config.yml'):
        logging.error('配置文件 config.yml 不存在，请检查！')
        raise FileNotFoundError('config.yml')
    with open('config.yml', encoding='utf-8') as f:
        config = normalize_config(yaml.load(f, yaml.FullLoader) or {})
        logging.info('成功读取到配置文件')
    return config


def clear_config_cache() -> None:
    read_config.cache_clear()


class AutoBase(ABC):
    def __init__(
        self,
        config: Mapping | None = None,
        stop_event: threading.Event | None = None,
        visible_handoff_event: threading.Event | None = None,
        foreground_request_event: threading.Event | None = None,
        manual_intervention_sink: Callable[[ManualInterventionRequest], None]
        | None = None,
    ):
        raw_config = normalize_config(
            config if config is not None else read_config()
        )
        # Preserve the user's headless intent while using a minimized normal
        # browser when manual handoff is enabled. A true headless session
        # cannot expose the already-mounted captcha later without losing it.
        self.config = background_manual_config(raw_config)
        self.stop_event = stop_event or threading.Event()
        self.visible_handoff_event = visible_handoff_event or threading.Event()
        self.foreground_request_event = foreground_request_event or threading.Event()
        self.manual_intervention_sink = manual_intervention_sink
        self.mode = self.config['mode']
        self.driver = None
        try:
            self.check_control_requests()
            self.driver = self.init_driver()
            self.token = self.login()
            if self.config.get('_auto_hide_after_manual'):
                self.hide_browser_window(refresh_current=True)
        except Exception:
            if self.driver:
                self.driver.quit()
            raise

    def run(self) -> None:
        self.finish_days_list()

    def init_driver(self) -> webdriver.Edge:
        browser = self.config['browser']
        options = getattr(webdriver, browser.lower()).options.Options()
        raw_options = str(self.config.get('options', ''))
        if is_headless_options(raw_options):
            arguments = split_browser_options(raw_options)
            if not any(
                item.lower() == '--window-size'
                or item.lower().startswith('--window-size=')
                for item in arguments
            ):
                arguments.append('--window-size=1365,900')
            launch_options = join_browser_options(arguments)
        else:
            launch_options = visible_browser_options(raw_options, browser)
        self.config['options'] = launch_options
        for argument in split_browser_options(launch_options):
            options.add_argument(argument)
        #Chrome相对路径
        browser_binary = str(self.config.get('browser_binary', '')).strip().strip('"')
        if browser_binary and os.path.exists(browser_binary):
            options.binary_location = browser_binary
        #进一步把Chromedriver与Chrome输出丢进垃圾桶里面
        service_class = getattr(webdriver, browser.lower()).service.Service
        service_kwargs = {
            'log_output': subprocess.DEVNULL,
            'popen_kw': {
                'creation_flags': getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            },
        }
        driver_path = str(self.config.get('driver_path', '')).strip().strip('"')
        if driver_path and os.path.exists(driver_path):
            service = service_class(driver_path, **service_kwargs)
        else:
            if driver_path:
                logging.info(
                    '未找到配置的 WebDriver，将尝试 Selenium Manager：%s',
                    driver_path,
                )
            service = service_class(**service_kwargs)
        driver = getattr(webdriver, browser)(service=service, options=options)
        self.driver = driver
        try:
            if self.config.get('_launch_hidden'):
                driver.minimize_window()
            else:
                driver.maximize_window()
        except Exception:
            if not self.config.get('_launch_hidden'):
                driver.set_window_size(1280, 900)
        driver.get(self.config['list_url'])
        driver.implicitly_wait(3)
        if self.config.get('foreground_browser'):
            self.bring_browser_to_front('刷课复测窗口')
        self.check_control_requests()
        return driver

    def bring_browser_to_front(
        self,
        reason: str = '需要人工验证',
        keep_topmost: bool = False,
    ) -> bool:
        """Restore and foreground this Selenium window on Windows."""
        if os.name != 'nt' or not self.driver:
            return False
        if self.is_headless:
            logging.warning('当前浏览器使用 headless 模式，无法显示人工验证窗口')
            return False

        original_title = None
        try:
            import ctypes
            from ctypes import wintypes

            original_title = self.driver.execute_script('return document.title;')
            session_id = str(getattr(self.driver, 'session_id', '') or '')[-12:]
            window_id = str(getattr(self.driver, 'current_window_handle', '') or '')[-12:]
            marker = '-'.join((
                'AutoEwt',
                reason,
                str(os.getpid()),
                session_id,
                window_id,
                uuid.uuid4().hex,
            ))
            self.driver.execute_script('document.title = arguments[0];', marker)
            user32 = ctypes.WinDLL('user32', use_last_error=True)
            handles = []
            enum_proc_type = ctypes.WINFUNCTYPE(
                wintypes.BOOL,
                wintypes.HWND,
                wintypes.LPARAM,
            )
            user32.EnumWindows.argtypes = [enum_proc_type, wintypes.LPARAM]
            user32.EnumWindows.restype = wintypes.BOOL
            user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
            user32.GetWindowTextLengthW.restype = ctypes.c_int
            user32.GetWindowTextW.argtypes = [
                wintypes.HWND,
                wintypes.LPWSTR,
                ctypes.c_int,
            ]
            user32.GetWindowTextW.restype = ctypes.c_int
            user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
            user32.ShowWindow.restype = wintypes.BOOL
            user32.SetWindowPos.argtypes = [
                wintypes.HWND,
                wintypes.HWND,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                wintypes.UINT,
            ]
            user32.SetWindowPos.restype = wintypes.BOOL
            user32.BringWindowToTop.argtypes = [wintypes.HWND]
            user32.BringWindowToTop.restype = wintypes.BOOL
            user32.SetForegroundWindow.argtypes = [wintypes.HWND]
            user32.SetForegroundWindow.restype = wintypes.BOOL
            user32.GetWindowThreadProcessId.argtypes = [
                wintypes.HWND,
                ctypes.POINTER(wintypes.DWORD),
            ]
            user32.GetWindowThreadProcessId.restype = wintypes.DWORD

            @enum_proc_type
            def collect_window(hwnd, _):
                length = user32.GetWindowTextLengthW(hwnd)
                if length <= 0:
                    return True
                title = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, title, length + 1)
                if marker in title.value:
                    handles.append(hwnd)
                    return False
                return True

            for _ in range(60):
                handles.clear()
                user32.EnumWindows(collect_window, 0)
                if handles:
                    break
                time.sleep(0.05)
            if not handles:
                logging.warning('未能定位需要置前的浏览器窗口，请检查任务栏')
                return False

            hwnd = handles[0]
            self._browser_hwnd = int(hwnd)
            swp_flags = 0x0001 | 0x0002 | 0x0040
            user32.ShowWindow(hwnd, 9)
            user32.SetWindowPos(hwnd, wintypes.HWND(-1), 0, 0, 0, 0, swp_flags)
            user32.BringWindowToTop(hwnd)
            focused = bool(user32.SetForegroundWindow(hwnd))
            process_id = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
            if not focused and process_id.value:
                command = (
                    '$shell=New-Object -ComObject WScript.Shell; '
                    f'exit ([int](-not $shell.AppActivate({process_id.value})))'
                )
                completed = subprocess.run(
                    [
                        'powershell.exe',
                        '-NoProfile',
                        '-NonInteractive',
                        '-WindowStyle',
                        'Hidden',
                        '-Command',
                        command,
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                    check=False,
                )
                focused = completed.returncode == 0
            try:
                import winsound

                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            except (ImportError, RuntimeError):
                pass
            if keep_topmost:
                self._manual_topmost_hwnd = int(hwnd)
            else:
                user32.SetWindowPos(
                    hwnd,
                    wintypes.HWND(-2),
                    0,
                    0,
                    0,
                    0,
                    swp_flags,
                )
            if not focused:
                logging.warning('浏览器已恢复到桌面，但 Windows 未授予输入焦点，请从任务栏打开')
            return True
        except Exception as exc:
            logging.warning('浏览器窗口置前失败：%s', exc)
            return False
        finally:
            if original_title is not None:
                try:
                    self.driver.execute_script(
                        'document.title = arguments[0];',
                        original_title,
                    )
                except Exception:
                    pass

    def release_browser_topmost(self) -> None:
        hwnd = getattr(self, '_manual_topmost_hwnd', None)
        self._manual_topmost_hwnd = None
        if os.name != 'nt' or not hwnd:
            return
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.WinDLL('user32', use_last_error=True)
            user32.SetWindowPos.argtypes = [
                wintypes.HWND,
                wintypes.HWND,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                wintypes.UINT,
            ]
            user32.SetWindowPos.restype = wintypes.BOOL
            user32.SetWindowPos(
                wintypes.HWND(hwnd),
                wintypes.HWND(-2),
                0,
                0,
                0,
                0,
                0x0001 | 0x0002 | 0x0040,
            )
        except Exception:
            pass

    def _locate_current_browser_hwnd(self) -> int | None:
        """Locate the top-level HWND that owns Selenium's current page."""
        if os.name != 'nt' or not self.driver or self.is_headless:
            return None
        original_title = None
        try:
            import ctypes
            from ctypes import wintypes

            original_title = self.driver.execute_script('return document.title;')
            marker = '-'.join((
                'AutoEwtHidden',
                str(os.getpid()),
                str(getattr(self.driver, 'session_id', '') or '')[-12:],
                str(getattr(self.driver, 'current_window_handle', '') or '')[-12:],
                uuid.uuid4().hex,
            ))
            self.driver.execute_script('document.title = arguments[0];', marker)
            user32 = ctypes.WinDLL('user32', use_last_error=True)
            enum_proc_type = ctypes.WINFUNCTYPE(
                wintypes.BOOL,
                wintypes.HWND,
                wintypes.LPARAM,
            )
            user32.EnumWindows.argtypes = [enum_proc_type, wintypes.LPARAM]
            user32.EnumWindows.restype = wintypes.BOOL
            user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
            user32.GetWindowTextLengthW.restype = ctypes.c_int
            user32.GetWindowTextW.argtypes = [
                wintypes.HWND,
                wintypes.LPWSTR,
                ctypes.c_int,
            ]
            user32.GetWindowTextW.restype = ctypes.c_int
            handles = []

            @enum_proc_type
            def collect_window(hwnd, _):
                length = user32.GetWindowTextLengthW(hwnd)
                if length <= 0:
                    return True
                title = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, title, length + 1)
                if marker in title.value:
                    handles.append(int(hwnd))
                    return False
                return True

            for _ in range(20):
                handles.clear()
                user32.EnumWindows(collect_window, 0)
                if handles:
                    break
                time.sleep(0.025)
            if handles:
                self._browser_hwnd = handles[0]
                return handles[0]
        except Exception as exc:
            logging.debug('定位后台浏览器窗口失败：%s', exc)
        finally:
            if original_title is not None:
                try:
                    self.driver.execute_script(
                        'document.title = arguments[0];',
                        original_title,
                    )
                except Exception:
                    pass
        return None

    def hide_browser_window(self, *, refresh_current: bool = False) -> bool:
        """Hide the current automatic browser window without ending its session."""
        if not self.config.get('_auto_hide_after_manual'):
            return False
        hwnd = None
        if refresh_current or not getattr(self, '_browser_hwnd', None):
            hwnd = self._locate_current_browser_hwnd()
        hwnd = hwnd or getattr(self, '_browser_hwnd', None)
        if os.name != 'nt' or not hwnd:
            return False
        self.release_browser_topmost()
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.WinDLL('user32', use_last_error=True)
            user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
            user32.ShowWindow.restype = wintypes.BOOL
            user32.ShowWindow(wintypes.HWND(hwnd), 0)
            logging.info('浏览器窗口已转入后台（原浏览器会话仍在运行）')
            return True
        except Exception as exc:
            logging.warning('隐藏人工浏览器窗口失败：%s', exc)
            return False

    @property
    def is_headless(self) -> bool:
        return is_headless_options(str(self.config.get('options', '')))

    @property
    def logical_headless(self) -> bool:
        """Return the user's headless intent, even for a hidden handoff session."""
        return bool(
            self.config.get('_logical_headless', self.is_headless)
        )

    def check_control_requests(self) -> None:
        """Handle UI requests from the Selenium-owning worker thread."""
        stop_event = getattr(self, 'stop_event', None)
        if stop_event is not None and stop_event.is_set():
            raise TaskStopRequested('任务已由用户停止')

        visible_handoff_event = getattr(self, 'visible_handoff_event', None)
        if visible_handoff_event is not None and visible_handoff_event.is_set():
            if self.is_headless:
                raise VisibleHandoffRequested('用户请求打开当前任务预览')
            if self.driver:
                visible_handoff_event.clear()
                self.bring_browser_to_front('用户预览运行中的任务')

        foreground_request_event = getattr(self, 'foreground_request_event', None)
        if (
            foreground_request_event is not None
            and foreground_request_event.is_set()
            and self.driver
        ):
            foreground_request_event.clear()
            if not self.is_headless:
                # A user preview explicitly promotes a temporary manual window
                # to a persistent visible session.
                self.config['_auto_hide_after_manual'] = False
                self.config['_launch_hidden'] = False
                self.bring_browser_to_front('用户预览运行中的任务')

    def emit_manual_intervention(
        self,
        kind: str,
        reason: str,
        phase: str = 'required',
    ) -> None:
        sink = getattr(self, 'manual_intervention_sink', None)
        if not sink:
            return
        try:
            sink(ManualInterventionRequest(
                kind=kind,
                reason=reason,
                phase=phase,
                headless=self.logical_headless,
            ))
        except Exception:
            logging.exception('人工介入状态通知失败')

    def login(self) -> str:
        """
        登录
        :return: token
        """
        logging.info('登录账号……')
        token = self._token_cookie()
        if token:
            return token
        wait = WebDriverWait(self.driver, 20)
        try:
            username = wait.until(
                EC.presence_of_element_located((By.ID, 'login__password_userName'))
            )
        except TimeoutException:
            # 已有登录态或目标页未要求登录时直接继续。
            token = self._token_cookie()
            if token:
                return token
            raise
        password = wait.until(
            EC.presence_of_element_located((By.ID, 'login__password_password'))
        )
        username.clear()
        username.send_keys(self.config['username'])
        password.clear()
        password.send_keys(self.config['password'])
        try:
            # 定位 privacy__agreement 区域内的 checkbox
            agreement_checkbox = self.driver.find_element(
                By.CSS_SELECTOR,
                '.privacy__agreement .ant-checkbox-input'
            )
            # 如果尚未勾选，则点击勾选
            if not agreement_checkbox.is_selected():
                self.click(agreement_checkbox)
                logging.info('已自动勾选用户协议')
            else:
                logging.info('用户协议已处于勾选状态，跳过')
        except Exception as e:
            logging.warning(f'勾选用户协议复选框失败: {e}')
        self.driver.find_element(By.CLASS_NAME, 'ant-btn-block').submit()
        try:
            time.sleep(1 * self.config.get('delay_multiplier'))
            # 查找弹窗中的"同意并继续"按钮
            agree_btn = self.driver.find_element(
                By.CSS_SELECTOR,
                '.privacy__agreement__modal-btn .ant-btn-primary'
            )
            self.click(agree_btn)
            logging.info('已自动点击弹窗中的「同意并继续」按钮')
        except Exception:
            logging.info('未出现协议确认弹窗，跳过')

        return self._wait_for_login_token()

    def _wait_for_login_token(self) -> str:
        """Wait for real login completion while leaving verification to the user."""
        started_at = time.monotonic()
        deadline = started_at + self.config.get('login_wait_timeout', 300.0)
        manual_notified = False
        while True:
            self.check_control_requests()
            token = self._token_cookie()
            if token:
                if manual_notified:
                    self.hide_browser_window()
                    self.emit_manual_intervention(
                        'login_verification',
                        '登录验证已完成',
                        phase='resolved',
                    )
                return token

            now = time.monotonic()
            if now >= deadline:
                raise TimeoutException('等待登录或人工验证超时，未取得有效登录状态')
            manual_visible = self._manual_login_verification_visible()
            if (
                manual_visible
                and self.is_headless
                and not self.config.get('_background_manual_session')
            ):
                raise HumanVerificationRequiredError(
                    '登录页面出现验证码或安全验证',
                    kind='login_verification',
                )
            if not manual_notified and manual_visible:
                logging.warning(
                    '登录尚未完成；如页面显示验证码或短信验证，请在浏览器中手动完成'
                )
                self.emit_manual_intervention(
                    'login_verification',
                    '登录页面出现验证码或安全验证',
                )
                if self.config.get('foreground_on_manual'):
                    self.bring_browser_to_front('登录需要人工验证')
                manual_notified = True
            time.sleep(0.25)

    def _manual_login_verification_visible(self) -> bool:
        try:
            return bool(self.driver.execute_script(
                """
                const selector = "#captcha, [class*='captcha'], iframe[src*='captcha'], "
                    + "[class*='verify'], [class*='slider']";
                const text = ((document.body && document.body.innerText) || '')
                    .replace(/\\s+/g, ' ');
                const visible = (element) => {
                    if (!element) return false;
                    const style = getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    return style.display !== 'none'
                        && style.visibility !== 'hidden'
                        && Number(style.opacity || 1) > 0
                        && rect.width > 20
                        && rect.height > 20;
                };
                return Array.from(document.querySelectorAll(selector)).some(visible)
                    || /验证码|短信验证|拖动滑块|安全验证/.test(text);
                """
            ))
        except WebDriverException:
            return False

    def _token_cookie(self) -> str | None:
        cookies = self.driver.get_cookies()
        for cookie in cookies:
            if cookie['name'] == 'token':
                return cookie['value']
        return None

    def click(self, btn: WebElement) -> None:
        """
        通过调用 .click(); 事件点击一个按钮，这比直接在 Python 中 .click() 更稳定
        对于可能被遮挡而无法点击的按钮，应使用此方法
        :param btn: 要点击的按钮
        :return: None
        """
        self.check_control_requests()
        self.driver.execute_script('arguments[0].click();', btn)

    def click_and_switch(self, btn: WebElement) -> None:
        """
        点击按钮并切换到新页面
        :param btn: 要点击的按钮
        """
        original_handle = self.driver.current_window_handle
        original_handles = set(self.driver.window_handles)
        original_url = self.driver.current_url
        self._return_page = (original_handle, original_url)
        self.click(btn)
        timeout = max(4.0, 8.0 * self.config.get('delay_multiplier'))
        try:
            WebDriverWait(self.driver, timeout).until(
                lambda driver: bool(set(driver.window_handles) - original_handles)
                or driver.current_url != original_url
            )
        except TimeoutException:
            logging.warning('课程页面打开较慢，继续在当前窗口检查')

        new_handles = set(self.driver.window_handles) - original_handles
        if new_handles:
            self.driver.switch_to.window(new_handles.pop())
        elif original_handle in self.driver.window_handles:
            self.driver.switch_to.window(original_handle)
        hidden = self.hide_browser_window(refresh_current=True)
        time.sleep(2 * self.config.get('delay_multiplier'))
        if self.config.get('_auto_hide_after_manual') and not hidden:
            self.hide_browser_window(refresh_current=True)

    def close_and_switch(self) -> None:
        """关闭当前页面并返回到首页"""
        handles = self.driver.window_handles
        if len(handles) > 1:
            current = self.driver.current_window_handle
            self.driver.close()
            remaining = [handle for handle in handles if handle != current]
            return_handle = getattr(self, '_return_page', (None, None))[0]
            self.driver.switch_to.window(
                return_handle if return_handle in remaining else remaining[0]
            )
        else:
            try:
                self.driver.back()
                return_url = getattr(self, '_return_page', (None, None))[1]
                if return_url:
                    try:
                        WebDriverWait(self.driver, 10).until(
                            lambda driver: driver.current_url == return_url
                        )
                    except TimeoutException:
                        logging.warning('返回课程列表超时，直接恢复原任务页面')
                        self.driver.get(return_url)
            except WebDriverException:
                return
        hidden = self.hide_browser_window(refresh_current=True)
        time.sleep(1 * self.config.get('delay_multiplier'))
        if self.config.get('_auto_hide_after_manual') and not hidden:
            self.hide_browser_window(refresh_current=True)

    def finish_days_list(self) -> None:
        """完成所有天"""
        self._progress_day_scope = 'day:unknown'
        time.sleep(5 * self.config.get('delay_multiplier'))
        selector = 'li[data-active="true"], li[data-active="false"]'
        initial_days = self.driver.find_elements(By.CSS_SELECTOR, selector)
        day_count = len(initial_days)
        logging.info(f'一共有 {day_count} 天的任务')
        emit_progress(
            '日期遍历',
            0,
            day_count,
            '天',
            finished=day_count == 0,
            kind='days',
            scope='task',
        )
        try:
            for i in range(day_count):
                self.check_control_requests()
                # Completing a day can re-render the whole list. Re-query every index
                # instead of carrying stale WebElements across days.
                days = self.driver.find_elements(By.CSS_SELECTOR, selector)
                if i >= len(days):
                    raise StaleElementReferenceException(
                        f'日期列表刷新后缺少第 {i + 1} 天'
                    )
                try:
                    day_label = ' '.join(
                        (days[i].get_attribute('innerText') or days[i].text or '').split()
                    )
                except StaleElementReferenceException:
                    day_label = ''
                logging.info(f'================ 第 {i + 1} / {day_count} 天 ================')
                logging.info(
                    '正在从第一天逐日扫描：页面顺序第 %s 天%s',
                    i + 1,
                    f'（{day_label}）' if day_label else '',
                )
                self._progress_day_scope = f'day:{i + 1}'
                self.finish_a_day(days[i])
                emit_progress(
                    '日期遍历',
                    i + 1,
                    day_count,
                    '天',
                    finished=i + 1 >= day_count,
                    kind='days',
                    scope='task',
                )
        finally:
            self._progress_day_scope = 'day:unknown'

    @abstractmethod
    def finish_a_day(self, day: WebElement) -> None:
        """
        完成一天的任务
        :param day: 该天在网页上的标签
        """
        ...
