from __future__ import annotations

import copy
from datetime import timedelta

from .gameplay_battle_session import battle_state
from .gameplay_catalog import GameplayError, number
from .gameplay_events import event_data
from .gameplay_records import find, put, rows
from .gameplay_character_levels import effective_character_level


def epoch(game, event):
    return str(event_data(game, event["id"]).get("epoch", ":".join(str(event.get(key, "")) for key in ("id", "open_at", "end_at", "close_at"))))


def context(game, event, party_no):
    party = find(game.state, "t_exploration_parties", party_no=party_no)
    if party is None:
        raise GameplayError("Event battle party is missing")
    selected = [row for row in rows(game.state, "t_exploration_party_characters")
                if row["t_exploration_party_id"] == party["id"] and row["t_character_id"]]
    characters = [find(game.state, "t_characters", id=row["t_character_id"]) for row in selected]
    members = {str(row["m_character_id"]): effective_character_level(game, row) for row in characters if row is not None}
    return {"m_event_id": event["id"], "event_epoch": epoch(game, event),
            "members": members, "party_characters": list(map(int, members))}


def active(game, kind):
    session = battle_state(game)["active"]
    if session is None or session["kind"] != kind:
        raise GameplayError("No matching event battle is active")
    event = game.catalog.get("m_events", session["context"]["m_event_id"])
    expected_type, table = {"event_miner": (1, "m_event_miner_quests"), "event_mutant": (2, "m_event_disaster_quests")}[kind]
    definition = game.catalog.get(table, session["quest_id"])
    if event["type"] != expected_type or definition["m_event_id"] != event["id"]:
        raise GameplayError("Event battle does not belong to its saved event")
    if find(game.state, "t_events", m_event_id=event["id"]) is None:
        raise GameplayError("Event battle requires an existing event participation record")
    if session["context"].get("event_epoch") != epoch(game, event):
        raise GameplayError("Event battle belongs to another event period")
    return session, event


def replay(game, digest):
    data = battle_state(game)
    if data["active"] is None and data["last_result"] and data["last_result"]["digest"] == digest:
        return copy.deepcopy(data["last_result"]["response"])
    return None


def finish(game, digest, response):
    data = battle_state(game)
    data["active"] = None
    data["last_result"] = {"digest": digest, "response": copy.deepcopy(response)}
    return response


def recovery_counter(game, event_id, limit):
    today = (game.now - timedelta(hours=5)).date().isoformat()
    data = event_data(game, event_id).setdefault("battle_recovery", {"day": today, "used": 0})
    if data["day"] != today:
        data.update(day=today, used=0)
    row = put(game.state, "t_event_timed_parameters", {"m_event_id": event_id, "type": 2}, {
        "amount": max(0, limit - data["used"]), "capacity": limit, "recovery": limit,
        "duration_type": 2, "recovery_duration": 1, "last_recovery_at": game.now.isoformat()})
    return data, row


def elapsed(game, session, value):
    return number(value, 0, session["time_limit"] or 86400)
