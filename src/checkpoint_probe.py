"""Capture the real checkpoint DOM without handling or resuming playback.

The probe reads the normal ``config.yml`` but never logs credentials. It opens
one configured course, starts playback once through the player's visible play
button, then becomes read-only. The first pause or newly visible modal/overlay
is persisted as JSON, HTML, and a screenshot before the browser is closed.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from selenium.common import StaleElementReferenceException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from auto_base import clear_config_cache, read_config
from auto_video.auto_video import AutoVideo, compact_text
from runner import setup_logging


DAY_SELECTOR = 'li[data-active="true"], li[data-active="false"]'
COURSE_ACTION_RE = (
    '已学完',
    '去学习',
    '开始学习',
    '继续学习',
    '播放',
    '学',
)
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / 'checkpoint_probe_output'
PLAY_START_GRACE_SECONDS = 8.0


OBSERVER_SCRIPT = r"""
const isVisible = (element) => {
  if (!(element instanceof Element)) return false;
  const style = getComputedStyle(element);
  const rect = element.getBoundingClientRect();
  return style.display !== 'none'
    && style.visibility !== 'hidden'
    && style.opacity !== '0'
    && rect.width > 0
    && rect.height > 0;
};
const compact = (value) => String(value || '').replace(/\s+/g, ' ').trim();
const describe = (element, source) => {
  const style = getComputedStyle(element);
  const rect = element.getBoundingClientRect();
  const text = compact(element.innerText || element.textContent).slice(0, 2000);
  const role = compact(element.getAttribute('role'));
  const className = compact(element.className);
  const actionText = /我知道了|点击通过检查|通过检查|继续播放|继续学习|跳过|确定|确认/.test(text);
  const largeFloatingLayer = ['fixed', 'absolute'].includes(style.position)
    && rect.width * rect.height >= innerWidth * innerHeight * 0.08
    && Number(style.zIndex) >= 10;
  const modalLike = role === 'dialog'
    || element.getAttribute('aria-modal') === 'true'
    || /modal|dialog|popup|overlay|mask|checkpoint|question|inspect|risk/i.test(className)
    || actionText
    || largeFloatingLayer;
  return {
    at: new Date().toISOString(),
    source,
    tag: element.tagName,
    id: element.id || '',
    className,
    role,
    text,
    modalLike,
    rect: {x: rect.x, y: rect.y, width: rect.width, height: rect.height},
    style: {
      display: style.display,
      visibility: style.visibility,
      opacity: style.opacity,
      position: style.position,
      zIndex: style.zIndex
    },
    outerHTML: element.outerHTML
  };
};
const recordTree = (node, source) => {
  if (!(node instanceof Element)) return;
  const candidates = [node, ...node.querySelectorAll('*')];
  for (const element of candidates) {
    if (!isVisible(element)) continue;
    const entry = describe(element, source);
    if (!entry.text && !entry.modalLike) continue;
    window.__autoewtCheckpointProbe.events.push(entry);
  }
};
if (window.__autoewtCheckpointProbe && window.__autoewtCheckpointProbe.observer) {
  window.__autoewtCheckpointProbe.observer.disconnect();
}
window.__autoewtCheckpointProbe = {
  installedAt: new Date().toISOString(),
  events: [],
  observer: null
};
const observer = new MutationObserver((mutations) => {
  for (const mutation of mutations) {
    if (mutation.type === 'childList') {
      for (const node of mutation.addedNodes) recordTree(node, 'added');
    } else if (mutation.type === 'attributes' && isVisible(mutation.target)) {
      const entry = describe(mutation.target, `attribute:${mutation.attributeName}`);
      if (entry.text || entry.modalLike) {
        window.__autoewtCheckpointProbe.events.push(entry);
      }
    }
  }
});
observer.observe(document.body || document.documentElement, {
  childList: true,
  subtree: true,
  attributes: true,
  attributeFilter: ['class', 'style', 'hidden', 'aria-hidden', 'aria-modal', 'role']
});
window.__autoewtCheckpointProbe.observer = observer;
return window.__autoewtCheckpointProbe.installedAt;
"""


SNAPSHOT_SCRIPT = r"""
const compact = (value) => String(value || '').replace(/\s+/g, ' ').trim();
const visible = (element) => {
  if (!element) return false;
  const style = getComputedStyle(element);
  const rect = element.getBoundingClientRect();
  return style.display !== 'none'
    && style.visibility !== 'hidden'
    && style.opacity !== '0'
    && rect.width > 0
    && rect.height > 0;
};
const serialize = (element) => {
  const rect = element.getBoundingClientRect();
  return {
    tag: element.tagName,
    id: element.id || '',
    className: compact(element.className),
    role: compact(element.getAttribute('role')),
    text: compact(element.innerText || element.textContent).slice(0, 2000),
    rect: {x: rect.x, y: rect.y, width: rect.width, height: rect.height},
    outerHTML: element.outerHTML
  };
};
const events = window.__autoewtCheckpointProbe
  ? window.__autoewtCheckpointProbe.events.slice()
  : [];
if (window.__autoewtCheckpointProbe) {
  window.__autoewtCheckpointProbe.events.length = 0;
}
const modalSelector = [
  '[role="dialog"]', '[aria-modal="true"]', '.ant-modal', '.ant-modal-mask',
  '[class*="modal"]', '[class*="dialog"]', '[class*="popup"]',
  '[class*="overlay"]', '[class*="mask"]'
].join(',');
const overlays = Array.from(document.querySelectorAll(modalSelector))
  .filter(visible)
  .map(serialize);
const buttons = Array.from(document.querySelectorAll(
  'button, a, [role="button"], .ant-btn, [class*="btn"], [class*="button"]'
)).filter(visible).map(serialize).filter((item) => item.text).slice(0, 200);
const video = document.querySelector('video');
const bodyText = compact(document.body && document.body.innerText);
return {
  at: new Date().toISOString(),
  url: location.href,
  title: document.title,
  video: video ? {
    paused: Boolean(video.paused),
    ended: Boolean(video.ended),
    currentTime: Number(video.currentTime || 0),
    duration: Number(video.duration || 0),
    readyState: Number(video.readyState || 0),
    networkState: Number(video.networkState || 0)
  } : null,
  events,
  overlays,
  buttons,
  riskTextVisible: bodyText.includes('检测到网络不稳定或开启了第三方辅助工具')
    || bodyText.includes('学习数据无法被记录')
};
"""


def select_day(auto: AutoVideo, day_text: str) -> str:
    for _ in range(3):
        days = auto.driver.find_elements(By.CSS_SELECTOR, DAY_SELECTOR)
        try:
            for day in days:
                label = compact_text(day.get_attribute('innerText') or day.text)
                if day_text not in label:
                    continue
                auto.click(day)
                WebDriverWait(auto.driver, 20).until(
                    lambda driver: any(
                        day_text in compact_text(item.get_attribute('innerText'))
                        and item.get_attribute('data-active') == 'true'
                        for item in driver.find_elements(By.CSS_SELECTOR, DAY_SELECTOR)
                    )
                )
                time.sleep(max(1.0, auto.config['delay_multiplier']))
                return label
        except StaleElementReferenceException:
            continue
    raise LookupError(f'未找到日期“{day_text}”')


def _action_score(element: WebElement) -> tuple[int, int]:
    text = compact_text(element.get_attribute('innerText') or element.text)
    class_name = element.get_attribute('class') or ''
    exact = next((index for index, label in enumerate(COURSE_ACTION_RE) if text == label), 99)
    legacy = 0 if 'btn-AoqsA' in class_name else 1
    return exact, legacy


def find_course_action(auto: AutoVideo, course_text: str) -> WebElement:
    literal = json.dumps(course_text, ensure_ascii=False)
    candidates = auto.driver.execute_script(
        r"""
const target = arguments[0];
const compact = (value) => String(value || '').replace(/\s+/g, ' ').trim();
const visible = (element) => {
  const style = getComputedStyle(element);
  const rect = element.getBoundingClientRect();
  return style.display !== 'none' && style.visibility !== 'hidden'
    && rect.width > 0 && rect.height > 0;
};
const elementText = (element) => compact(element.innerText || element.textContent);
const titleNodes = Array.from(document.querySelectorAll('body *'))
  .filter((element) => visible(element) && elementText(element).includes(target))
  .sort((left, right) => elementText(left).length - elementText(right).length);
const actions = [];
for (const title of titleNodes) {
  let container = title;
  for (let depth = 0; container && container !== document.body && depth < 12; depth += 1) {
    const text = elementText(container);
    if (text.length > 2000) break;
    const descendants = Array.from(container.querySelectorAll(
      "button, a, [role='button'], div[class*='btn'], span[class*='btn']"
    )).filter((element) => {
      const actionText = compact(element);
      return visible(element) && actionText.length <= 80
        && /^(已学完|去学习|开始学习|继续学习|播放|学)$/.test(actionText);
    });
    if (descendants.length) {
      actions.push(...descendants);
      break;
    }
    container = container.parentElement;
  }
}
return Array.from(new Set(actions));
""",
        course_text,
    )
    if not candidates:
        raise LookupError(f'未找到课程“{course_text}”的播放/重看入口（{literal}）')
    return min(candidates, key=_action_score)


def start_playback_once(driver) -> None:
    video = WebDriverWait(driver, 30).until(
        EC.presence_of_element_located((By.TAG_NAME, 'video'))
    )
    driver.execute_script(OBSERVER_SCRIPT)
    play_buttons = driver.find_elements(By.CLASS_NAME, 'vjs-big-play-button')
    visible = [button for button in play_buttons if button.is_displayed()]
    if not visible:
        raise LookupError('未找到可见的播放器大播放按钮；为避免脚本恢复播放，探针已停止')
    visible[0].click()
    logging.info('已原生点击一次播放按钮；后续探针只读，不处理或恢复任何暂停')
    WebDriverWait(driver, 10).until(
        lambda _: float(video.get_attribute('currentTime') or 0) > 0
        or not bool(driver.execute_script('return arguments[0].paused;', video))
        or bool(driver.execute_script(
            'return window.__autoewtCheckpointProbe.events.length;',
        ))
    )


def capture_reason(
    snapshot: dict[str, Any],
    *,
    playback_started: bool,
    seconds_after_click: float,
) -> str | None:
    if snapshot.get('riskTextVisible'):
        return 'playback-risk-warning'
    events = snapshot.get('events') or []
    if any(event.get('modalLike') for event in events):
        return 'new-modal-or-overlay'
    video = snapshot.get('video') or {}
    if video.get('paused') and playback_started and not video.get('ended'):
        return 'video-paused'
    if video.get('paused') and seconds_after_click >= PLAY_START_GRACE_SECONDS:
        return 'playback-never-started'
    return None


def save_capture(driver, output_dir: Path, payload: dict[str, Any]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    stem = output_dir / f'checkpoint-{stamp}'
    try:
        driver.execute_script(
            "const video = document.querySelector('video'); if (video) video.pause();"
        )
    except Exception:
        logging.exception('暂停现场视频失败，仍继续保存捕获数据')

    (stem.with_suffix('.json')).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    (stem.with_suffix('.html')).write_text(driver.page_source, encoding='utf-8')
    driver.save_screenshot(str(stem.with_suffix('.png')))
    return stem


def probe(auto: AutoVideo, timeout: float, output_dir: Path) -> Path:
    started_at = time.monotonic()
    playback_started = False
    accumulated_events: list[dict[str, Any]] = []
    while time.monotonic() - started_at < timeout:
        snapshot = auto.driver.execute_script(SNAPSHOT_SCRIPT)
        events = snapshot.get('events') or []
        accumulated_events.extend(events)
        video = snapshot.get('video') or {}
        if not video.get('paused') and float(video.get('currentTime') or 0) > 0:
            playback_started = True
        reason = capture_reason(
            snapshot,
            playback_started=playback_started,
            seconds_after_click=time.monotonic() - started_at,
        )
        if reason:
            payload = dict(snapshot)
            payload['reason'] = reason
            payload['playbackStarted'] = playback_started
            payload['events'] = accumulated_events
            stem = save_capture(auto.driver, output_dir, payload)
            logging.warning('已捕获并安全停止：%s', reason)
            return stem
        time.sleep(0.2)
    raise TimeoutException(f'{timeout:.0f} 秒内未捕获到暂停或新弹层')


def run(day_text: str, course_text: str, timeout: float, output_dir: Path) -> int:
    setup_logging('log', use_tqdm_handler=False)
    clear_config_cache()
    auto = None
    try:
        auto = AutoVideo(config=read_config())
        selected_day = select_day(auto, day_text)
        logging.info('真实账号已选择日期：%s', selected_day)
        action = find_course_action(auto, course_text)
        logging.info('已定位异常课程：%s', course_text)
        auto.click_and_switch(action)
        start_playback_once(auto.driver)
        stem = probe(auto, timeout, output_dir)
        logging.info('捕获文件：%s.[json|html|png]', stem)
        return 0
    except (LookupError, TimeoutException) as exc:
        logging.error('检查点探针未完成：%s', exc)
        return 2
    finally:
        if auto and auto.driver:
            auto.driver.quit()


def main() -> int:
    parser = argparse.ArgumentParser(
        description='只读捕获真实视频检查点 DOM；不会处理弹窗或恢复暂停。'
    )
    parser.add_argument('--day', default='8月4日')
    parser.add_argument('--course', default='频率与概率')
    parser.add_argument('--timeout', type=float, default=1200.0)
    parser.add_argument('--output-dir', type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    return run(args.day, args.course, max(1.0, args.timeout), args.output_dir)


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    raise SystemExit(main())
