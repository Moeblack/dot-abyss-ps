const assert = require("node:assert/strict");
const { test } = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {
  parseField,
  normalizeChoices,
  fieldChoices,
  validateJsonNumbers,
} = require("../web/fields.js");

test("integer boundaries reject rounded identifiers", () => {
  assert.equal(parseField({ type: "long" }, "1234567890123"), 1234567890123);
  for (const value of ["9007199254740993", "1.5", "NaN", "Infinity"]) {
    assert.throws(() => parseField({ type: "long" }, value));
  }
  assert.throws(() => parseField({ type: "integer", min: 1, max: 30 }, "31"));
  assert.equal(parseField({ type: "integer", required: false }, ""), undefined);
});

test("typed choices and optional boolean preserve omission", () => {
  assert.deepEqual(
    normalizeChoices([
      { value: 0, label: "保留" },
      { value: false, label: "关闭" },
    ]),
    [
      { value: 0, label: "保留" },
      { value: false, label: "关闭" },
    ],
  );
  const choices = fieldChoices({ type: "boolean", required: false });
  assert.equal(choices[0].value, undefined);
  assert.equal(choices[1].value, true);
  assert.equal(choices[2].value, false);
  assert.equal(fieldChoices({ type: "boolean", default: false }), null);
});

test("ids and json validate nested unsafe numbers", () => {
  assert.deepEqual(parseField({ type: "ids" }, "1，2\n3"), [1, 2, 3]);
  assert.deepEqual(parseField({ type: "json" }, '{"point":12}'), { point: 12 });
  assert.throws(() => parseField({ type: "ids" }, "[9007199254740993]"));
  assert.throws(() =>
    parseField({ type: "json" }, '{"amount":9007199254740993}'),
  );
  assert.throws(() => parseField({ type: "json" }, '{"amount":1e309}'));
  assert.throws(() => validateJsonNumbers({ a: [Infinity] }));
});

test("demo adapter stays isolated while covering all workspace reads", async () => {
  const context = { window: {}, URL, Date, Map, Number, JSON, Error };
  vm.runInNewContext(
    fs.readFileSync(path.join(__dirname, "../web/demo.js"), "utf8"),
    context,
  );
  const request = context.window.createConsoleDemo();
  for (const endpoint of [
    "/admin/status",
    "/admin/schema",
    "/admin/profiles",
    "/admin/profiles/1/snapshot",
    "/admin/profiles/1/history",
    "/admin/rules",
    "/admin/master/tables",
    "/admin/master/table/m_characters",
    "/admin/catalog?table=m_characters",
  ]) {
    assert.ok(await request(endpoint));
  }
  const preview = await request("/admin/profiles/1/preview", {
    operation: "character.growth",
  });
  assert.equal(preview.ticket, "demo-preview");
  await request("/admin/profiles/1/apply", { ticket: preview.ticket });
  const snapshot = await request("/admin/profiles/1/snapshot");
  assert.equal(snapshot.sync.revision, 1);
  assert.equal(context.fetch, undefined);
});
