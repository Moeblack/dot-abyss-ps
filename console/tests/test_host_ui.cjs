const assert = require("node:assert/strict");
const { test } = require("node:test");
const { policy, consoleUrl, frameSource, coordinator, demoRequest } = require("../host_web/core.js");

const stopped = { state: "stopped", ready: false, managed: true, can_start: true, can_stop: false, can_restart: false, instance: "0" };
const running = { state: "running", ready: true, managed: true, can_start: false, can_stop: true, can_restart: true,
  origin: "http://127.0.0.1:18083", instance: "1" };
const ticket = { url: "http://127.0.0.1:18083/console/#ticket=local-ticket" };

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

test("service controls honor ownership, availability and busy state", () => {
  assert.equal(policy(stopped).start, true);
  assert.equal(policy(running).restart, true);
  assert.equal(policy(running, "stop").restart, false);
  assert.equal(policy({ ...running, state: "external" }).stop, false);
  assert.equal(policy({ ...running, managed: false }).restart, false);
  assert.equal(policy({ ...stopped, state: "starting" }).start, false);
  assert.equal(policy(null).start, false);
});

test("console tickets cannot navigate away from the configured local console", () => {
  assert.equal(consoleUrl(ticket.url, running, false), ticket.url);
  for (const url of ["https://example.com/console/#ticket=x", "http://127.0.0.1:18084/console/#ticket=x",
    "http://user@127.0.0.1:18083/console/#ticket=x", "http://127.0.0.1:18083/other/#ticket=x",
    "http://127.0.0.1:18083/console/"]) assert.throws(() => consoleUrl(url, running, false));
  assert.throws(() => consoleUrl("https://example.com", running, true));
});

test("demo works from a local file and HTTP without contacting the game port", () => {
  assert.equal(frameSource("/demo-console/?demo=1", true, "file:///E:/project/console/host_web/index.html?demo=1"),
    "file:///E:/project/console/web/index.html?demo=1");
  assert.equal(frameSource("/demo-console/?demo=1", true, "http://127.0.0.1:18084/?demo=1"), "/demo-console/?demo=1");
  assert.equal(frameSource(ticket.url, false, "http://127.0.0.1:18084/"), ticket.url);
});

test("offline shell starts, embeds once per instance, and keeps the frame on cancelled stop", async () => {
  const request = demoRequest();
  let approved = false;
  const host = coordinator({ request, demo: true, confirm: async () => approved });
  await host.boot();
  assert.equal(host.state.status.state, "stopped");
  assert.equal(host.state.frameUrl, "");
  assert.equal(await host.operate("start"), true);
  assert.equal(host.state.frameUrl, "/demo-console/?demo=1");
  const instance = host.state.frameInstance;
  await host.refresh();
  assert.equal(host.state.frameInstance, instance);
  assert.equal(await host.operate("stop"), false);
  assert.equal(host.state.status.ready, true);
  assert.equal(host.state.frameInstance, instance);
  approved = true;
  await host.operate("restart");
  assert.notEqual(host.state.frameInstance, instance);
  await host.operate("stop");
  assert.equal(host.state.frameUrl, "");
  assert.equal(host.state.status.ready, false);
});

test("late status requests cannot replace a completed start", async () => {
  const old = deferred();
  let checks = 0;
  const request = async (path) => {
    if (path === "/host/status") return ++checks === 1 ? stopped : old.promise;
    if (path === "/host/service/start") return running;
    if (path === "/host/console-ticket") return ticket;
    throw new Error(path);
  };
  const host = coordinator({ request });
  await host.boot();
  const pending = host.refresh();
  await host.operate("start");
  old.resolve(stopped);
  await pending;
  assert.equal(host.state.status.ready, true);
  assert.equal(host.state.frameUrl, ticket.url);
  assert.equal(host.state.checking, false);
});

test("repeated start clicks issue one request and clear busy state after failure", async () => {
  const start = deferred();
  let calls = 0;
  const host = coordinator({ request: async (path) => {
    if (path === "/host/status") return stopped;
    if (path === "/host/service/start") { calls++; return start.promise; }
    if (path === "/host/console-ticket") return ticket;
    throw new Error(path);
  } });
  await host.boot();
  const first = host.operate("start");
  assert.equal(await host.operate("start"), false);
  assert.equal(calls, 1);
  start.resolve({ state: "error", ready: false, managed: true, can_start: true, message: "start failed" });
  await first;
  assert.equal(host.state.busy, null);
  assert.equal(host.state.frameUrl, "");
  assert.equal(host.state.status.state, "error");
});

test("polling waits for authentication and ticket failures remain recoverable", async () => {
  const auth = deferred();
  let checks = 0;
  let tickets = 0;
  const host = coordinator({ request: async (path) => {
    if (path === "/host/auth") return auth.promise;
    if (path === "/host/status") { checks++; return running; }
    if (path === "/host/console-ticket") {
      if (++tickets === 1) throw new Error("temporary ticket failure");
      return ticket;
    }
    throw new Error(path);
  } });
  const pending = host.boot("once");
  await host.refresh();
  assert.equal(checks, 0);
  auth.resolve({});
  await pending;
  assert.equal(host.state.frameUrl, "");
  assert.match(host.state.error, /ticket failure/);
  await host.refresh();
  assert.equal(host.state.frameUrl, ticket.url);
  assert.equal(host.state.error, "");
});

test("manual reconnect refreshes a ticket but polling keeps the same frame", async () => {
  let tickets = 0;
  const host = coordinator({ request: async (path) => {
    if (path === "/host/status") return running;
    if (path === "/host/console-ticket") return { url: ticket.url + ++tickets };
    throw new Error(path);
  } });
  await host.boot();
  const first = host.state.frameUrl;
  await host.refresh();
  assert.equal(tickets, 1);
  assert.equal(host.state.frameUrl, first);
  await host.reconnect();
  assert.equal(tickets, 2);
  assert.notEqual(host.state.frameUrl, first);
});

test("a ticket requested before stop cannot reopen the old console", async () => {
  const oldTicket = deferred();
  const host = coordinator({ request: async (path) => {
    if (path === "/host/status") return running;
    if (path === "/host/console-ticket") return oldTicket.promise;
    if (path === "/host/service/stop") return stopped;
    throw new Error(path);
  } });
  const boot = host.boot();
  await Promise.resolve();
  await host.operate("stop");
  oldTicket.resolve(ticket);
  await boot;
  assert.equal(host.state.status.state, "stopped");
  assert.equal(host.state.frameUrl, "");
});
