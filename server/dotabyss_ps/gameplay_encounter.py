from __future__ import annotations

import copy
import random
from datetime import timedelta

from .gameplay_battle_rewards import grant_display_rewards, settle_battle
from .gameplay_battle_session import battle_state, create_session, session_view
from .gameplay_catalog import GameplayError, number, parse_timestamp
from .gameplay_economy import consume
from .gameplay_effects import extra_drop_bonus
from .gameplay_main_repeat import skipped_result
from .gameplay_mode_supplies import daily_supply, spend_supply
from .gameplay_receipts import feature_data, operation_digest
from .gameplay_records import find, put, rows
from .gameplay_routes import route
from .gameplay_statistics import record as record_statistic


def progress(game):
    data = feature_data(game, "encounter")
    if not data:
        data["next_id"] = max((row["id"] for row in rows(game.state, "t_idle_exploration_encounters")), default=0) + 1
    data.setdefault("campaigns", {"current_id": 0, "entries": {}, "origins": {}})
    if set(data) != {"next_id", "campaigns"}:
        raise GameplayError("Stored encounter progress is invalid")
    number(data["next_id"])
    return data


def prepare_encounters(game):
    from .gameplay_encounter_campaign import supply_capacity

    if "m_encounter_quests" not in game.catalog.tables:
        return
    supply = daily_supply(game, 4, supply_capacity(game))
    supply["amount"] = min(supply["amount"], supply["capacity"])
    daily_supply(game, 14, game.rules["encounter_daily_recoveries"])


def settle_encounters(game):
    from .gameplay_encounter_campaign import clip_expiration, generation_settings, register_encounter, settle_campaigns

    if "m_encounter_quests" not in game.catalog.tables:
        return
    prepare_encounters(game)
    records = rows(game.state, "t_idle_exploration_encounters")
    active = battle_state(game)["active"]
    pinned = active.get("context", {}).get("encounter_id") if active and active["kind"] == "encounter" else None
    records[:] = [row for row in records if row["id"] == pinned or parse_timestamp(row["end_at"]) > game.now]
    settle_campaigns(game)
    status = rows(game.state, "t_idle_exploration_statuses")[0]
    levels = [row for row in game.catalog.rows("m_idle_exploration_levels") if row["level"] == status["level"]]
    if len(levels) != 1:
        raise GameplayError("Encounter level settings are missing", 503, 5036)
    level = levels[0]
    from .gameplay_idle import encounter_capacity
    capacity = encounter_capacity(game)
    for relation in rows(game.state, "t_idle_exploration_party_relations"):
        area = find(game.state, "t_idle_exploration_map_areas", m_idle_exploration_map_area_id=relation["m_idle_exploration_map_area_id"])
        if area is None:
            continue
        settings = [row for row in game.catalog.rows("m_idle_exploration_map_area_difficulties")
                    if row["m_idle_exploration_map_area_id"] == area["m_idle_exploration_map_area_id"] and row["difficulty"] <= area["difficulty"]]
        if not settings:
            continue
        setting = max(settings, key=lambda row: row["difficulty"])
        rule = game.catalog.get("m_idle_exploration_encounter_rules", setting["m_idle_exploration_encounter_rule_id"])
        interval = number(rule["encount_interval"], 1, 86400)
        campaign = generation_settings(game, relation["m_idle_exploration_map_area_id"], rule, level["enemy_level"])
        if campaign:
            interval = campaign[1]
        cursor = parse_timestamp(relation["last_encount_at"])
        end = min(game.now, parse_timestamp(relation["exploring_stop_at"]))
        ticks = max(0, int((end - cursor).total_seconds()) // interval)
        if not ticks:
            continue
        options = [row for row in game.catalog.rows("m_idle_exploration_encounters")
                   if row["group_id"] == rule["m_idle_exploration_encount_group_id"]
                   and row["border_enemy_level_low"] <= level["enemy_level"] <= row["border_enemy_level_high"] and row["weight"] > 0]
        if campaign:
            options = campaign[2]
        for tick in range(ticks, max(0, ticks - capacity), -1):
            if len(records) >= capacity or not options:
                break
            occurred = cursor + timedelta(seconds=tick * interval)
            rng = random.Random(f"{game.user_id}:{relation['party_no']}:{occurred.isoformat()}")
            selected = rng.choices(options, weights=[row["weight"] for row in options], k=1)[0]
            expires = occurred + timedelta(seconds=number(selected["time_limit"], 1, 604800))
            if campaign:
                expires = clip_expiration(game, campaign[0], expires)
            if expires <= game.now:
                continue
            game.catalog.get("m_encounter_quests", selected["m_encounter_quest_id"])
            data = progress(game)
            identifier = data["next_id"]
            data["next_id"] += 1
            created = put(game.state, "t_idle_exploration_encounters", {"id": identifier}, {
                "encount_type": 10 if campaign else 0, "encount_party_no": relation["party_no"],
                "m_idle_exploration_map_area_id": relation["m_idle_exploration_map_area_id"],
                "m_encounter_quest_id": selected["m_encounter_quest_id"],
                "begin_at": occurred.isoformat(), "end_at": expires.isoformat()}, game.user_id)
            if campaign:
                register_encounter(game, created, campaign[0])
        relation["last_encount_at"] = (cursor + timedelta(seconds=ticks * interval)).isoformat()


def encounter(game, identifier, *, at=None):
    from .gameplay_encounter_campaign import validate_encounter

    row = find(game.state, "t_idle_exploration_encounters", id=number(identifier))
    if row is None:
        raise GameplayError("Encounter is no longer available")
    at = game.now if at is None else at
    if not parse_timestamp(row["begin_at"]) <= at < parse_timestamp(row["end_at"]):
        raise GameplayError("Encounter has expired")
    validate_encounter(game, row, at)
    return row, game.catalog.get("m_encounter_quests", row["m_encounter_quest_id"])


def start(game, body):
    from .gameplay_encounter_campaign import supply_capacity

    identifier = number(body["t_idle_exploration_encounter_id"])
    row, definition = encounter(game, identifier)
    data = battle_state(game)
    if data["active"] is None:
        spend_supply(game, 4, supply_capacity(game))
    return create_session(game, definition, kind="encounter", quest_id=definition["id"],
                          party_no=number(body["party_no"], 1, 10), context={"encounter_id": identifier})


def finish(game, session, stage_results, *, update_skip_config=True):
    from .gameplay_encounter_campaign import record_clear

    row, definition = encounter(game, session["context"]["encounter_id"], at=parse_timestamp(session["start_at"]))
    status = rows(game.state, "t_statuses")[0]
    before_exp, before_gold = status["character_exp"], status["gold"]
    result = settle_battle(game, session, stage_results)["battle_result"]
    rewards = [reward for reward in game.catalog.rows("m_encounter_quest_rewards") if reward["m_encounter_quest_id"] == definition["id"]]
    result["clear_rewards"] = grant_display_rewards(game, rewards)
    result.update(grant_gold=status["gold"] - before_gold, grant_character_exp=status["character_exp"] - before_exp,
                  before_character_exp=before_exp, after_character_exp=status["character_exp"])
    result.pop("event_drops", None)
    party = find(game.state, "t_exploration_parties", party_no=session["party_no"])
    character_ids = {member["t_character_id"] for member in rows(game.state, "t_exploration_party_characters")
                     if party and member["t_exploration_party_id"] == party["id"] and member["t_character_id"]}
    if update_skip_config:
        put(game.state, "t_idle_exploration_encounter_skip_configs", {"m_encounter_quest_id": definition["id"]}, {
            "skip_extra_drop_probability": session.get("ability_extra_drop", 0), "skip_additional_drop_rate": extra_drop_bonus(game),
            "skip_party_characters": [character["m_character_id"] for character in rows(game.state, "t_characters") if character["id"] in character_ids]}, game.user_id)
    rows(game.state, "t_idle_exploration_encounters").remove(row)
    record_clear(game, row)
    record_statistic(game.progress, game.now, 150100)
    return result


def clear(game, body):
    digest = operation_digest("api/idle-exploration/encounter/clear-quest", "POST", body)
    data = battle_state(game)
    previous = data["last_result"]
    if previous is not None and previous["digest"] == digest:
        return copy.deepcopy(previous["response"])
    session = data["active"]
    if session is None or session["kind"] != "encounter":
        raise GameplayError("No encounter battle is active")
    result = finish(game, session, body["stage_results"])
    data["active"] = None
    data["last_result"] = {"digest": digest, "response": copy.deepcopy(result)}
    return result


def close(game, body):
    number(body["play_time"], 0, 86400)
    data = battle_state(game)
    if data["active"] is not None and data["active"]["kind"] != "encounter":
        raise GameplayError("A different battle is active")
    data["active"] = None
    records = rows(game.state, "t_idle_exploration_encounters")
    records[:] = [row for row in records if parse_timestamp(row["end_at"]) > game.now]
    return session_view(None)


def skip(game, body):
    from .gameplay_encounter_campaign import supply_capacity

    digest = operation_digest("api/idle-exploration/encounter/skip-quest", "POST", body)
    data = battle_state(game)
    previous = data["last_result"]
    if previous is not None and previous["digest"] == digest:
        return copy.deepcopy(previous["response"])
    if data["active"] is not None:
        raise GameplayError("A battle is already active")
    row, definition = encounter(game, body["t_idle_exploration_encounter_id"])
    if find(game.state, "t_idle_exploration_encounter_skip_configs", m_encounter_quest_id=definition["id"]) is None:
        raise GameplayError("Clear this encounter type before skipping")
    spend_supply(game, 4, supply_capacity(game))
    create_session(game, definition, kind="encounter", quest_id=definition["id"], party_no=1,
                   context={"encounter_id": row["id"], "is_skip": True}, validate_members=False)
    session = data["active"]
    result = finish(game, session, skipped_result(session["stage_detail"], session["stage_type"]), update_skip_config=False)
    result.update(skip_count=1, event_drops=[])
    data["active"] = None
    data["last_result"] = {"digest": digest, "response": copy.deepcopy(result)}
    return result


def recover(game, body):
    from .gameplay_encounter_campaign import supply_capacity

    count = number(body["recovery_count"], 1, 100)
    supply = daily_supply(game, 4, supply_capacity(game))
    if supply["amount"] + count > supply["capacity"]:
        raise GameplayError("Encounter supply is already full")
    spend_supply(game, 14, game.rules["encounter_daily_recoveries"], count)
    consume(game, [{"content_type": 10, "content_id": 0, "amount": count * game.rules["encounter_recovery_cost"]}])
    supply["amount"] += count
    return {}


ENCOUNTER_ROUTES = {
    "api/idle-exploration/encounter/start-quest": route("POST", "t_idle_exploration_encounter_id party_no", start),
    "api/idle-exploration/encounter/clear-quest": route("POST", "stage_results", clear),
    "api/idle-exploration/encounter/close-quest": route("POST", "play_time", close),
    "api/idle-exploration/encounter/surrender-quest": route("POST", "play_time", close),
    "api/idle-exploration/encounter/skip-quest": route("POST", "t_idle_exploration_encounter_id", skip),
    "api/idle-exploration/encounter/recovery-supply": route("POST", "recovery_count", recover),
}
