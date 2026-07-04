(function () {
  const PORT_NAME = "autoewt";
  const STEP_IDLE = "idle";
  const STEP_LOGIN = "login";
  const STEP_COURSE_LIST = "courseList";
  const STEP_VIDEO = "video";
  const DONE_RE = /已学完|已完成|已结束|已提交|已批改/;
  const EXPIRED_RE = /已截止|已过期|已失效|超时/;
  const COURSE_ACTION_RE = /去学习|开始学习|继续学习|播放|去收听|去查看|学\s*\d+%/;
  const EXACT_COURSE_ACTION_RE = /^(去学习|开始学习|继续学习|播放|去收听|去查看|学\s*\d+%)$/;
  const CHECKPOINT_ACTION_RE = /我知道了|知道了|点击通过检查|通过检查|继续播放|继续学习|跳过|确定|确认/;
  const PYTHON_CHECKPOINT_ACTION_RE = /点击通过检查|跳过/;
  const PAUSED_CHECKPOINT_ACTION_RE = /我知道了|知道了|通过检查|继续播放|继续学习|确定|确认/;
  const MISSED_CHECKPOINT_RE = /错过了所有看课检测点|再认真观看一次/;
  const missedCheckpointReplayElements = new WeakSet();

  let port = null;
  let config = {};
  let automationRunning = false;
  let automationTimer = 0;
  let automationBusy = false;
  let lastLoginSubmitAt = 0;
  let lastLessonClickAt = 0;
  let lastLessonClickSignature = "";
  let lastLessonClickLabel = "";
  let lastLessonClickUrl = "";
  let lastDayClickAt = 0;
  let lastCheckpointTapAt = 0;
  let lastCheckpointSignature = "";
  let videoEndReported = false;
  let lastReportedAutomation = "";
  let checkpointObserverInstalled = false;
  let childSessionActive = false;
  let fmEntryExitReported = false;
  let lastFmEntryUrl = "";
  const failedCourseSignatures = new Set();
  const finishedOneClickSignatures = new Set();

  function connect() {
    if (port) {
      return port;
    }
    try {
      port = browser.runtime.connectNative(PORT_NAME);
      port.onMessage.addListener((message) => {
        if (!message || typeof message !== "object") {
          return;
        }
        if (message.type === "probe") {
          reportState("probe");
        } else if (message.type === "config") {
          config = Object.assign({}, config, message.config || {});
          syncChildSessionStateFromConfig();
          if (config.automation_running) {
            startAutomation("config");
          } else if (config.auto_fill_login && isLoginPage()) {
            fillLogin("config", Boolean(config.auto_submit_login));
          }
        } else if (message.type === "fillLogin") {
          fillLogin("manual", Boolean(message.submit));
        } else if (message.type === "automation") {
          config = Object.assign({}, config, message.config || {});
          if (message.action === "start") {
            startAutomation("manual");
          } else if (message.action === "stop") {
            stopAutomation("manual");
          }
        } else if (message.type === "childSessionClosed") {
          childSessionActive = false;
          if (message.automationRunning || automationRunning || config.automation_running) {
            automationRunning = true;
            scheduleAutomation(300);
          }
        } else if (message.type === "childSessionOpened") {
          childSessionActive = true;
          lastLessonClickSignature = "";
          lastLessonClickLabel = "";
          lastLessonClickUrl = "";
        }
      });
      port.onDisconnect.addListener(() => {
        port = null;
      });
    } catch (error) {
      port = null;
    }
    return port;
  }

  function postMessage(message) {
    const nativePort = connect();
    if (!nativePort) {
      return false;
    }
    try {
      nativePort.postMessage(message);
      return true;
    } catch (error) {
      port = null;
      return false;
    }
  }

  function logAutomation(message, payload) {
    const key = message + JSON.stringify(payload || {});
    if (key === lastReportedAutomation && !/进度|播放/.test(message)) {
      return;
    }
    lastReportedAutomation = key;
    postMessage(Object.assign({
      type: "automationLog",
      message,
      step: inferStep(),
      url: location.href,
      timestamp: Date.now()
    }, payload || {}));
  }

  function videoProgress(video) {
    if (!video || !Number.isFinite(video.duration) || video.duration <= 0) {
      return -1;
    }
    return Math.max(0, Math.min(1, video.currentTime / video.duration));
  }

  function collectState(reason) {
    const videos = Array.from(document.querySelectorAll("video"));
    const firstVideo = videos[0] || null;
    const courseCandidates = firstVideo ? [] : summarizeCourseCandidates();
    return {
      type: "pageState",
      reason,
      title: document.title || "",
      url: location.href,
      readyState: document.readyState,
      videoCount: videos.length,
      videoPaused: firstVideo ? firstVideo.paused : null,
      videoEnded: firstVideo ? firstVideo.ended : null,
      videoProgress: videoProgress(firstVideo),
      courseCandidateCount: courseCandidates.length,
      courseCandidateSummary: courseCandidates.slice(0, 5).join(" | "),
      childSessionActive,
      visibilityState: document.visibilityState,
      timestamp: Date.now()
    };
  }

  function reportState(reason) {
    if (reason === "interval" && shouldPauseAutomationForVisibility()) {
      return;
    }
    postMessage(collectState(reason));
  }

  function reportLoginFill(payload) {
    postMessage(Object.assign({
      type: "loginFill",
      url: location.href,
      timestamp: Date.now()
    }, payload));
  }

  function queryFirst(selectors) {
    for (const selector of selectors) {
      const element = document.querySelector(selector);
      if (element) {
        return element;
      }
    }
    return null;
  }

  function xpathAll(expression, root) {
    const result = [];
    const iterator = document.evaluate(
      expression,
      root || document,
      null,
      XPathResult.ORDERED_NODE_ITERATOR_TYPE,
      null
    );
    let node = iterator.iterateNext();
    while (node) {
      result.push(node);
      node = iterator.iterateNext();
    }
    return result;
  }

  function setNativeValue(element, value) {
    if (!element) {
      return false;
    }
    const prototype = Object.getPrototypeOf(element);
    const descriptor = Object.getOwnPropertyDescriptor(prototype, "value");
    if (descriptor && descriptor.set) {
      descriptor.set.call(element, value);
    } else {
      element.value = value;
    }
    element.dispatchEvent(new Event("input", { bubbles: true }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  }

  function visible(element) {
    if (!element) {
      return false;
    }
    const rect = element.getBoundingClientRect();
    const style = window.getComputedStyle(element);
    return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
  }

  function clickElementInfo(element) {
    if (!element) {
      return { clicked: false };
    }
    const anchor = element.closest ? element.closest("a[target]") : null;
    if (anchor) {
      anchor.setAttribute("target", "_self");
    }
    element.scrollIntoView({ block: "center", inline: "center" });
    const rect = element.getBoundingClientRect();
    const clientX = Math.max(0, rect.left + rect.width / 2);
    const clientY = Math.max(0, rect.top + rect.height / 2);
    const target = document.elementFromPoint(clientX, clientY) || element;
    const eventInit = {
      bubbles: true,
      cancelable: true,
      view: window,
      clientX,
      clientY,
      button: 0,
      buttons: 1
    };
    try {
      target.dispatchEvent(new PointerEvent("pointerdown", Object.assign({ pointerId: 1, pointerType: "mouse" }, eventInit)));
      target.dispatchEvent(new PointerEvent("pointerup", Object.assign({ pointerId: 1, pointerType: "mouse", buttons: 0 }, eventInit)));
    } catch (error) {
      // Older engines may not expose PointerEvent to content scripts.
    }
    target.dispatchEvent(new MouseEvent("mouseover", eventInit));
    target.dispatchEvent(new MouseEvent("mousedown", eventInit));
    target.dispatchEvent(new MouseEvent("mouseup", Object.assign({}, eventInit, { buttons: 0 })));
    target.dispatchEvent(new MouseEvent("click", Object.assign({}, eventInit, { buttons: 0 })));
    if (typeof target.click === "function") {
      target.click();
    }
    return {
      clicked: true,
      clientX,
      clientY,
      viewportWidth: viewportWidth(),
      viewportHeight: viewportHeight(),
      devicePixelRatio: Number(window.devicePixelRatio || 1),
      targetText: textOf(target).slice(0, 80)
    };
  }

  function clickElement(element) {
    return clickElementInfo(element).clicked;
  }

  function viewportWidth() {
    return Number((window.visualViewport && window.visualViewport.width) || window.innerWidth || document.documentElement.clientWidth || 0);
  }

  function viewportHeight() {
    return Number((window.visualViewport && window.visualViewport.height) || window.innerHeight || document.documentElement.clientHeight || 0);
  }

  function requestNativeTap(clickInfo, label, reason, taskKind) {
    if (!clickInfo || !clickInfo.clicked || !Number.isFinite(clickInfo.clientX) || !Number.isFinite(clickInfo.clientY)) {
      return false;
    }
    const message = {
      type: "nativeTap",
      reason,
      label: String(label || "").slice(0, 80),
      clientX: clickInfo.clientX,
      clientY: clickInfo.clientY,
      viewportWidth: clickInfo.viewportWidth,
      viewportHeight: clickInfo.viewportHeight,
      devicePixelRatio: clickInfo.devicePixelRatio,
      timestamp: Date.now()
    };
    if (taskKind) {
      message.taskKind = taskKind;
    }
    return postMessage(message);
  }

  function isFmPage() {
    return /spiritual-growth|xinqing/i.test(location.href) || Boolean(document.querySelector("#FMbg"));
  }

  function handleFmEntryPage() {
    reportState("fmEntry");
    if (fmEntryExitReported && lastFmEntryUrl === location.href) {
      return;
    }
    fmEntryExitReported = true;
    lastFmEntryUrl = location.href;
    logAutomation("已进入 FM 任务页，返回课程列表");
    setTimeout(() => {
      postMessage({
        type: "closeChildSession",
        reason: "fmEntry",
        url: location.href,
        timestamp: Date.now()
      });
      history.back();
    }, 800);
  }

  function shouldFinishOneClickPage() {
    return config.child_task_kind === "oneClick" || isFmPage();
  }

  function findPlaybackButton() {
    const direct = Array.from(document.querySelectorAll([
      ".vjs-big-play-button",
      ".vjs-play-control",
      "[aria-label*='播放']",
      "[title*='播放']",
      "[class*='play' i]",
      "[id*='play' i]"
    ].join(","))).find(visible);
    if (direct) {
      return direct;
    }
    if (!isFmPage()) {
      return null;
    }

    const width = viewportWidth();
    const height = viewportHeight();
    if (width <= 0 || height <= 0) {
      return null;
    }
    return Array.from(document.querySelectorAll("button, a, [role='button'], [onclick], div, span, i, svg"))
      .filter(visible)
      .filter((element) => {
        const rect = element.getBoundingClientRect();
        const area = rect.width * rect.height;
        return rect.width >= 24
          && rect.width <= 110
          && rect.height >= 24
          && rect.height <= 110
          && area >= 500
          && rect.top > height * 0.55
          && rect.left > width * 0.15
          && rect.right < width * 0.75;
      })
      .sort((left, right) => {
        const leftRect = left.getBoundingClientRect();
        const rightRect = right.getBoundingClientRect();
        const leftScore = Math.abs((leftRect.left + leftRect.width / 2) / width - 0.435)
          + Math.abs((leftRect.top + leftRect.height / 2) / height - 0.86);
        const rightScore = Math.abs((rightRect.left + rightRect.width / 2) / width - 0.435)
          + Math.abs((rightRect.top + rightRect.height / 2) / height - 0.86);
        return leftScore - rightScore || elementArea(left) - elementArea(right);
      })[0] || null;
  }

  function textOf(element) {
    return (element && (element.innerText || element.textContent || element.value || element.getAttribute("aria-label")) || "").trim();
  }

  function findLoginButton() {
    const byClass = document.querySelector(".ant-btn-block, button[type='submit'], input[type='submit']");
    if (byClass && visible(byClass)) {
      return byClass;
    }
    const candidates = Array.from(document.querySelectorAll("button, input[type='submit'], input[type='button'], a"));
    return candidates.find((element) => {
      if (!visible(element)) {
        return false;
      }
      return /登录|登\s*录|login|sign\s*in/i.test(textOf(element));
    }) || null;
  }

  function hasCaptcha() {
    const candidates = Array.from(document.querySelectorAll("input, textarea"));
    return candidates.some((element) => {
      const haystack = [
        element.id,
        element.name,
        element.placeholder,
        element.getAttribute("aria-label"),
        element.className
      ].join(" ");
      return /captcha|verify|code|验证码|校验码|图形码|短信/i.test(haystack);
    });
  }

  function clickAgreementControls() {
    let clicked = false;
    const agreementInput = document.querySelector(".privacy__agreement .ant-checkbox-input");
    if (agreementInput && !agreementInput.checked) {
      clicked = clickElement(agreementInput.closest(".ant-checkbox-wrapper, label, .ant-checkbox") || agreementInput) || clicked;
    }
    const autoLogin = document.querySelector("#login__password_autoLogin");
    if (autoLogin && !autoLogin.checked) {
      clicked = clickElement(autoLogin.closest("label, .ant-checkbox-wrapper, .ant-checkbox") || autoLogin) || clicked;
    }
    return clicked;
  }

  function clickAgreementModal() {
    const modalButton = document.querySelector(".privacy__agreement__modal-btn .ant-btn-primary");
    if (modalButton && visible(modalButton)) {
      clickElement(modalButton);
      logAutomation("已点击协议弹窗中的同意按钮");
      return true;
    }
    const buttons = Array.from(document.querySelectorAll("button, a")).filter(visible);
    const agree = buttons.find((button) => /同意并继续|同意|继续/.test(textOf(button)));
    if (agree) {
      clickElement(agree);
      logAutomation("已点击协议确认按钮");
      return true;
    }
    return false;
  }

  function fillLogin(reason, submit) {
    const username = String(config.username || "").trim();
    const password = String(config.password || "");
    if (!username && !password) {
      return {
        usernameFilled: false,
        passwordFilled: false,
        captchaDetected: false,
        submitted: false
      };
    }

    const usernameInput = queryFirst([
      "#login__password_userName",
      "input[name='userName']",
      "input[name='username']",
      "input[name='account']",
      "input[name='mobile']",
      "input[name='phone']",
      "input[id*='user' i]",
      "input[id*='account' i]",
      "input[placeholder*='账号']",
      "input[placeholder*='用户名']",
      "input[placeholder*='手机']",
      "input[type='text']"
    ]);
    const passwordInput = queryFirst([
      "#login__password_password",
      "input[name='password']",
      "input[id*='password' i]",
      "input[placeholder*='密码']",
      "input[type='password']"
    ]);

    const usernameFilled = username ? setNativeValue(usernameInput, username) : false;
    const passwordFilled = password ? setNativeValue(passwordInput, password) : false;
    const captchaDetected = hasCaptcha();
    let submitted = false;
    let agreementClicked = false;

    if (usernameFilled || passwordFilled) {
      agreementClicked = clickAgreementControls();
    }

    if (submit && usernameFilled && passwordFilled && !captchaDetected) {
      const now = Date.now();
      if (now - lastLoginSubmitAt > 3500) {
        lastLoginSubmitAt = now;
        const loginButton = findLoginButton();
        if (loginButton) {
          submitted = clickElement(loginButton);
        }
      }
    } else if (passwordInput && visible(passwordInput)) {
      passwordInput.focus();
    } else if (usernameInput && visible(usernameInput)) {
      usernameInput.focus();
    }

    const payload = {
      reason,
      usernameFilled,
      passwordFilled,
      submitted,
      agreementClicked,
      captchaDetected,
      usernameSelectorFound: Boolean(usernameInput),
      passwordSelectorFound: Boolean(passwordInput)
    };
    reportLoginFill(payload);
    return payload;
  }

  function inferStep() {
    if (document.querySelector("video")) {
      return STEP_VIDEO;
    }
    if (isLoginPage()) {
      return STEP_LOGIN;
    }
    if (/teacher\.ewt360\.com|web\.ewt360\.com/.test(location.host)) {
      return STEP_COURSE_LIST;
    }
    return STEP_IDLE;
  }

  function isLoginPage() {
    return Boolean(
      document.querySelector("#login__password_userName, #login__password_password, #login__password")
      || /\/login|register\/#\/login/i.test(location.href)
    );
  }

  function startAutomation(reason) {
    const wasRunning = automationRunning;
    automationRunning = true;
    if (!wasRunning) {
      logAutomation("自动刷课已启动", { reason });
    }
    scheduleAutomation(200);
  }

  function stopAutomation(reason) {
    automationRunning = false;
    if (automationTimer) {
      clearTimeout(automationTimer);
      automationTimer = 0;
    }
    logAutomation("自动刷课已停止", { reason });
  }

  function scheduleAutomation(delay) {
    if (!automationRunning) {
      return;
    }
    if (shouldPauseAutomationForVisibility()) {
      return;
    }
    if (automationTimer) {
      clearTimeout(automationTimer);
    }
    automationTimer = setTimeout(runAutomation, delay);
  }

  async function runAutomation() {
    if (!automationRunning || automationBusy || shouldPauseAutomationForVisibility()) {
      return;
    }
    automationBusy = true;
    try {
      await automationStep();
    } catch (error) {
      logAutomation("自动化步骤异常", { error: String(error && error.message || error) });
    } finally {
      automationBusy = false;
      scheduleAutomation(1500);
    }
  }

  function shouldPauseAutomationForVisibility() {
    return childSessionActive || (document.visibilityState === "hidden" && !document.querySelector("video"));
  }

  function syncChildSessionStateFromConfig() {
    if (!config.child_session_active) {
      childSessionActive = false;
      return;
    }
    if (document.visibilityState === "hidden" && !document.querySelector("video")) {
      childSessionActive = true;
    }
  }

  async function automationStep() {
    clickAgreementModal();
    reportState("automation");

    if (!config.username || !config.password) {
      logAutomation("缺少账号或密码，请先在配置页填写");
      return;
    }
    if (!config.list_url) {
      logAutomation("缺少课程列表 URL，请先在配置页填写");
      return;
    }

    if (isLoginPage()) {
      const result = fillLogin("automation", true);
      if (result.captchaDetected) {
        logAutomation("检测到验证码或短信验证，请手动完成后继续");
      } else if (result.usernameFilled && result.passwordFilled) {
        logAutomation(result.submitted ? "已提交登录表单" : "已填入登录信息，等待提交/跳转");
      } else {
        logAutomation("等待登录表单出现");
      }
      return;
    }

    if (shouldFinishOneClickPage()) {
      handleFmEntryPage();
      return;
    }

    const video = document.querySelector("video");
    if (video) {
      handleVideo(video);
      return;
    }

    const result = clickNextCourse();
    if (result.clicked) {
      logAutomation("已点击未完成课程", { label: result.label });
      return;
    }
    if (result.dayClicked) {
      logAutomation("已切换到任务日期", { dayIndex: result.dayIndex + 1 });
      return;
    }
    if (result.totalDays > 0) {
      logAutomation("暂未找到未完成视频课程", {
        totalDays: result.totalDays,
        dayIndex: result.dayIndex + 1,
        buttons: result.buttonCount
      });
    } else {
      logAutomation("等待课程列表加载");
    }
  }

  function clickNextCourse() {
    markStaleLessonClick();

    const days = Array.from(document.querySelectorAll('li[data-active="true"], li[data-active="false"]')).filter(visible);
    const startIndex = Math.max(0, Number(config.day_to_start_on || 1) - 1);
    let selectedDayIndex = days.findIndex((day) => day.getAttribute("data-active") === "true");
    if (selectedDayIndex < startIndex) {
      selectedDayIndex = -1;
    }
    if (selectedDayIndex < 0 && days.length > 0) {
      const dayIndex = Math.min(startIndex, days.length - 1);
      clickElement(days[dayIndex]);
      return { clicked: false, dayClicked: true, dayIndex, totalDays: days.length, buttonCount: 0 };
    }

    const candidates = findCourseButtonCandidates(false);
    const candidate = candidates[0] || null;

    if (candidate) {
      if (lastLessonClickSignature === candidate.signature && location.href === lastLessonClickUrl && !document.querySelector("video")) {
        return {
          clicked: false,
          dayClicked: false,
          totalDays: days.length,
          dayIndex: Math.max(0, selectedDayIndex),
          buttonCount: candidates.length
        };
      }
      const now = Date.now();
      if (now - lastLessonClickAt < 5000) {
        return {
          clicked: false,
          dayClicked: false,
          totalDays: days.length,
          dayIndex: Math.max(0, selectedDayIndex),
          buttonCount: candidates.length
        };
      }
      lastLessonClickAt = now;
      lastLessonClickSignature = candidate.signature;
      lastLessonClickLabel = candidate.label;
      lastLessonClickUrl = location.href;
      if (candidate.kind === "oneClick") {
        finishedOneClickSignatures.add(candidate.signature);
      }
      const clickInfo = clickElementInfo(candidate.element);
      requestNativeTap(clickInfo, candidate.label, "course", candidate.kind);
      return {
        clicked: true,
        label: candidate.label,
        dayClicked: false,
        totalDays: days.length,
        dayIndex: Math.max(0, selectedDayIndex),
        buttonCount: candidates.length
      };
    }

    const nextDayIndex = nextAvailableDayIndex(days, selectedDayIndex, startIndex);
    if (nextDayIndex >= 0) {
      const now = Date.now();
      if (now - lastDayClickAt > 1500) {
        lastDayClickAt = now;
        clickElement(days[nextDayIndex]);
      }
      return { clicked: false, dayClicked: true, dayIndex: nextDayIndex, totalDays: days.length, buttonCount: 0 };
    }

    return {
      clicked: false,
      dayClicked: false,
      totalDays: days.length,
      dayIndex: Math.max(0, selectedDayIndex),
      buttonCount: candidates.length
    };
  }

  function markStaleLessonClick() {
    if (!lastLessonClickSignature) {
      return;
    }
    const clickTimedOut = Date.now() - lastLessonClickAt > 8000;
    if (clickTimedOut && location.href === lastLessonClickUrl && !document.querySelector("video")) {
      failedCourseSignatures.add(lastLessonClickSignature);
      logAutomation("课程入口点击后没有进入播放页，已跳过该入口", { label: lastLessonClickLabel });
      lastLessonClickSignature = "";
      lastLessonClickLabel = "";
      lastLessonClickUrl = "";
    } else if (location.href !== lastLessonClickUrl || document.querySelector("video")) {
      lastLessonClickSignature = "";
      lastLessonClickLabel = "";
      lastLessonClickUrl = "";
    }
  }

  function nextAvailableDayIndex(days, selectedDayIndex, startIndex) {
    if (!days.length) {
      return -1;
    }
    const selected = selectedDayIndex >= 0 ? selectedDayIndex : startIndex - 1;
    const next = Math.max(startIndex, selected + 1);
    return next < days.length ? next : -1;
  }

  function findCourseButtonCandidates(includeFailed) {
    const legacyLessonButtons = xpathAll(
      "//div[contains(@class, 'btn-AoqsA') and .//text()[contains(., '学')] and not(.//text()[contains(., '已学完')])]"
    ).filter(visible);
    const legacyOneClickButtons = xpathAll(
      "//div[contains(@class, 'btn-AoqsA') and (.//text()[contains(., '去收听')] or .//text()[contains(., '去查看')]) and not(.//text()[contains(., '已学完')])]"
    ).filter(visible);
    const lessonButtons = Array.from(document.querySelectorAll("div[class*='btn'], button, a, [role='button']"))
      .filter((element) => {
        const text = compactText(element);
        return visible(element) && text.length <= 60 && COURSE_ACTION_RE.test(text) && isActionLikeElement(element);
      });
    const oneClickButtons = xpathAll(
      "//*[normalize-space(.)='去收听' or normalize-space(.)='去查看' or normalize-space(.)='去学习' or normalize-space(.)='开始学习' or normalize-space(.)='继续学习' or normalize-space(.)='播放']"
    ).filter(visible).map(actionableElement).filter(isActionLikeElement);
    const missedReplayButtons = findMissedCheckpointReplayButtons();
    const fallbackButtons = findFallbackCourseButtons();
    const elements = uniqueElements(missedReplayButtons.concat(legacyLessonButtons, legacyOneClickButtons, lessonButtons, oneClickButtons, fallbackButtons).map(actionableElement));
    const seenSignatures = new Set();
    return elements
      .filter((element) => visible(element))
      .map((element) => ({
        element,
        label: labelForCourseElement(element),
        signature: courseSignature(element),
        kind: courseActionKind(element),
        score: courseActionScore(element)
      }))
      .filter((candidate) => {
        if (seenSignatures.has(candidate.signature)) {
          return false;
        }
        seenSignatures.add(candidate.signature);
        if (!isActionLikeElement(candidate.element)) {
          return false;
        }
        if (!includeFailed && failedCourseSignatures.has(candidate.signature)) {
          return false;
        }
        if (!includeFailed && candidate.kind === "oneClick" && finishedOneClickSignatures.has(candidate.signature)) {
          return false;
        }
        return !isClosedCourseElement(candidate.element);
      })
      .sort((left, right) => {
        const leftRect = left.element.getBoundingClientRect();
        const rightRect = right.element.getBoundingClientRect();
        return courseKindWeight(left.kind) - courseKindWeight(right.kind)
          || left.score - right.score
          || leftRect.top - rightRect.top
          || leftRect.left - rightRect.left;
      });
  }

  function findFallbackCourseButtons() {
    const candidates = Array.from(document.querySelectorAll("button, a, [role='button'], div, span")).filter(visible);
    const exact = candidates
      .filter((element) => EXACT_COURSE_ACTION_RE.test(compactText(element)))
      .sort((left, right) => elementArea(left) - elementArea(right));
    const broad = candidates
      .filter((element) => {
        const text = compactText(element);
        if (DONE_RE.test(text) || EXPIRED_RE.test(text)) {
          return false;
        }
        return text.length <= 40 && COURSE_ACTION_RE.test(text);
      })
      .sort((left, right) => elementArea(left) - elementArea(right));
    return exact.concat(broad).map(actionableElement).filter(isActionLikeElement);
  }

  function findMissedCheckpointReplayButtons() {
    const warningNodes = Array.from(document.querySelectorAll("body *"))
      .filter((element) => visible(element) && MISSED_CHECKPOINT_RE.test(compactText(element)));
    const buttons = [];
    for (const warning of warningNodes) {
      const match = findMissedCheckpointContainer(warning);
      if (!match) {
        continue;
      }
      const replay = match.replay;
      missedCheckpointReplayElements.add(replay);
      const action = actionableElement(replay);
      missedCheckpointReplayElements.add(action);
      buttons.push(action);
    }
    return uniqueElements(buttons).filter(Boolean);
  }

  function findMissedCheckpointContainer(warning) {
    let current = warning;
    let best = null;
    for (let depth = 0; current && current !== document.body && depth < 12; depth += 1) {
      const text = compactText(current);
      if (MISSED_CHECKPOINT_RE.test(text)) {
        const replays = replayButtonsInside(current, warning);
        if (replays.length) {
          const rect = current.getBoundingClientRect();
          const score = elementArea(current) + Math.max(0, text.length - 60) * 18 + depth * 120
            + Math.max(0, rect.height - 260) * 12;
          if (!best || score < best.score) {
            best = {
              container: current,
              replay: replays[0],
              score
            };
          }
        }
      }
      current = current.parentElement;
    }
    return best;
  }

  function replayButtonsInside(container, warning) {
    const warningRect = warning.getBoundingClientRect();
    return Array.from(container.querySelectorAll("button, a, [role='button'], div, span"))
      .filter((element) => {
        const text = compactText(element);
        return visible(element) && /已学完/.test(text) && text.length <= 48;
      })
      .sort((left, right) => replayButtonScore(left, warningRect) - replayButtonScore(right, warningRect));
  }

  function replayButtonScore(element, warningRect) {
    const rect = element.getBoundingClientRect();
    const yDistance = Math.abs((rect.top + rect.bottom) / 2 - (warningRect.top + warningRect.bottom) / 2);
    const xBias = Math.max(0, viewportWidth() - rect.right) * 0.05;
    const actionBias = isActionLikeElement(element) ? 0 : 150;
    return yDistance * 2 + elementArea(element) * 0.02 + xBias + actionBias;
  }

  function elementArea(element) {
    const rect = element.getBoundingClientRect();
    return Math.max(1, rect.width * rect.height);
  }

  function actionableElement(element) {
    let current = element;
    for (let i = 0; current && i < 5; i += 1) {
      const style = window.getComputedStyle(current);
      const text = textOf(current);
      if ((DONE_RE.test(text) && !isMissedCheckpointReplayContext(current)) || EXPIRED_RE.test(text)) {
        return element;
      }
      if (text.length > 180) {
        return element;
      }
      if (
        current.matches("button, a, [role='button']")
        || style.cursor === "pointer"
        || /btn|button|operate|action|study|learn/i.test(String(current.className || ""))
      ) {
        return current;
      }
      current = current.parentElement;
    }
    return element;
  }

  function isActionLikeElement(element) {
    if (!element) {
      return false;
    }
    const style = window.getComputedStyle(element);
    const className = String(element.className || "");
    if (element.matches("button, a, [role='button']")) {
      return true;
    }
    if (style.cursor === "pointer") {
      return true;
    }
    return /(^|[-_\s])(btn|button|operate|action|study|learn)([-_\s]|$)/i.test(className);
  }

  function courseActionKind(element) {
    const text = compactText(element);
    const className = String(element && element.className || "");
    const isPythonButton = className.includes("btn-AoqsA")
      || Boolean(element && element.closest && element.closest("[class*='btn-AoqsA']"));
    if (isPythonButton && /学/.test(text) && !DONE_RE.test(text)) {
      return "video";
    }
    if (isPythonButton && /去收听|去查看/.test(text) && !DONE_RE.test(text)) {
      return "oneClick";
    }
    if (/去收听|去查看/.test(text)) {
      return "oneClick";
    }
    return "video";
  }

  function courseKindWeight(kind) {
    if (kind === "video") {
      return 0;
    }
    if (kind === "oneClick") {
      return 1;
    }
    return 2;
  }

  function courseActionScore(element) {
    const text = compactText(element);
    if (isMissedCheckpointReplayContext(element)) {
      return -1;
    }
    if (/去学习|开始学习|继续学习/.test(text)) {
      return 0;
    }
    if (/^学\s*\d+%$/.test(text)) {
      return 1;
    }
    if (/学\s*\d+%/.test(text)) {
      return 2;
    }
    if (/播放/.test(text)) {
      return 3;
    }
    if (/去收听|去查看/.test(text)) {
      return 4;
    }
    return 8;
  }

  function uniqueElements(elements) {
    const seen = new Set();
    return elements.filter((element) => {
      if (!element || seen.has(element)) {
        return false;
      }
      seen.add(element);
      return true;
    });
  }

  function compactText(element) {
    return textOf(element).replace(/\s+/g, " ").trim();
  }

  function courseContainer(element) {
    const actionText = compactText(element);
    let current = element;
    for (let i = 0; current && current !== document.body && i < 7; i += 1) {
      const text = compactText(current);
      if (current !== element && text.length > actionText.length + 4 && text.length < 500) {
        return current;
      }
      if (current.matches && text.length < 500 && current.matches("li, tr, [class*='item'], [class*='card'], [class*='course'], [class*='lesson'], [class*='task']")) {
        return current;
      }
      current = current.parentElement;
    }
    return element.parentElement || element;
  }

  function labelForCourseElement(element) {
    const text = compactText(element);
    if (text.length <= 80) {
      return text;
    }
    const containerText = compactText(courseContainer(element));
    return (containerText || text).slice(0, 80);
  }

  function courseSignature(element) {
    const rect = element.getBoundingClientRect();
    return [
      labelForCourseElement(element),
      compactText(courseContainer(element)).slice(0, 120),
      Math.round(rect.top / 8),
      Math.round(rect.left / 8)
    ].join("|");
  }

  function isClosedCourseElement(element) {
    if (!element) {
      return true;
    }
    if (isMissedCheckpointReplayContext(element)) {
      return false;
    }
    if (element.matches && element.matches("[disabled], [aria-disabled='true']")) {
      return true;
    }
    const className = String(element.className || "");
    if (/\b(disabled|disable|forbid|expired|finish|done)\b/i.test(className)) {
      return true;
    }
    const containerText = compactText(courseContainer(element));
    return DONE_RE.test(containerText) || EXPIRED_RE.test(containerText);
  }

  function isMissedCheckpointReplayContext(element) {
    if (!element) {
      return false;
    }
    if (missedCheckpointReplayElements.has(element)) {
      return true;
    }
    if (MISSED_CHECKPOINT_RE.test(compactText(element))) {
      return true;
    }
    const container = courseContainer(element);
    return MISSED_CHECKPOINT_RE.test(compactText(container));
  }

  function summarizeCourseCandidates() {
    return findCourseButtonCandidates(true).map((candidate) => {
      const state = failedCourseSignatures.has(candidate.signature) ? "已跳过" : "可尝试";
      const text = compactText(courseContainer(candidate.element)) || candidate.label;
      return `${state}:${text.slice(0, 64)}`;
    });
  }

  function handleVideo(video) {
    const checkpointClicked = clickCheckpointButtons({ videoPaused: Boolean(video.paused), source: "loop" });
    if (video.muted !== true) {
      video.muted = true;
    }
    if (video.paused && !video.ended) {
      const playButton = findPlaybackButton();
      let playClickInfo = null;
      const playLabel = isFmPage() ? "播放音频" : "播放视频";
      let nativePlayTapRequested = false;
      const requestPlayTap = (reason) => {
        if (nativePlayTapRequested || !playClickInfo) {
          return false;
        }
        nativePlayTapRequested = requestNativeTap(playClickInfo, playLabel, reason);
        return nativePlayTapRequested;
      };
      if (playButton && visible(playButton)) {
        playClickInfo = clickElementInfo(playButton);
        setTimeout(() => {
          if (video.paused && !video.ended) {
            requestPlayTap("videoPlayDelayed");
          }
        }, 300);
      }
      const playResult = video.play();
      if (playResult && typeof playResult.catch === "function") {
        playResult.catch(() => {
          if (requestPlayTap("videoPlayRetry")) {
            logAutomation("脚本播放被拦截，已改用原生点击播放按钮");
          } else {
            logAutomation("等待播放按钮出现或原生手势解锁");
          }
        });
      }
      logAutomation("正在尝试播放视频");
    } else if (checkpointClicked) {
      logAutomation("已处理视频检查点，等待继续播放");
    }

    postMessage({
      type: "automationProgress",
      currentTime: Number(video.currentTime || 0),
      duration: Number(video.duration || 0),
      progress: videoProgress(video),
      paused: Boolean(video.paused),
      ended: Boolean(video.ended),
      url: location.href,
      timestamp: Date.now()
    });

    const progress = videoProgress(video);
    const nearEnd = progress >= 0.995 || (
      Number.isFinite(video.duration)
      && video.duration > 0
      && Number.isFinite(video.currentTime)
      && video.duration - video.currentTime <= 1.5
    );
    if (!video.ended && !nearEnd) {
      videoEndReported = false;
    }

    if ((video.ended || nearEnd) && !videoEndReported) {
      videoEndReported = true;
      logAutomation("视频已结束，返回课程列表");
      setTimeout(() => {
        postMessage({
          type: "closeChildSession",
          reason: video.ended ? "videoEnded" : "videoNearEnd",
          url: location.href,
          timestamp: Date.now()
        });
        history.back();
      }, 800);
    }
  }

  function checkpointPattern(videoPaused) {
    return videoPaused
      ? new RegExp(`${PYTHON_CHECKPOINT_ACTION_RE.source}|${PAUSED_CHECKPOINT_ACTION_RE.source}`)
      : PYTHON_CHECKPOINT_ACTION_RE;
  }

  function clickCheckpointButtons(options) {
    const videoPaused = Boolean(options && options.videoPaused);
    const pattern = checkpointPattern(videoPaused);
    const xpathCandidates = xpathAll(
      "//*[contains(text(), '我知道了') or contains(text(), '知道了') or contains(text(), '点击通过检查') or contains(text(), '通过检查') or contains(text(), '跳过') or contains(text(), '继续播放') or contains(text(), '继续学习') or contains(text(), '确定') or contains(text(), '确认')]"
    ).filter(visible);
    const buttonCandidates = Array.from(document.querySelectorAll("button, a, [role='button'], .ant-btn, div, span"))
      .filter((element) => visible(element) && pattern.test(compactText(element)));
    const checkpoints = uniqueElements(xpathCandidates.concat(buttonCandidates).map(actionableElement))
      .filter((element) => visible(element))
      .filter((element) => {
        const text = compactText(element);
        return text.length > 0 && text.length <= 120 && pattern.test(text);
      })
      .sort((left, right) => checkpointScore(left) - checkpointScore(right) || elementArea(left) - elementArea(right));

    const checkpoint = checkpoints[0] || null;
    if (!checkpoint) {
      return false;
    }
    const signature = compactText(checkpoint).slice(0, 80);
    const now = Date.now();
    if (signature === lastCheckpointSignature && now - lastCheckpointTapAt < 1500) {
      return false;
    }
    lastCheckpointSignature = signature;
    lastCheckpointTapAt = now;
    const clickInfo = clickElementInfo(checkpoint);
    requestNativeTap(clickInfo, signature || "视频检查点", "checkpoint");
    logAutomation("点击了检查点或答题点", { label: signature });
    return true;
  }

  function checkpointScore(element) {
    const text = compactText(element);
    if (/点击通过检查/.test(text)) {
      return 0;
    }
    if (/跳过/.test(text)) {
      return 1;
    }
    if (/我知道了|知道了/.test(text)) {
      return 2;
    }
    if (/通过检查/.test(text)) {
      return 3;
    }
    if (/继续播放|继续学习/.test(text)) {
      return 4;
    }
    return 8;
  }

  function installCheckpointObserver() {
    if (checkpointObserverInstalled || typeof MutationObserver === "undefined") {
      return;
    }
    const root = document.body || document.documentElement;
    if (!root) {
      return;
    }
    checkpointObserverInstalled = true;
    const observer = new MutationObserver(() => {
      const video = document.querySelector("video");
      if (!automationRunning || !video) {
        return;
      }
      if (clickCheckpointButtons({ videoPaused: Boolean(video.paused), source: "mutation" })) {
        scheduleAutomation(500);
      }
    });
    observer.observe(root, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["class", "style", "hidden", "aria-hidden"]
    });
    document.addEventListener("pause", (event) => {
      if (event.target && event.target.tagName === "VIDEO" && automationRunning) {
        [100, 500, 1000].forEach((delay) => {
          setTimeout(() => {
            if (clickCheckpointButtons({ videoPaused: true, source: "pause" })) {
              scheduleAutomation(300);
            }
          }, delay);
        });
      }
    }, true);
  }

  window.addEventListener("load", () => {
    installCheckpointObserver();
    reportState("load");
    clickAgreementModal();
    if (automationRunning || config.automation_running) {
      startAutomation("load");
    } else if (config.auto_fill_login && isLoginPage()) {
      fillLogin("load", Boolean(config.auto_submit_login));
    }
  });
  installCheckpointObserver();
  document.addEventListener("visibilitychange", () => {
    syncChildSessionStateFromConfig();
    reportState("visibilitychange");
    if (automationRunning && !shouldPauseAutomationForVisibility()) {
      scheduleAutomation(0);
    }
  });
  setInterval(() => {
    if (shouldPauseAutomationForVisibility()) {
      return;
    }
    reportState("interval");
    if (automationRunning) {
      scheduleAutomation(0);
    } else if (config.auto_fill_login && isLoginPage()) {
      fillLogin("interval", false);
    }
  }, 5000);
  reportState("installed");
})();
