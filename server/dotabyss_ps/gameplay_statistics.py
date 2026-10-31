from __future__ import annotations

from datetime import date, timedelta
import re

from .gameplay_catalog import GameplayError, number
from .gameplay_records import rows


def bucket(progress):
    entry = progress["features"].setdefault("statistics", {"version": 1, "data": {}})
    data = entry["data"]
    if not data:
        data.update(initialized=False, totals={}, days={})
    if set(data) != {"initialized", "totals", "days"} or type(data["initialized"]) is not bool or not isinstance(data["totals"], dict) or not isinstance(data["days"], dict):
        raise GameplayError("Stored statistics are invalid")
    if len(data["days"]) > 400 or len(data["totals"]) > 10000:
        raise GameplayError("Stored statistics exceed their limit")
    for day, values in data["days"].items():
        try:
            valid = isinstance(day, str) and date.fromisoformat(day).isoformat() == day
        except ValueError:
            valid = False
        if not valid or not isinstance(values, dict) or len(values) > 10000:
            raise GameplayError("Stored daily statistics are invalid")
    for values in [data["totals"], *data["days"].values()]:
        for key, value in values.items():
            if not isinstance(key, str) or not re.fullmatch(r"[1-9][0-9]{0,8}:[0-9]{1,19}:[0-9]{1,19}", key):
                raise GameplayError("Stored statistic key is invalid")
            number(value, 0, 2 ** 31 - 1)
    return data


def key(situation, target=0, sub=0):
    return f"{number(situation, 1, 999999999)}:{number(target, 0)}:{number(sub, 0)}"


def record(progress, now, situation, amount=1, target=0, sub=0, *, mode="add"):
    amount = number(amount, 0, 2 ** 31 - 1)
    data = bucket(progress)
    identity = key(situation, target, sub)
    day = now.date().isoformat()
    today = data["days"].setdefault(day, {})
    cutoff = (now.date() - timedelta(days=399)).isoformat()
    data["days"] = {k: v for k, v in data["days"].items() if k >= cutoff}
    for values in (data["totals"], today):
        old = values.get(identity)
        value = (old or 0) + amount if mode == "add" else max(old or 0, amount) if mode == "max" else min(old, amount) if old is not None else amount
        if mode not in ("add", "max", "min"):
            raise ValueError("Invalid statistics aggregation")
        values[identity] = number(value, 0, 2 ** 31 - 1)


def initialize(state, progress, catalog):
    data = bucket(progress)
    if data["initialized"]:
        return
    totals = data["totals"]
    totals[key(40000)] = len(rows(state, "t_characters"))
    for table, master, field in (("t_weapons", "m_weapons", "m_weapon_id"), ("t_armors", "m_armors", "m_armor_id"), ("t_accessories", "m_accessories", "m_accessory_id")):
        for gear in rows(state, table):
            definition = catalog.get(master, gear[field])
            for identity in (key(80000), key(80002, definition["rank"]), key(80003, definition["rarity"])):
                totals[identity] = totals.get(identity, 0) + 1
    totals[key(40300)] = sum(r["limit_break_count"] for r in rows(state, "t_characters"))
    totals[key(200000)] = sum(r["total_play_count"] for r in rows(state, "t_tavern_shops"))
    data["initialized"] = True


def value(progress, now, mission, *, mode="add", start=None, data=None):
    data = bucket(progress) if data is None else data
    identity = key(mission["situation_type"], mission.get("target_id", 0), mission.get("sub_target_id", 0))
    kind = mission["mission_type"]
    if kind in (2, 8):
        return data["days"].get(now.date().isoformat(), {}).get(identity)
    if kind in (3, 6):
        start = max(start or date.min, now.date() - timedelta(days=now.weekday()))
    if start is None:
        return data["totals"].get(identity)
    values = [v[identity] for day, v in data["days"].items() if start <= date.fromisoformat(day) <= now.date() and identity in v]
    if not values:
        return None
    return sum(values) if mode == "add" else max(values) if mode == "max" else min(values)


def record_grant(progress, now, kind, identifier, amount, catalog):
    if progress is None:
        return
    if kind in (70, 80, 90):
        definition = catalog.get({70: "m_weapons", 80: "m_armors", 90: "m_accessories"}[kind], identifier)
        record(progress, now, 80000, amount)
        record(progress, now, 80002, amount, definition["rank"])
        record(progress, now, 80003, amount, definition["rarity"])
    elif kind == 40:
        record(progress, now, 40000, amount)


def record_operation(game, path, body, before, result):
    def delta_sum(table, field):
        old = {r["id"]: r[field] for r in before[table]["up"]}
        return sum(max(0, r[field] - old.get(r["id"], r[field])) for r in rows(game.state, table))
    if path == "api/character/limit-break":
        record(game.progress, game.now, 40300, delta_sum("t_characters", "limit_break_count"))
    elif path in ("api/character/gift/give", "api/character/gift"):
        record(game.progress, game.now, 40400, sum(r["amount"] for r in body["use_items"]))
        if path.endswith("/give"):
            record(game.progress, game.now, 40500, delta_sum("t_characters", "bond_point"))
    elif path == "api/exchange/execute":
        record(game.progress, game.now, 60000, body["exchange_count"])
    elif path == "api/equipment/disassembly/execute":
        amount = sum(len(before[t]["up"]) - len(rows(game.state, t)) for t in ("t_weapons", "t_armors", "t_accessories"))
        record(game.progress, game.now, 90000, amount)
    elif path in ("api/idle-exploration/receive", "api/idle-exploration/boost"):
        if result.get("grant_gold") or result.get("grant_character_exp") or result.get("drops"):
            record(game.progress, game.now, 150000)
    elif path.startswith("api/tavern/"):
        count = delta_sum("t_tavern_shops", "total_play_count")
        if count:
            record(game.progress, game.now, 200000, count)
    elif path == "api/gacha/exec":
        definition = game.catalog.get("m_gachas", body["m_gacha_id"])
        record(game.progress, game.now, 250000, body["exec_count"] * definition["lottery_num"])
