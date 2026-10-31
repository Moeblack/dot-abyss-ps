from __future__ import annotations

import random
from datetime import timedelta

from .gameplay_catalog import GameplayError, JST, number, parse_timestamp
from .gameplay_effects import research_effects
from .gameplay_library import chapter_complete
from .gameplay_receipts import feature_data
from .gameplay_records import find, put, rows

INITIAL_DEFENSIVE_UNITS = (1002, 2001, 3001, 3002, 4001, 5001, 6001, 9000)


def server_day(now):
    return (now.astimezone(JST) - timedelta(hours=5)).date().isoformat()


def disaster_data(game):
    data = feature_data(game, "disaster")
    if not data:
        existing = rows(game.state, "t_disaster_slots")
        data.update(day=server_day(game.now), free_summons=0, generation=0,
                    daily_spawned=any(row["m_disaster_quest_id"] for row in existing),
                    cleared_quests={}, slot_versions={str(slot): 0 for slot in range(4)}, initialized=False)
    if set(data) != {"day", "free_summons", "generation", "daily_spawned", "cleared_quests", "slot_versions", "initialized"}:
        raise GameplayError("Stored disaster progress is invalid")
    if type(data["initialized"]) is not bool or type(data["daily_spawned"]) is not bool:
        raise GameplayError("Stored disaster flags are invalid")
    try:
        if server_day(parse_timestamp(data["day"] + "T12:00:00+09:00")) != data["day"]:
            raise ValueError()
    except (TypeError, ValueError) as error:
        raise GameplayError("Stored disaster day is invalid") from error
    number(data["free_summons"], 0, 10000)
    number(data["generation"], 0)
    if not isinstance(data["slot_versions"], dict) or set(data["slot_versions"]) != {"0", "1", "2", "3"}:
        raise GameplayError("Stored disaster slot versions are invalid")
    for value in data["slot_versions"].values():
        number(value, 0)
    if not isinstance(data["cleared_quests"], dict) or len(data["cleared_quests"]) > 10000:
        raise GameplayError("Stored disaster receipts are invalid")
    for key, record in data["cleared_quests"].items():
        if not isinstance(key, str) or not key.isdecimal() or not isinstance(record, dict) or set(record) != {"clears", "first_at", "last_at"}:
            raise GameplayError("Stored disaster quest receipt is invalid")
        number(int(key))
        number(record["clears"], 1, 2 ** 31 - 1)
        for field in ("first_at", "last_at"):
            try:
                parse_timestamp(record[field])
            except (TypeError, ValueError) as error:
                raise GameplayError("Stored disaster receipt timestamp is invalid") from error
    return data


def status_record(game):
    status = find(game.state, "t_disaster_statuses")
    if status is None:
        status = put(game.state, "t_disaster_statuses", {}, {
            "disaster_level": 1, "disaster_point": 0, "disaster_level_record_high": 1,
            "reports": [], "skippable_disaster_level": 0, "skip_extra_drop_probability": 0,
            "skip_additional_drop_rate": 0, "last_appear_at": game.now.isoformat(),
            "last_disaster_defeat_at": ""}, game.user_id)
    return status


def slot_record(game, slot):
    slot = number(slot, 0, 3)
    record = find(game.state, "t_disaster_slots", slot=slot)
    if record is None:
        record = put(game.state, "t_disaster_slots", {"slot": slot}, {
            "is_special_boss": int(slot == 0), "disaster_level": 0,
            "m_disaster_boss_id": 0, "m_disaster_quest_id": 0, "last_appear_at": game.now.isoformat()}, game.user_id)
    return record


def restriction_met(game, restriction):
    if restriction["restrict_type"] == 0:
        return True
    if restriction["restrict_type"] == 3:
        return chapter_complete(game, restriction["restrict_param_1"])
    raise GameplayError("Disaster release condition is not supported", 503, 5036)


def boss_unlocked(game, identifier):
    game.catalog.get("m_disaster_bosses", identifier)
    return all(restriction_met(game, row) for row in game.catalog.rows("m_disaster_boss_restricts")
               if row["m_disaster_boss_id"] == identifier)


def level_unlocked(game, level):
    return all(restriction_met(game, row) for row in game.catalog.rows("m_disaster_level_restricts")
               if row["disaster_level"] <= level)


def level_definition(game, level):
    definition = next((row for row in game.catalog.rows("m_disaster_levels") if row["disaster_level"] == level), None)
    if definition is None:
        raise GameplayError("Disaster level master is missing", 503, 5036)
    return definition


def prepare_disaster(game):
    data = disaster_data(game)
    status = status_record(game)
    level_definition(game, status["disaster_level"])
    for slot in range(4):
        slot_record(game, slot)
    if not data["initialized"]:
        for identifier in INITIAL_DEFENSIVE_UNITS:
            game.catalog.get("m_defensive_units", identifier)
            if find(game.state, "t_defensive_units", m_defensive_unit_id=identifier) is None:
                put(game.state, "t_defensive_units", {"m_defensive_unit_id": identifier}, {"level": 1}, game.user_id)
        data["initialized"] = True
    for effect in research_effects(game, 500):
        identifier, level = number(effect["param_1"]), number(effect["param_2"])
        game.catalog.get("m_defensive_units", identifier)
        if not any(row["m_defensive_unit_id"] == identifier and row["level"] == level for row in game.catalog.rows("m_defensive_unit_levels")):
            raise GameplayError("Research defensive level is not defined", 503, 5036)
        unit = find(game.state, "t_defensive_units", m_defensive_unit_id=identifier)
        if unit is None:
            put(game.state, "t_defensive_units", {"m_defensive_unit_id": identifier}, {"level": level}, game.user_id)
        else:
            unit["level"] = max(unit["level"], level)
    ordinary_bosses = {row["m_disaster_boss_id"] for table in ("m_disaster_boss_appears", "m_disaster_sp_boss_appears") for row in game.catalog.rows(table)}
    for definition in game.catalog.rows("m_disaster_bosses"):
        if definition["id"] not in ordinary_bosses:
            continue
        if boss_unlocked(game, definition["id"]) and find(game.state, "t_disaster_bosses", m_disaster_boss_id=definition["id"]) is None:
            put(game.state, "t_disaster_bosses", {"m_disaster_boss_id": definition["id"]}, {"is_new": 1}, game.user_id)
    return status, data


def clear_slot(game, slot):
    row = slot_record(game, slot)
    row.update(disaster_level=0, m_disaster_boss_id=0, m_disaster_quest_id=0, last_appear_at=game.now.isoformat())
    data = disaster_data(game)
    data["slot_versions"][str(slot)] += 1


def expire_slots(game, *, timeout):
    result = []
    for slot in range(4):
        row = slot_record(game, slot)
        if row["m_disaster_quest_id"]:
            result.append({"slot": slot, "m_disaster_boss_id": row["m_disaster_boss_id"],
                           "disaster_level": row["disaster_level"], "rewards": [], "is_timeout": int(timeout)})
            clear_slot(game, slot)
    return result


def rotate_day(game):
    status, data = prepare_disaster(game)
    day = server_day(game.now)
    if data["day"] == day:
        return []
    from .gameplay_battle_session import battle_state

    active = battle_state(game)["active"]
    if active is not None and active["kind"] == "disaster":
        return []
    expired = expire_slots(game, timeout=True)
    data.update(day=day, free_summons=0, daily_spawned=False)
    status.update(last_appear_at=game.now.isoformat(), reports=[], skippable_disaster_level=0,
                  skip_extra_drop_probability=0, skip_additional_drop_rate=0)
    return expired


def candidates(game, *, special=False, boss_id=0):
    level = status_record(game)["disaster_level"]
    table = "m_disaster_sp_boss_appears" if special else "m_disaster_boss_appears"
    return [row for row in game.catalog.rows(table) if row["disaster_level"] == level
            and (not boss_id or row["m_disaster_boss_id"] == boss_id) and boss_unlocked(game, row["m_disaster_boss_id"])]


def choose_appearance(game, options):
    if not options:
        raise GameplayError("No unlocked disaster is available at this level")
    data = disaster_data(game)
    data["generation"] += 1
    rng = random.Random(f"disaster:{game.user_id}:{data['generation']}")
    return rng.choice(sorted(options, key=lambda row: row["id"]))


def add_report(game, boss_id, message_type):
    messages = [row for row in game.catalog.rows("m_disaster_boss_messages")
                if row["m_disaster_boss_id"] == boss_id and row["message_type"] == message_type]
    if messages:
        reports = status_record(game)["reports"]
        reports.append({"message_id": min(messages, key=lambda row: row["id"])["id"], "replies": []})
        del reports[:-20]


def place_appearance(game, slot, appearance):
    row = slot_record(game, slot)
    row.update(m_disaster_boss_id=appearance["m_disaster_boss_id"], m_disaster_quest_id=appearance["m_disaster_quest_id"],
               disaster_level=appearance["disaster_level"], last_appear_at=game.now.isoformat())
    disaster_data(game)["slot_versions"][str(slot)] += 1
    status_record(game)["last_appear_at"] = game.now.isoformat()
    add_report(game, appearance["m_disaster_boss_id"], 1)
    return {"slot": slot, "m_disaster_boss_id": row["m_disaster_boss_id"], "disaster_level": row["disaster_level"]}


def fill_daily_slots(game):
    _, data = prepare_disaster(game)
    if data["daily_spawned"]:
        return []
    options = candidates(game)
    result = []
    for slot in (1, 2, 3):
        if not slot_record(game, slot)["m_disaster_quest_id"]:
            appearance = choose_appearance(game, options)
            result.append(place_appearance(game, slot, appearance))
            options = [row for row in options if row["m_disaster_boss_id"] != appearance["m_disaster_boss_id"]] or candidates(game)
    data["daily_spawned"] = True
    return result


def maybe_special(game):
    status = status_record(game)
    if slot_record(game, 0)["m_disaster_quest_id"] or status["disaster_point"] < level_definition(game, status["disaster_level"])["max_disaster_point"]:
        return []
    options = candidates(game, special=True)
    return [place_appearance(game, 0, choose_appearance(game, options))] if options else []


def appearance_for_slot(game, row):
    table = "m_disaster_sp_boss_appears" if row["is_special_boss"] else "m_disaster_boss_appears"
    result = next((entry for entry in game.catalog.rows(table) if entry["m_disaster_quest_id"] == row["m_disaster_quest_id"]
                   and entry["m_disaster_boss_id"] == row["m_disaster_boss_id"] and entry["disaster_level"] == row["disaster_level"]), None)
    if result is None:
        raise GameplayError("Disaster slot has no matching appearance master", 503, 5036)
    return result
