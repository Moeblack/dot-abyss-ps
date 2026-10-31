(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const own = (value, key) => Object.prototype.hasOwnProperty.call(value, key);
  const pretty = (value) => JSON.stringify(value, null, 2);
  const compact = (value, max = 80) => {
    const text =
      value == null
        ? "—"
        : typeof value === "object"
          ? JSON.stringify(value)
          : String(value);
    return text.length > max ? text.slice(0, max - 1) + "…" : text;
  };
  const labels = window.ConsoleLabels || {};
  const { tableLabel, fieldLabel, displayValue, displayTree, originalValue, rowName,
    referenceLabel, nativeIdentityField, recordIdentity, sourceRecords, displayRows, matchesRow, createReferenceLookup, preferredColumns } =
    window.ConsoleVocabulary;
  const { profileLabel, activeProfileId, initialProfile, launchState } =
    window.ConsoleLauncher;
  const demo = new URLSearchParams(location.search).get("demo") === "1";
  const demoRequest = demo ? window.createConsoleDemo() : null;
  const state = {
    schema: { groups: [], actions: [] },
    status: {},
    profiles: [],
    profile: null,
    snapshot: {},
    group: "overview",
    epoch: 0,
    view: 0,
    editor: 0,
    pending: 0,
    preview: null,
    currentAction: null,
    currentForm: null,
    selectedHistory: null,
    connected: false,
    launcher: null,
    launching: false,
  };
  const groupNames = {
    overview: "连接与概况",
    currency: "货币与体力",
    inventory: "背包与材料",
    character: "角色与成长",
    characters: "角色与成长",
    affection: "好感与羁绊",
    appearance: "服装与外观",
    equipment: "装备与魔晶石",
    party: "编队与预设",
    building: "设施与研究",
    story: "剧情与教程",
    mission: "任务与邮件",
    missions: "任务与邮件",
    gacha: "抽卡与代币",
    shop: "商城与权益",
    tavern: "酒馆",
    idle: "探索队",
    battle: "常驻战斗",
    event: "活动与复刻",
    events: "活动与复刻",
    profile: "玩家资料",
    social: "社交与主页",
    rules: "本地规则",
    master: "主数据",
    history: "操作记录",
    backups: "存档备份",
  };
  const timingNames = {
    live: "同步后生效",
    page: "刷新页面后生效",
    next_battle: "下场战斗生效",
    next_round: "下轮生效",
    master: "重新加载主数据",
    relogin: "重新登录后生效",
  };
  const paths = {
    grid: "M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z",
    user: "M20 21v-2a7 7 0 0 0-14 0v2M16 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0",
    spark: "m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3",
    box: "m3 7 9-4 9 4-9 4-9-4v10l9 4 9-4V7M12 11v10",
    shield: "m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3",
    heart:
      "M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8L12 21l8.8-8.6a5.5 5.5 0 0 0 0-7.8",
    shirt: "m8 3-5 3 2 5 3-1v11h8V10l3 1 2-5-5-3a4 4 0 0 1-8 0",
    team: "M8 21v-2a5 5 0 0 1 10 0v2M17 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0M3 18v-2a4 4 0 0 1 3-4",
    book: "M12 6c-3-3-7-3-10-2v16c3-1 7-1 10 2 3-3 7-3 10-2V4c-3-1-7-1-10 2v16",
    coin: "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0M12 7v10M15 8h-4a2 2 0 0 0 0 4h2a2 2 0 0 1 0 4H9",
    flag: "M4 22V3c5-4 10 4 16 0v12c-6 4-11-4-16 0",
    gear: "m9 3 1-2h4l1 2 3 2 2 1v4l-2 1v3l2 1v4l-2 1-3 2-1 2h-4l-1-2-3-2-2-1v-4l2-1v-3L4 10V6l2-1 3-2M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0",
    search: "M18 18l4 4M19 10A9 9 0 1 1 1 10a9 9 0 0 1 18 0",
    check: "m5 12 4 4L19 6",
    clock: "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0M12 6v6l4 2",
    database:
      "M20 5c0 2-4 4-8 4S4 7 4 5s4-4 8-4 8 2 8 4v14c0 2-4 4-8 4s-8-2-8-4V5M4 12c0 2 4 4 8 4s8-2 8-4",
    download: "M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5",
    mail: "M3 5h18v14H3V5l9 7 9-7",
    sword: "m4 20 5-5m-3-3 6 6M8 13 17 4l4-1-1 4-9 9",
    map: "m3 5 6-2 6 2 6-2v16l-6 2-6-2-6 2V5M9 3v16M15 5v16",
    cup: "M5 3h12v8a6 6 0 0 1-12 0V3M17 5h3a3 3 0 0 1 0 6h-3M8 21h6M11 17v4",
  };
  const icons = {
    overview: "grid",
    currency: "coin",
    inventory: "box",
    character: "user",
    characters: "user",
    affection: "heart",
    appearance: "shirt",
    equipment: "shield",
    party: "team",
    building: "grid",
    story: "book",
    mission: "mail",
    missions: "mail",
    gacha: "spark",
    shop: "box",
    tavern: "cup",
    idle: "map",
    battle: "sword",
    event: "flag",
    events: "flag",
    profile: "user",
    rules: "gear",
    master: "database",
    history: "clock",
    backups: "download",
  };
  function icon(name) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    const path = document.createElementNS(svg.namespaceURI, "path");
    path.setAttribute("d", paths[name] || paths.grid);
    svg.append(path);
    return svg;
  }
  function h(tag, props, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props || {})) {
      if (key.startsWith("on"))
        node.addEventListener(key.slice(2).toLowerCase(), value);
      else if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (value === true) node.setAttribute(key, "");
      else if (value !== false && value != null) node.setAttribute(key, value);
    }
    for (const child of children.flat(Infinity))
      if (child != null)
        node.append(
          child.nodeType ? child : document.createTextNode(String(child)),
        );
    return node;
  }
  function button(text, action, kind = "secondary", extras = {}) {
    return h(
      "button",
      { class: "button " + kind, type: "button", onClick: action, ...extras },
      text,
    );
  }
  function timing(value) {
    return (
      state.schema.timings?.[value] ||
      timingNames[value] ||
      value ||
      "同步后生效"
    );
  }
  function notify(text) {
    $("status-message").textContent = text;
  }
  function error(value) {
    $("error-message").textContent = value.message || String(value);
    $("error-banner").hidden = false;
    notify("操作未完成；参数已保留。");
  }
  function clearError() {
    $("error-banner").hidden = true;
  }
  async function api(path, body) {
    if (demo) return demoRequest(path, body);
    if (!path.startsWith("/admin/") && path !== "/console/auth")
      throw new Error("管理接口路径无效。");
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 35000);
    state.pending++;
    document.body.classList.add("busy");
    try {
      const response = await fetch(path, {
        method: body === undefined ? "GET" : "POST",
        headers: {
          Accept: "application/json",
          "X-Console-Request": "1",
          ...(body === undefined ? {} : { "Content-Type": "application/json" }),
        },
        credentials: "same-origin",
        redirect: "error",
        signal: controller.signal,
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
      const result = await response.json().catch(() => {
        throw new Error("服务返回格式无效，请确认控制台与服务版本一致。");
      });
      if (!response.ok)
        throw new Error(
          result.error?.message ||
            result.detail ||
            `请求失败（${response.status}）`,
        );
      return result;
    } catch (failure) {
      if (failure.name === "AbortError")
        throw new Error("请求超时，请检查本地服务后重试。");
      throw failure;
    } finally {
      clearTimeout(timeout);
      state.pending--;
      document.body.classList.toggle("busy", state.pending > 0);
    }
  }
  function query(path, params) {
    return (
      path +
      "?" +
      new URLSearchParams(
        Object.entries(params).filter(([, v]) => v != null),
      ).toString()
    );
  }
  function scoped() {
    return `${state.epoch}/${state.view}`;
  }
  function invalidate() {
    state.editor++;
    state.preview = null;
    document
      .querySelectorAll("[data-apply]")
      .forEach((node) => (node.disabled = true));
  }
  function details(title, value) {
    $("detail-title").textContent = title;
    const original = originalValue(value), displayed = displayTree(value);
    const annotated = pretty(original) !== pretty(displayed);
    $("detail-names").hidden = !annotated;
    $("detail-original").open = !annotated;
    $("detail-display").textContent = annotated ? pretty(displayed) : "";
    $("detail-content").textContent = typeof value === "string" ? value : pretty(original);
    $("detail-dialog").showModal();
  }
  function cellContent(row, key) {
    const label = row._display?.[key], raw = row[key];
    if (typeof label === "string" && typeof raw === "number")
      return h("div", { class: "reference-value" }, h("span", {}, label),
        h("small", {}, (key === "id" || key.endsWith("_id") ? "ID " : "原值 ") + raw));
    let value = displayValue(row, key);
    if (key === "table" && /^[mt]_/.test(value)) value = tableLabel(value);
    if (key === "key" && typeof value === "string") value = fieldLabel(value);
    return typeof value === "string" ? value : compact(value, 240);
  }
  function empty(title, description, symbol = "grid") {
    return h(
      "div",
      { class: "empty" },
      h("div", { class: "empty-icon" }, icon(symbol)),
      h("strong", {}, title),
      h("p", {}, description),
    );
  }
  function table(rows, columns, onSelect, selected) {
    const cols = columns?.length
      ? columns
      : preferredColumns(rows, 7).map((key) => [key, fieldLabel(key)]);
    const body = h("tbody");
    rows.forEach((row, index) => {
      const tr = h("tr", {
        class: onSelect ? "selectable" : "",
        tabIndex: onSelect ? 0 : null,
        onClick: () => onSelect?.(row, index),
        onKeydown: (event) => {
          if (event.key === "Enter") onSelect?.(row, index);
        },
      });
      if (selected?.(row, index)) tr.classList.add("selected");
      for (const [key] of cols) {
        const value = displayValue(row, key);
        tr.append(
          h(
            "td",
            {
              title: compact(originalValue(row[key]), 2000),
              class: typeof value === "number" ? "number-cell" : "text-cell",
            },
            cellContent(row, key),
          ),
        );
      }
      body.append(tr);
    });
    return h(
      "div",
      { class: "table-wrap" },
      h(
        "table",
        { class: "data-table" },
        h(
          "thead",
          {},
          h(
            "tr",
            {},
            cols.map(([key, label]) =>
              h("th", { title: key }, label === key ? fieldLabel(key) : label),
            ),
          ),
        ),
        body,
      ),
    );
  }
  function sourceSelector(values, ariaLabel) {
    const entries = values.map((value) =>
      typeof value === "string" ? { name: value } : value,
    );
    const select = h("select", { "aria-label": ariaLabel });
    const search = h("input", {
      type: "search",
      placeholder: "搜索中文类别或原字段名",
      "aria-label": "搜索" + ariaLabel,
    });
    const hint = h("small", { class: "source-key" });
    const element = h(
      "div",
      { class: "source-selector" },
      search,
      select,
      hint,
    );
    function updateHint() {
      hint.textContent = select.value
        ? "原始标识：" + select.value
        : "没有匹配的类别";
    }
    function filter() {
      const previous = select.value;
      const query = search.value.trim().toLowerCase();
      const matches = entries.filter((item) =>
        (tableLabel(item.name) + item.name).toLowerCase().includes(query),
      );
      select.replaceChildren(
        ...matches.map((item) =>
          h(
            "option",
            { value: item.name },
            tableLabel(item.name) +
              (item.count == null ? "" : ` · ${item.count} 条`),
          ),
        ),
      );
      if (matches.some((item) => item.name === previous))
        select.value = previous;
      select.disabled = !matches.length;
      updateHint();
      if (previous && previous !== select.value)
        select.dispatchEvent(new Event("change"));
    }
    search.addEventListener("input", filter);
    select.addEventListener("change", updateHint);
    filter();
    return { element, select };
  }
  function nativeRows(source) {
    const rows = sourceRecords(state.snapshot, source);
    const display = state.snapshot.record_display?.[source] || {};
    const identities = state.snapshot.record_identity_fields || {};
    const identity = own(identities, source) ? identities[source] : nativeIdentityField(source);
    return rows.map((row) => {
      const id = recordIdentity(row, identity);
      return { ...row, _display: { ...row._display, ...display[String(id)] }, _identity_field: identity };
    });
  }
  async function catalog(source, search = "", offset = 0) {
    if (source.startsWith("t_")) {
      const rows = nativeRows(source).filter((row) => matchesRow(row, search));
      const identities = state.snapshot.record_identity_fields || {};
      return { rows: rows.slice(offset, offset + 100), total: rows.length,
        identity_field: own(identities, source) ? identities[source] : nativeIdentityField(source) };
    }
    const result = await api(
      query("/admin/catalog", { table: source, q: search, offset, limit: 100 }),
    );
    const identity = own(result, "identity_field") ? result.identity_field : "id";
    return { ...result, rows: (result.rows || []).map((row) => ({ ...row, _identity_field: identity })) };
  }

  const { parseField, normalizeChoices, fieldChoices, validateJsonNumbers } =
    window.ConsoleFields;
  function makeForm(specs, values = {}) {
    const grid = h("form", {
      class: "form-grid",
      onSubmit: (event) => event.preventDefault(),
    });
    const controls = new Map();
    const lookup = createReferenceLookup((table, ids) => api("/admin/resolve", { table, ids }));
    const form = { element: grid, values, controls, read, set, source,
      remember: lookup.remember, referenceSummary };
    for (const spec of specs) {
      const type = spec.type || "string",
        value = own(values, spec.name) ? values[spec.name] : spec.default;
      const wide =
        ["json", "object", "array", "list", "ids"].includes(type) ||
        spec.multiline;
      const wrap = h("div", { class: "field" + (wide ? " wide" : "") });
      const id = "field-" + state.view + "-" + state.editor + "-" + spec.name;
      const labelText =
        spec.label && spec.label !== spec.name
          ? spec.label
          : labels[spec.name] || fieldLabel(spec.name);
      const label = h("label", { for: id, title: spec.name }, labelText);
      if (spec.required) label.append(h("span", { class: "required" }, "*"));
      wrap.append(label);
      let input;
      const choices = fieldChoices(spec);
      if (choices) {
        input = h("select", { id });
        if (spec.required && value === undefined)
          input.append(h("option", { value: "" }, "请选择"));
        choices.forEach((item, index) =>
          input.append(h("option", { value: String(index) }, item.label)),
        );
        const index = choices.findIndex((item) => item.value === value);
        input.value = index < 0 ? "" : String(index);
      } else if (["boolean", "bool"].includes(type)) {
        input = h("input", { id, type: "checkbox" });
        input.checked = !!value;
      } else if (wide) {
        input = h("textarea", {
          id,
          rows: type === "ids" ? 2 : 4,
          spellcheck: "false",
        });
        input.value =
          value === undefined
            ? ""
            : typeof value === "object"
              ? pretty(value)
              : String(value);
      } else {
        input = h("input", {
          id,
          type: "text",
          inputmode: ["integer", "int", "long", "number"].includes(type)
            ? "decimal"
            : null,
          placeholder: !spec.required ? "留空保持不变" : null,
        });
        input.value = value == null ? "" : String(value);
      }
      if (spec.readonly || spec.editable === false) {
        input.disabled = true;
        label.append(h("span", { class: "badge muted" }, "只读"));
      }
      let inputWrap = input;
      if (input.type === "checkbox")
        inputWrap = h(
          "div",
          { class: "toggle-row" },
          input,
          h("span", {}, "启用"),
        );
      else if ((spec.source || spec.source_by) && !spec.readonly && spec.editable !== false)
        inputWrap = h(
          "div",
          { class: "input-combo" },
          input,
          button("选择…", () => pick(form, spec.name), "secondary"),
        );
      wrap.append(inputWrap);
      const reference = spec.source || spec.source_by
        ? h("small", { class: "reference-summary", "aria-live": "polite" }) : null;
      if (reference) wrap.append(reference);
      const hints = [spec.description || spec.help || ""];
      const displayed = values._display?.[spec.name];
      if (!reference && typeof displayed === "string" && displayed !== String(value ?? ""))
        hints.push("中文显示：" + displayed);
      if (!reference && displayed && typeof displayed === "object")
        wrap.append(button("查看原记录名称对照", () => details(labelText, {
          value, _display: { value: displayed },
        }), "ghost"));
      if (spec.name && labelText !== spec.name) hints.push(spec.name);
      if (spec.min != null || spec.max != null)
        hints.push(`范围 ${spec.min ?? "不限"}～${spec.max ?? "不限"}`);
      if (type === "ids") hints.push("支持多个 ID；留空可配合“全部”选项。");
      if (hints.some(Boolean))
        wrap.append(h("small", {}, hints.filter(Boolean).join(" ")));
      grid.append(wrap);
      controls.set(spec.name, { spec, input, choices, reference, referenceVersion: 0, referenceTimer: null });
      input.addEventListener("input", () => changed(spec.name));
      input.addEventListener("change", () => changed(spec.name));
    }
    function changed(name) {
      invalidate();
      for (const [key, { spec }] of controls)
        if (spec.source_by?.field === name && key !== name) {
          if (spec.readonly || spec.editable === false) updateReference(key, true);
          else set(key, spec.type === "ids" ? [] : "");
        }
      updateReference(name);
    }
    function read() {
      const result = {};
      for (const [name, { spec, input, choices }] of controls) {
        if (spec.readonly || spec.editable === false) continue;
        let value;
        if (choices) value = choices[Number(input.value)]?.value;
        else if (input.type === "checkbox") value = input.checked;
        else value = parseField(spec, input.value);
        if (choices && input.value === "") value = undefined;
        if (value === undefined && spec.required)
          throw new Error(`${spec.label || name}：请选择一个值。`);
        if (value !== undefined) result[name] = value;
      }
      return result;
    }
    function set(name, value) {
      const item = controls.get(name);
      if (!item) return;
      if (item.choices)
        item.input.value = String(
          item.choices.findIndex((choice) => choice.value === value),
        );
      else if (item.input.type === "checkbox") item.input.checked = !!value;
      else
        item.input.value =
          typeof value === "object" ? pretty(value) : String(value ?? "");
      invalidate();
      updateReference(name, true);
    }
    function source(name) {
      const spec = controls.get(name).spec;
      if (!spec.source_by) return spec.source;
      const value = selectedValue(spec.source_by.field);
      return spec.source_by.values?.[String(value)];
    }
    function selectedValue(name) {
      const item = controls.get(name);
      if (!item || item.input.value === "") return undefined;
      return item.choices ? item.choices[Number(item.input.value)]?.value : item.input.value;
    }
    function referenceSummary() {
      return [...controls.values()].filter((item) => item.reference?.textContent)
        .map((item) => ({ field: item.spec.label || fieldLabel(item.spec.name), value: item.reference.textContent }));
    }
    function updateReference(name, immediate = false) {
      const item = controls.get(name);
      if (!item?.reference) return;
      clearTimeout(item.referenceTimer);
      const version = ++item.referenceVersion, scope = scoped();
      item.reference.textContent = "";
      item.reference.title = "";
      item.referenceTimer = setTimeout(async () => {
        if (state.currentForm !== form || scope !== scoped() || version !== item.referenceVersion) return;
        let ids;
        try {
          const value = parseField(item.spec, item.input.value);
          ids = value == null ? [] : Array.isArray(value) ? value : [value];
          if (ids.some((id) => !Number.isSafeInteger(id))) return;
        } catch { return; }
        if (!ids.length) return;
        const table = source(name);
        if (!table) {
          const value = selectedValue(item.spec.source_by?.field);
          item.reference.textContent = value == null ? "请先选择内容类型。" : "此类型没有名称目录，原 ID 保留。";
          return;
        }
        item.reference.textContent = "正在匹配名称…";
        try {
          let result;
          if (table.startsWith("t_")) {
            const rows = nativeRows(table), labels = new Map(rows.map((row) => [String(recordIdentity(row)), rowName(row)]));
            result = { entries: ids.slice(0, 100).map((id) => ({ id, name: labels.get(String(id)) || "" })), omitted: Math.max(0, ids.length - 100) };
          } else result = await lookup.resolve(table, ids);
          if (state.currentForm !== form || scope !== scoped() || version !== item.referenceVersion) return;
          item.reference.textContent = result.entries.map(({ id, name }) => referenceLabel(id, name)).join("；")
            + (result.omitted ? `；另有 ${result.omitted} 个 ID，完整值保留在输入框中。` : "");
        } catch (failure) {
          if (state.currentForm !== form || scope !== scoped() || version !== item.referenceVersion) return;
          item.reference.textContent = "名称暂不可读取，原 ID 已保留。";
          item.reference.title = failure.message;
        }
      }, immediate ? 0 : 200);
    }
    for (const name of controls.keys()) updateReference(name, true);
    return form;
  }
  let picker = null;
  async function pick(form, name) {
    const spec = form.controls.get(name).spec;
    if (spec.readonly || spec.editable === false) return;
    const source = form.source(name);
    if (!source) {
      error(new Error("请先选择内容类型。"));
      return;
    }
    picker = {
      form,
      name,
      source,
      multiple: spec.type === "ids",
      selected: new Map(),
      offset: 0,
      scope: scoped(),
      request: 0,
    };
    $("picker-title").textContent = "选择" + (spec.label || name);
    $("picker-subtitle").textContent =
      spec.type === "ids"
        ? "可选择多条记录，按名称或 ID 缩小范围。"
        : "选择一条记录后填入。";
    $("picker-query").value = "";
    $("picker-dialog").showModal();
    await loadPicker();
  }
  async function loadPicker() {
    if (!picker) return;
    const current = picker,
      request = ++current.request;
    try {
      const result = await catalog(
        current.source,
        $("picker-query").value,
        current.offset,
      );
      if (
        picker !== current ||
        request !== current.request ||
        current.scope !== scoped()
      )
        return;
      const holder = $("picker-results");
      const rows = result.rows || [];
      current.identity_field = own(result, "identity_field") ? result.identity_field
        : current.source.startsWith("t_") ? nativeIdentityField(current.source) : "id";
      const columns = preferredColumns(rows, 5).filter(
        (key) => !key.includes("description"),
      );
      const body = h("tbody");
      rows.forEach((row) => {
        const id = recordIdentity(row, current.identity_field);
        const selectable = id != null && /^-?\d+$/.test(String(id)) && Number.isSafeInteger(Number(id));
        const check = h("input", {
          type: current.multiple ? "checkbox" : "radio",
          name: "picker-row",
          disabled: !selectable,
          title: selectable ? "" : "该行没有可选择的唯一整数 ID。",
          "aria-label": selectable ? "选择 " + referenceLabel(id, rowName(row)) : "无可选 ID",
        });
        check.checked = current.selected.has(id);
        const tr = h("tr", { class: check.checked ? "selected" : "" });
        check.addEventListener("change", () => {
          if (!current.multiple) current.selected.clear();
          if (check.checked) current.selected.set(id, row);
          else current.selected.delete(id);
          if (!current.multiple) loadPicker();
          else tr.classList.toggle("selected", check.checked);
        });
        tr.append(h("td", {}, check));
        columns.forEach((key) =>
          tr.append(
            h(
              "td",
              {
                title: compact(originalValue(row[key]), 2000),
                class:
                  typeof displayValue(row, key) === "number" ? "number-cell" : "text-cell",
              },
              cellContent(row, key),
            ),
          ),
        );
        body.append(tr);
      });
      holder.replaceChildren(
        h(
          "div",
          { class: "table-wrap" },
          h(
            "table",
            { class: "data-table" },
            h(
              "thead",
              {},
              h(
                "tr",
                {},
                h("th", {}, "选择"),
                columns.map((key) => h("th", { title: key }, fieldLabel(key))),
              ),
            ),
            body,
          ),
        ),
      );
      $("picker-count").textContent =
        `${current.offset + 1}–${current.offset + rows.length} / ${result.total} 条`;
      $("picker-prev").disabled = current.offset === 0;
      $("picker-next").disabled = current.offset + 100 >= result.total;
    } catch (failure) {
      error(failure);
    }
  }

  function navigation() {
    const groups = [
      { id: "overview", label: "连接与概况" },
      ...state.schema.groups,
      { id: "rules", label: "本地规则" },
      { id: "master", label: "主数据" },
      { id: "history", label: "操作记录" },
      { id: "backups", label: "存档备份" },
    ];
    const nav = $("navigation");
    nav.replaceChildren();
    const seen = new Set();
    groups.forEach((group) => {
      if (seen.has(group.id)) return;
      seen.add(group.id);
      if (group.id === "rules") nav.append(h("div", { class: "nav-divider" }));
      nav.append(
        h(
          "button",
          {
            class: "nav-button" + (group.id === state.group ? " active" : ""),
            "data-group": group.id,
            onClick: () => showGroup(group.id),
          },
          icon(icons[group.id] || "grid"),
          group.label === group.id
            ? groupNames[group.id] || group.id
            : group.label,
        ),
      );
    });
  }
  function showGroup(group) {
    state.group = group;
    state.view++;
    invalidate();
    state.currentAction = null;
    state.currentForm = null;
    clearError();
    navigation();
    const label = state.schema.groups.find((item) => item.id === group)?.label;
    $("page-title").textContent =
      label && label !== group ? label : groupNames[group] || group;
    $("page-count").hidden = true;
    $("workspace").replaceChildren();
    const render =
      {
        overview: renderOverview,
        rules: renderRules,
        master: renderMaster,
        history: renderHistory,
        backups: renderBackups,
      }[group] || renderDomain;
    render();
  }
  function description(text) {
    $("page-description").textContent = text;
  }
  function renderOverview() {
    description("选择存档启动游戏，也可以先创建一份从教程开始的新存档。");
    const master = state.status.master || {};
    const panel = h(
      "section",
      { class: "panel overview" },
      h("span", { class: "badge good" }, demo ? "界面演示" : "本地服务"),
      h("h2", {}, state.connected ? "准备开始冒险" : "连接本地服务"),
      h(
        "p",
        {},
        "顶部启动按钮使用当前选中的存档。游戏运行时，可以查看资料；管理操作仍会先预览，再保存。",
      ),
      h(
        "dl",
        {},
        h("dt", {}, "当前服务"),
        h("dd", {}, demo ? "隔离演示数据" : location.origin),
        h("dt", {}, "当前存档"),
        h(
          "dd",
          {},
          profileLabel(
            state.profiles.find((item) => item.id === state.profile),
          ),
        ),
        h("dt", {}, "主数据版本"),
        h(
          "dd",
          {},
          master.revision || state.snapshot.master_revision || "尚未读取",
        ),
        h("dt", {}, "可用管理操作"),
        h("dd", {}, `${state.schema.actions.length} 项`),
      ),
      h(
        "div",
        { class: "button-group" },
        button("新建存档", () => openProfileDialog("create")),
        button(
          "管理角色",
          () =>
            showGroup(
              state.schema.groups.find((item) =>
                item.id.startsWith("character"),
              )?.id || "overview",
            ),
          "primary",
        ),
        button("查看操作记录", () => showGroup("history")),
      ),
    );
    $("workspace").append(panel);
  }
  function renderDomain() {
    description(
      "编辑成长、解锁与进度；修改前先检查关联数据，应用后查看同步状态。",
    );
    const actions = state.schema.actions.filter(
      (item) => item.group === state.group,
    );
    $("page-count").hidden = false;
    $("page-count").textContent = actions.length + " 项操作";
    const left = h("section", { class: "panel" }),
      right = h("section", { class: "panel", id: "editor-panel" });
    $("workspace").append(h("div", { class: "workspace-grid" }, left, right));
    const tabs = h("div", { class: "tabs" }),
      content = h("div");
    left.append(tabs, content);
    let mode = "actions";
    for (const [key, title] of [
      ["actions", "可用操作"],
      ["state", "存档数据"],
      ["catalog", "内容目录"],
    ])
      tabs.append(
        h(
          "button",
          {
            class: "tab" + (key === mode ? " active" : ""),
            onClick: (event) => {
              mode = key;
              tabs
                .querySelectorAll("button")
                .forEach((node) =>
                  node.classList.toggle("active", node === event.currentTarget),
                );
              drawList();
            },
          },
          title,
        ),
      );
    function drawList() {
      content.replaceChildren();
      if (mode === "state") {
        renderStateBrowser(content);
        return;
      }
      if (mode === "catalog") {
        renderCatalogBrowser(content);
        return;
      }
      const input = h("input", {
          type: "search",
          placeholder: "搜索操作",
          "aria-label": "搜索操作",
        }),
        list = h("div", { class: "action-list" });
      content.append(
        h(
          "div",
          { class: "list-search" },
          h("div", { class: "search-box" }, icon("search"), input),
        ),
        list,
      );
      const filter = () => {
        const q = input.value.toLowerCase();
        list.replaceChildren();
        for (const action of actions.filter((item) =>
          (item.label + item.id + item.description).toLowerCase().includes(q),
        ))
          list.append(
            h(
              "button",
              {
                class:
                  "action-item" +
                  (state.currentAction?.id === action.id ? " active" : ""),
                "data-action": action.id,
                onClick: () => {
                  selectAction(action);
                  list
                    .querySelectorAll("button")
                    .forEach((node) =>
                      node.classList.toggle(
                        "active",
                        node.dataset.action === action.id,
                      ),
                    );
                },
              },
              h(
                "span",
                { class: "action-icon" },
                icon(icons[state.group] || "spark"),
              ),
              h(
                "span",
                { class: "action-copy" },
                h("strong", {}, action.label),
                h("small", {}, timing(action.timing)),
              ),
            ),
          );
        if (!list.children.length)
          list.append(empty("没有匹配的操作", "换个关键词，或选择其他功能。"));
      };
      input.addEventListener("input", filter);
      filter();
    }
    drawList();
    if (actions.length) selectAction(actions[0]);
    else
      right.append(
        empty("当前版本没有可用操作", "可以切换到存档数据和内容目录进行核对。"),
      );
  }
  function editorShell(title, summary, schedule, form, previewAction) {
    const panel = $("editor-panel");
    panel.replaceChildren(
      h(
        "div",
        { class: "editor-intro" },
        h(
          "div",
          { class: "editor-intro-top" },
          h("h2", {}, title),
          h("span", { class: "badge" }, timing(schedule)),
        ),
        h("p", {}, summary),
      ),
    );
    const tabs = h("div", { class: "tabs" });
    const editButton = h(
        "button",
        { class: "tab active", onClick: () => switchTab(false) },
        "编辑参数",
      ),
      previewButton = h(
        "button",
        { class: "tab", onClick: () => switchTab(true) },
        "修改预览",
      );
    tabs.append(editButton, previewButton);
    const edit = form.element,
      preview = h("div", {
        class: "preview",
        hidden: true,
        id: "preview-body",
      });
    preview.append(
      empty("预览尚未生成", "填写参数后，点击下方的“预览修改”。", "check"),
    );
    function switchTab(show) {
      edit.hidden = show;
      preview.hidden = !show;
      editButton.classList.toggle("active", !show);
      previewButton.classList.toggle("active", show);
    }
    panel.append(
      tabs,
      edit,
      preview,
      h(
        "div",
        { class: "editor-footer" },
        h("p", {}, "只影响当前选中的本地存档。\n应用前可查看完整变更预览。"),
        h(
          "div",
          { class: "button-group" },
          button("预览修改", previewAction),
          button("应用修改", applyPreview, "primary", {
            "data-apply": true,
            disabled: true,
          }),
        ),
      ),
    );
    state.currentForm = form;
    state.switchPreview = switchTab;
  }
  function selectAction(action) {
    invalidate();
    state.currentAction = action;
    const form = makeForm(action.fields || []);
    editorShell(
      action.label,
      action.description || "服务端会校验前置条件和关联数据。",
      action.timing,
      form,
      () => {
        try {
          if (!state.profile || !state.snapshot.version)
            throw new Error("请先选择已初始化的存档。");
          requestPreview(`/admin/profiles/${state.profile}`, {
            operation: action.id,
            args: form.read(),
            expected_version: state.snapshot.version,
          });
        } catch (failure) {
          error(failure);
        }
      },
    );
  }
  async function requestPreview(endpoint, payload) {
    invalidate();
    const key = scoped(),
      edit = state.editor;
    clearError();
    try {
      const result = await api(endpoint + "/preview", payload);
      if (key !== scoped() || edit !== state.editor) return;
      state.preview = {
        endpoint,
        ticket: result.ticket,
        scope: key,
        editor: edit,
      };
      const target = $("preview-body");
      target.replaceChildren(
        h(
          "div",
          { class: "preview-summary" },
          icon("check"),
          h(
            "div",
            {},
            h("h3", {}, result.summary || "修改预览"),
            h("span", { class: "badge" }, timing(result.timing)),
          ),
        ),
      );
      if (result.changes?.length)
        target.append(
          table(
            result.changes,
            [
              ["table", "数据"],
              ["added", "新增"],
              ["updated", "修改"],
              ["removed", "删除"],
            ],
            (change) => details(`${change.table || "数据"} · 修改明细`, change),
          ),
          h(
            "p",
            { class: "read-only-note" },
            "点击数据行查看修改前后明细；批量变更显示服务端提供的样本。",
          ),
        );
      if (result.warnings?.length)
        target.append(
          h(
            "div",
            { class: "preview-note" },
            result.warnings.map((text) => h("p", {}, text)),
          ),
        );
      const args = payload.args || payload.changes;
      if (args && !args.snapshot) {
        const names = state.currentForm?.referenceSummary?.() || [];
        if (names.length) target.append(h("h3", {}, "内容名称"), table(names, [["field", "参数"], ["value", "名称与 ID"]]));
        target.append(
          h("h3", {}, "提交内容"),
          h("pre", { class: "preview-args" }, pretty(args)),
        );
      }
      target.append(
        h(
          "p",
          { class: "read-only-note" },
          "预览只用于确认。数据变化或预览过期后，需要重新生成。",
        ),
      );
      state.switchPreview?.(true);
      document
        .querySelectorAll("[data-apply]")
        .forEach((node) => (node.disabled = !result.ticket));
      notify("预览已生成。核对变化后即可应用。");
    } catch (failure) {
      error(failure);
    }
  }
  async function applyPreview() {
    const preview = state.preview;
    if (
      !preview ||
      preview.scope !== scoped() ||
      preview.editor !== state.editor
    )
      return;
    invalidate();
    clearError();
    try {
      const result = await api(preview.endpoint + "/apply", {
        ticket: preview.ticket,
      });
      if (preview.scope !== scoped()) return;
      notify((result.summary || "修改已保存") + " · " + timing(result.timing));
      const target = $("preview-body");
      target?.replaceChildren(
        h(
          "div",
          { class: "preview-summary" },
          icon("check"),
          h(
            "div",
            {},
            h("h3", {}, "修改已保存"),
            h("p", {}, result.summary || ""),
          ),
        ),
        h(
          "div",
          { class: "preview-note" },
          timing(result.timing) + "。请留意下方客户端同步状态。",
        ),
      );
      await refreshSnapshot(false);
      if (state.group === "rules" || state.group === "master")
        showGroup(state.group);
    } catch (failure) {
      error(failure);
    }
  }

  function renderStateBrowser(target) {
    const records = state.snapshot.state?.records || {},
      progress = state.snapshot.progress || {};
    const names = [
      ...new Set([...Object.keys(records), ...Object.keys(state.snapshot.record_sources || {})].sort()),
      ...Object.keys(progress)
        .sort()
        .map((name) => "progress." + name),
    ];
    const source = sourceSelector(names, "存档数据表"),
      select = source.select;
    const result = h("div");
    target.append(
      source.element,
      result,
      h(
        "div",
        { class: "read-only-note" },
        "数据仅供核对；修改请使用对应操作。最多显示前 500 行。",
      ),
    );
    const render = () => {
      const name = select.value;
      let value = name.startsWith("progress.")
        ? progress[name.slice(9)]
        : nativeRows(name);
      const annotations = name.startsWith("progress.")
        ? state.snapshot.progress_display?.[name.slice(9)] : undefined;
      const rows = displayRows(value, annotations);
      result.replaceChildren(
        table(rows.slice(0, 500), null, (row) =>
          details(tableLabel(name), row),
        ),
      );
    };
    select.addEventListener("change", render);
    if (names.length) render();
    else
      result.append(
        empty("还没有存档数据", "请先选择已经在本地游戏中初始化的存档。"),
      );
  }
  function renderCatalogBrowser(target) {
    const sources = new Set();
    for (const action of state.schema.actions.filter(
      (item) => item.group === state.group,
    ))
      for (const spec of action.fields || []) {
        if (spec.source) sources.add(spec.source);
        Object.values(spec.source_by?.values || {}).forEach((source) =>
          sources.add(source),
        );
      }
    const source = sourceSelector(
        sources.size ? [...sources].sort() : ["m_characters"],
        "内容目录",
      ),
      select = source.select;
    const input = h("input", { type: "search", placeholder: "名称或 ID" }),
      rows = h("div"),
      pager = h("div", { class: "pagination" });
    let offset = 0,
      serial = 0;
    target.append(
      source.element,
      h(
        "div",
        { class: "search-row" },
        input,
        button("查找", () => load(0)),
      ),
      rows,
      pager,
    );
    async function load(start) {
      offset = start;
      const ticket = ++serial,
        key = scoped();
      if (!select.value) {
        rows.replaceChildren(empty("没有匹配的目录", "换一个类别关键词。"));
        pager.replaceChildren();
        return;
      }
      try {
        const result = await catalog(select.value, input.value, start);
        if (ticket !== serial || key !== scoped()) return;
        rows.replaceChildren(
          table(result.rows || [], null, (row) =>
            details(tableLabel(select.value), row),
          ),
        );
        pager.replaceChildren(
          button("上一页", () => load(Math.max(0, offset - 100)), "ghost", {
            disabled: offset === 0,
          }),
          h(
            "span",
            {},
            `${offset + 1}–${offset + (result.rows || []).length} / ${result.total}`,
          ),
          button("下一页", () => load(offset + 100), "ghost", {
            disabled: offset + 100 >= result.total,
          }),
        );
      } catch (failure) {
        error(failure);
      }
    }
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") load(0);
    });
    select.addEventListener("change", () => load(0));
    load(0);
  }

  async function renderRules() {
    description("调整已有的本地规则；正在进行的战斗和轮次保留原快照。");
    const key = scoped();
    try {
      const result = await api("/admin/rules");
      if (key !== scoped()) return;
      const specs = new Map(
        (result.specs || []).map((spec) => [spec.name, spec]),
      );
      const left = h("section", { class: "panel" }),
        right = h("section", { class: "panel", id: "editor-panel" });
      const input = h("input", {
          type: "search",
          placeholder: "搜索规则名称或字段",
        }),
        list = h("div", { class: "action-list" });
      left.append(
        h(
          "div",
          { class: "panel-header" },
          h("h3", {}, "规则目录"),
          h(
            "span",
            { class: "badge muted" },
            Object.keys(result.values).length + " 项",
          ),
        ),
        h(
          "div",
          { class: "list-search" },
          h("div", { class: "search-box" }, icon("search"), input),
        ),
        list,
      );
      $("workspace").append(h("div", { class: "workspace-grid" }, left, right));
      function select(name) {
        invalidate();
        const spec = {
          ...(specs.get(name) || { name, type: "json", readonly: true }),
          label: labels[name] || specs.get(name)?.label || name,
          description: name,
        };
        const form = makeForm([spec], { [name]: result.values[name] });
        editorShell(
          spec.label,
          "只提交你改动的规则。初始赠送配置不会重复发放到旧档。",
          "page",
          form,
          () => {
            try {
              const values = form.read(),
                changes = Object.fromEntries(
                  Object.entries(values).filter(
                    ([field, value]) =>
                      JSON.stringify(value) !==
                      JSON.stringify(result.values[field]),
                  ),
                );
              if (!Object.keys(changes).length)
                throw new Error("请先修改可编辑的规则。");
              requestPreview("/admin/rules", {
                changes,
                expected_version: result.version,
              });
            } catch (failure) {
              error(failure);
            }
          },
        );
      }
      function filter() {
        const q = input.value.toLowerCase();
        list.replaceChildren();
        for (const [name, value] of Object.entries(result.values).filter(
          ([name]) =>
            (name + (labels[name] || specs.get(name)?.label || ""))
              .toLowerCase()
              .includes(q),
        ))
          list.append(
            h(
              "button",
              { class: "action-item", onClick: () => select(name) },
              h("span", { class: "action-icon" }, icon("gear")),
              h(
                "span",
                { class: "action-copy" },
                h("strong", {}, labels[name] || specs.get(name)?.label || name),
                h("small", {}, "当前值：" + compact(value, 50)),
              ),
            ),
          );
      }
      input.addEventListener("input", filter);
      filter();
      const first = Object.keys(result.values).find((name) => specs.has(name));
      if (first) select(first);
    } catch (failure) {
      error(failure);
    }
  }
  async function renderMaster() {
    description(
      "生成经过校验的主数据覆盖版本。只读字段保留原值，发布后按提示重新加载。",
    );
    const key = scoped();
    try {
      const result = await api("/admin/master/tables");
      if (key !== scoped()) return;
      const left = h("section", { class: "panel" }),
        right = h("section", { class: "panel", id: "editor-panel" });
      const source = sourceSelector(result.tables || [], "主数据表"),
        selector = source.select;
      const search = h("input", {
          type: "search",
          placeholder: "搜索记录名称或 ID",
        }),
        rowbox = h("div"),
        pager = h("div", { class: "pagination" });
      left.append(
        h("div", { class: "panel-header" }, h("h3", {}, "主数据目录")),
        source.element,
        h(
          "div",
          { class: "search-row" },
          search,
          button("搜索", () => load(0)),
        ),
        rowbox,
        pager,
      );
      $("workspace").append(h("div", { class: "workspace-grid" }, left, right));
      right.append(
        empty("选择一条记录", "只开放服务端已确认可编辑的字段。", "database"),
      );
      let offset = 0,
        serial = 0;
      async function load(start) {
        invalidate();
        right.replaceChildren(empty("正在读取主数据", "请稍候…", "database"));
        offset = start;
        const current = ++serial;
        const name = selector.value;
        if (!name) {
          rowbox.replaceChildren(
            empty("没有匹配的数据表", "换一个类别关键词。"),
          );
          pager.replaceChildren();
          return;
        }
        try {
          const data = await api(
            query("/admin/master/table/" + encodeURIComponent(name), {
              q: search.value,
              offset,
              limit: 100,
            }),
          );
          if (key !== scoped() || current !== serial) return;
          const rows = (data.rows || []).map((row) => ({
            index: row.index,
            ...row.values,
            _display: row.display || {},
            _identity_field: own(data, "identity_field") ? data.identity_field : "id",
          }));
          rowbox.replaceChildren(
            table(rows, null, (row) => {
              invalidate();
              const specs = (data.fields || []).map((spec) => ({
                ...spec,
                label: spec.label || fieldLabel(spec.name),
                readonly: !spec.editable,
              }));
              const form = makeForm(specs, row);
              editorShell(
                tableLabel(name) +
                  " · " +
                  (displayValue(row, "name") ||
                    row.id ||
                    "第 " + (row.index + 1) + " 行"),
                "编辑值保存在覆盖层，原始主表保持不变。",
                "master",
                form,
                () => {
                  try {
                    const changes = Object.fromEntries(
                      Object.entries(form.read()).filter(
                        ([field, value]) =>
                          JSON.stringify(value) !== JSON.stringify(row[field]),
                      ),
                    );
                    if (!Object.keys(changes).length)
                      throw new Error("尚未修改可编辑字段。");
                    requestPreview("/admin/master", {
                      table: name,
                      index: row.index,
                      changes,
                      expected_version: data.version,
                    });
                  } catch (failure) {
                    error(failure);
                  }
                },
              );
            }),
          );
          pager.replaceChildren(
            button("上一页", () => load(Math.max(0, offset - 100)), "ghost", {
              disabled: offset === 0,
            }),
            h(
              "span",
              {},
              `${offset + 1}–${offset + rows.length} / ${data.total}`,
            ),
            button("下一页", () => load(offset + 100), "ghost", {
              disabled: offset + 100 >= data.total,
            }),
          );
        } catch (failure) {
          error(failure);
        }
      }
      selector.addEventListener("change", () => load(0));
      search.addEventListener("keydown", (event) => {
        if (event.key === "Enter") load(0);
      });
      if (selector.value) load(0);
    } catch (failure) {
      error(failure);
    }
  }

  async function historyEntries() {
    if (!state.profile) return [];
    return (
      (await api(`/admin/profiles/${state.profile}/history`)).entries || []
    );
  }
  function formatHistory(entries) {
    return entries.map((row) => ({
      ...row,
      at:
        typeof row.at === "number"
          ? new Date(row.at * 1000).toLocaleString()
          : row.at,
      timing: timing(row.timing),
      status:
        row.status === "saved"
          ? "已保存"
          : row.status === "backup"
            ? "备份"
            : row.status,
    }));
  }
  async function renderHistory() {
    description("每次管理操作均保留记录，可核对保存结果与生效时机。");
    const key = scoped();
    try {
      const entries = await historyEntries();
      if (key !== scoped()) return;
      $("workspace").append(
        h(
          "section",
          { class: "panel" },
          h(
            "div",
            { class: "panel-header" },
            h("h3", {}, "最近操作"),
            h("span", { class: "badge muted" }, entries.length + " 条"),
          ),
          entries.length
            ? table(
                formatHistory(entries),
                [
                  ["id", "编号"],
                  ["at", "时间"],
                  ["summary", "操作"],
                  ["timing", "生效方式"],
                  ["status", "状态"],
                ],
                (row) => details("操作记录", row),
              )
            : empty(
                "还没有管理记录",
                "应用第一项修改后，会在这里留下记录。",
                "clock",
              ),
        ),
      );
    } catch (failure) {
      error(failure);
    }
  }
  async function renderBackups() {
    description("备份和导出当前进度；恢复也会先生成预览，不会直接覆盖存档。");
    const toolbar = h(
      "div",
      { class: "toolbar" },
      button("创建备份", createBackup, "primary"),
      button("导出存档", exportSave),
      button("导入并预览", importSave),
      button("预览恢复到操作前", restoreHistory),
    );
    const entriesPanel = h("section", { class: "panel" }),
      previewPanel = h("section", { class: "panel", id: "editor-panel" });
    $("workspace").append(
      toolbar,
      h("div", { class: "split-vertical" }, entriesPanel, previewPanel),
    );
    state.selectedHistory = null;
    const form = { element: h("div") };
    editorShell(
      "恢复预览",
      "选择一条历史记录或导入同版本存档。",
      "relogin",
      form,
      restoreHistory,
    );
    const key = scoped();
    try {
      const entries = (await historyEntries()).filter(
        (item) => !["rules.update", "master.update"].includes(item.operation),
      );
      if (key !== scoped()) return;
      const render = () =>
        entriesPanel.replaceChildren(
          h("div", { class: "panel-header" }, h("h3", {}, "可恢复的历史记录")),
          entries.length
            ? table(
                formatHistory(entries),
                [
                  ["id", "编号"],
                  ["at", "时间"],
                  ["summary", "操作／备份"],
                  ["status", "状态"],
                ],
                (row) => {
                  state.selectedHistory = row;
                  invalidate();
                  render();
                },
                (row) => row.id === state.selectedHistory?.id,
              )
            : empty(
                "还没有可恢复的记录",
                "先创建一份备份，或导入已有的存档文件。",
                "download",
              ),
        );
      render();
    } catch (failure) {
      error(failure);
    }
  }
  async function createBackup() {
    const key = scoped();
    try {
      if (!state.profile || !state.snapshot.version)
        throw new Error("请先选择已初始化的存档。");
      const result = await api(`/admin/profiles/${state.profile}/backup`, {
        expected_version: state.snapshot.version,
      });
      if (key !== scoped()) return;
      notify(result.summary || "备份已创建。");
      showGroup("backups");
    } catch (failure) {
      error(failure);
    }
  }
  async function exportSave() {
    try {
      if (!state.profile) throw new Error("请先选择存档。");
      const profile = state.profile,
        result = await api(`/admin/profiles/${profile}/export`);
      const url = URL.createObjectURL(
        new Blob([pretty(result)], { type: "application/json;charset=utf-8" }),
      );
      const link = h("a", {
        href: url,
        download: `dotabyss-profile-${profile}.json`,
      });
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      notify("存档已导出。");
    } catch (failure) {
      error(failure);
    }
  }
  function importSave() {
    const input = h("input", {
      type: "file",
      accept: ".json,application/json",
    });
    const key = scoped(),
      profile = state.profile,
      version = state.snapshot.version;
    input.addEventListener("change", async () => {
      try {
        const file = input.files[0];
        if (!file) return;
        if (file.size > 32 * 1024 * 1024)
          throw new Error("存档文件超过 32 MiB，请检查选择的文件。");
        const snapshot = JSON.parse(await file.text());
        validateJsonNumbers(snapshot);
        if (key !== scoped()) return;
        await requestPreview(`/admin/profiles/${profile}`, {
          operation: "save.import",
          args: { snapshot },
          expected_version: version,
        });
      } catch (failure) {
        error(failure);
      }
    });
    input.click();
  }
  function restoreHistory() {
    if (!state.selectedHistory) {
      error(new Error("请先选择一条备份或历史记录。"));
      return;
    }
    requestPreview(`/admin/profiles/${state.profile}`, {
      operation: "save.restore",
      args: { history_id: state.selectedHistory.id, which: "before" },
      expected_version: state.snapshot.version,
    });
  }

  function showSync(sync = {}) {
    const names = {
      pending: "等待客户端同步",
      applied: "客户端已应用",
      acknowledged: "客户端已应用",
      unchanged: "暂无待同步修改",
      connected: "客户端已连接",
      deferred: "等待安全时机",
      waiting_safe_point: "等待安全时机",
      relogin_required: "需要重新登录",
      requires_relogin: "需要重新登录",
      error: "同步失败",
      failed: "同步失败",
    };
    const status = names[sync.status] || sync.status || "等待客户端状态";
    const activeId = activeProfileId(state.launcher);
    const mismatch = activeId && activeId !== state.profile;
    $("sync-label").textContent =
      status +
      (sync.revision != null
        ? ` · ${sync.ack_revision || 0}/${sync.revision}`
        : "") +
      (sync.client_connected === false
        ? mismatch
          ? " · 游戏使用另一存档"
          : " · 该存档未连接游戏"
        : "");
    $("sync-dot").className =
      "status-dot " +
      (["applied", "acknowledged"].includes(sync.status)
        ? ""
        : ["error", "failed"].includes(sync.status)
          ? "error"
          : "pending");
  }
  const rememberedProfileKey =
    "dotabyss-selected-profile:" + (demo ? "demo" : location.origin);
  function rememberProfile(id) {
    try {
      localStorage.setItem(rememberedProfileKey, String(id));
    } catch {}
  }
  function rememberedProfile() {
    try {
      return localStorage.getItem(rememberedProfileKey);
    } catch {
      return null;
    }
  }
  function renderProfileOptions() {
    $("profile-select").replaceChildren(
      ...state.profiles.map((profile) =>
        h(
          "option",
          { value: profile.id },
          `${profileLabel(profile)} · ID ${profile.id}${profile.has_state ? "" : "（尚未建档）"}`,
        ),
      ),
    );
    if (state.profile) $("profile-select").value = String(state.profile);
    const manage = demo || state.status.capabilities?.profile_management;
    $("rename-profile-button").disabled = !state.profile || !manage;
    $("create-profile-button").disabled = !state.connected || !manage;
    $("create-profile-button").title = manage
      ? "创建从教程开始的新存档"
      : "重启服务加载存档管理接口后可用";
  }
  function renderLauncher() {
    const profile = state.profiles.find((item) => item.id === state.profile);
    const policy = launchState(
      state.launcher,
      state.profile,
      profile?.has_state,
    );
    $("launch-game-button").disabled = state.launching || policy.disabled;
    $("launch-game-button").textContent = state.launching
      ? "正在启动…"
      : policy.label;
    $("launcher-hint").textContent = policy.message;
    const activeId = activeProfileId(state.launcher);
    const active = state.profiles.find((item) => item.id === activeId);
    const hint = $("profile-context");
    hint.classList.toggle("mismatch", !!activeId && activeId !== state.profile);
    const selectedText = profile
      ? `当前编辑：${profileLabel(profile)}（ID ${profile.id}）`
      : "尚未选择存档";
    const runningText = state.launcher?.running
      ? activeId
        ? `游戏使用：${profileLabel(active) === "未选择存档" ? state.launcher.profile_name || "未知存档" : profileLabel(active)}（ID ${activeId}）`
        : "游戏正在运行，尚未确认存档"
      : state.launcher?.available === false
        ? "游戏状态暂不可用"
        : "游戏尚未运行";
    hint.replaceChildren(
      h("span", {}, selectedText),
      h("span", {}, runningText),
    );
    if (activeId && activeId !== state.profile && active)
      hint.append(
        button("查看游戏中的存档", () => selectProfile(activeId), "ghost"),
      );
    showSync(state.snapshot.sync);
  }
  async function refreshLauncher() {
    try {
      state.launcher = await api("/admin/launcher/status");
    } catch (failure) {
      state.launcher = {
        available: false,
        running: false,
        reason: failure.message,
      };
    }
    renderLauncher();
  }
  async function reloadProfiles() {
    const result = await api("/admin/profiles");
    state.profiles = result.profiles || [];
    renderProfileOptions();
    renderLauncher();
  }
  async function launchGame() {
    const id = state.profile,
      profile = state.profiles.find((item) => item.id === id);
    if (
      state.launching ||
      launchState(state.launcher, id, profile?.has_state).disabled
    )
      return;
    state.launching = true;
    renderLauncher();
    try {
      const result = await api("/admin/launcher/start", { profile_id: id });
      await refreshLauncher();
      notify(
        result.message ||
          `已提交启动：${profileLabel(profile)}。请在游戏窗口继续。`,
      );
    } catch (failure) {
      error(failure);
    } finally {
      state.launching = false;
      renderLauncher();
    }
  }
  let profileForm = null;
  function openProfileDialog(mode) {
    if (!state.connected) {
      error(new Error("请先连接本地服务。"));
      return;
    }
    if (!demo && !state.status.capabilities?.profile_management) {
      error(
        new Error("当前服务尚未加载存档管理接口，请重启服务后重新打开启动器。"),
      );
      return;
    }
    const profile = state.profiles.find((item) => item.id === state.profile);
    if (mode === "rename" && !profile) return;
    profileForm = {
      mode,
      profile,
      requestId: crypto.randomUUID(),
      signature: null,
      busy: false,
    };
    $("profile-dialog-title").textContent =
      mode === "create" ? "新建存档" : "重命名存档";
    $("profile-dialog-description").textContent =
      mode === "create"
        ? "独立保存进度，从新手教程开始。"
        : "只修改启动器中的名称，游戏昵称与内部标识保持不变。";
    $("profile-label").value = mode === "rename" ? profileLabel(profile) : "";
    $("profile-player-name").value = "";
    $("player-name-field").hidden = mode !== "create";
    $("save-profile-button").textContent =
      mode === "create" ? "创建存档" : "保存名称";
    $("save-profile-button").disabled = false;
    $("profile-form-error").hidden = true;
    $("profile-dialog").showModal();
    $("profile-label").focus();
  }
  async function saveProfile(event) {
    event.preventDefault();
    const form = profileForm;
    if (!form || form.busy) return;
    const label = $("profile-label").value.trim(),
      playerName = $("profile-player-name").value.trim();
    if (!label) {
      $("profile-form-error").textContent = "请填写存档名称。";
      $("profile-form-error").hidden = false;
      return;
    }
    const signature = JSON.stringify({ label, playerName });
    if (form.signature && form.signature !== signature)
      form.requestId = crypto.randomUUID();
    form.signature = signature;
    form.busy = true;
    $("save-profile-button").disabled = true;
    $("profile-form-error").hidden = true;
    try {
      const result =
        form.mode === "create"
          ? await api("/admin/profiles", {
              label,
              ...(playerName ? { player_name: playerName } : {}),
              operation_id: form.requestId,
            })
          : await api(`/admin/profiles/${form.profile.id}/label`, {
              label,
              expected_label: form.profile.label || form.profile.name,
            });
      await reloadProfiles();
      if (form !== profileForm || !$("profile-dialog").open) return;
      $("profile-dialog").close();
      const profile = result.profile || result;
      if (form.mode === "create") await selectProfile(profile.id);
      else if (state.profile === form.profile.id) showGroup(state.group);
      notify(
        form.mode === "create"
          ? "新存档已创建并选中，可以启动游戏。"
          : "存档名称已更新。",
      );
    } catch (failure) {
      if (form !== profileForm) return;
      $("profile-form-error").textContent = failure.message;
      $("profile-form-error").hidden = false;
    } finally {
      form.busy = false;
      if (form === profileForm) $("save-profile-button").disabled = false;
    }
  }
  async function refreshSnapshot(invalidateChanged = true) {
    if (
      !state.profile ||
      !state.profiles.find((profile) => profile.id === state.profile)?.has_state
    )
      return;
    const epoch = state.epoch,
      profile = state.profile;
    const result = await api(`/admin/profiles/${profile}/snapshot`);
    if (epoch !== state.epoch) return;
    if (invalidateChanged && result.version !== state.snapshot.version)
      invalidate();
    state.snapshot = result;
    showSync(result.sync);
    renderLauncher();
  }
  async function selectProfile(id) {
    state.epoch++;
    const epoch = state.epoch;
    state.profile = Number(id);
    renderProfileOptions();
    rememberProfile(state.profile);
    state.snapshot = {};
    invalidate();
    $("picker-dialog").close();
    picker = null;
    renderLauncher();
    if (
      !state.profiles.find((profile) => profile.id === state.profile)?.has_state
    ) {
      showGroup("overview");
      notify("这份存档尚未初始化。可以创建一份新存档后启动游戏。");
      return;
    }
    try {
      await refreshSnapshot();
      if (epoch !== state.epoch) return;
      showGroup(state.group);
      notify("已读取当前存档。选择功能后可以开始编辑。");
    } catch (failure) {
      if (epoch !== state.epoch) return;
      showGroup("overview");
      error(failure);
    }
  }
  async function refresh() {
    clearError();
    const key = scoped();
    try {
      await Promise.all([reloadProfiles(), refreshLauncher()]);
      await refreshSnapshot();
      if (key !== scoped()) return;
      showGroup(state.group);
      notify("已刷新当前数据。");
    } catch (failure) {
      error(failure);
    }
  }
  async function initialize() {
    try {
      if (demo) $("demo-banner").hidden = false;
      else {
        const fragment = new URLSearchParams(location.hash.slice(1));
        const ticket = fragment.get("ticket");
        if (ticket) {
          history.replaceState(null, "", location.pathname + location.search);
          await api("/console/auth", { ticket });
        }
      }
      const [status, schema, profiles, launcher] = await Promise.all([
        api("/admin/status"),
        api("/admin/schema"),
        api("/admin/profiles"),
        api("/admin/launcher/status").catch((failure) => ({
          available: false,
          running: false,
          reason: failure.message,
        })),
      ]);
      state.status = status;
      state.schema = schema;
      state.profiles = profiles.profiles || [];
      state.launcher = launcher;
      state.connected = true;
      $("connection-label").textContent = demo ? "隔离演示数据" : location.host;
      $("connection-dot").className = "status-dot";
      renderProfileOptions();
      state.group = demo
        ? state.schema.groups.find((group) => group.id.startsWith("character"))
            ?.id || "overview"
        : "overview";
      if (state.profiles.length)
        await selectProfile(
          initialProfile(state.profiles, state.launcher, rememberedProfile()),
        );
      else {
        navigation();
        renderOverview();
        notify("服务已连接，但尚未创建游戏存档。");
      }
      renderLauncher();
    } catch (failure) {
      $("connection-label").textContent = "未连接";
      $("connection-dot").className = "status-dot error";
      navigation();
      renderOverview();
      error(failure);
    }
  }
  $("profile-select").addEventListener("change", (event) =>
    selectProfile(event.target.value),
  );
  $("create-profile-button").addEventListener("click", () =>
    openProfileDialog("create"),
  );
  $("rename-profile-button").addEventListener("click", () =>
    openProfileDialog("rename"),
  );
  $("launch-game-button").addEventListener("click", launchGame);
  $("profile-form").addEventListener("submit", saveProfile);
  $("refresh-button").addEventListener("click", refresh);
  $("dismiss-error").addEventListener("click", clearError);
  $("help-button").addEventListener("click", () =>
    $("help-dialog").showModal(),
  );
  document
    .querySelectorAll("[data-close-dialog]")
    .forEach((node) =>
      node.addEventListener("click", () => node.closest("dialog").close()),
    );
  $("picker-search").addEventListener("click", () => {
    picker.offset = 0;
    loadPicker();
  });
  $("picker-query").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      picker.offset = 0;
      loadPicker();
    }
  });
  $("picker-prev").addEventListener("click", () => {
    picker.offset = Math.max(0, picker.offset - 100);
    loadPicker();
  });
  $("picker-next").addEventListener("click", () => {
    picker.offset += 100;
    loadPicker();
  });
  $("picker-confirm").addEventListener("click", () => {
    if (!picker || picker.scope !== scoped()) return;
    const ids = [...picker.selected.keys()];
    if (!ids.length) return;
    const { form, name, multiple } = picker;
    form.remember(picker.source, [...picker.selected.values()], picker.identity_field);
    if (multiple) {
      let prior = [];
      try {
        prior =
          parseField(
            form.controls.get(name).spec,
            form.controls.get(name).input.value,
          ) || [];
      } catch {}
      form.set(name, [...new Set([...prior, ...ids])]);
    } else form.set(name, ids[0]);
    $("picker-dialog").close();
    picker = null;
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "F5") {
      event.preventDefault();
      refresh();
    }
    if (event.ctrlKey && event.key.toLowerCase() === "f") {
      const input = document.querySelector("#workspace input[type=search]");
      if (input) {
        event.preventDefault();
        input.focus();
        input.select();
      }
    }
  });
  setInterval(async () => {
    if (state.connected && !state.pending && !document.hidden) {
      try {
        await refreshLauncher();
        await refreshSnapshot();
      } catch (failure) {
        notify(failure.message);
      }
    }
  }, 6000);
  window.ConsoleCore = { parseField, normalizeChoices, makeForm, state };
  initialize();
})();
