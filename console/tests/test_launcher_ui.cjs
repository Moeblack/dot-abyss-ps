const assert = require("node:assert/strict");
const { test } = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {
  tableLabel,
  fieldLabel,
  displayValue,
  preferredColumns,
  terms,
} = require("../web/vocabulary.js");
const {
  initialProfile,
  launchState,
  profileLabel,
} = require("../web/launcher.js");

test("known source names are readable and still distinguish definitions from saves", () => {
  assert.equal(tableLabel("m_accessories"), "饰品（基础数据）");
  assert.equal(tableLabel("t_accessories"), "饰品（存档数据）");
  assert.equal(tableLabel("t_weapon_enchantments"), "武器附魔（存档数据）");
  assert.equal(fieldLabel("asset_id"), "资源编号");
  assert.equal(
    fieldLabel("m_character_condition_resistance_level_id"),
    "角色异常抗性等级编号",
  );
  const index = path.resolve(
    __dirname,
    "../../artifacts/pc-content/index.json",
  );
  if (fs.existsSync(index)) {
    const tables = Object.keys(
      JSON.parse(fs.readFileSync(index, "utf8")).tables,
    );
    assert.deepEqual(
      [
        ...new Set(
          tables
            .flatMap((name) => name.split("_").slice(1))
            .filter((part) => !terms[part]),
        ),
      ],
      [],
    );
  }
});

test("translated display names never replace editable raw fields", () => {
  const row = {
    id: 1,
    name: "炎上の指輪",
    asset_id: 100,
    _display: { name: "烈焰指環" },
  };
  const before = JSON.stringify(row);
  assert.equal(displayValue(row, "name"), "烈焰指環");
  assert.equal(displayValue(row, "asset_id"), 100);
  assert.deepEqual(preferredColumns([row], 3), ["id", "name", "asset_id"]);
  assert.equal(JSON.stringify(row), before);
  assert.deepEqual(
    preferredColumns(
      [{ id: 3, m_weapon_id: 1, _display: { name: "鐵劍" } }],
      2,
    ),
    ["id", "name"],
  );
});

test("initial selection prioritizes the running save and remembers explicit choices otherwise", () => {
  const profiles = [
    { id: 1, name: "old", has_state: true },
    { id: 4, name: "new", label: "第二次冒险", has_state: true },
  ];
  assert.equal(
    initialProfile(profiles, { running: true, profile_id: 4 }, "1"),
    4,
  );
  assert.equal(
    initialProfile(profiles, { running: false, configured_profile_id: 4 }, "1"),
    1,
  );
  assert.equal(
    initialProfile(
      profiles,
      { running: false, configured_profile_id: 4 },
      "99",
    ),
    4,
  );
  assert.equal(initialProfile([], {}, null), null);
  assert.equal(profileLabel(profiles[1]), "第二次冒险");
});

test("starting cannot silently switch a running game to another save", () => {
  assert.equal(
    launchState({ available: true, running: false, can_start: true }, 4, true)
      .disabled,
    false,
  );
  const blocked = launchState(
    { available: true, running: true, profile_id: 1 },
    4,
    true,
  );
  assert.equal(blocked.disabled, true);
  assert.match(blocked.message, /另一份存档/);
  assert.equal(
    launchState({ available: true, running: false }, 4, false).disabled,
    true,
  );
  assert.equal(
    launchState({ available: false, reason: "需要更新" }, 4, true).message,
    "需要更新",
  );
});

test("demo supports profile creation rename and launch without external requests", async () => {
  const context = { window: {}, URL, Date, Map, Number, JSON, Error };
  vm.runInNewContext(
    fs.readFileSync(path.join(__dirname, "../web/demo.js"), "utf8"),
    context,
  );
  const request = context.window.createConsoleDemo();
  const created = await request("/admin/profiles", {
    label: "新冒险",
    player_name: "玩家",
    operation_id: "test-1",
  });
  const retry = await request("/admin/profiles", {
    label: "新冒险",
    player_name: "玩家",
    operation_id: "test-1",
  });
  assert.equal(created.profile.id, retry.profile.id);
  assert.equal((await request("/admin/profiles")).profiles.length, 2);
  const renamed = await request(`/admin/profiles/${created.profile.id}/label`, {
    label: "重命名",
    expected_label: "新冒险",
  });
  assert.equal(renamed.profile.label, "重命名");
  await assert.rejects(
    request(`/admin/profiles/${created.profile.id}/label`, {
      label: "旧窗口",
      expected_label: "新冒险",
    }),
  );
  await request("/admin/launcher/start", { profile_id: created.profile.id });
  assert.equal(
    (await request("/admin/launcher/status")).profile_id,
    created.profile.id,
  );
  await assert.rejects(request("/admin/launcher/start", { profile_id: 1 }));
  assert.equal(context.fetch, undefined);
});
