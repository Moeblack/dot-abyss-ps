from __future__ import annotations

from .gameplay_catalog import GameplayError, available, number, parse_timestamp
from .gameplay_mode_supplies import daily_anchor
from .gameplay_receipts import feature_data
from .gameplay_records import find, put
from .gameplay_routes import route
from .gameplay_event_effects import boost_event_rewards, event_additional_drop_rate


def event_data(game, event_id):
    key = str(number(event_id))
    data = feature_data(game, "events")
    entries = data.setdefault("instances", {})
    if not isinstance(entries, dict) or len(entries) > 10000:
        raise GameplayError("Stored event instances are invalid")
    entry = entries.setdefault(key, {"epoch": 1})
    if not isinstance(entry, dict):
        raise GameplayError("Stored event instance is invalid")
    number(entry.get("epoch"))
    return entry


def event_is_reopened(game, event_id):
    return bool(game.rules["event_open_all"] or event_id in game.rules["event_open_ids"])


def event_available(game, event_id, definition, start="open_at", end="end_at"):
    return event_is_reopened(game, event_id) or available(definition, game.now, start, end)


def campaign_available(game, definition, *, allow_closed=False):
    return bool(game.rules["event_open_all"]) or available(definition, game.now, "open_at", "close_at" if allow_closed else "end_at")


def participation_allowed(game, event):
    from .gameplay_tutorial import can_unlock_feature

    feature_id = 1000 + event["type"]
    current = find(game.state, "t_feature_locks", m_feature_lock_id=feature_id)
    return bool(current and current["is_unlocked"]) or can_unlock_feature(game, feature_id)


def require_event(game, event_id, kinds=None, *, allow_closed=False):
    event = game.catalog.get("m_events", number(event_id))
    permitted = (kinds,) if isinstance(kinds, int) else kinds
    if permitted is not None and event["type"] not in permitted:
        raise GameplayError("Event type does not match this endpoint", 400, 4007)
    if not event_available(game, event_id, event, end="close_at" if allow_closed else "end_at"):
        raise GameplayError("Event is outside its available period")
    if find(game.state, "t_events", m_event_id=event_id) is None:
        raise GameplayError("Join the event before using this endpoint")
    return event


def event_supply(game, event_id, *, type_id=1, initial=None, capacity=None, recovery=None):
    number(type_id, 1, 100)
    existing = find(game.state, "t_event_timed_parameters", m_event_id=event_id, type=type_id)
    capacity = number(capacity if capacity is not None else existing["capacity"] if existing else game.rules["event_daily_supply"], 0, 100000)
    recovery = number(recovery if recovery is not None else existing["recovery"] if existing else capacity, 0, 100000)
    anchor = daily_anchor(game.now)
    if existing is None:
        return put(game.state, "t_event_timed_parameters", {"m_event_id": event_id, "type": type_id}, {
            "amount": number(initial if initial is not None else capacity, 0, 100000), "capacity": capacity,
            "recovery": recovery, "duration_type": 2, "recovery_duration": 1, "last_recovery_at": anchor.isoformat()})
    previous = parse_timestamp(existing["last_recovery_at"])
    if previous < anchor:
        days = max(1, (anchor.date() - previous.date()).days)
        if existing["amount"] < capacity:
            existing["amount"] = min(capacity, existing["amount"] + recovery * days)
        existing["last_recovery_at"] = anchor.isoformat()
    existing.update(capacity=capacity, recovery=recovery)
    return existing


def spend_event_supply(game, event_id, count=1, *, type_id=1):
    supply = event_supply(game, event_id, type_id=type_id)
    count = number(count, 1, 100000)
    if supply["amount"] < count:
        raise GameplayError("Insufficient event attempts", 409, 4093)
    supply["amount"] -= count
    return supply


def record_event_clear(game, event, quest_id, stage_results, context, count=1):
    from .gameplay_event_missions import record_clear

    return record_clear(game, event, quest_id, stage_results, context, count)


def join(game, body):
    event_id = number(body["m_event_id"])
    event = game.catalog.get("m_events", event_id)
    if not event_available(game, event_id, event):
        raise GameplayError("Event is not open for participation")
    if not participation_allowed(game, event):
        raise GameplayError("Event participation requirements have not been met")
    if find(game.state, "t_events", m_event_id=event_id) is None:
        put(game.state, "t_events", {"m_event_id": event_id}, {
            "join_at": game.now.isoformat(), "is_event_stage_effect_enabled": 1,
            "created_at": game.now.strftime("%Y-%m-%d %H:%M:%S"), "updated_at": game.now.isoformat()})
    event_data(game, event_id)
    prepare_event(game, event)
    return {}


def top(game, body):
    event = require_event(game, body["m_event_id"], allow_closed=True)
    prepare_event(game, event)
    return {}


def prepare_event(game, event):
    from .gameplay_event_bonus import prepare_event_bonus

    prepare_event_bonus(game)
    if event["type"] == 1:
        from .gameplay_event_miner import prepare_miner

        prepare_miner(game, event)
    elif event["type"] == 2:
        from .gameplay_event_mutant import prepare_mutant

        prepare_mutant(game, event)
    if event["type"] in (3, 5, 7, 8, 9):
        from .gameplay_event_quests import prepare_event_quests

        prepare_event_quests(game, event)


EVENT_ROUTES = {
    "api/event/join": route("POST", "m_event_id", join),
    "api/event/top": route("POST", "m_event_id", top),
}
