from __future__ import annotations

from datetime import timedelta

from .gameplay_catalog import GameplayError, available, number, parse_timestamp
from .gameplay_records import find, rows
from .gameplay_statistics import key as statistic_key


EVENT_TARGETS = {
    8: ("story", True, 110001),
    15: ("hunt", False, 110002),
    17: ("training", True, 110004),
    18: ("disaster", False, None),
    19: ("miner", False, 110000),
    23: ("speedrun", False, None),
    24: ("commission", False, 110003),
}
RECHECK_CONDITIONS = {*EVENT_TARGETS, 14, 16, 21, 22}


def event_target(catalog, definition):
    kind = definition["condition_type"]
    name, staged, _ = EVENT_TARGETS[kind]
    identifier = definition["condition_id"]
    table = f"m_event_{name}_{'stages' if kind == 8 else 'quests'}"
    target = catalog.get(table, identifier)
    if staged and kind != 8:
        node = catalog.get(f"m_event_{name}_stages", target[f"m_event_{name}_stage_id"])
    else:
        node = target
    return node["m_event_id"], node, target


def owner_context(catalog, definition):
    kind, target = definition["condition_type"], definition["condition_id"]
    if kind in EVENT_TARGETS:
        return "event", event_target(catalog, definition)[0]
    if kind in (14, 21):
        event = catalog.get("m_events", target)
        if event["type"] != {14: 6, 21: 4}[kind]:
            raise GameplayError("Recommended product refers to a different event type", 503, 5036)
        return "event", target
    if kind == 16:
        return "campaign", catalog.get("m_encounter_campaigns", target)["id"]
    if kind == 22:
        mission = catalog.get("m_missions", target)
        term_id = mission.get("m_mission_term_id", 0)
        if not term_id:
            return None
        term = catalog.get("m_mission_terms", term_id)
        group = term["category_group"]
        owners = [("event", event["id"]) for event in catalog.rows("m_events")
                  if group and event["m_mission_term_category_group"] == group]
        owners.extend(("campaign", campaign["id"]) for campaign in catalog.rows("m_encounter_campaigns")
                      if campaign["m_mission_term_id"] == term_id)
        if len(owners) > 1:
            raise GameplayError("Recommended mission belongs to multiple events", 503, 5036)
        return owners[0] if owners else None
    return None


def event_progress(game, event_id):
    return game.progress["features"].get("events", {}).get("data", {}).get("instances", {}).get(str(event_id), {})


def counter(data, *path):
    for index, key in enumerate(path):
        if not isinstance(data, dict):
            raise GameplayError("Stored recommendation progress is invalid")
        data = data.get(key, 0 if index == len(path) - 1 else {})
    return number(data, 0)


def event_clear_count(game, definition):
    kind = definition["condition_type"]
    name, staged, situation = EVENT_TARGETS[kind]
    event_id, node, target = event_target(game.catalog, definition)
    data = event_progress(game, event_id)
    record_table = f"t_event_{name}_{'stages' if staged else 'quests'}"
    record_field = f"m_event_{name}_{'stage' if staged else 'quest'}_id"
    record = find(game.state, record_table, **{record_field: node["id"]})
    native = int(bool(record and (record.get("is_cleared") if kind == 18 else record.get("status") == 99)))
    if kind == 18:
        return max(native, counter(data, "mutant", "quests", str(node["id"]), "clears"))
    if kind == 19:
        stored = counter(data, "miner", "clears", str(node["id"]))
    else:
        stored = counter(data, "quests", f"{name}:{node['id']}", "clears")
    if kind == 8:
        targets = [quest["id"] for quest in game.catalog.rows("m_event_story_quests")
                   if quest["m_event_story_stage_id"] == node["id"]]
    else:
        targets = [target["id"]]
    counts = [counter(data, "mission_stats", "totals", statistic_key(situation, identifier)) for identifier in targets] if situation else []
    if kind == 23:
        time = counter(data, "mission_stats", "totals", statistic_key(110201, target["id"]))
        counts.append(int(time > 0))
    return max([native, stored, *counts])


def eligible(game, definition):
    from .gameplay_plans import missions

    kind, target, required = definition["condition_type"], definition["condition_id"], definition["condition_value"]
    if kind == 0:
        return True
    if kind == 3:
        return any((not target or character["m_character_id"] == target) and character["limit_break_count"] >= required
                   for character in rows(game.state, "t_characters"))
    if kind == 4:
        return find(game.state, "t_characters", m_character_id=target) is not None
    if kind == 5:
        return rows(game.state, "t_prologue_tutorials")[0]["status"] == target
    if kind == 9:
        ids = {row["m_mission_id"] for row in game.catalog.rows("m_special_mission_days") if row["m_special_mission_id"] == target}
        return sum(f"once:{identifier}" in game.progress["mission_claims"] for identifier in ids) >= required
    if kind == 20:
        entry = find(game.state, "t_gachas", m_gacha_id=target)
        return entry is not None and entry["exec_count"] >= required
    if kind in EVENT_TARGETS:
        return event_clear_count(game, definition) >= required
    if kind == 16:
        game.catalog.get("m_encounter_campaigns", target)
        campaigns = game.progress["features"].get("encounter", {}).get("data", {}).get("campaigns", {})
        counts = campaigns.get("entries", {}).get(str(target), {}).get("clear_counts", {})
        if not isinstance(counts, dict):
            raise GameplayError("Stored encounter campaign progress is invalid")
        return sum(number(value, 0) for value in counts.values()) >= required
    if kind in (14, 21):
        owner_context(game.catalog, definition)
        data = event_progress(game, target)
        if kind == 14:
            record = find(game.state, "t_event_box_gachas", m_event_id=target)
            value = data.get("box", {}).get("current_box_no", record["current_box_no"] if record else 0)
        else:
            value = data.get("board", {}).get("current_sheet", 0)
            required = game.rules.get("recommendation_board_sheet_requirements", {}).get(str(definition["id"]), required)
        return number(value, 0) >= number(required, 1)
    if kind == 22:
        from .gameplay_event_missions import evaluator_for_mission

        mission = game.catalog.get("m_missions", target)
        evaluator = evaluator_for_mission(game, target) if owner_context(game.catalog, definition) else missions(game)
        return bool(evaluator and evaluator.completed(mission))
    situation = {1: 20000, 6: 160000, 7: 170002, 11: 210000, 12: 200000, 13: 100000}.get(kind)
    if situation is None:
        raise GameplayError("Unknown shop release condition", 503, 5036)
    return (missions(game).value({"situation_type": situation, "target_id": target, "sub_target_id": 0, "mission_type": 1}) or 0) >= required


def active(game, definition, owner):
    from .gameplay_events import campaign_available, event_available

    if not available(definition, game.now):
        return False
    if owner is None:
        return True
    kind, identifier = owner
    if kind == "event":
        return find(game.state, "t_events", m_event_id=identifier) is not None and event_available(
            game, identifier, game.catalog.get("m_events", identifier))
    campaigns = game.progress["features"].get("encounter", {}).get("data", {}).get("campaigns", {})
    if game.rules["event_open_all"] and campaigns.get("current_id") != identifier:
        return False
    return str(identifier) in campaigns.get("entries", {}) and campaign_available(game, game.catalog.get("m_encounter_campaigns", identifier))


def collect(game, data):
    result = []
    for definition in game.catalog.rows("m_payment_recommends"):
        owner = owner_context(game.catalog, definition)
        if not active(game, definition, owner):
            continue
        key = str(definition["id"])
        recheck = definition["condition_type"] in RECHECK_CONDITIONS or definition["condition_type"] == 3 and definition["condition_id"] != 0
        matched = eligible(game, definition) if recheck or key not in data["released"] else True
        if not matched:
            continue
        if key not in data["released"]:
            end = parse_timestamp(definition["end_at"])
            if definition["valid_minutes"] > 0:
                end = min(end, game.now + timedelta(minutes=definition["valid_minutes"]))
            data["released"][key] = end.isoformat()
        if parse_timestamp(data["released"][key]) > game.now:
            result.append(definition)
    return result
