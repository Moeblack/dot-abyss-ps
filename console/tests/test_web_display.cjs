const assert = require("node:assert/strict");
const { test } = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const vocabulary = require("../web/vocabulary.js");
const fields = require("../web/fields.js");
const { displayValue, displayTree, originalValue, referenceLabel, recordIdentity,
  nativeIdentityField, sourceRecords, matchesRow, createReferenceLookup } = vocabulary;

function browser() {
  class Element {
    constructor(tag = "div") {
      this.tagName = tag;
      this.nodeType = 1;
      this.value = "";
      this.textContent = "";
      this.children = [];
      this.listeners = new Map();
      this.classList = { add() {}, remove() {}, toggle() {} };
    }
    setAttribute(name, value) { this[name] = value; }
    addEventListener(name, handler) {
      if (!this.listeners.has(name)) this.listeners.set(name, []);
      this.listeners.get(name).push(handler);
    }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = children; }
    emit(name) { for (const handler of this.listeners.get(name) || []) handler({ target: this }); }
    showModal() {}
    close() {}
  }
  const nodes = new Map(), timers = new Map(), requests = [];
  let sequence = 0;
  const document = {
    body: new Element(),
    getElementById(id) { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); },
    createElement: (tag) => new Element(tag),
    createElementNS: (_namespace, tag) => new Element(tag),
    createTextNode: (text) => ({ nodeType: 3, textContent: text }),
    querySelectorAll: () => [], querySelector: () => null, addEventListener() {},
  };
  const window = { ConsoleVocabulary: vocabulary, ConsoleFields: fields,
    ConsoleLauncher: require("../web/launcher.js") };
  const context = { window, document, URLSearchParams, AbortController, location: { search: "" },
    setTimeout(fn, delay) { timers.set(++sequence, { fn, delay }); return sequence; },
    clearTimeout(id) { timers.delete(id); }, setInterval() {},
    fetch(url, options) { return new Promise((resolve) => requests.push({ url, options, resolve })); },
  };
  const source = fs.readFileSync(path.join(__dirname, "../web/app.js"), "utf8");
  assert.match(source, /\sinitialize\(\);\s*\}\)\(\);\s*$/);
  vm.runInNewContext(source.replace(/\sinitialize\(\);\s*\}\)\(\);\s*$/, "\n})();"), context);
  const core = window.ConsoleCore;
  async function tick() {
    for (const [id, timer] of [...timers]) {
      if (timer.delay >= 1000) continue;
      timers.delete(id); timer.fn();
    }
    for (let index = 0; index < 8; index++) await Promise.resolve();
  }
  async function reply(index, labels) {
    requests[index].resolve({ ok: true, json: async () => ({ labels, identity_field: "id" }) });
    await tick();
  }
  function form(specs, values) {
    const result = core.makeForm(specs, values);
    core.state.currentForm = result;
    return result;
  }
  return { core, form, tick, reply, requests };
}

test("nested reward labels preserve IDs, positions and raw values", () => {
  const row = { id: 8, m_character_id: 123, rewards: [
    { content_type: 30, content_id: 99, amount: 2 }, { content_type: 10, content_id: 0, amount: 50 },
  ], _display: { m_character_id: "艾莉西亞", rewards: [{ content_id: "治療藥" }, null] } };
  const before = JSON.stringify(row);
  assert.equal(displayValue(row, "m_character_id"), "艾莉西亞（123）");
  assert.deepEqual(displayValue(row, "rewards"), [
    { content_type: 30, content_id: "治療藥（99）", amount: 2 }, { content_type: 10, content_id: 0, amount: 50 },
  ]);
  assert.equal(JSON.stringify(row), before);
  assert.equal(originalValue(row).m_character_id, 123);
  assert.equal(referenceLabel(404, ""), "ID 404（未匹配名称）");
});

test("preview nested rows expose names without metadata in copyable raw data", () => {
  const value = { examples: [{ before: { id: 1, m_item_id: 7, _display: { m_item_id: "原材料" } },
    after: { id: 1, m_item_id: 8, _display: { m_item_id: "新材料" }, _identity_field: "id" } }] };
  assert.equal(displayTree(value).examples[0].after.m_item_id, "新材料（8）");
  const raw = originalValue(value);
  assert.deepEqual(raw.examples[0].after, { id: 1, m_item_id: 8 });
  assert.doesNotMatch(JSON.stringify(raw), /_display|_identity_field/);
});

test("identity metadata wins over neighboring foreign IDs and null disables selection", () => {
  const row = { m_event_id: 30, m_event_disaster_quest_id: 47 };
  assert.equal(recordIdentity(row, nativeIdentityField("t_event_disaster_quests")), 47);
  assert.equal(recordIdentity({ id: 12 }, null), undefined);
  assert.equal(recordIdentity({ id: 12, _identity_field: null }), undefined);
  assert.deepEqual(vocabulary.preferredColumns([{ ...row, status: 1, amount: 2,
    _identity_field: "m_event_disaster_quest_id", _display: { name: "事件挑战" } }], 2),
  ["m_event_disaster_quest_id", "name"]);
});

test("virtual sources follow only declared own-property paths", () => {
  const snapshot = { state: { records: {} }, record_sources: { t_presents: ["progress", "entries"] },
    progress: { entries: [{ id: 5 }] } };
  assert.deepEqual(sourceRecords(snapshot, "t_presents"), [{ id: 5 }]);
  assert.deepEqual(sourceRecords(snapshot, "unknown"), []);
  snapshot.progress = Object.create({ entries: [{ id: 6 }] });
  assert.deepEqual(sourceRecords(snapshot, "t_presents"), []);
});

test("search accepts original, translated, nested names and full-width IDs", () => {
  const row = { id: 123, name: "炎上の指輪", rewards: [{ content_id: 8 }],
    _display: { name: "燃燒戒指", rewards: [{ content_id: "治療藥" }] } };
  for (const query of ["炎上", "燃燒", "１２３", "治療藥"]) assert.ok(matchesRow(row, query));
  assert.equal(matchesRow(row, "不存在"), false);
});

test("lookup bounds each batch and keeps identical names separate by ID", async () => {
  const calls = [];
  const lookup = createReferenceLookup(async (source, ids) => {
    calls.push({ source, ids });
    return { labels: Object.fromEntries(ids.map((id) => [id, "同名道具"])) };
  });
  const ids = Array.from({ length: 205 }, (_, index) => index + 1);
  const result = await lookup.resolve("m_items", ids);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].ids.length, 100);
  assert.equal(result.entries.length, 100);
  assert.equal(result.omitted, 105);
  assert.notEqual(referenceLabel(result.entries[0].id, result.entries[0].name),
    referenceLabel(result.entries[1].id, result.entries[1].name));
  assert.equal(ids.length, 205);
});

test("picker labels survive filling while submissions retain numeric IDs", async () => {
  const page = browser();
  const form = page.form([{ name: "ids", type: "ids", source: "m_characters" }]);
  form.remember("m_characters", [
    { id: 1, name: "原名", _display: { name: "同名角色" } },
    { id: 2, name: "別名", _display: { name: "同名角色" } },
  ], "id");
  form.set("ids", [1, 2]);
  await page.tick();
  assert.equal(page.requests.length, 0);
  assert.match(form.controls.get("ids").reference.textContent, /同名角色（ID 1）.*同名角色（ID 2）/);
  assert.equal(JSON.stringify(form.read()), '{"ids":[1,2]}');
});

test("late ID responses cannot overwrite a newer input label", async () => {
  const page = browser();
  const form = page.form([{ name: "id", type: "integer", source: "m_items" }], { id: 1 });
  await page.tick();
  form.set("id", 2);
  await page.tick();
  assert.equal(page.requests.length, 2);
  assert.equal(page.requests[0].url, "/admin/resolve");
  assert.deepEqual(JSON.parse(page.requests[1].options.body), { table: "m_items", ids: ["2"] });
  await page.reply(1, { 2: "新道具" });
  await page.reply(0, { 1: "旧道具" });
  assert.equal(form.controls.get("id").reference.textContent, "新道具（ID 2）");
  assert.equal(form.read().id, 2);
});

test("polymorphic source changes clear old IDs and discard stale labels", async () => {
  const page = browser();
  const form = page.form([
    { name: "kind", type: "choice", choices: [{ value: 30, label: "道具" }, { value: 40, label: "角色" }], default: 30 },
    { name: "id", type: "integer", source_by: { field: "kind", values: { 30: "m_items", 40: "m_characters" } } },
  ], { id: 1 });
  await page.tick();
  form.controls.get("kind").input.value = "1";
  form.controls.get("kind").input.emit("change");
  assert.equal(form.controls.get("id").input.value, "");
  form.set("id", 1);
  await page.tick();
  await page.reply(1, { 1: "角色一" });
  await page.reply(0, { 1: "道具一" });
  assert.equal(form.controls.get("id").reference.textContent, "角色一（ID 1）");
  assert.equal(form.read().kind, 40);
});

test("owned names come from the selected snapshot without network calls", async () => {
  const page = browser();
  page.core.state.snapshot = { state: { records: { t_event_disaster_quests: { up: [{ m_event_id: 30, m_event_disaster_quest_id: 47 }] } } },
    record_identity_fields: { t_event_disaster_quests: "m_event_disaster_quest_id" },
    record_display: { t_event_disaster_quests: { 47: { name: "活动挑战" } } } };
  const form = page.form([{ name: "id", type: "integer", source: "t_event_disaster_quests" }], { id: 47 });
  await page.tick();
  assert.equal(page.requests.length, 0);
  assert.equal(form.controls.get("id").reference.textContent, "活动挑战（ID 47）");
});

test("profile switches discard outstanding name lookups", async () => {
  const page = browser();
  const form = page.form([{ name: "id", type: "integer", source: "m_items" }], { id: 1 });
  await page.tick();
  page.core.state.epoch++;
  await page.reply(0, { 1: "旧存档名称" });
  assert.doesNotMatch(form.controls.get("id").reference.textContent, /旧存档名称/);
});

test("a late lookup does not overwrite newer picker labels in the cache", async () => {
  let finish;
  const lookup = createReferenceLookup(() => new Promise((resolve) => { finish = resolve; }));
  const pending = lookup.resolve("m_items", [1]);
  lookup.remember("m_items", [{ id: 1, name: "新名称" }], "id");
  finish({ labels: { 1: "旧名称" } });
  await pending;
  assert.equal((await lookup.resolve("m_items", [1])).entries[0].name, "新名称");
});

test("demo name resolution stays read-only and never accesses a service", async () => {
  const context = { window: {}, URL, Date, Map, Number, JSON, Error };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../web/demo.js"), "utf8"), context);
  const request = context.window.createConsoleDemo();
  const before = JSON.stringify(await request("/admin/profiles/1/snapshot"));
  const names = await request("/admin/resolve", { table: "m_accessories", ids: [32010110, 999] });
  assert.equal(names.labels[32010110], "烈焰指环");
  assert.equal(names.labels[999], undefined);
  assert.equal(JSON.stringify(await request("/admin/profiles/1/snapshot")), before);
  assert.equal(context.fetch, undefined);
});

test("progress display metadata follows object keys and array positions without editing progress", () => {
  const progress = { active: { m_event_id: 9, amount: 2 } };
  const before = JSON.stringify(progress);
  const rows = vocabulary.displayRows(progress, { active: { m_event_id: "复刻活动" } });
  assert.deepEqual(displayValue(rows[0], "value"), { m_event_id: "复刻活动（9）", amount: 2 });
  const array = vocabulary.displayRows([{ m_character_id: 3 }, 7], [{ m_character_id: "角色三" }, "任务七"]);
  assert.equal(displayValue(array[0], "m_character_id"), "角色三（3）");
  assert.equal(displayValue(array[1], "value"), "任务七（7）");
  assert.equal(JSON.stringify(progress), before);
});

test("source hints distinguish an unselected type from currency without a name catalog", async () => {
  const page = browser();
  const form = page.form([
    { name: "kind", type: "choice", required: true, choices: [{ value: 10, label: "货币" }, { value: 30, label: "道具" }] },
    { name: "id", type: "integer", source_by: { field: "kind", values: { 30: "m_items" } } },
  ], { id: 0 });
  await page.tick();
  assert.equal(form.controls.get("id").reference.textContent, "请先选择内容类型。");
  form.controls.get("kind").input.value = "0";
  form.controls.get("kind").input.emit("change");
  form.set("id", 0);
  await page.tick();
  assert.equal(form.controls.get("id").reference.textContent, "此类型没有名称目录，原 ID 保留。");
  assert.equal(page.requests.length, 0);
  assert.equal(form.read().id, 0);
});

test("reference metadata does not make readonly master fields editable or clear their IDs", async () => {
  const page = browser();
  const form = page.form([
    { name: "kind", type: "choice", choices: [{ value: 30, label: "道具" }, { value: 40, label: "角色" }], default: 30 },
    { name: "id", type: "integer", editable: false, source_by: { field: "kind", values: { 30: "m_items", 40: "m_characters" } } },
  ], { id: 7 });
  form.controls.get("kind").input.value = "1";
  form.controls.get("kind").input.emit("change");
  assert.equal(form.controls.get("id").input.value, "7");
  assert.equal(form.controls.get("id").input.disabled, true);
  assert.equal(form.read().id, undefined);
  await page.tick();
  await page.reply(0, { 7: "角色七" });
  assert.equal(form.controls.get("id").reference.textContent, "角色七（ID 7）");
});
