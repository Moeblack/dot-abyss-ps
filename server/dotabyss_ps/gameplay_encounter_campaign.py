from __future__ import annotations

import copy
from datetime import timedelta

from .gameplay_catalog import GameplayError, number, parse_timestamp
from .gameplay_missions import Missions
from .gameplay_records import find, put, rows
from .gameplay_routes import route


def campaign_data(game):
    from .gameplay_encounter import progress

    data = progress(game)["campaigns"]
    if not isinstance(data, dict) or set(data) != {"current_id", "entries", "origins"}:
        raise GameplayError("Stored encounter campaigns are invalid")
    number(data["current_id"], 0)
    if not isinstance(data["entries"], dict) or not isinstance(data["origins"], dict):
        raise GameplayError("Stored encounter campaign history is invalid")
    if data["current_id"] and str(data["current_id"]) not in data["entries"]:
        raise GameplayError("Selected encounter campaign has no join record")
    for key, entry in data["entries"].items():
        if not isinstance(key, str) or not key.isascii() or not key.isdecimal() or not isinstance(entry, dict):
            raise GameplayError("Stored encounter campaign identity is invalid")
        if set(entry) != {"join_at", "bonus_progress", "pending_bonus", "clear_counts"} or not isinstance(entry["clear_counts"], dict):
            raise GameplayError("Stored encounter campaign progress is invalid")
        parse_timestamp(entry["join_at"])
        number(entry["bonus_progress"], 0, 2 ** 31 - 1)
        for amount in entry["clear_counts"].values():
            number(amount, 0, 2 ** 31 - 1)
        pending = entry["pending_bonus"]
        if pending is not None:
            if not isinstance(pending, dict) or set(pending) != {"quest_id", "party_no", "area_id", "time_limit"}:
                raise GameplayError("Stored pending campaign bonus is invalid")
            for value in pending.values():
                number(value)
    for origin in data["origins"].values():
        if not isinstance(origin, dict) or set(origin) != {"campaign_id", "bonus"} or type(origin["bonus"]) is not bool:
            raise GameplayError("Stored encounter campaign origin is invalid")
        number(origin["campaign_id"])
    return data


def available(game, definition, *, at=None, allow_closed=False):
    from .gameplay_events import campaign_available

    if at is None:
        return campaign_available(game, definition, allow_closed=allow_closed)
    context = copy.copy(game)
    context.now = at
    return campaign_available(context, definition, allow_closed=allow_closed)


def selected_campaign(game):
    if "m_encounter_campaigns" not in game.catalog.tables:
        return None
    identifier = campaign_data(game)["current_id"]
    if not identifier:
        return None
    definition = game.catalog.get("m_encounter_campaigns", identifier)
    return definition if available(game, definition) else None


def supply_capacity(game):
    definition = selected_campaign(game)
    return game.rules["encounter_daily_supply"] + (number(definition["additional_encounter_supply"], 0, 1000) if definition else 0)


def joined_campaigns(game):
    data = campaign_data(game)
    return [{"id": int(identifier), "user_id": game.user_id, "m_encounter_campaign_id": int(identifier), "join_at": entry["join_at"]}
            for identifier, entry in sorted(data["entries"].items(), key=lambda pair: int(pair[0]))]


def campaign_entry(game, identifier):
    entry = campaign_data(game)["entries"].get(str(identifier))
    if entry is None:
        raise GameplayError("Join this encounter campaign first")
    return entry


def campaign_areas(game, definition):
    return [row["m_idle_exploration_map_area_id"] for row in game.catalog.rows("m_encounter_campaign_map_areas")
            if row["group_id"] == definition["m_encounter_campaign_map_area_group_id"]]


def mappings(game, definition):
    quests = sorted([row for row in game.catalog.rows("m_encounter_campaign_quests")
                     if row["group_id"] == definition["m_encounter_campaign_quest_group_id"]], key=lambda row: (row["exploration_level"], row["id"]))
    bonuses = sorted([row for row in game.catalog.rows("m_encounter_campaign_bonus_quests")
                      if row["group_id"] == definition["m_encounter_campaign_bonus_quest_group_id"]], key=lambda row: row["id"])
    if not quests or len(quests) != len(bonuses):
        raise GameplayError("Encounter campaign quest tiers are incomplete", 503, 5036)
    return list(zip(quests, bonuses))


def listing(game, body):
    identifier = number(body["m_encounter_campaigns_id"])
    definition = game.catalog.get("m_encounter_campaigns", identifier)
    if not available(game, definition, allow_closed=True):
        raise GameplayError("Encounter campaign is outside its available period")
    entry = campaign_data(game)["entries"].get(str(identifier), {})
    evaluator = Missions(game.state, game.progress, game.catalog, game.now, game.user_id, game.rules)
    missions = {name: [] for name in ("daily", "weekly", "achievement", "limited")}
    for mission in game.catalog.rows("m_missions"):
        if mission["m_mission_term_id"] != definition["m_mission_term_id"] or not evaluator.prerequisites(mission):
            continue
        category = "daily" if mission["mission_type"] in (2, 8) else "weekly" if mission["mission_type"] in (3, 6) else "achievement" if mission["mission_type"] == 1 else "limited"
        missions[category].append(evaluator.view(mission))
    return {"additional_encounter_supply": definition["additional_encounter_supply"], "extend_map_areas": campaign_areas(game, definition),
            "missions": missions, "require_clear_count": max(0, definition["require_clear_count_for_bonus"] - entry.get("bonus_progress", 0)),
            "encounters": [{"m_encounter_quest_id": quest["m_encounter_quest_id"], "m_encounter_quest_id_bonus": bonus["m_encounter_quest_id"],
                            "exploration_level": quest["exploration_level"]} for quest, bonus in mappings(game, definition)]}


def join(game, body):
    from .gameplay_encounter import prepare_encounters

    identifier = number(body["m_encounter_campaign_id"])
    definition = game.catalog.get("m_encounter_campaigns", identifier)
    if not available(game, definition):
        raise GameplayError("Encounter campaign is not open for participation")
    mappings(game, definition)
    data = campaign_data(game)
    first = str(identifier) not in data["entries"]
    if first:
        data["entries"][str(identifier)] = {"join_at": game.now.isoformat(), "bonus_progress": 0, "pending_bonus": None, "clear_counts": {}}
    changed = data["current_id"] != identifier
    data["current_id"] = identifier
    for area_id in campaign_areas(game, definition):
        master = game.catalog.get("m_idle_exploration_map_areas", area_id)
        if find(game.state, "t_idle_exploration_maps", m_idle_exploration_map_id=master["m_idle_exploration_map_id"]) is None:
            put(game.state, "t_idle_exploration_maps", {"m_idle_exploration_map_id": master["m_idle_exploration_map_id"]}, {}, game.user_id)
        if find(game.state, "t_idle_exploration_map_areas", m_idle_exploration_map_area_id=area_id) is None:
            put(game.state, "t_idle_exploration_map_areas", {"m_idle_exploration_map_area_id": area_id}, {"difficulty": 1}, game.user_id)
    prepare_encounters(game)
    supply = find(game.state, "t_timed_parameters", type=4)
    if first:
        supply["amount"] = min(supply["capacity"], supply["amount"] + definition["additional_encounter_supply"])
    if changed:
        for relation in rows(game.state, "t_idle_exploration_party_relations"):
            relation["last_encount_at"] = game.now.isoformat()
    return {}


def generation_settings(game, area_id, rule, enemy_level):
    if "m_encounter_campaigns" not in game.catalog.tables:
        return None
    definition = selected_campaign(game)
    if definition is None or area_id not in campaign_areas(game, definition):
        campaign_area = any(row["m_idle_exploration_map_area_id"] == area_id for row in game.catalog.rows("m_encounter_campaign_map_areas"))
        return (None, number(rule["encount_interval"], 1, 86400), []) if campaign_area else None
    options = [row for row in game.catalog.rows("m_idle_exploration_encounters")
               if row["group_id"] == definition["m_idle_exploration_encount_group_id"]
               and row["border_enemy_level_low"] <= enemy_level <= row["border_enemy_level_high"] and row["weight"] > 0]
    interval = definition["override_encount_interval"] or rule["encount_interval"]
    return definition, number(interval, 1, 86400), options


def register_encounter(game, row, definition, *, bonus=False):
    campaign_data(game)["origins"][str(row["id"])] = {"campaign_id": definition["id"], "bonus": bool(bonus)}


def clip_expiration(game, definition, expires):
    from .gameplay_events import event_is_reopened

    return expires if event_is_reopened(game, definition["id"]) else min(expires, parse_timestamp(definition["end_at"]))


def validate_encounter(game, row, at):
    origin = campaign_data(game)["origins"].get(str(row["id"]))
    if origin and not available(game, game.catalog.get("m_encounter_campaigns", origin["campaign_id"]), at=at):
        raise GameplayError("This encounter campaign is no longer playable")


def settle_campaigns(game):
    from .gameplay_encounter import progress

    if "m_encounter_campaigns" not in game.catalog.tables:
        return
    data = campaign_data(game)
    existing = {str(row["id"]) for row in rows(game.state, "t_idle_exploration_encounters")}
    data["origins"] = {key: value for key, value in data["origins"].items() if key in existing}
    level = rows(game.state, "t_idle_exploration_statuses")[0]["level"]
    capacity = next(row["encounter_quest_slot_count"] for row in game.catalog.rows("m_idle_exploration_levels") if row["level"] == level)
    for key, entry in data["entries"].items():
        pending = entry["pending_bonus"]
        definition = game.catalog.get("m_encounter_campaigns", int(key))
        if pending is None or not available(game, definition) or len(rows(game.state, "t_idle_exploration_encounters")) >= capacity:
            continue
        identifier = progress(game)["next_id"]
        progress(game)["next_id"] += 1
        expires = clip_expiration(game, definition, game.now + timedelta(seconds=pending["time_limit"]))
        row = put(game.state, "t_idle_exploration_encounters", {"id": identifier}, {
            "encount_type": 11, "encount_party_no": pending["party_no"], "m_idle_exploration_map_area_id": pending["area_id"],
            "m_encounter_quest_id": pending["quest_id"], "begin_at": game.now.isoformat(), "end_at": expires.isoformat()}, game.user_id)
        register_encounter(game, row, definition, bonus=True)
        entry["pending_bonus"] = None


def record_clear(game, encounter):
    data = campaign_data(game)
    origin = data["origins"].pop(str(encounter["id"]), None)
    if origin is None:
        return
    definition = game.catalog.get("m_encounter_campaigns", origin["campaign_id"])
    entry = campaign_entry(game, definition["id"])
    identifier = encounter["m_encounter_quest_id"]
    count = number(entry["clear_counts"].get(str(identifier), 0) + 1, 0, 2 ** 31 - 1)
    entry["clear_counts"][str(identifier)] = count
    if origin["bonus"] or not available(game, definition):
        return
    entry["bonus_progress"] += 1
    needed = number(definition["require_clear_count_for_bonus"], 1, 100000)
    if entry["bonus_progress"] < needed or entry["pending_bonus"] is not None:
        return
    level = rows(game.state, "t_idle_exploration_statuses")[0]["level"]
    tiers = mappings(game, definition)
    eligible = [(quest, bonus) for quest, bonus in tiers if quest["exploration_level"] <= level]
    quest, bonus = eligible[-1] if eligible else tiers[0]
    allowed = {row["m_encounter_quest_id"] for row in game.catalog.rows("m_encounter_campaign_bonus_quest_appears")
               if row["group_id"] == bonus["m_encounter_campaign_bonus_quest_appear_group_id"]}
    if identifier not in allowed:
        return
    entry["bonus_progress"] -= needed
    entry["pending_bonus"] = {"quest_id": bonus["m_encounter_quest_id"], "party_no": encounter["encount_party_no"],
                              "area_id": encounter["m_idle_exploration_map_area_id"], "time_limit": number(bonus["bonus_quest_time_limit"], 1, 604800)}
    settle_campaigns(game)


ENCOUNTER_CAMPAIGN_ROUTES = {
    "api/idle-exploration/campaign/{0}": route("GET", "m_encounter_campaigns_id", listing, "m_encounter_campaigns_id"),
    "api/idle-exploration/campaign/join": route("POST", "m_encounter_campaign_id", join),
}
