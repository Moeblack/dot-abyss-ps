(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const demo = new URLSearchParams(location.search).get("demo") === "1";
  const { coordinator, policy, demoRequest, frameSource, translationCoordinator, translationPolicy } = window.ConsoleHost;
  const simulated = demo ? demoRequest() : null;
  const allowed = new Set(["/host/auth", "/host/status", "/host/logs", "/host/console-ticket",
    "/host/service/start", "/host/service/stop", "/host/service/restart",
    "/host/translation/status", "/host/translation/install", "/host/translation/logs"]);
  let frameUrl = "";
  let dismissed = "";
  let readingLogs = false;
  let readingTranslationLogs = false;
  let translationLogState = "";

  async function request(path, body) {
    if (!allowed.has(path)) throw new Error("启动器接口路径无效。");
    if (simulated) return simulated(path, body);
    const abort = new AbortController();
    const timeout = setTimeout(() => abort.abort(), path === "/host/status" ? 10000 : 65000);
    try {
      const response = await fetch(path, {
        method: body === undefined ? "GET" : "POST",
        headers: { Accept: "application/json", "X-Console-Request": "1",
          ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
        credentials: "same-origin", redirect: "error", signal: abort.signal,
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
      const result = await response.json().catch(() => {
        throw Object.assign(new Error("启动器返回格式无效，请重新打开启动器。"), { status: response.status });
      });
      if (!response.ok)
        throw Object.assign(new Error(result?.error?.message || result?.detail || result?.message || `请求失败（${response.status}）`),
          { status: response.status });
      return result;
    } catch (failure) {
      if (failure.name === "AbortError") throw new Error("连接超时。请刷新状态，或查看服务日志。");
      if (failure instanceof TypeError) throw new Error("无法连接本地启动器。请重新打开启动器后重试。");
      throw failure;
    } finally {
      clearTimeout(timeout);
    }
  }

  function confirm(action) {
    const name = action === "stop" ? "停止" : "重启";
    $("confirm-title").textContent = `${name}服务？`;
    $("confirm-action").textContent = `${name}服务`;
    const dialog = $("confirm-dialog");
    dialog.returnValue = "cancel";
    return new Promise((resolve) => {
      dialog.addEventListener("close", () => resolve(dialog.returnValue === "confirm"), { once: true });
      dialog.showModal();
    });
  }

  function render(state) {
    const display = policy(state.status, state.busy);
    const status = state.status || {};
    $("state-label").textContent = display.label;
    $("state-label").title = status.message || "";
    $("state-dot").className = `status-dot ${display.tone}`;
    $("process-id").textContent = status.pid ? `PID ${status.pid}` : "";
    $("start-service").disabled = !display.start;
    $("stop-service").disabled = !display.stop;
    $("restart-service").disabled = !display.restart;
    $("refresh-status").disabled = !!state.busy || state.checking;
    $("start-service").textContent = state.busy === "start" ? "正在启动…" : "启动服务";
    $("restart-service").textContent = state.busy === "restart" ? "正在重启…" : "重启";
    $("stop-service").textContent = state.busy === "stop" ? "正在停止…" : "停止";
    $("service-origin").textContent = status.origin || "等待连接";
    $("data-directory").textContent = status.data_dir || "等待连接";
    $("ownership-label").textContent = display.external ? "外部窗口管理" : status.managed ? "由本启动器管理" : "本机服务";
    $("welcome-title").textContent = status.ready ? "正在连接工作台" : display.transitional ? display.label : state.error ? "连接暂时中断" : "服务尚未启动";
    $("welcome-message").textContent = status.message || (status.ready
      ? "服务已就绪，正在打开存档与游戏控制台。"
      : "启动本地服务后，即可选择存档、进入游戏和管理进度。");
    $("external-message").hidden = !display.external;
    $("welcome-start").disabled = !display.start;
    $("welcome-start").hidden = !!status.ready;
    $("welcome-retry").disabled = !!state.busy || state.checking;
    $("welcome-retry").textContent = status.ready ? "重新连接控制台" : "刷新状态";
    $("log-summary").textContent = status.log_path ? "可查看本次服务运行日志" : "查看最近运行信息";
    $("error-message").textContent = state.error;
    $("error-banner").hidden = !state.error || state.error === dismissed;
    if (frameUrl !== state.frameUrl) {
      frameUrl = state.frameUrl;
      $("frame-loading").hidden = !frameUrl;
      if (frameUrl) $("console-frame").src = frameSource(frameUrl, demo, location.href);
      else $("console-frame").removeAttribute("src");
    }
    $("welcome").hidden = !!frameUrl;
    $("console-stage").hidden = !frameUrl;
  }

  const host = coordinator({ request, changed: render, confirm, demo });
  const translation = translationCoordinator({ request, changed: renderTranslation });

  function setTranslationDetails(open) {
    $("translation-details").hidden = !open;
    $("toggle-translation").setAttribute("aria-expanded", String(open));
    $("toggle-translation").textContent = open ? "收起" : "详情";
    if (open) loadTranslationLogs();
  }

  function renderTranslation(state) {
    const status = state.status || {};
    const display = translationPolicy(state.status, state.submitting, state.error);
    $("translation-state").textContent = display.label;
    $("translation-dot").className = `status-dot ${display.tone}`;
    $("translation-hint").textContent = state.submitting ? "正在准备翻译任务…" : display.message;
    $("install-translation").disabled = !display.canStart;
    $("install-translation").textContent = display.action;
    $("install-translation").title = display.message;
    $("refresh-translation").disabled = state.submitting || state.checking;
    $("translation-phase").textContent = state.submitting ? "正在提交任务…" : status.message || "尚未开始";
    $("translation-percent").textContent = `${state.submitting ? 0 : display.percent}%`;
    $("translation-progress").value = state.submitting ? 0 : display.percent;
    const plugin = status.plugin_version ? `插件 ${status.plugin_version}` : status.installed ? "插件已安装" : "尚未安装翻译";
    const revision = typeof status.translation_revision === "string" ? ` · 译文 ${status.translation_revision.slice(0, 8)}` : "";
    $("translation-version").textContent = plugin + revision + (status.restart_required && status.state === "succeeded" ? " · 下次启动游戏生效" : "");
    const signature = [status.job_id, status.state, status.phase, status.percent].join(":");
    if (translationLogState !== signature) {
      translationLogState = signature;
      if (!$("translation-details").hidden) loadTranslationLogs();
    }
  }

  async function loadTranslationLogs() {
    if (readingTranslationLogs) return;
    readingTranslationLogs = true;
    $("refresh-translation-logs").disabled = true;
    const job = translation.state.status?.job_id;
    try {
      const result = await request("/host/translation/logs");
      if (job === translation.state.status?.job_id)
        $("translation-log").textContent = result.text || "目前还没有翻译安装日志。";
    } catch (failure) {
      if (job === translation.state.status?.job_id)
        $("translation-log").textContent = failure.message || String(failure);
    } finally {
      readingTranslationLogs = false;
      $("refresh-translation-logs").disabled = false;
    }
  }

  async function loadLogs() {
    if (readingLogs) return;
    readingLogs = true;
    $("refresh-logs").disabled = true;
    try {
      const result = await request("/host/logs");
      $("log-content").textContent = result.text || "目前还没有运行日志。";
      $("log-path").textContent = result.path || "尚未创建日志文件";
    } catch (failure) {
      $("log-content").textContent = failure.message || String(failure);
    } finally {
      readingLogs = false;
      $("refresh-logs").disabled = false;
    }
  }
  for (const action of ["start", "stop", "restart"]) {
    $(`${action}-service`).addEventListener("click", () => { dismissed = ""; host.operate(action); });
  }
  $("welcome-start").addEventListener("click", () => { dismissed = ""; host.operate("start"); });
  for (const id of ["refresh-status", "welcome-retry"]) {
    $(id).addEventListener("click", () => { dismissed = ""; host.reconnect(); });
  }
  $("dismiss-error").addEventListener("click", () => { dismissed = host.state.error; render(host.state); });
  $("refresh-logs").addEventListener("click", loadLogs);
  $("install-translation").addEventListener("click", () => {
    setTranslationDetails(true);
    translation.start();
  });
  $("toggle-translation").addEventListener("click", () => setTranslationDetails($("translation-details").hidden));
  $("refresh-translation").addEventListener("click", () => translation.refresh());
  $("refresh-translation-logs").addEventListener("click", loadTranslationLogs);
  $("log-panel").addEventListener("toggle", () => { if ($("log-panel").open) loadLogs(); });
  $("console-frame").addEventListener("load", () => { if (frameUrl) $("frame-loading").hidden = true; });
  function refreshVisible() {
    if (document.hidden) return;
    host.refresh();
    if (host.state.status && host.state.busy !== "auth") translation.refresh();
  }
  document.addEventListener("visibilitychange", refreshVisible);
  const ticket = new URLSearchParams(location.hash.slice(1)).get("ticket");
  if (location.hash) history.replaceState(null, "", location.pathname + location.search);
  $("demo-banner").hidden = !demo;
  render(host.state);
  renderTranslation(translation.state);
  host.boot(ticket).then(() => { if (host.state.status) translation.refresh(); });
  setInterval(refreshVisible, 2500);
})();
