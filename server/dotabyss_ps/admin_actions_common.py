from __future__ import annotations

import copy
import math

from .gameplay_catalog import GameplayError, identifiers, number
from .gameplay_economy import text_argument
from .gameplay_records import find, rows
from .protocol import validate_data

MAX_VALUE = 2 ** 31 - 1


def field(name, label, kind="integer", *, required=True, **options):
    if kind == "integer" and (name.endswith("_id") or options.get("source")):
        options.setdefault("max", 2 ** 63 - 1)
    return {"name": name, "label": label, "type": kind, "required": required, **options}


def choice(name, label, values, **options):
    return field(name, label, "choice", choices=[{"value": value, "label": text} for value, text in values], **options)


def spec(identifier, group, label, fields=(), *, description="", timing="live"):
    return {"id": identifier, "group": group, "label": label, "description": description,
            "fields": list(fields), "timing": timing}


def selection_fields(source):
    return [field("ids", "选择内容", "ids", required=False, default=[], source=source),
            field("all", "选择当前版本全部内容", "boolean", required=False, default=False)]


def arguments(definition, values):
    if not isinstance(values, dict):
        raise GameplayError("管理参数必须是对象", 400, 4007)
    fields = {item["name"]: item for item in definition["fields"]}
    if set(values) - fields.keys():
        raise GameplayError("管理参数包含未知字段", 400, 4007)
    result = {}
    for name, item in fields.items():
        if name not in values:
            if "default" in item:
                value = copy.deepcopy(item["default"])
            elif item["required"]:
                raise GameplayError("缺少参数：" + item["label"], 400, 4007)
            else:
                continue
        else:
            value = values[name]
        kind = item["type"]
        if kind == "integer":
            number(value, item.get("min", 0), item.get("max", MAX_VALUE))
        elif kind == "number":
            if type(value) not in (int, float) or not math.isfinite(value) or not item.get("min", 0) <= value <= item.get("max", MAX_VALUE):
                raise GameplayError("数值超出允许范围：" + item["label"], 400, 4007)
        elif kind == "boolean":
            if type(value) is not bool:
                raise GameplayError("布尔参数格式错误：" + item["label"], 400, 4007)
        elif kind == "choice":
            if not any(type(value) is type(option["value"]) and value == option["value"] for option in item["choices"]):
                raise GameplayError("选项不受支持：" + item["label"], 400, 4007)
        elif kind == "ids":
            identifiers(value, 10000)
        elif kind == "string":
            text_argument(value, item.get("max_length", 300), empty=item.get("allow_empty", False), multiline=item.get("multiline", False))
        elif kind == "json":
            validate_data(value)
        else:
            raise ValueError("Unknown admin field type: " + kind)
        result[name] = value
    return result


def selected(game, args, master):
    values = list(args.get("ids", []))
    if args.get("all"):
        if values:
            raise GameplayError("全选与指定列表不能同时使用", 400, 4007)
        values = sorted(row["id"] for row in game.catalog.rows(master))
    if not values:
        raise GameplayError("至少选择一项内容", 400, 4007)
    for identifier in values:
        game.catalog.get(master, identifier)
    return values


def owned(game, table, identifier):
    result = find(game.state, table, id=number(identifier))
    if result is None:
        raise GameplayError("当前存档没有这项内容")
    return result


def no_battle(game):
    active = game.progress.get("features", {}).get("battle", {}).get("data", {}).get("active")
    if active is not None:
        raise GameplayError("请先结算或退出当前战斗，再修改玩法进度", 409, 4098)


def no_expedition(game):
    no_battle(game)
    run = game.progress.get("features", {}).get("nether", {}).get("data", {}).get("run")
    if run and not run.get("settled"):
        raise GameplayError("请先结束当前深渊远征，再删除角色或装备", 409, 4098)


def changed(label, count=None, *, timing="live", warnings=()):
    return {"summary": label if count is None else f"{label}：{count} 项", "timing": timing, "warnings": list(warnings)}


def remove_rows(game, table, predicate):
    values = rows(game.state, table)
    values[:] = [row for row in values if not predicate(row)]
