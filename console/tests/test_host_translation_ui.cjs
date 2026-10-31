const assert = require("node:assert/strict");
const { test } = require("node:test");
const { coordinator, translationPolicy, translationCoordinator, demoRequest } = require("../host_web/core.js");

const ready = { state: "ready", installed: false, enabled: false, can_start: true, game_running: false,
  language: "zh_Hant", reason: "", message: "准备安装", phase: "idle", percent: 0, job_id: null };
const running = { ...ready, state: "running", can_start: false, phase: "build", percent: 65, job_id: "job-1" };
const complete = { ...ready, state: "succeeded", installed: true, enabled: true, phase: "complete", percent: 100,
  job_id: "job-1", plugin_version: "1.2.0", translation_revision: "translation-1", restart_required: true };

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

test("translation controls require availability and honor game/process state", () => {
  assert.equal(translationPolicy(null).canStart, false);
  assert.equal(translationPolicy(ready).action, "一键安装");
  assert.equal(translationPolicy(ready).canStart, true);
  assert.equal(translationPolicy(complete).action, "更新翻译");
  assert.equal(translationPolicy(running).canStart, false);
  assert.equal(translationPolicy(ready, true).canStart, false);
  const playing = translationPolicy({ ...complete, game_running: true });
  assert.equal(playing.canStart, false);
  assert.match(playing.message, /退出游戏/);
  assert.equal(translationPolicy({ ...ready, state: "blocked" }).canStart, false);
  assert.equal(translationPolicy(ready, false, "offline").canStart, false);
});

test("display progress is bounded without changing the server state", () => {
  assert.equal(translationPolicy({ ...running, percent: 120 }).percent, 100);
  assert.equal(translationPolicy({ ...running, percent: -5 }).percent, 0);
  assert.equal(translationPolicy({ ...running, percent: NaN }).percent, 0);
  assert.equal(translationPolicy(running).percent, 65);
  assert.equal(running.percent, 65);
});

test("repeated clicks start one background job and polling follows its progress", async () => {
  const start = deferred();
  let installs = 0;
  let status = ready;
  const translation = translationCoordinator({ request: async (path, body) => {
    if (path === "/host/translation/status") return status;
    if (path === "/host/translation/install") { installs++; assert.deepEqual(body, {}); return start.promise; }
    throw new Error(path);
  } });
  await translation.refresh();
  const pending = translation.start();
  assert.equal(await translation.start(), false);
  assert.equal(installs, 1);
  assert.equal(translation.state.submitting, true);
  start.resolve(running);
  assert.equal(await pending, true);
  assert.equal(translation.state.status.job_id, "job-1");
  assert.equal(translation.state.submitting, false);
  status = complete;
  await translation.refresh();
  assert.equal(translation.state.status.installed, true);
  assert.equal(translation.state.status.percent, 100);
});

test("a stale pre-install status cannot replace the newly created job", async () => {
  const old = deferred();
  let checks = 0;
  const translation = translationCoordinator({ request: async (path) => {
    if (path === "/host/translation/status") return ++checks === 1 ? ready : old.promise;
    if (path === "/host/translation/install") return running;
    throw new Error(path);
  } });
  await translation.refresh();
  const poll = translation.refresh();
  await translation.start();
  old.resolve(ready);
  await poll;
  assert.equal(translation.state.status.state, "running");
  assert.equal(translation.state.status.job_id, "job-1");
  assert.equal(translation.state.checking, false);
});

test("translation API failure does not clear the service console frame", async () => {
  const request = async (path) => {
    if (path === "/host/status") return { state: "running", ready: true, managed: true,
      origin: "http://127.0.0.1:18083", instance: "service-1" };
    if (path === "/host/console-ticket") return { url: "http://127.0.0.1:18083/console/#ticket=local-ticket" };
    if (path === "/host/translation/status") throw new Error("Translation network failure");
    throw new Error(path);
  };
  const host = coordinator({ request });
  const translation = translationCoordinator({ request });
  await host.boot();
  const frame = host.state.frameUrl;
  await translation.refresh();
  assert.match(translation.state.error, /network failure/);
  assert.equal(host.state.frameUrl, frame);
  assert.equal(host.state.error, "");
  assert.equal(host.state.status.ready, true);
});

test("failed install leaves retry gated until status is refreshed", async () => {
  let broken = true;
  let installs = 0;
  const translation = translationCoordinator({ request: async (path) => {
    if (path === "/host/translation/status") return complete;
    if (path === "/host/translation/install") {
      installs++;
      if (broken) throw new Error("Game started before install");
      return running;
    }
    throw new Error(path);
  } });
  await translation.refresh();
  assert.equal(await translation.start(), false);
  assert.equal(translation.state.submitting, false);
  assert.equal(translation.state.status.installed, true);
  assert.equal(await translation.start(), false);
  assert.equal(installs, 1);
  broken = false;
  await translation.refresh();
  assert.equal(await translation.start(), true);
  assert.equal(installs, 2);
});

test("malformed status disables mutation and can recover on the next refresh", async () => {
  let status = { ...ready, can_start: "yes" };
  const translation = translationCoordinator({ request: async () => status });
  await translation.refresh();
  assert.equal(translation.state.status, null);
  assert.match(translation.state.error, /格式无效/);
  assert.equal(await translation.start(), false);
  status = ready;
  await translation.refresh();
  assert.equal(translation.state.error, "");
  assert.equal(translation.state.status.can_start, true);
});

test("a failed background job preserves installed status and allows repair", async () => {
  const failed = { ...complete, state: "failed", message: "下载校验失败，保留原翻译" };
  const translation = translationCoordinator({ request: async () => failed });
  await translation.refresh();
  const view = translationPolicy(translation.state.status);
  assert.equal(view.failed, true);
  assert.equal(view.canStart, true);
  assert.equal(view.action, "更新翻译");
  assert.match(view.message, /保留原翻译/);
});

test("demo installation and update complete without changing service state or using fetch", async () => {
  const request = demoRequest();
  const translation = translationCoordinator({ request });
  await translation.refresh();
  assert.equal(translation.state.status.installed, false);
  assert.equal(await translation.start(), true);
  assert.equal(translation.state.status.state, "running");
  for (let step = 0; step < 5; step++) await translation.refresh();
  assert.equal(translation.state.status.state, "succeeded");
  assert.equal(translation.state.status.installed, true);
  assert.equal(translation.state.status.restart_required, true);
  const first = translation.state.status.job_id;
  await translation.start();
  assert.notEqual(translation.state.status.job_id, first);
  assert.equal((await request("/host/status")).state, "stopped");
  assert.match((await request("/host/translation/logs")).text, /演示翻译安装完成/);
});
