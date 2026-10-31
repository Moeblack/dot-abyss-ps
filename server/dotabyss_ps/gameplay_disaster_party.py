from __future__ import annotations

from collections import Counter

from .gameplay_catalog import GameplayError, number
from .gameplay_disaster_state import prepare_disaster
from .gameplay_party_state import put_party_row
from .gameplay_receipts import feature_data
from .gameplay_records import find, put, rows
from .gameplay_routes import route


def quest_boss(game, quest_id):
    found = {row["m_disaster_boss_id"] for table in ("m_disaster_boss_appears", "m_disaster_sp_boss_appears")
             for row in game.catalog.rows(table) if row["m_disaster_quest_id"] == quest_id}
    if len(found) != 1:
        raise GameplayError("Disaster quest does not identify one boss", 503, 5036)
    return found.pop()


def formations(game, party_no, quest_id, disaster_type=2):
    parties = {row["id"] for row in rows(game.state, "t_exploration_parties") if row["party_no"] == party_no}
    if disaster_type in (3, 4):
        candidates = [row for row in rows(game.state, "t_disaster_parties")
                      if row["disaster_type"] == disaster_type and row["t_exploration_party_id"] in parties
                      and row["m_disaster_quest_id"] in (0, quest_id)]
        specific = [row for row in candidates if row["m_disaster_quest_id"] == quest_id]
        return {row["id"] for row in specific or candidates}
    boss_id = quest_boss(game, quest_id)
    candidates = [row for row in rows(game.state, "t_disaster_parties")
                  if row["disaster_type"] == 2 and row["t_exploration_party_id"] in parties
                  and row["m_disaster_boss_id"] in (0, boss_id)]
    specific = [row for row in candidates if row["m_disaster_boss_id"] == boss_id]
    selected = specific or candidates
    return {row["id"] for row in selected}


def formation_fuel(game, party_no, quest_id, disaster_type=2):
    identifiers = formations(game, party_no, quest_id, disaster_type)
    units = {row["id"]: row for row in rows(game.state, "t_defensive_units")}
    cost = 0
    for placement in rows(game.state, "t_disaster_party_defensive_units"):
        if placement["t_disaster_party_id"] in identifiers:
            unit = units.get(placement["t_defensive_unit_id"])
            if unit is None:
                raise GameplayError("Stored defensive formation references an unowned unit")
            cost += game.catalog.get("m_defensive_units", unit["m_defensive_unit_id"])["require_cost"]
    return number(cost, 0, 2 ** 31 - 1)


def edit(game, body):
    prepare_disaster(game)
    kind = number(body["disaster_type"], 1, 4)
    quest_id = number(body["m_disaster_quest_id"], 0)
    party_id = number(body["t_exploration_party_id"])
    if find(game.state, "t_exploration_parties", id=party_id) is None:
        raise GameplayError("Defensive formation requires an owned exploration party")
    boss_id = 0
    if kind == 1:
        if quest_id:
            raise GameplayError("Main-story defensive formation uses the shared zero quest identifier", 400, 4007)
        available_lines = max(row["available_defence_line"] for row in game.catalog.rows("m_defend_stages"))
    elif quest_id and kind in (3, 4):
        from .gameplay_events import require_event

        definition = game.catalog.get("m_event_disaster_quests" if kind == 3 else "m_event_story_quests", quest_id)
        event_id = definition["m_event_id"] if kind == 3 else game.catalog.get("m_event_story_stages", definition["m_event_story_stage_id"])["m_event_id"]
        require_event(game, event_id, 2 if kind == 3 else 3)
        if definition["stage_type"] != 2:
            raise GameplayError("The event quest does not use a defensive formation", 400, 4007)
        available_lines = game.catalog.get("m_defend_stages", definition["stage_id"])["available_defence_line"]
    elif quest_id:
        definition = game.catalog.get("m_disaster_quests", quest_id)
        boss_id = quest_boss(game, quest_id)
        available_lines = game.catalog.get("m_defend_stages", definition["stage_id"])["available_defence_line"]
    else:
        available_lines = max(row["available_defence_line"] for row in game.catalog.rows("m_defend_stages"))
    values = body["t_disaster_party_defensive_units"]
    if not isinstance(values, list) or len(values) > 100:
        raise GameplayError("Defensive formation exceeds its slot limit", 400, 4007)
    counts, positions, seen = Counter(), set(), set()
    checked = []
    for entry in values:
        if not isinstance(entry, dict) or set(entry) != {"placement_line", "placement_index", "t_defensive_unit_id"}:
            raise GameplayError("Invalid defensive placement fields", 400, 4007)
        line = number(entry["placement_line"], 0, available_lines)
        index = number(entry["placement_index"], 0, 1000)
        identifier = number(entry["t_defensive_unit_id"])
        unit = find(game.state, "t_defensive_units", id=identifier)
        if unit is None:
            raise GameplayError("Defensive unit is not owned")
        master = game.catalog.get("m_defensive_units", unit["m_defensive_unit_id"])
        if master["type"] == 900:
            raise GameplayError("The defensive object cannot occupy a placement slot", 400, 4007)
        key = (identifier, line, index)
        if key in seen or master["type"] != 100 and (line, index) in positions:
            raise GameplayError("Defensive placement is duplicated", 400, 4007)
        seen.add(key)
        if master["type"] != 100:
            positions.add((line, index))
        counts[unit["m_defensive_unit_id"]] += 1
        if counts[unit["m_defensive_unit_id"]] > master["placeable_count"]:
            raise GameplayError("Defensive unit placement count exceeds the master limit")
        checked.append(dict(entry))
    keys = {"disaster_type": kind, "m_disaster_quest_id": quest_id} if kind in (3, 4) else {"disaster_type": kind, "m_disaster_boss_id": boss_id}
    formation = find(game.state, "t_disaster_parties", **keys)
    if formation is None:
        formation = put(game.state, "t_disaster_parties", keys,
                        {"m_disaster_quest_id": quest_id, "m_disaster_boss_id": boss_id, "t_exploration_party_id": party_id}, game.user_id)
    else:
        formation.update(m_disaster_quest_id=quest_id, t_exploration_party_id=party_id)
    placements = rows(game.state, "t_disaster_party_defensive_units")
    old_max = max((row["id"] for row in placements), default=0)
    cursors = feature_data(game, "party").setdefault("next_ids", {})
    table = "t_disaster_party_defensive_units"
    cursors[table] = max(cursors.get(table, 1), old_max + 1)
    selected = {(row["t_defensive_unit_id"], row["placement_line"], row["placement_index"]) for row in checked}
    placements[:] = [row for row in placements if row["t_disaster_party_id"] != formation["id"]
                     or (row["t_defensive_unit_id"], row["placement_line"], row["placement_index"]) in selected]
    for entry in checked:
        put_party_row(game, table, {"t_disaster_party_id": formation["id"], **entry}, {}, game.user_id)
    return {}


DISASTER_PARTY_ROUTES = {"api/party/disaster/edit/all": route("POST", "disaster_type m_disaster_quest_id t_exploration_party_id t_disaster_party_defensive_units", edit)}
