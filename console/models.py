from __future__ import annotations

import json
import math
from dataclasses import dataclass, field


GROUP_LABELS = {
    "overview": "连接与概况", "profile": "资料与存档", "wallet": "货币与体力",
    "currency": "货币与体力", "items": "背包与材料", "inventory": "背包与材料",
    "characters": "角色与成长", "character": "角色与成长", "growth": "好感与羁绊",
    "affection": "好感与羁绊", "skins": "服装与外观", "equipment": "装备与魔晶石",
    "party": "编队与预设", "parties": "编队与预设", "facilities": "设施与研究",
    "research": "设施与研究", "story": "剧情与教程", "tutorial": "剧情与教程",
    "missions": "任务与邮件", "mission": "任务与邮件", "mail": "任务与邮件", "gacha": "抽卡与代币",
    "shop": "商城与权益", "entitlements": "商城与权益", "tavern": "酒馆",
    "idle": "探索队", "battle": "常驻战斗", "modes": "常驻战斗",
    "nether": "深渊远征", "events": "活动与复刻", "event": "活动与复刻", "social": "社交与主页",
    "appearance": "服装与外观", "building": "设施与研究",
    "rules": "本地规则", "master": "主数据", "history": "操作记录", "backups": "存档备份",
}


def compact(value, limit: int = 90) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    text = str(value).replace("\n", " ")
    return text if len(text) <= limit else text[:limit - 1] + "…"


def parse_field(spec: dict, value):
    label = spec.get("label", spec.get("name", "字段"))
    kind = spec.get("type", "string")
    if kind in {"boolean", "bool"}:
        if isinstance(value, bool):
            return value
        if str(value).lower() in {"1", "true", "yes", "是"}:
            return True
        if str(value).lower() in {"0", "false", "no", "否"}:
            return False
        raise ValueError(f"{label}：请选择是或否。")
    if isinstance(value, str):
        value = value.strip()
    if value == "":
        if spec.get("required", False):
            raise ValueError(f"{label}：不能为空。")
        if "default" in spec:
            return spec["default"]
        return None
    try:
        if kind in {"integer", "int", "long", "short", "byte", "uint", "ulong", "int32", "int64"}:
            if isinstance(value, bool):
                raise ValueError
            parsed = int(value)
            if isinstance(value, float) and value != parsed:
                raise ValueError
        elif kind in {"number", "float", "double", "decimal"}:
            parsed = float(value)
            if not math.isfinite(parsed):
                raise ValueError
        elif kind in {"array", "list", "ids", "integer[]"}:
            parsed = value if isinstance(value, list) else json.loads(value) if str(value).startswith("[") else [part.strip() for part in str(value).replace("，", ",").replace("\n", ",").split(",") if part.strip()]
            if not isinstance(parsed, list):
                raise ValueError
            item_type = spec.get("items", {}).get("type") if isinstance(spec.get("items"), dict) else spec.get("items")
            if kind in {"ids", "integer[]"} or item_type in {"int", "integer"}:
                parsed = [parse_field({"type": "integer", "label": label}, part) for part in parsed]
        elif kind in {"object", "json"}:
            parsed = value if isinstance(value, (dict, list)) else json.loads(value)
            if kind == "object" and not isinstance(parsed, dict):
                raise ValueError
        else:
            parsed = str(value)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label}：请输入有效的{ {'int': '整数', 'integer': '整数', 'number': '数值', 'float': '数值', 'object': 'JSON 对象'}.get(kind, '内容')}。") from exc
    if isinstance(parsed, (int, float)) and not isinstance(parsed, bool):
        lower, upper = spec.get("min", spec.get("minimum")), spec.get("max", spec.get("maximum"))
        if lower is not None and parsed < lower:
            raise ValueError(f"{label}：不能小于 {lower}。")
        if upper is not None and parsed > upper:
            raise ValueError(f"{label}：不能大于 {upper}。")
    return parsed


@dataclass
class Action:
    id: str
    label: str
    group: str
    description: str = ""
    fields: list[dict] = field(default_factory=list)
    timing: str = ""

    @classmethod
    def from_dict(cls, value: dict):
        identity = str(value.get("id", value.get("operation", value.get("name", ""))))
        fields = value.get("fields", value.get("parameters", []))
        if isinstance(fields, dict):
            fields = [dict(spec if isinstance(spec, dict) else {"type": spec}, name=name) for name, spec in fields.items()]
        return cls(identity, value.get("label", value.get("title", identity)), value.get("group", value.get("category", "profile")), value.get("description", ""), fields, value.get("timing", value.get("sync", "")))


def actions_from_schema(schema: dict) -> list[Action]:
    values = schema.get("actions", schema.get("operations", []))
    if isinstance(values, dict):
        values = [dict(value, id=name) for name, value in values.items()]
    return [Action.from_dict(value) for value in values]
