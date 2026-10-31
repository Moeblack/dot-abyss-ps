window.createConsoleDemo = function () {
  const groups = [
    ["currency", "货币与体力"],
    ["inventory", "背包与材料"],
    ["character", "角色与成长"],
    ["appearance", "服装与外观"],
    ["equipment", "装备与魔晶石"],
    ["party", "编队与预设"],
    ["building", "设施与研究"],
    ["story", "剧情与教程"],
    ["mission", "任务与邮件"],
    ["gacha", "抽卡与代币"],
    ["shop", "商城与权益"],
    ["tavern", "酒馆"],
    ["idle", "探索队"],
    ["battle", "常驻战斗"],
    ["event", "活动与复刻"],
    ["profile", "玩家资料"],
  ].map(([id, label]) => ({ id, label }));
  const batch = [
    {
      name: "ids",
      label: "角色",
      type: "ids",
      source: "m_characters",
      default: [101, 102],
    },
    {
      name: "all",
      label: "应用到全部已有角色",
      type: "boolean",
      default: false,
    },
  ];
  const actions = [
    {
      id: "character.growth",
      group: "character",
      label: "调整角色成长",
      description:
        "设置已有角色的等级、突破和经验。技能上限与相关条件会由服务端一起校验，留空的字段保持不变。",
      timing: "next_battle",
      fields: [
        ...batch,
        {
          name: "level",
          label: "等级",
          type: "integer",
          min: 1,
          max: 30,
          default: 25,
        },
        { name: "exp", label: "累计经验", type: "long" },
        {
          name: "limit_break_count",
          label: "突破次数",
          type: "integer",
          min: 0,
          max: 10,
        },
        { name: "core_point", label: "突破核心点", type: "integer", min: 0 },
      ],
    },
    {
      id: "character.unlock",
      group: "character",
      label: "解锁角色",
      description: "按选择获得角色，同时补齐默认外观与技能关联记录。",
      timing: "page",
      fields: batch,
    },
    {
      id: "character.affection",
      group: "character",
      label: "好感与羁绊",
      description: "调整好感度与可分配羁绊点，检查对应剧情和员工解锁条件。",
      timing: "page",
      fields: [
        ...batch,
        { name: "intimacy_point", label: "好感度", type: "integer" },
        { name: "bond_point", label: "羁绊点", type: "integer" },
      ],
    },
    {
      id: "character.skills",
      group: "character",
      label: "调整技能等级",
      description: "按角色当前成长条件校验技能等级。",
      timing: "next_battle",
      fields: [
        ...batch,
        { name: "level", label: "技能等级", type: "integer", min: 1, max: 10 },
      ],
    },
    {
      id: "character.bond",
      group: "character",
      label: "羁绊属性分配",
      description: "设置各项羁绊属性等级。",
      timing: "next_battle",
      fields: [
        ...batch,
        {
          name: "allocations",
          label: "属性分配",
          type: "json",
          default: { attack: 1, hp: 1 },
        },
      ],
    },
    {
      id: "character.resistance",
      group: "character",
      label: "异常抗性",
      description: "调整角色的异常状态抗性成长。",
      timing: "next_battle",
      fields: batch,
    },
    {
      id: "currency.set",
      group: "currency",
      label: "修改货币余额",
      description: "设置指定货币的本地余额。",
      timing: "live",
      fields: [
        {
          name: "kind",
          label: "货币类型",
          type: "choice",
          choices: [
            { value: "gold", label: "金币" },
            { value: "free_currency", label: "免费宝石" },
          ],
          default: "gold",
        },
        {
          name: "amount",
          label: "数量",
          type: "integer",
          min: 0,
          default: 1000000,
        },
      ],
    },
    {
      id: "appearance.unlock",
      group: "appearance",
      label: "解锁外观",
      description: "解锁当前版本已有服装或武器外观。",
      timing: "page",
      fields: [
        {
          name: "kind",
          label: "外观类型",
          type: "choice",
          choices: [
            { value: "character", label: "角色服装" },
            { value: "weapon", label: "武器外观" },
          ],
          default: "character",
        },
        {
          name: "ids",
          label: "外观",
          type: "ids",
          source_by: {
            field: "kind",
            values: {
              character: "m_character_skins",
              weapon: "m_weapon_skins",
            },
          },
        },
      ],
    },
    {
      id: "story.unlock",
      group: "story",
      label: "解锁剧情回想",
      description: "只解锁阅读入口，不会自动重复发放阅读奖励。",
      timing: "page",
      fields: [
        {
          name: "ids",
          label: "剧情",
          type: "ids",
          source: "m_novel_characters",
        },
        {
          name: "all",
          label: "解锁全部已有剧情",
          type: "boolean",
          default: false,
        },
      ],
    },
    {
      id: "shop.pass",
      group: "shop",
      label: "调整通行证权益",
      description: "留空项保持不变。高级权益和等级奖励领取状态分开管理。",
      timing: "page",
      fields: [
        { name: "id", label: "通行证", type: "integer", default: 1 },
        { name: "point", label: "通行证经验", type: "integer" },
        { name: "premium", label: "高级权益", type: "boolean" },
      ],
    },
    {
      id: "equipment.edit",
      group: "equipment",
      label: "修改饰品属性",
      description:
        "使用合成饰品数据检查长名称、字段显示和选择器；不会修改真实存档。",
      timing: "next_battle",
      fields: [
        {
          name: "equipment_id",
          label: "已持有饰品",
          type: "integer",
          source: "t_accessories",
          default: 1,
        },
        {
          name: "master_id",
          label: "饰品定义",
          type: "integer",
          source: "m_accessories",
          default: 31010110,
        },
        { name: "up_count", label: "属性参数", type: "integer", default: 15 },
      ],
    },
    ...[
      "inventory",
      "party",
      "building",
      "mission",
      "gacha",
      "tavern",
      "idle",
      "battle",
      "event",
      "profile",
    ].map((group) => ({
      id: group + ".demo",
      group,
      label: groups.find((item) => item.id === group).label + "设置",
      description: "这是用于界面检查的合成操作；实际可用能力从服务端读取。",
      timing: "page",
      fields: [
        { name: "id", label: "记录 ID", type: "integer", default: 1 },
        { name: "value", label: "设置值", type: "integer" },
      ],
    })),
  ];
  const rows = [
    { id: 101, name: "角色示例 A", rarity: 3, level: 25 },
    { id: 102, name: "角色示例 B", rarity: 3, level: 20 },
    { id: 103, name: "角色示例 C", rarity: 2, level: 15 },
    { id: 104, name: "角色示例 D", rarity: 2, level: 10 },
  ];
  const accessoryRows = [
    {
      id: 31010110,
      asset_id: 43001001,
      name: "試験用の指輪",
      group_no: 10001,
      quality_type: 1,
      effect_type: 2,
      limit_count: 999,
      display_order: 1,
      description: "表示確認用の合成データ",
      _display: {
        name: "界面演示专用的星光守护指环：用于核对完整长名称和窄面板横向滚动",
        description: "只用于界面验证的合成数据。",
      },
    },
    {
      id: 32010110,
      asset_id: 43001002,
      name: "炎の指輪",
      group_no: 20001,
      quality_type: 2,
      effect_type: 3,
      limit_count: 999,
      display_order: 2,
      description: "合成データ",
      _display: { name: "烈焰指环", description: "只用于界面验证。" },
    },
    {
      id: 33010110,
      asset_id: 43001003,
      name: "氷の指輪",
      group_no: 30001,
      quality_type: 3,
      effect_type: 4,
      limit_count: 999,
      display_order: 3,
      description: "合成データ",
      _display: { name: "冰霜指环", description: "只用于界面验证。" },
    },
  ];
  let revision = 0;
  const records = {
    t_characters: { up: rows, down: [] },
    t_accessories: {
      up: [
        {
          id: 1,
          user_id: 1,
          m_accessory_id: 31010110,
          up_count: 15,
          quality_type: 1,
          is_lock: 0,
          is_appraisal: 1,
          appraisal_count: 2,
        },
      ],
      down: [],
    },
    t_users: {
      up: [
        { id: 1, name: "界面演示存档", gold: 1000000, free_currency: 30000 },
      ],
      down: [],
    },
  };
  const history = [];
  const profiles = [
    {
      id: 1,
      name: "demo-main",
      label: "第一次冒险",
      player_name: "司令官",
      has_state: true,
      master_revision: "demo",
      online: false,
    },
  ];
  const launcher = {
    available: true,
    running: false,
    profile_id: null,
    configured_profile_id: 1,
    can_start: true,
    reason: "",
  };
  const createdProfiles = new Map();
  return async function (path, body) {
    const url = new URL(path, "http://demo.invalid"),
      route = url.pathname;
    if (route === "/admin/status")
      return {
        service: "dotabyss-console",
        enabled: true,
        version: 1,
        master: { revision: "演示主数据 · 1.15.0" },
      };
    if (route === "/admin/schema") return { groups, actions };
    if (route === "/admin/launcher/status") return { ...launcher };
    if (route === "/admin/launcher/start") {
      const selected = profiles.find(
        (profile) => profile.id === body.profile_id,
      );
      if (!selected) throw new Error("请选择有效的演示存档。");
      if (launcher.running && launcher.profile_id !== selected.id)
        throw new Error("演示游戏正在使用另一份存档，请保留现有运行状态。");
      launcher.running = true;
      launcher.profile_id = selected.id;
      launcher.profile_name = selected.label;
      selected.online = true;
      return { ...launcher, message: "演示游戏已启动；没有运行真实程序。" };
    }
    if (route === "/admin/profiles" && body) {
      if (createdProfiles.has(body.operation_id))
        return {
          profile: createdProfiles.get(body.operation_id),
          created: false,
        };
      if (profiles.some((profile) => profile.label === body.label))
        throw new Error("这个存档名称已经存在。");
      const profile = {
        id: profiles.length + 1,
        name: "demo-" + (profiles.length + 1),
        label: body.label,
        player_name: body.player_name || body.label,
        has_state: true,
        master_revision: "demo",
        online: false,
      };
      profiles.push(profile);
      createdProfiles.set(body.operation_id, profile);
      return { profile, created: true };
    }
    if (/^\/admin\/profiles\/\d+\/label$/.test(route)) {
      const profile = profiles.find(
        (item) => item.id === Number(route.split("/")[3]),
      );
      if (!profile || profile.label !== body.expected_label)
        throw new Error("存档名称已经变化，请刷新后再试。");
      if (
        profiles.some((item) => item !== profile && item.label === body.label)
      )
        throw new Error("这个存档名称已经存在。");
      profile.label = body.label;
      return { profile };
    }
    if (route === "/admin/profiles")
      return { profiles: profiles.map((profile) => ({ ...profile })) };
    if (route.endsWith("/snapshot"))
      return {
        profile_id: Number(route.split("/")[3]),
        version: "demo" + revision,
        master_revision: "demo",
        state: { records },
        record_identity_fields: { t_characters: "id", t_accessories: "id", t_users: "id" },
        record_display: {
          t_accessories: {
            1: {
              name: accessoryRows[0]._display.name,
              m_accessory_id: accessoryRows[0]._display.name,
            },
          },
        },
        progress: { story: { chapter: 1 } },
        sync: {
          revision,
          ack_revision: revision,
          status: revision ? "applied" : "unchanged",
          client_connected:
            launcher.running &&
            launcher.profile_id === Number(route.split("/")[3]),
        },
      };
    if (route.endsWith("/history")) return { entries: history };
    if (route.endsWith("/preview"))
      return {
        ticket: "demo-preview",
        summary: "预览修改 · 合成数据",
        timing: body.operation?.startsWith("character")
          ? "next_battle"
          : "page",
        changes: [{ table: "t_characters", added: 0, updated: 2, removed: 0 }],
        warnings: ["这是界面演示；应用操作不会连接或修改真实游戏存档。"],
      };
    if (route.endsWith("/apply")) {
      revision++;
      history.unshift({
        id: revision,
        at: Math.floor(Date.now() / 1000),
        operation: "demo.apply",
        summary: "演示修改",
        timing: "page",
        status: "saved",
      });
      return {
        version: "demo" + revision,
        revision,
        status: "saved",
        summary: "演示修改已完成",
        timing: "page",
      };
    }
    if (route.endsWith("/backup")) {
      history.unshift({
        id: ++revision,
        at: Math.floor(Date.now() / 1000),
        operation: "save.backup",
        summary: "演示备份",
        timing: "relogin",
        status: "backup",
      });
      return { summary: "演示备份已创建" };
    }
    if (route.endsWith("/export"))
      return {
        format: "demo",
        profile_id: 1,
        master_revision: "demo",
        state: { records },
        progress: {},
      };
    if (route === "/admin/rules")
      return {
        version: "demo-rules",
        values: {
          character_level_cap: 30,
          starting_gold: 1000000,
          event_open_all: 1,
          event_open_ids: [],
          gacha_expired_token_gold_per_token: 1,
        },
        specs: [
          {
            name: "character_level_cap",
            label: "角色等级上限",
            type: "integer",
            min: 1,
            max: 30,
          },
          {
            name: "starting_gold",
            label: "新存档初始金币",
            type: "integer",
            min: 0,
          },
          {
            name: "event_open_all",
            label: "开放全部活动",
            type: "integer",
            min: 0,
            max: 1,
          },
          { name: "event_open_ids", label: "指定开放活动", type: "json" },
          {
            name: "gacha_expired_token_gold_per_token",
            label: "过期代币兑换比例",
            type: "integer",
            min: 0,
          },
        ],
      };
    if (route === "/admin/master/tables")
      return {
        tables: [
          { name: "m_characters", count: 4, editable: true },
          { name: "m_character_skins", count: 4, editable: true },
          { name: "m_events", count: 4, editable: true },
          { name: "m_accessories", count: 3, editable: true },
        ],
      };
    if (route === "/admin/resolve") {
      if (!body || !Array.isArray(body.ids) || body.ids.length > 100)
        throw new Error("演示名称查询每次最多 100 个 ID。");
      const selected = new Set(body.ids.map(String));
      const definitions = body.table === "m_accessories" ? accessoryRows : rows;
      return { labels: Object.fromEntries(definitions.filter((row) => selected.has(String(row.id)))
        .map((row) => [String(row.id), row._display?.name || row.name])), identity_field: "id", version: "demo-master" };
    }
    const source = url.searchParams.get("table") || route.split("/").pop();
    const sourceRows = source === "m_accessories" ? accessoryRows : rows;
    const q = (url.searchParams.get("q") || "").toLowerCase(),
      offset = Number(url.searchParams.get("offset") || 0),
      filtered = sourceRows.filter((row) =>
        JSON.stringify(row).toLowerCase().includes(q),
      );
    if (route === "/admin/catalog")
      return {
        rows: filtered.slice(offset, offset + 100),
        total: filtered.length,
        identity_field: "id",
      };
    if (route.startsWith("/admin/master/table/"))
      return {
        version: "demo-master",
        identity_field: "id",
        rows: filtered.slice(offset, offset + 100).map((row, index) => {
          const { _display, ...values } = row;
          return { index, values, display: _display || {} };
        }),
        fields:
          source === "m_accessories"
            ? Object.keys(accessoryRows[0])
                .filter((name) => !name.startsWith("_"))
                .map((name) => ({
                  name,
                  type:
                    typeof accessoryRows[0][name] === "number"
                      ? "long"
                      : "string",
                  editable: !["id", "asset_id"].includes(name),
                }))
            : [
                { name: "id", type: "long", editable: false },
                { name: "name", type: "string", editable: false },
                { name: "rarity", type: "integer", editable: false },
                { name: "level", type: "integer", editable: true },
              ],
        total: filtered.length,
      };
    throw new Error("演示模式不提供该操作：" + route);
  };
};
