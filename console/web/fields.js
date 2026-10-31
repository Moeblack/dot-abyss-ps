(function (root) {
  "use strict";
  const own = (value, key) => Object.prototype.hasOwnProperty.call(value, key);
  function normalizeChoices(choices) {
    if (!Array.isArray(choices))
      return Object.entries(choices || {}).map(([value, label]) => ({
        value,
        label,
      }));
    return choices.map((item) =>
      typeof item === "object" && item !== null
        ? {
            value: item.value ?? item.id,
            label: item.label ?? item.name ?? String(item.value ?? item.id),
          }
        : { value: item, label: String(item) },
    );
  }
  function fieldChoices(spec) {
    if (
      ["boolean", "bool"].includes(spec.type) &&
      !spec.required &&
      !own(spec, "default")
    ) {
      return [
        { label: "保持不变", value: undefined },
        { label: "是", value: true },
        { label: "否", value: false },
      ];
    }
    return spec.choices ? normalizeChoices(spec.choices) : null;
  }
  function validateJsonNumbers(value) {
    if (
      typeof value === "number" &&
      (!Number.isFinite(value) ||
        (Number.isInteger(value) && !Number.isSafeInteger(value)))
    ) {
      throw new Error("JSON 中的数字超出可安全编辑范围。");
    }
    if (value && typeof value === "object")
      Object.values(value).forEach(validateJsonNumbers);
  }
  function parseField(spec, raw) {
    const type = spec.type || "string";
    if (raw === "" || raw === undefined || raw === null) {
      if (spec.required && !spec.allow_empty)
        throw new Error(`${spec.label || spec.name}：不能为空。`);
      if (spec.allow_empty && type === "string") return "";
      return own(spec, "default") ? structuredClone(spec.default) : undefined;
    }
    let value;
    if (
      [
        "int",
        "integer",
        "long",
        "short",
        "ushort",
        "uint",
        "ulong",
        "byte",
        "int64",
        "int32",
      ].includes(type)
    ) {
      if (!/^-?\d+$/.test(String(raw)))
        throw new Error(`${spec.label || spec.name}：请输入整数。`);
      value = Number(raw);
      if (!Number.isSafeInteger(value))
        throw new Error(
          `${spec.label || spec.name}：超出浏览器可安全编辑的整数范围。`,
        );
    } else if (["number", "float", "double", "decimal"].includes(type)) {
      value = Number(raw);
      if (!Number.isFinite(value))
        throw new Error(`${spec.label || spec.name}：请输入有效数值。`);
    } else if (type === "ids") {
      try {
        value = String(raw).trim().startsWith("[")
          ? JSON.parse(raw)
          : String(raw)
              .split(/[,，\s]+/)
              .filter(Boolean)
              .map(Number);
        if (
          !Array.isArray(value) ||
          value.some((item) => !Number.isSafeInteger(item))
        )
          throw new Error();
      } catch {
        throw new Error(
          `${spec.label || spec.name}：请输入逗号分隔的整数 ID。`,
        );
      }
    } else if (["json", "object", "array", "list"].includes(type)) {
      try {
        value = JSON.parse(raw);
        validateJsonNumbers(value);
        if (
          type === "object" &&
          (!value || Array.isArray(value) || typeof value !== "object")
        )
          throw new Error();
        if (["array", "list"].includes(type) && !Array.isArray(value))
          throw new Error();
      } catch {
        throw new Error(`${spec.label || spec.name}：JSON 格式无效。`);
      }
    } else value = String(raw);
    const min = spec.min ?? spec.minimum,
      max = spec.max ?? spec.maximum;
    if (
      typeof value === "number" &&
      ((min != null && value < min) || (max != null && value > max))
    )
      throw new Error(
        `${spec.label || spec.name}：超出允许范围 ${min ?? "不限"}～${max ?? "不限"}。`,
      );
    return value;
  }

  const api = {
    parseField,
    normalizeChoices,
    fieldChoices,
    validateJsonNumbers,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.ConsoleFields = api;
})(globalThis);
