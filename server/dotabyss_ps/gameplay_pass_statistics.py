from __future__ import annotations

import copy
import re
from datetime import date, datetime, time, timedelta

from .gameplay_catalog import GameplayError, JST, available, number, parse_timestamp
from .gameplay_statistics import aggregation_mode, bucket, key


def window(definition, kind, now):
    start, end = (parse_timestamp(definition[field]).astimezone(JST) for field in ("start_at", "end_at"))
    if start > end:
        raise GameplayError("Mission pass dates are invalid", 503, 5036)
    if kind == 6:
        today = now.astimezone(JST)
        boundary = today.replace(hour=5, minute=0, second=0, microsecond=0) - timedelta(days=(today.weekday() - start.weekday()) % 7)
        if boundary > today:
            boundary -= timedelta(days=7)
        start, end = max(start, boundary), min(end, boundary + timedelta(days=7) - timedelta(microseconds=1))
    return start, end


def period_key(identifier, kind, start, end):
    return f"{identifier}:{kind}:{start.isoformat()}:{end.isoformat()}"


def mission_window(game, mission):
    definition = game.catalog.get("m_mission_passes", mission["m_mission_pass_id"])
    start, end = window(definition, mission["mission_type"], game.now)
    if mission.get("m_mission_term_id"):
        term = game.catalog.get("m_mission_terms", mission["m_mission_term_id"])
        if term.get("start_at"):
            start = max(start, parse_timestamp(term["start_at"]))
        if term.get("end_at"):
            end = min(end, parse_timestamp(term["end_at"]))
    return start, end


def claim_key(mission, definition, now):
    start, _ = window(definition, mission["mission_type"], now)
    return f"{start.date().isoformat()}:{mission['id']}" if mission["mission_type"] == 6 else f"once:{mission['id']}"


def _aggregate(previous, amount, mode):
    value = (previous or 0) + amount if mode == "add" else max(previous or 0, amount) if mode == "max" else min(previous, amount) if previous is not None else amount
    return number(value, 0, 2 ** 31 - 1)


def _validate(data):
    if not isinstance(data, dict) or set(data) != {"legacy_before", "periods"} or not isinstance(data["periods"], dict) or len(data["periods"]) > 128:
        raise GameplayError("Stored mission pass statistics are invalid")
    try:
        parse_timestamp(data["legacy_before"])
    except (ValueError, TypeError, AttributeError) as error:
        raise GameplayError("Stored mission pass migration date is invalid") from error
    for identity, period in data["periods"].items():
        if not isinstance(identity, str) or len(identity) > 128 or not isinstance(period, dict) or set(period) != {"start_at", "end_at", "modes", "values"}:
            raise GameplayError("Stored mission pass period is invalid")
        try:
            start, end = parse_timestamp(period["start_at"]), parse_timestamp(period["end_at"])
        except (ValueError, TypeError, AttributeError) as error:
            raise GameplayError("Stored mission pass period dates are invalid") from error
        if start > end or not isinstance(period["modes"], dict) or not isinstance(period["values"], dict) or len(period["modes"]) > 10000 or set(period["values"]) - period["modes"].keys():
            raise GameplayError("Stored mission pass counters are invalid")
        for counter, mode in period["modes"].items():
            if not isinstance(counter, str) or not re.fullmatch(r"[1-9][0-9]{0,8}:[0-9]{1,19}:[0-9]{1,19}", counter) or mode not in ("add", "max", "min"):
                raise GameplayError("Stored mission pass counter identity is invalid")
        for amount in period["values"].values():
            number(amount, 0, 2 ** 31 - 1)
    return data


def _seed(statistics, start, end, now, identity, mode):
    result = None
    for day, values in statistics["days"].items():
        began = datetime.combine(date.fromisoformat(day), time(), JST)
        # Legacy daily totals cannot be split across a partial first or last day.
        if start <= began <= now and began + timedelta(days=1) - timedelta(microseconds=1) <= end and identity in values:
            result = _aggregate(result, values[identity], mode)
    return result


def _migrate_claims(game, mission, definition):
    if mission["mission_type"] != 6:
        return
    start, end = window(definition, 6, game.now)
    claims = game.progress["mission_claims"]
    target = claim_key(mission, definition, game.now)
    if target in claims:
        return
    for identifier, rewards in list(claims.items()):
        match = re.fullmatch(r"(\d{4})-W(\d{2}):" + str(mission["id"]), identifier)
        if match is None:
            continue
        try:
            monday = datetime.combine(date.fromisocalendar(int(match[1]), int(match[2]), 1), time(), JST)
        except ValueError as error:
            raise GameplayError("Stored weekly mission receipt is invalid") from error
        if monday <= end and monday + timedelta(days=7) > start:
            claims[target] = copy.deepcopy(rewards)
            return


def prepare(game):
    if "m_mission_passes" not in getattr(game.catalog, "tables", {}):
        return
    statistics = bucket(game.progress)
    missions = game.progress["features"].setdefault("missions", {"version": 1, "data": {}})["data"]
    data = _validate(missions.setdefault("pass_statistics", {"legacy_before": game.now.isoformat(), "periods": {}}))
    definitions = {row["id"]: row for row in game.catalog.rows("m_mission_passes") if available(row, game.now)}
    selected = [row for row in game.catalog.rows("m_missions") if row.get("m_mission_pass_id") in definitions and row["mission_type"] in (5, 6)]
    for mission in selected:
        identifier, kind = mission["m_mission_pass_id"], mission["mission_type"]
        definition = definitions[identifier]
        start, end = mission_window(game, mission)
        if not start <= game.now <= end:
            continue
        identity = period_key(identifier, kind, start, end)
        period = data["periods"].setdefault(identity, {"start_at": start.isoformat(), "end_at": end.isoformat(), "modes": {}, "values": {}})
        if period["start_at"] != start.isoformat() or period["end_at"] != end.isoformat():
            raise GameplayError("Mission pass statistics dates changed", 503, 5036)
        counter = key(mission["situation_type"], mission.get("target_id", 0), mission.get("sub_target_id", 0))
        mode = aggregation_mode(mission["situation_type"])
        if counter not in period["modes"]:
            period["modes"][counter] = mode
            value = _seed(statistics, start, end, game.now, counter, mode)
            if value is not None:
                period["values"][counter] = value
        elif period["modes"][counter] != mode:
            raise GameplayError("Mission pass counter aggregation changed", 503, 5036)
        if start <= parse_timestamp(data["legacy_before"]):
            _migrate_claims(game, mission, definition)
    cutoff = game.now - timedelta(days=399)
    data["periods"] = {identity: period for identity, period in data["periods"].items() if parse_timestamp(period["end_at"]) >= cutoff}
    if len(data["periods"]) > 128:
        raise GameplayError("Stored mission pass periods exceed their limit")


def record_periods(progress, now, identity, amount, mode):
    data = progress["features"].get("missions", {}).get("data", {}).get("pass_statistics")
    if data is None:
        return
    for period in _validate(data)["periods"].values():
        if identity not in period["modes"] or not parse_timestamp(period["start_at"]) <= now <= parse_timestamp(period["end_at"]):
            continue
        if period["modes"][identity] != mode:
            raise GameplayError("Mission pass statistic uses a different aggregation mode")
        period["values"][identity] = _aggregate(period["values"].get(identity), amount, mode)


def value(game, mission):
    definition = game.catalog.get("m_mission_passes", mission["m_mission_pass_id"])
    if not available(definition, game.now):
        return None
    start, end = mission_window(game, mission)
    if not start <= game.now <= end:
        return None
    identity = period_key(definition["id"], mission["mission_type"], start, end)
    data = game.progress["features"].get("missions", {}).get("data", {}).get("pass_statistics", {})
    if identity not in data.get("periods", {}):
        prepare(game)
        data = game.progress["features"]["missions"]["data"]["pass_statistics"]
    period = data["periods"][identity]
    return period["values"].get(key(mission["situation_type"], mission.get("target_id", 0), mission.get("sub_target_id", 0)))
