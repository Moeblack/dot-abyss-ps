(function (root, factory) {
  const value = factory();
  if (typeof module === "object" && module.exports) module.exports = value;
  if (root) root.ConsoleHost = value;
})(typeof window !== "undefined" ? window : null, function () {
  "use strict";
  const labels = {
    stopped: "服务未启动",
    starting: "正在启动",
    running: "服务运行中",
    stopping: "正在停止",
    error: "服务需要处理",
    external: "已连接外部服务",
    conflict: "端口被占用",
  };

  function policy(status, busy) {
    const transitional = ["starting", "stopping"].includes(status?.state);
    const external = status?.state === "external";
    return {
      label: labels[status?.state] || "正在连接启动器",
      tone: ["error", "conflict"].includes(status?.state)
        ? "error"
        : external ? "warning" : status?.ready ? "good" : "muted",
      start: !!status?.can_start && !status.ready && !busy && !transitional,
      stop: !!status?.can_stop && !!status.managed && !external && !busy && !transitional,
      restart: !!status?.can_restart && !!status.managed && !external && !busy && !transitional,
      external,
      transitional,
    };
  }

  function consoleUrl(value, status, demo) {
    if (demo) {
      if (value !== "/demo-console/?demo=1") throw new Error("演示控制台地址无效。");
      return value;
    }
    const origin = new URL(status.origin);
    const url = new URL(value);
    if (
      origin.protocol !== "http:" ||
      !["127.0.0.1", "localhost", "[::1]"].includes(origin.hostname) ||
      url.origin !== origin.origin || url.pathname !== "/console/" ||
      url.username || url.password || url.search ||
      !new URLSearchParams(url.hash.slice(1)).get("ticket")
    ) throw new Error("启动器返回了不匹配的控制台地址。");
    return url.href;
  }

  function frameSource(value, demo, location) {
    return demo && new URL(location).protocol === "file:"
      ? new URL("../web/index.html?demo=1", location).href
      : value;
  }

  function coordinator({ request, changed = () => {}, confirm = async () => true, demo = false }) {
    const state = { status: null, busy: null, checking: false, error: "", frameUrl: "", frameInstance: null };
    let generation = 0;
    const emit = () => changed({ ...state });
    function clearFrame() {
      state.frameUrl = "";
      state.frameInstance = null;
    }
    async function attachFrame(current) {
      if (!state.status?.ready || state.frameUrl) return;
      try {
        const result = await request("/host/console-ticket", {});
        if (current !== generation || !state.status?.ready) return;
        state.frameUrl = consoleUrl(result.url, state.status, demo);
        state.frameInstance = state.status.instance;
      } catch (failure) {
        if (current !== generation) return;
        state.error = failure.message || String(failure);
      }
      emit();
    }
    async function acceptStatus(status, current) {
      if (current !== generation) return;
      if (!status || !Object.hasOwn(labels, status.state)) throw new Error("服务状态格式无效。");
      if (!status.ready || state.frameInstance !== status.instance) clearFrame();
      state.status = status;
      state.error = "";
      emit();
      await attachFrame(current);
    }
    async function refresh() {
      if (state.busy || state.checking) return;
      const current = generation;
      state.checking = true;
      emit();
      try {
        await acceptStatus(await request("/host/status"), current);
      } catch (failure) {
        if (current === generation) {
          state.status = null;
          state.error = failure.message || String(failure);
          clearFrame();
        }
      } finally {
        if (current === generation) {
          state.checking = false;
          emit();
        }
      }
    }
    async function boot(ticket) {
      if (ticket && !demo) {
        state.busy = "auth";
        emit();
        try {
          await request("/host/auth", { ticket });
        } catch (failure) {
          state.error = failure.message || String(failure);
          state.busy = null;
          emit();
          return;
        }
        state.busy = null;
      }
      await refresh();
    }
    async function reconnect() {
      if (state.busy) return;
      generation++;
      state.checking = false;
      clearFrame();
      emit();
      await refresh();
    }
    async function operate(action) {
      if (!["start", "stop", "restart"].includes(action) || !policy(state.status, state.busy)[action]) return false;
      state.busy = action;
      emit();
      if (state.status?.ready && action !== "start") {
        let approved = false;
        try {
          approved = await confirm(action);
        } catch (failure) {
          state.error = failure.message || String(failure);
        } finally {
          if (!approved) {
            state.busy = null;
            emit();
          }
        }
        if (!approved) return false;
      }
      const current = ++generation;
      state.checking = false;
      state.error = "";
      clearFrame();
      emit();
      try {
        await acceptStatus(await request(`/host/service/${action}`, {}), current);
        return true;
      } catch (failure) {
        if (current === generation) {
          state.status = null;
          state.error = failure.message || String(failure);
          clearFrame();
        }
        return false;
      } finally {
        if (current === generation) {
          state.busy = null;
          emit();
        }
      }
    }
    return { state, boot, refresh, reconnect, operate };
  }

  function translationPolicy(status, submitting = false, error = "") {
    const running = status?.state === "running" || submitting;
    const failed = status?.state === "failed" || !!error;
    const installed = !!status?.installed;
    const blocked = !!status?.game_running || status?.state === "blocked";
    let label = installed ? (status.enabled ? "已启用" : "已安装，尚未启用") : "未安装";
    if (!status) label = "等待检查";
    if (running) label = installed ? "正在更新" : "正在安装";
    else if (failed) label = "需要处理";
    return {
      label,
      running,
      failed,
      canStart: !!status?.can_start && !running && !blocked && !error,
      action: running ? "正在处理…" : installed ? "更新翻译" : "一键安装",
      tone: failed ? "error" : blocked ? "warning" : installed && status.enabled ? "good" : "muted",
      message: error || (status?.game_running ? "请先退出游戏，再安装或更新翻译。" : "") ||
        status?.reason || status?.message || "安装繁体译文、字体和兼容插件，完成后重新启动游戏生效。",
      percent: Number.isFinite(status?.percent) ? Math.max(0, Math.min(100, status.percent)) : 0,
    };
  }

  function translationCoordinator({ request, changed = () => {} }) {
    const states = new Set(["ready", "blocked", "running", "succeeded", "failed"]);
    const state = { status: null, submitting: false, checking: false, error: "" };
    let generation = 0;
    const emit = () => changed({ ...state });
    function accept(status, current) {
      if (current !== generation) return;
      if (!status || !states.has(status.state) || typeof status.can_start !== "boolean")
        throw new Error("翻译状态格式无效，请刷新后重试。");
      state.status = status;
      state.error = "";
      emit();
    }
    async function refresh() {
      if (state.submitting || state.checking) return;
      const current = generation;
      state.checking = true;
      emit();
      try {
        accept(await request("/host/translation/status"), current);
      } catch (failure) {
        if (current === generation) {
          state.status = null;
          state.error = failure.message || String(failure);
        }
      } finally {
        if (current === generation) {
          state.checking = false;
          emit();
        }
      }
    }
    async function start() {
      if (!translationPolicy(state.status, state.submitting, state.error).canStart) return false;
      const current = ++generation;
      state.submitting = true;
      state.checking = false;
      state.error = "";
      emit();
      try {
        accept(await request("/host/translation/install", {}), current);
        return true;
      } catch (failure) {
        if (current === generation) state.error = failure.message || String(failure);
        return false;
      } finally {
        if (current === generation) {
          state.submitting = false;
          emit();
        }
      }
    }
    return { state, refresh, start };
  }

  function demoRequest() {
    let instance = 0;
    let running = false;
    const log = ["演示启动器已就绪。所有操作均使用本页模拟数据。"];
    const translationLog = ["演示翻译功能已就绪，没有连接任何下载服务。"];
    let translationJob = 0;
    let translationStep = 0;
    let translated = false;
    let translating = false;
    const phases = [
      ["preparing", "准备翻译环境", 5], ["sources", "读取译文与插件源码", 25],
      ["dependencies", "校验字体和依赖", 50], ["build", "构建兼容插件", 75],
      ["install", "备份并安装译文", 90], ["complete", "演示翻译安装完成", 100],
    ];
    const translationStatus = () => {
      const [phase, message, percent] = phases[translationStep];
      return {
        state: translating ? "running" : translated ? "succeeded" : "ready",
        installed: translated, enabled: translated, can_start: !translating, game_running: false,
        language: "zh_Hant", reason: "", message: translationJob ? message : "可体验繁体翻译的安装流程。",
        phase: translationJob ? phase : "idle", percent: translationJob ? percent : 0,
        job_id: translationJob ? `demo-translation-${translationJob}` : null,
        plugin_version: translated ? "1.2.0" : null, translation_revision: translated ? "demo-revision" : null,
        restart_required: translated,
      };
    };
    const status = () => ({
      state: running ? "running" : "stopped", ready: running, managed: true,
      can_start: !running, can_stop: running, can_restart: running,
      origin: "http://127.0.0.1:18083", data_dir: "演示存档目录", pid: running ? 1000 + instance : null,
      log_path: "演示日志", message: running ? "演示服务已就绪。" : "点击启动，查看完整控制台。",
      instance: String(instance),
    });
    return async (path) => {
      if (path === "/host/translation/status") {
        if (translating) {
          translationStep = Math.min(translationStep + 1, phases.length - 1);
          translationLog.push(phases[translationStep][1]);
          if (translationStep === phases.length - 1) { translated = true; translating = false; }
        }
        return translationStatus();
      }
      if (path === "/host/translation/install") {
        if (translating) throw new Error("演示翻译任务正在进行。");
        translationJob++;
        translationStep = 0;
        translating = true;
        translationLog.push(phases[0][1]);
        return translationStatus();
      }
      if (path === "/host/translation/logs") return { text: translationLog.join("\n"), path: "演示翻译日志" };
      if (path === "/host/status" || path === "/host/auth") return status();
      if (path === "/host/logs") return { text: log.join("\n"), path: "演示日志" };
      if (path === "/host/console-ticket") {
        if (!running) throw new Error("请先启动演示服务。");
        return { url: "/demo-console/?demo=1" };
      }
      if (!["/host/service/start", "/host/service/stop", "/host/service/restart"].includes(path))
        throw new Error("未知演示操作。");
      running = path !== "/host/service/stop";
      if (running) instance++;
      log.push(running ? `演示服务已启动，第 ${instance} 次运行。` : "演示服务已停止，存档保持不变。");
      return status();
    };
  }
  return { policy, consoleUrl, frameSource, coordinator, translationPolicy, translationCoordinator, demoRequest };
});
