from __future__ import annotations

import copy

from .gameplay_battle_rewards import grant_display_rewards, settle_battle
from .gameplay_battle_session import battle_state, create_session, session_view
from .gameplay_catalog import GameplayError, identifiers, number, parse_timestamp
from .gameplay_disaster_party import formation_fuel
from .gameplay_disaster_state import (
    add_report, appearance_for_slot, boss_unlocked, candidates, choose_appearance, clear_slot,
    disaster_data, expire_slots, fill_daily_slots, level_definition, level_unlocked,
    maybe_special, place_appearance, prepare_disaster, rotate_day, server_day, slot_record, status_record,
)
from .gameplay_economy import consume
from .gameplay_effects import extra_drop_bonus
from .gameplay_main_repeat import skipped_result
from .gameplay_receipts import operation_digest
from .gameplay_records import find, rows
from .gameplay_routes import route
from .gameplay_statistics import record
from .gameplay_tutorial import can_unlock_feature


def is_disaster_quest_cleared(game, quest_id):
    state = game.progress.get("features", {}).get("disaster", {}).get("data", {})
    entry = state.get("cleared_quests", {}).get(str(quest_id))
    return isinstance(entry, dict) and type(entry.get("clears")) is int and entry["clears"] > 0


def require_access(game):
    existing = find(game.state, "t_feature_locks", m_feature_lock_id=3)
    if not (existing and existing["is_unlocked"]) and not can_unlock_feature(game, 3):
        raise GameplayError("Disaster battles have not been unlocked")
    return prepare_disaster(game)


def refresh(game, body):
    require_access(game)
    rotate_day(game)
    return {"appears": fill_daily_slots(game) + maybe_special(game)}


def boss_check(game, body):
    require_access(game)
    selected = identifiers(body["m_disaster_boss_ids"], 100)
    for identifier in selected:
        row = find(game.state, "t_disaster_bosses", m_disaster_boss_id=identifier)
        if row is None or not boss_unlocked(game, identifier):
            raise GameplayError("Disaster boss is not unlocked")
        row["is_new"] = 0
    return {}


def summon(game, body):
    _, data = require_access(game)
    if battle_state(game)["active"] is not None:
        raise GameplayError("Finish the active battle before summoning a disaster")
    expired = rotate_day(game)
    boss_id = number(body["m_disaster_boss_id"], 0)
    if boss_id:
        boss = game.catalog.get("m_disaster_bosses", boss_id)
        if not boss["summonable"] or not boss_unlocked(game, boss_id):
            raise GameplayError("This disaster boss cannot be summoned")
    options = candidates(game, boss_id=boss_id)
    if not options:
        raise GameplayError("No eligible disaster can be summoned")
    items = body["use_items"]
    if not isinstance(items, list) or len(items) > 1:
        raise GameplayError("Invalid disaster summon cost", 400, 4007)
    if items:
        item = items[0]
        if not isinstance(item, dict) or set(item) != {"m_item_id", "amount"} or item["m_item_id"] != 120001 or item["amount"] != 1:
            raise GameplayError("Disaster ticket summon requires one permit", 400, 4007)
        number(item["m_item_id"])
        number(item["amount"])
        consume(game, [{"content_type": 30, "content_id": 120001, "amount": 1}])
    else:
        if data["free_summons"] >= game.rules["disaster_free_summons_per_day"]:
            raise GameplayError("Daily free disaster summons are exhausted")
        data["free_summons"] += 1
    available = [slot_record(game, slot) for slot in (1, 2, 3)]
    target = next((row for row in available if not row["m_disaster_quest_id"]), min(available, key=lambda row: (row["last_appear_at"], row["slot"])))
    if target["m_disaster_quest_id"]:
        expired.append({"slot": target["slot"], "m_disaster_boss_id": target["m_disaster_boss_id"],
                        "disaster_level": target["disaster_level"], "rewards": [], "is_timeout": 0})
    appeared = place_appearance(game, target["slot"], choose_appearance(game, options))
    return {"expires": expired, "appears": [appeared]}


def patrol(game, body):
    status, data = require_access(game)
    if battle_state(game)["active"] is not None:
        raise GameplayError("Finish the active battle before changing disaster level")
    requested = number(body["disaster_level"], 0, 2 ** 31 - 1)
    target = requested or game.rules["disaster_patrol_default_level"]
    level_definition(game, target)
    if target > status["disaster_level_record_high"] or not level_unlocked(game, target):
        raise GameplayError("Requested disaster level has not been unlocked")
    previous = status["disaster_level"]
    if target == previous and not status["disaster_point"]:
        return {"before_disaster_level": previous, "after_disaster_level": previous, "expires": []}
    cost = level_definition(game, previous)["require_patrol_cost"]
    consume(game, [{"content_type": 50, "content_id": 0, "amount": cost}])
    expired = expire_slots(game, timeout=False)
    status.update(disaster_level=target, disaster_point=0, reports=[])
    data["daily_spawned"] = False
    return {"before_disaster_level": previous, "after_disaster_level": target, "expires": expired}


def active_slot(game, slot):
    row = slot_record(game, slot)
    if not row["m_disaster_quest_id"]:
        raise GameplayError("No disaster occupies this slot")
    if not boss_unlocked(game, row["m_disaster_boss_id"]):
        raise GameplayError("Disaster boss requirements have not been met")
    appearance_for_slot(game, row)
    return row


def start(game, body):
    require_access(game)
    rotate_day(game)
    slot, party_no = number(body["slot"], 0, 3), number(body["party_no"], 1, 10)
    row = active_slot(game, slot)
    definition = game.catalog.get("m_disaster_quests", row["m_disaster_quest_id"])
    data = disaster_data(game)
    context = {"slot": slot, "slot_version": data["slot_versions"][str(slot)], "boss_id": row["m_disaster_boss_id"],
               "disaster_level": row["disaster_level"], "is_special_boss": row["is_special_boss"]}
    previous = battle_state(game)["active"]
    result = create_session(game, definition, kind="disaster", quest_id=definition["id"], party_no=party_no, context=context)
    if previous is None:
        cost = formation_fuel(game, party_no, definition["id"])
        if cost:
            consume(game, [{"content_type": 30, "content_id": 130001, "amount": cost}])
    return result


def complete_disaster(game, session, stage_results):
    context = session["context"]
    row = active_slot(game, context["slot"])
    data = disaster_data(game)
    if (data["slot_versions"][str(context["slot"])] != context["slot_version"]
            or row["m_disaster_quest_id"] != session["quest_id"]):
        raise GameplayError("The disaster slot has changed since this battle started")
    appearance = appearance_for_slot(game, row)
    generic = settle_battle(game, session, stage_results)["battle_result"]
    table = "m_disaster_sp_level_rewards" if context["is_special_boss"] else "m_disaster_level_rewards"
    rewards = [reward for reward in game.catalog.rows(table) if reward["disaster_level"] == context["disaster_level"]]
    displayed = grant_display_rewards(game, rewards)
    status = status_record(game)
    if data["day"] != server_day(game.now):
        expire_slots(game, timeout=True)
        data.update(day=server_day(game.now), free_summons=0, daily_spawned=False)
        status.update(skippable_disaster_level=0, skip_extra_drop_probability=0, skip_additional_drop_rate=0)
    status.update(skippable_disaster_level=max(status["skippable_disaster_level"], context["disaster_level"]),
                  last_disaster_defeat_at=game.now.isoformat(), skip_extra_drop_probability=session.get("ability_extra_drop", 0),
                  skip_additional_drop_rate=extra_drop_bonus(game))
    key = str(session["quest_id"])
    entry = data["cleared_quests"].setdefault(key, {"clears": 0, "first_at": game.now.isoformat(), "last_at": game.now.isoformat()})
    entry.update(clears=number(entry["clears"] + 1, 1, 2 ** 31 - 1), last_at=game.now.isoformat())
    clear_slot(game, context["slot"])
    if context["is_special_boss"]:
        next_level = status["disaster_level"] + 1
        maximum = max(level["disaster_level"] for level in game.catalog.rows("m_disaster_levels"))
        if next_level <= maximum and level_unlocked(game, next_level):
            expire_slots(game, timeout=False)
            status.update(disaster_level=next_level, disaster_point=0,
                          disaster_level_record_high=max(status["disaster_level_record_high"], next_level))
            data["daily_spawned"] = False
            fill_daily_slots(game)
        else:
            status["disaster_point"] = 0
    else:
        status["disaster_point"] = min(level_definition(game, status["disaster_level"])["max_disaster_point"],
                                        status["disaster_point"] + appearance["disaster_point"])
        maybe_special(game)
    add_report(game, context["boss_id"], 2)
    record(game.progress, game.now, 160100)
    record(game.progress, game.now, 160000, status["disaster_level_record_high"], mode="max")
    return {"drops": generic["drops"], "first_rewards": [], "clear_rewards": displayed,
            "grant_character_exp": generic["grant_character_exp"], "before_character_exp": generic["before_character_exp"],
            "after_character_exp": generic["after_character_exp"]}


def clear(game, body):
    require_access(game)
    data = battle_state(game)
    digest = operation_digest("api/disaster/clear-quest", "POST", body)
    if data["last_result"] is not None and data["last_result"]["digest"] == digest:
        return copy.deepcopy(data["last_result"]["response"])
    session = data["active"]
    if session is None or session["kind"] != "disaster":
        raise GameplayError("No disaster battle is active")
    result = complete_disaster(game, session, body["stage_results"])
    data["active"] = None
    data["last_result"] = {"digest": digest, "response": copy.deepcopy(result)}
    return result


def abandon(game, body):
    require_access(game)
    number(body["play_time"], 0, 86400000)
    data = battle_state(game)
    if data["active"] is not None and data["active"]["kind"] != "disaster":
        raise GameplayError("This endpoint can only close a disaster battle")
    data["active"] = None
    return session_view(None)


def skip(game, body):
    status, progress = require_access(game)
    if battle_state(game)["active"] is not None:
        raise GameplayError("Finish the active battle before skipping")
    rotate_day(game)
    row = active_slot(game, number(body["slot"], 0, 3))
    if not status["last_disaster_defeat_at"]:
        raise GameplayError("Win a disaster battle today before skipping")
    if (server_day(parse_timestamp(status["last_disaster_defeat_at"])) != server_day(game.now)
            or row["disaster_level"] > status["skippable_disaster_level"]):
        raise GameplayError("This disaster level is not skippable today")
    definition = game.catalog.get("m_disaster_quests", row["m_disaster_quest_id"])
    context = {"slot": row["slot"], "slot_version": progress["slot_versions"][str(row["slot"])],
               "boss_id": row["m_disaster_boss_id"], "disaster_level": row["disaster_level"], "is_special_boss": row["is_special_boss"]}
    create_session(game, definition, kind="disaster", quest_id=definition["id"], party_no=1,
                   context={**context, "is_skip": True}, validate_members=False,
                   ability_bonus=status["skip_extra_drop_probability"])
    data = battle_state(game)
    session = data["active"]
    before_gold = rows(game.state, "t_statuses")[0]["gold"]
    result = complete_disaster(game, session, skipped_result(session["stage_detail"], session["stage_type"]))
    data["active"] = None
    return {**result, "grant_gold": rows(game.state, "t_statuses")[0]["gold"] - before_gold}


DISASTER_ROUTES = {
    "api/disaster/refresh": route("POST", "", refresh),
    "api/disaster/boss/check": route("POST", "m_disaster_boss_ids", boss_check),
    "api/disaster/summon": route("POST", "m_disaster_boss_id use_items", summon),
    "api/disaster/patrol": route("POST", "disaster_level", patrol),
    "api/disaster/start-quest": route("POST", "slot party_no", start),
    "api/disaster/clear-quest": route("POST", "stage_results", clear),
    "api/disaster/close-quest": route("POST", "play_time", abandon),
    "api/disaster/surrender-quest": route("POST", "play_time", abandon),
    "api/disaster/skip-quest": route("POST", "slot", skip),
}
