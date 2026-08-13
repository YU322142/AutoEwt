const assert = require("node:assert/strict");
const test = require("node:test");

const discovery = require("../app/src/main/assets/autoewt/content-script.js");

class FakeElement {
  constructor(text, options = {}) {
    this.innerText = text;
    this.textContent = text;
    this.className = options.className || "";
    this.parentElement = options.parentElement || null;
    this.style = {};
    this.attributes = Object.assign({}, options.attributes);
    this.children = options.children || [];
    this.rect = Object.assign({ left: 0, top: 0, width: 200, height: 100 }, options.rect);
    this.children.forEach((child) => {
      child.parentElement = this;
    });
  }

  closest() {
    return this;
  }

  contains(other) {
    let current = other;
    while (current) {
      if (current === this) {
        return true;
      }
      current = current.parentElement;
    }
    return false;
  }

  getAttribute(name) {
    return Object.prototype.hasOwnProperty.call(this.attributes, name)
      ? this.attributes[name]
      : null;
  }

  getBoundingClientRect() {
    return Object.assign({}, this.rect, {
      right: this.rect.left + this.rect.width,
      bottom: this.rect.top + this.rect.height
    });
  }

  querySelectorAll() {
    return this.children;
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }
}

class CheckpointElement extends FakeElement {
  constructor(text, options = {}) {
    super(text, options);
    this.tagName = String(options.tagName || "div").toUpperCase();
    this.id = options.id || "";
    this.clickCount = 0;
    this.dispatchCount = 0;
    this.onClick = options.onClick || null;
  }

  click() {
    this.clickCount += 1;
    if (typeof this.onClick === "function") {
      this.onClick();
    }
  }

  closest(selector) {
    let current = this;
    while (current) {
      if (current.matches && current.matches(selector)) {
        return current;
      }
      current = current.parentElement;
    }
    return null;
  }

  dispatchEvent() {
    this.dispatchCount += 1;
    return true;
  }

  getAttribute(name) {
    if (name === "class") {
      return this.className;
    }
    if (name === "id") {
      return this.id || null;
    }
    return super.getAttribute(name);
  }

  matches(selector) {
    return String(selector || "")
      .split(",")
      .some((part) => matchesCheckpointSelector(this, part.trim()));
  }

  querySelectorAll(selector) {
    const descendants = [];
    const visit = (element) => {
      element.children.forEach((child) => {
        descendants.push(child);
        visit(child);
      });
    };
    visit(this);
    return descendants.filter((element) => element.matches && element.matches(selector));
  }

  scrollIntoView() {}
}

function matchesCheckpointSelector(element, selector) {
  if (!selector) {
    return false;
  }
  const tagName = String(element.tagName || "").toLowerCase();
  const className = String(element.className || "");
  const classNames = className.split(/\s+/).filter(Boolean);
  if (/^[a-z][a-z0-9-]*$/i.test(selector)) {
    return tagName === selector.toLowerCase();
  }
  if (selector.startsWith("#")) {
    return element.id === selector.slice(1);
  }
  if (selector.startsWith(".")) {
    return classNames.includes(selector.slice(1));
  }
  const classContains = selector.match(/^\[class\*=['"]([^'"]+)['"]\]$/);
  if (classContains) {
    return className.includes(classContains[1]);
  }
  const attribute = selector.match(/^(?:([a-z][a-z0-9-]*))?\[([^\]=*]+)(\*=|=)?['"]?([^'"\]]*)['"]?\]$/i);
  if (attribute) {
    if (attribute[1] && tagName !== attribute[1].toLowerCase()) {
      return false;
    }
    const actual = element.getAttribute(attribute[2]);
    if (!attribute[3]) {
      return actual !== null;
    }
    if (attribute[3] === "*=") {
      return String(actual || "").includes(attribute[4]);
    }
    return String(actual || "") === attribute[4];
  }
  return false;
}

function installDom(elements, options = {}) {
  const body = new FakeElement(options.bodyText || "", {
    rect: { left: 0, top: 0, width: 1280, height: 720 }
  });
  global.location = {
    href: options.url || "https://teacher.ewt360.com/ewtbend/bend/index/index.html#/student/homework"
  };
  global.document = {
    body,
    documentElement: body,
    querySelectorAll(selector) {
      if (options.selectorMap && Object.prototype.hasOwnProperty.call(options.selectorMap, selector)) {
        return options.selectorMap[selector];
      }
      return elements;
    }
  };
  global.window = {
    innerWidth: 1280,
    innerHeight: 720,
    devicePixelRatio: 1,
    getComputedStyle() {
      return { display: "block", visibility: "visible" };
    }
  };
}

function installCheckpointDom(elements, options = {}) {
  const body = new CheckpointElement(options.bodyText || "", {
    tagName: "body",
    rect: { left: 0, top: 0, width: 1280, height: 720 }
  });
  const allElements = [];
  const visit = (element) => {
    allElements.push(element);
    element.children.forEach(visit);
  };
  elements.forEach((element) => {
    if (!element.parentElement) {
      element.parentElement = body;
    }
    visit(element);
  });
  global.location = {
    href: options.url || "https://teacher.ewt360.com/ewtbend/bend/index/index.html#/homework/play-videos",
    pathname: "/ewtbend/bend/index/index.html",
    hash: "#/homework/play-videos"
  };
  discovery.setCheckpointTestConfig(options.config || {});
  global.history = {
    backCount: 0,
    back() {
      this.backCount += 1;
    }
  };
  global.document = {
    body,
    documentElement: body,
    readyState: "complete",
    title: "checkpoint test",
    visibilityState: "visible",
    addEventListener() {},
    elementFromPoint() {
      return options.pointElement || null;
    },
    querySelector(selector) {
      if (selector === "video") {
        return options.video || null;
      }
      return allElements.find((element) => element.matches(selector)) || null;
    },
    querySelectorAll(selector) {
      if (selector === "body *") {
        return allElements;
      }
      if (selector === "video") {
        return options.video ? [options.video] : [];
      }
      return allElements.filter((element) => element.matches(selector));
    }
  };
  global.window = {
    innerWidth: 1280,
    innerHeight: 720,
    devicePixelRatio: 1,
    screen: { width: 1280, height: 720 },
    getComputedStyle(element) {
      return {
        cursor: element && element.attributes && element.attributes.cursor || "default",
        display: "block",
        visibility: "visible"
      };
    }
  };
  global.MouseEvent = class MouseEvent {
    constructor(type, init) {
      this.type = type;
      Object.assign(this, init);
    }
  };
  global.PointerEvent = class PointerEvent extends global.MouseEvent {};
}

function checkpointVideo() {
  return {
    currentTime: 20,
    duration: 120,
    ended: false,
    muted: false,
    paused: false,
    pauseCount: 0,
    playCount: 0,
    pause() {
      this.paused = true;
      this.pauseCount += 1;
    },
    play() {
      this.playCount += 1;
      return Promise.resolve();
    }
  };
}

test.afterEach(() => {
  delete global.document;
  delete global.history;
  delete global.location;
  delete global.MouseEvent;
  delete global.PointerEvent;
  delete global.window;
  discovery.resetCheckpointTestState();
  discovery.resetDiscoveryTestState();
});

test("normalizes task status tabs with optional counters", () => {
  assert.equal(discovery.discoveryFilterLabel("进行中1份"), "进行中");
  assert.equal(discovery.discoveryFilterLabel("进行中 ( 12 份 )"), "进行中");
  assert.equal(discovery.discoveryFilterLabel("未开始0份"), "未开始");
  assert.equal(discovery.discoveryFilterLabel("已截止"), "已截止");
  assert.equal(discovery.discoveryFilterLabel("进行中1份 未开始 已截止"), "");
});

test("recognizes both task list routes without treating holiday details as a list", () => {
  assert.equal(discovery.discoveryPageKind("https://host/#/student/homework"), "standard");
  assert.equal(discovery.discoveryPageKind("https://host/#/student/homework?tab=doing"), "standard");
  assert.equal(discovery.discoveryPageKind("https://host/#/holiday/student/home?sceneId=summer"), "holiday");
  assert.equal(
    discovery.discoveryPageKind("https://host/#/holiday/student-task-overview?homeworkId=12345678"),
    ""
  );
  assert.equal(
    discovery.discoverySourcePageKind("https://host/#/holiday/student-task-overview?homeworkId=12345678"),
    "holiday"
  );

  const fromStandard = discovery.discoveryTargetUrls("https://host/#/student/homework", []);
  assert.match(fromStandard[0], /#\/student\/homework$/);
  assert.match(fromStandard[1], /#\/holiday\/student\/home$/);

  const fromHolidayDetail = discovery.discoveryTargetUrls(
    "https://host/#/holiday/student-task-overview?homeworkId=12345678",
    []
  );
  assert.match(fromHolidayDetail[0], /#\/holiday\/student\/home$/);
  assert.match(fromHolidayDetail[1], /#\/student\/homework$/);
});

test("normalizes holiday task tabs with optional counters", () => {
  assert.equal(discovery.discoveryHolidayFilterLabel("常规任务"), "常规任务");
  assert.equal(discovery.discoveryHolidayFilterLabel("专项提升（2项）"), "专项提升");
  assert.equal(discovery.discoveryHolidayFilterLabel("常规任务 专项提升"), "");
});

test("collects the visible summer task before switching status tabs", () => {
  const activeFilter = new FakeElement("进行中1份", {
    className: "active-jPsEo",
    rect: { left: 120, top: 80, width: 180, height: 46 }
  });
  const futureFilter = new FakeElement("未开始", {
    rect: { left: 120, top: 126, width: 180, height: 46 }
  });
  const expiredFilter = new FakeElement("已截止", {
    rect: { left: 120, top: 172, width: 180, height: 46 }
  });
  const summerTask = new FakeElement(
    "自批 暑期预习学习计划-物化生组合 多学科 专题 "
      + "布置人：盐城中学 开始时间：2026年8月3日 00:00 "
      + "截止时间：2026年8月22日 23:59 开始学习",
    {
      className: "card-rXRzY",
      rect: { left: 360, top: 180, width: 310, height: 240 }
    }
  );
  installDom([activeFilter, futureFilter, expiredFilter, summerTask]);

  const result = discovery.collectInitialDiscoveryCandidates();
  const state = discovery.discoveryTestState();

  assert.deepEqual(result, { collected: true, filter: "进行中", added: 1 });
  assert.equal(state.currentFilter, "进行中");
  assert.equal(state.initialPageCollected, true);
  assert.equal(state.candidates.length, 1);
  assert.equal(state.candidates[0].title, "自批 暑期预习学习计划-物化生组合 多学科 专题");
  assert.equal(state.candidates[0].status, "进行中（未完成）");
  assert.equal(state.candidates[0].filter, "进行中");

  assert.deepEqual(
    discovery.collectInitialDiscoveryCandidates(),
    { collected: false, filter: "进行中", added: 0 }
  );
  assert.equal(discovery.discoveryTestState().candidates.length, 1);
});

test("collects a task page that has no standard status tabs", () => {
  const task = new FakeElement(
    "暑假学习计划 布置人：学校 开始时间：2026年7月1日 "
      + "截止时间：2026年8月31日 去学习",
    {
      className: "holiday-task-card",
      rect: { left: 300, top: 160, width: 360, height: 180 }
    }
  );
  installDom([task]);

  const result = discovery.collectInitialDiscoveryCandidates();
  const state = discovery.discoveryTestState();

  assert.deepEqual(result, { collected: true, filter: "", added: 1 });
  assert.equal(state.candidates.length, 1);
  assert.equal(state.candidates[0].status, "未完成");
});

test("collects a metadata-free task from the active holiday tab and records its source", () => {
  const regular = new FakeElement("常规任务", {
    attributes: { "aria-selected": "true" },
    rect: { left: 520, top: 100, width: 140, height: 44 }
  });
  const special = new FakeElement("专项提升（2项）", {
    rect: { left: 680, top: 100, width: 160, height: 44 }
  });
  const task = new FakeElement("物理 45分钟 未开始 去学习", {
    className: "task-card-container",
    rect: { left: 300, top: 180, width: 560, height: 140 }
  });
  installDom([regular, special, task], {
    url: "https://teacher.ewt360.com/ewtbend/bend/index/index.html#/holiday/student/home?sceneId=summer"
  });

  assert.equal(discovery.findDiscoveryFilter("专项提升"), special);
  const result = discovery.collectInitialDiscoveryCandidates();
  const candidate = discovery.discoveryTestState().candidates[0];

  assert.deepEqual(result, { collected: true, filter: "常规任务", added: 1 });
  assert.equal(candidate.title, "物理 45分钟");
  assert.equal(candidate.status, "未开始");
  assert.equal(candidate.filter, "常规任务");
  assert.equal(candidate.pageKind, "holiday");
  assert.match(candidate.listUrl, /#\/holiday\/student\/home\?sceneId=summer$/);
});

test("does not turn a holiday category into a completion status", () => {
  const special = new FakeElement("专项提升", {
    className: "active",
    rect: { left: 680, top: 100, width: 160, height: 44 }
  });
  const task = new FakeElement("数学思维提升 30分钟 去完成", {
    className: "task-card-container",
    rect: { left: 300, top: 180, width: 560, height: 140 }
  });
  installDom([special, task], {
    url: "https://teacher.ewt360.com/ewtbend/bend/index/index.html#/holiday/student/home"
  });

  discovery.collectInitialDiscoveryCandidates();
  const candidate = discovery.discoveryTestState().candidates[0];

  assert.equal(candidate.filter, "专项提升");
  assert.equal(candidate.status, "未完成");
});

test("accepts long semantic holiday cards without legacy metadata and filters submitted cards", () => {
  const longTask = new FakeElement(`${"暑假专题说明".repeat(45)} 去学习`, {
    className: "section task-card-container",
    rect: { left: 300, top: 180, width: 560, height: 180 }
  });
  const submitted = new FakeElement("化学专题 20分钟 已提交 去学习", {
    className: "section task-card-container",
    rect: { left: 300, top: 380, width: 560, height: 140 }
  });
  installDom([longTask, submitted], {
    url: "https://teacher.ewt360.com/ewtbend/bend/index/index.html#/holiday/student/home"
  });

  assert.deepEqual(discovery.discoveryTaskCards(false), [longTask]);
  assert.deepEqual(discovery.discoveryTaskCards(true), [submitted]);
});

test("holiday empty state is ready without waiting for standard status tabs", () => {
  installDom([], {
    url: "https://teacher.ewt360.com/ewtbend/bend/index/index.html#/holiday/student/home",
    bodyText: "我的学习任务 常规任务 专项提升 暂无学习任务"
  });

  assert.equal(discovery.homeworkDiscoveryUiReady(), true);
});

test("finds 即刻开启 only inside a holiday dialog", () => {
  const inside = new FakeElement("即刻开启", {
    rect: { left: 520, top: 460, width: 160, height: 54 }
  });
  const modal = new FakeElement("欢迎进入暑假任务 即刻开启", {
    className: "holiday-modal",
    children: [inside],
    rect: { left: 300, top: 180, width: 680, height: 420 }
  });
  const dialogSelector = "[role='dialog'], .ant-modal, [class*='modal'], [class*='dialog']";
  installDom([modal, inside], {
    url: "https://teacher.ewt360.com/ewtbend/bend/index/index.html#/holiday/student/home",
    selectorMap: { [dialogSelector]: [modal] }
  });
  assert.equal(discovery.findHolidayDiscoveryOnboardingButton(), inside);

  installDom([inside], {
    url: "https://teacher.ewt360.com/ewtbend/bend/index/index.html#/holiday/student/home",
    selectorMap: { [dialogSelector]: [] }
  });
  assert.equal(discovery.findHolidayDiscoveryOnboardingButton(), null);
});

test("candidate source check requires returning to the matching list route", () => {
  installDom([], {
    url: "https://teacher.ewt360.com/ewtbend/bend/index/index.html#/holiday/student/home"
  });
  assert.equal(discovery.candidateIsOnCurrentDiscoveryPage({ pageKind: "standard" }), false);
  assert.equal(discovery.candidateIsOnCurrentDiscoveryPage({ pageKind: "holiday" }), true);
});

test("candidate selection switches back to its source tab before matching the card", () => {
  assert.equal(
    discovery.candidateNeedsDiscoveryFilterSwitch(
      { pageKind: "standard", filter: "进行中" },
      ""
    ),
    true
  );
  assert.equal(
    discovery.candidateNeedsDiscoveryFilterSwitch(
      { pageKind: "holiday", filter: "专项提升" },
      "常规任务"
    ),
    true
  );
  assert.equal(
    discovery.candidateNeedsDiscoveryFilterSwitch(
      { pageKind: "holiday", filter: "专项提升" },
      "专项提升"
    ),
    false
  );
});

test("keeps the task card and drops matching page containers", () => {
  const page = new FakeElement(
    "我的任务 进行中1份 暑期预习学习计划 布置人：学校 "
      + "开始时间：2026年8月1日 截止时间：2026年8月31日 开始学习",
    {
      className: "main-layout",
      rect: { left: 180, top: 80, width: 900, height: 600 }
    }
  );
  const card = new FakeElement(
    "暑期预习学习计划 布置人：学校 开始时间：2026年8月1日 "
      + "截止时间：2026年8月31日 开始学习",
    {
      className: "card-rXRzY",
      parentElement: page,
      rect: { left: 360, top: 180, width: 310, height: 240 }
    }
  );
  const activeFilter = new FakeElement("进行中1份", {
    className: "active-jPsEo",
    parentElement: page,
    rect: { left: 120, top: 80, width: 180, height: 46 }
  });
  installDom([page, card, activeFilter]);

  discovery.collectInitialDiscoveryCandidates();
  const state = discovery.discoveryTestState();

  assert.equal(state.candidates.length, 1);
  assert.equal(state.candidates[0].title, "暑期预习学习计划");
});

test("risk warning wins over slider and missed acknowledgement", () => {
  const acknowledgement = new CheckpointElement("我知道了", {
    tagName: "span",
    className: "btn-Ug8Kt",
    rect: { left: 520, top: 520, width: 140, height: 44 }
  });
  const slider = new CheckpointElement("", {
    className: "ecaptcha-slidebar-inner-button ecaptcha-slidebar-inner-button__normal",
    rect: { left: 510, top: 470, width: 44, height: 44 }
  });
  const container = new CheckpointElement(
    "认真度检测 近期看课操作异常，请您完成下方检测。向右拖动滑块填充拼图 30s后将错过当前检测",
    {
      className: "spc_video_earnest_check_box-mW2RL",
      children: [slider, acknowledgement],
      rect: { left: 240, top: 130, width: 780, height: 480 }
    }
  );
  installCheckpointDom([container], {
    bodyText: "检测到网络不稳定或开启了第三方辅助工具，学习数据无法被记录"
  });

  assert.equal(discovery.inspectCheckpointState({ videoPaused: true }), "blocked");
  assert.equal(acknowledgement.clickCount, 0);
  assert.equal(slider.clickCount, 0);
});

test("real earnest slider is blocked while risk text is visible", () => {
  const slider = new CheckpointElement("", {
    className: "ecaptcha-slidebar-inner-button ecaptcha-slidebar-inner-button__normal",
    rect: { left: 508, top: 561, width: 44, height: 44 }
  });
  const captcha = new CheckpointElement("向右拖动滑块填充拼图", {
    id: "captcha",
    children: [slider],
    rect: { left: 508, top: 417, width: 300, height: 190 }
  });
  const container = new CheckpointElement(
    "认真度检测 近期看课操作异常，请您完成下方检测。将图形拖动至正确位置：向右拖动滑块填充拼图",
    {
      className: "spc_video_earnest_check_box-mW2RL",
      children: [captcha],
      rect: { left: 64, top: 162, width: 974, height: 608 }
    }
  );
  installCheckpointDom([container]);

  assert.equal(discovery.inspectCheckpointState({ videoPaused: true }), "blocked");
  assert.equal(slider.clickCount, 0);
  assert.equal(slider.dispatchCount, 0);
});

test("slider-only checkpoint is manual and never dragged or clicked", () => {
  const slider = new CheckpointElement("向右拖动滑块填充拼图", {
    className: "ecaptcha-slidebar-inner-button ecaptcha-slidebar-inner-button__normal",
    rect: { left: 508, top: 561, width: 44, height: 44 }
  });
  const captcha = new CheckpointElement("向右拖动滑块填充拼图", {
    id: "captcha",
    children: [slider],
    rect: { left: 508, top: 417, width: 300, height: 190 }
  });
  installCheckpointDom([captcha]);

  assert.equal(discovery.inspectCheckpointState({ videoPaused: true }), "manual");
  assert.equal(slider.clickCount, 0);
  assert.equal(slider.dispatchCount, 0);
});

test("paused btn-Ug8Kt acknowledgement is missed and never clicked", () => {
  const acknowledgement = new CheckpointElement("我知道了", {
    tagName: "span",
    className: "btn-Ug8Kt",
    rect: { left: 510, top: 510, width: 140, height: 44 }
  });
  const overlay = new CheckpointElement("", {
    className: "video-result-dialog",
    attributes: { role: "dialog" },
    children: [acknowledgement],
    rect: { left: 300, top: 180, width: 600, height: 360 }
  });
  installCheckpointDom([overlay]);

  assert.equal(discovery.inspectCheckpointState({ videoPaused: true }), "missed");
  assert.equal(acknowledgement.clickCount, 0);
  assert.equal(acknowledgement.dispatchCount, 0);
});

test("recognizes the alternate 错过所有看课检查点 wording", () => {
  const warning = new CheckpointElement("本课程已学完，但错过所有看课检查点，请重新认真观看一遍", {
    className: "video-result-dialog",
    attributes: { role: "dialog" },
    rect: { left: 300, top: 180, width: 600, height: 360 }
  });
  installCheckpointDom([warning]);

  assert.equal(discovery.inspectCheckpointState({ videoPaused: true }), "missed");
});

test("exact acknowledgement without missed context fails closed", () => {
  const acknowledgement = new CheckpointElement("知道了", {
    tagName: "button",
    rect: { left: 520, top: 510, width: 140, height: 44 }
  });
  const overlay = new CheckpointElement("普通公告 知道了", {
    className: "ant-modal",
    children: [acknowledgement],
    rect: { left: 300, top: 180, width: 600, height: 360 }
  });
  installCheckpointDom([overlay]);

  assert.equal(discovery.inspectCheckpointState({ videoPaused: false }), "unknown");
  assert.equal(acknowledgement.clickCount, 0);
});

test("unknown overlay actions never click even when their labels are whitelisted", () => {
  const continueAction = new CheckpointElement("继续播放", {
    tagName: "button",
    rect: { left: 450, top: 500, width: 140, height: 44 }
  });
  const skipAction = new CheckpointElement("跳过", {
    tagName: "button",
    rect: { left: 610, top: 500, width: 140, height: 44 }
  });
  const overlay = new CheckpointElement("系统提示 继续播放 跳过", {
    className: "ant-modal",
    children: [continueAction, skipAction],
    rect: { left: 300, top: 180, width: 600, height: 360 }
  });
  installCheckpointDom([overlay]);

  assert.equal(discovery.inspectCheckpointState({ videoPaused: true }), "unknown");
  assert.equal(continueAction.clickCount, 0);
  assert.equal(skipAction.clickCount, 0);
});

test("strict whitelist clicks only inside a checkpoint container", () => {
  const action = new CheckpointElement("点击通过检查", {
    tagName: "button",
    rect: { left: 530, top: 500, width: 160, height: 44 }
  });
  const container = new CheckpointElement("视频检查点 点击通过检查", {
    className: "video-checkpoint-dialog",
    children: [action],
    rect: { left: 300, top: 180, width: 600, height: 360 }
  });
  installCheckpointDom([container], { pointElement: action });

  assert.equal(discovery.inspectCheckpointState({ videoPaused: true }), "pending");
  assert.equal(action.clickCount, 1);
  assert.ok(action.dispatchCount > 0);
  action.rect.width = 0;
  assert.equal(discovery.inspectCheckpointState({ videoPaused: true }), "handled");
});

test("checkpoint label change confirms the click", () => {
  const action = new CheckpointElement("点击通过检查", {
    tagName: "button",
    rect: { left: 530, top: 500, width: 160, height: 44 }
  });
  const container = new CheckpointElement("视频检查点 点击通过检查", {
    className: "video-checkpoint-dialog",
    children: [action],
    rect: { left: 300, top: 180, width: 600, height: 360 }
  });
  installCheckpointDom([container], { pointElement: action });

  assert.equal(discovery.inspectCheckpointState({ videoPaused: true }), "pending");
  action.innerText = "已通过";
  action.textContent = "已通过";
  assert.equal(discovery.inspectCheckpointState({ videoPaused: true }), "handled");
});

test("missed result wins when a clicked checkpoint disappears", () => {
  const action = new CheckpointElement("点击通过检查", {
    tagName: "button",
    rect: { left: 530, top: 500, width: 160, height: 44 }
  });
  const container = new CheckpointElement("视频检查点 点击通过检查", {
    className: "video-checkpoint-dialog",
    children: [action],
    rect: { left: 300, top: 180, width: 600, height: 360 }
  });
  installCheckpointDom([container], { pointElement: action });

  assert.equal(discovery.inspectCheckpointState({ videoPaused: true }), "pending");
  action.rect.width = 0;
  const missedAck = new CheckpointElement("我知道了", {
    className: "btn-Ug8Kt",
    rect: { left: 530, top: 500, width: 160, height: 44 }
  });
  const missed = new CheckpointElement(
    "视频已暂停 错过了所有看课检测点 我知道了",
    {
      className: "video-checkpoint-dialog",
      children: [missedAck],
      rect: { left: 300, top: 180, width: 600, height: 360 }
    }
  );
  installCheckpointDom([missed], { pointElement: missedAck });

  assert.equal(discovery.inspectCheckpointState({ videoPaused: true }), "missed");
});

test("missed replay returns after one successfully clicked checkpoint", () => {
  const action = new CheckpointElement("点击通过检查", {
    tagName: "button",
    rect: { left: 530, top: 500, width: 160, height: 44 }
  });
  const container = new CheckpointElement("视频检查点 点击通过检查", {
    className: "video-checkpoint-dialog",
    children: [action],
    rect: { left: 300, top: 180, width: 600, height: 360 }
  });
  action.onClick = () => {
    action.rect.width = 0;
  };
  const video = checkpointVideo();
  installCheckpointDom([container], {
    pointElement: action,
    video,
    config: { child_task_kind: "missedReplay" }
  });

  const originalSetTimeout = global.setTimeout;
  global.setTimeout = (callback) => {
    callback();
    return 1;
  };
  try {
    discovery.handleVideo(video);
  } finally {
    global.setTimeout = originalSetTimeout;
  }

  assert.equal(action.clickCount, 1);
  assert.equal(video.pauseCount, 1);
  assert.equal(video.playCount, 0);
  assert.equal(global.history.backCount, 1);
});

test("checkpoint confirmation times out safely while action remains visible", () => {
  const action = new CheckpointElement("点击通过检查", {
    tagName: "button",
    rect: { left: 530, top: 500, width: 160, height: 44 }
  });
  const container = new CheckpointElement("视频检查点 点击通过检查", {
    className: "video-checkpoint-dialog",
    children: [action],
    rect: { left: 300, top: 180, width: 600, height: 360 }
  });
  installCheckpointDom([container], { pointElement: action });
  const originalNow = Date.now;
  let now = 1000;
  Date.now = () => now;
  try {
    assert.equal(discovery.inspectCheckpointState({ videoPaused: true }), "pending");
    now += 5001;
    assert.equal(discovery.inspectCheckpointState({ videoPaused: true }), "unknown");
  } finally {
    Date.now = originalNow;
  }
});

test("course signature prefers stable identifiers over coordinates", () => {
  const first = new FakeElement("去学习", {
    attributes: { "data-course-id": "course-42" },
    rect: { left: 40, top: 120, width: 120, height: 40 }
  });
  const second = new FakeElement("去学习", {
    attributes: { "data-course-id": "course-42" },
    rect: { left: 640, top: 520, width: 120, height: 40 }
  });
  installDom([first, second]);
  assert.equal(discovery.courseStableIdentifier(first), "data-course-id:course-42");
  assert.equal(discovery.courseSignature(first), discovery.courseSignature(second));

  const fallbackFirst = new FakeElement("去学习", {
    rect: { left: 40, top: 120, width: 120, height: 40 }
  });
  const fallbackSecond = new FakeElement("去学习", {
    rect: { left: 640, top: 520, width: 120, height: 40 }
  });
  installDom([fallbackFirst, fallbackSecond]);
  assert.equal(discovery.courseSignature(fallbackFirst), discovery.courseSignature(fallbackSecond));

  const labeledFirst = new FakeElement("课程甲 去学习", {
    rect: { left: 40, top: 120, width: 120, height: 40 }
  });
  const labeledMoved = new FakeElement("课程甲 去学习", {
    rect: { left: 640, top: 520, width: 120, height: 40 }
  });
  const labeledDifferent = new FakeElement("课程乙 去学习", {
    rect: { left: 640, top: 520, width: 120, height: 40 }
  });
  installDom([labeledFirst, labeledMoved, labeledDifferent]);
  assert.equal(discovery.courseSignature(labeledFirst), discovery.courseSignature(labeledMoved));
  assert.notEqual(discovery.courseSignature(labeledFirst), discovery.courseSignature(labeledDifferent));
});

test("android course scanning always starts from the first day", () => {
  discovery.setCheckpointTestConfig({ day_to_start_on: 8 });
  assert.equal(discovery.automationStartDayIndex(), 0);
});

test("handleVideo never calls play for guarded checkpoint states", () => {
  const slider = new CheckpointElement("", {
    className: "ecaptcha-slidebar-inner-button__normal",
    rect: { left: 508, top: 561, width: 44, height: 44 }
  });
  const container = new CheckpointElement("向右拖动滑块填充拼图", {
    id: "captcha",
    children: [slider],
    rect: { left: 64, top: 162, width: 974, height: 558 }
  });
  const video = checkpointVideo();
  installCheckpointDom([container], { video });

  discovery.handleVideo(video);

  assert.equal(video.pauseCount, 1);
  assert.equal(video.playCount, 0);
  assert.equal(slider.clickCount, 0);
});

test("handleVideo pauses and never plays when risk text is present", () => {
  const container = new CheckpointElement(
    "认真度检测 近期看课操作异常，请您完成下方检测。30s后将错过当前检测",
    {
      className: "spc_video_earnest_check_box-mW2RL",
      rect: { left: 64, top: 162, width: 974, height: 558 }
    }
  );
  const video = checkpointVideo();
  installCheckpointDom([container], { video });

  discovery.handleVideo(video);

  assert.equal(video.pauseCount, 1);
  assert.equal(video.playCount, 0);
});

test("handleVideo pauses and never plays a missed acknowledgement", () => {
  const acknowledgement = new CheckpointElement("我知道了", {
    tagName: "span",
    className: "btn-Ug8Kt",
    rect: { left: 510, top: 510, width: 140, height: 44 }
  });
  const overlay = new CheckpointElement("", {
    className: "video-result-dialog",
    attributes: { role: "dialog" },
    children: [acknowledgement],
    rect: { left: 300, top: 180, width: 600, height: 360 }
  });
  const video = checkpointVideo();
  video.paused = true;
  installCheckpointDom([overlay], { video });

  const originalSetTimeout = global.setTimeout;
  global.setTimeout = (callback) => {
    callback();
    return 1;
  };
  try {
    discovery.handleVideo(video);
  } finally {
    global.setTimeout = originalSetTimeout;
  }

  assert.equal(video.pauseCount, 1);
  assert.equal(video.playCount, 0);
  assert.equal(acknowledgement.clickCount, 0);
});
