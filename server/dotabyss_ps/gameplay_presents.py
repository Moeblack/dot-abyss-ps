from __future__ import annotations

import copy

from .gameplay_catalog import GameplayError, available, identifiers, number
from .gameplay_receipts import feature_data
from .gameplay_records import grant
from .gameplay_routes import route

PRESENT_CONTENT_TYPES = frozenset((10, 20, 30, 31, 33, 40, 41, 45, 50, 52, 54, 56, 60, 70, 80, 90, 110, 111, 140, 141, 160, 165, 166, 180, 190))


def presents(game):
    data = feature_data(game, "presents")
    if not data:
        data.update(seeded=True, entries=[])
        for kind, setting in ((50, "starting_gold"), (110, "starting_character_exp"), (51, "starting_soul"), (10, "starting_free_currency")):
            amount = game.rules[setting]
            if amount:
                data["entries"].append({"id": len(data["entries"]) + 1, "content_type": kind, "content_id": 0, "amount": amount,
                    "begin_at": game.now.isoformat(), "end_at": None, "accepted_at": None, "m_present_title_id": 0,
                    "m_present_message_id": 0, "title": "Local welcome supplies", "message": "Local server starting supplies.", "is_subscription": 0})
    if set(data) != {"seeded", "entries"} or data["seeded"] is not True or not isinstance(data["entries"], list) or len(data["entries"]) > 10000:
        raise GameplayError("Stored presents are invalid")
    ids = []
    fields = {"id", "content_type", "content_id", "amount", "begin_at", "end_at", "accepted_at", "m_present_title_id", "m_present_message_id", "title", "message", "is_subscription"}
    for entry in data["entries"]:
        if not isinstance(entry, dict) or set(entry) != fields:
            raise GameplayError("Stored present fields are invalid")
        ids.append(number(entry["id"]))
        number(entry["amount"], 1, 2 ** 31 - 1)
        number(entry["content_type"], 1, 2 ** 31 - 1)
        number(entry["content_id"], 0)
        if entry["accepted_at"] is not None and not isinstance(entry["accepted_at"], str):
            raise GameplayError("Stored present receipt is invalid")
    if len(ids) != len(set(ids)):
        raise GameplayError("Stored present identifiers are duplicated")
    for entry in data["entries"]:
        if entry["content_type"] == 51:
            if entry["accepted_at"] is None and available(entry, game.now, "begin_at", "end_at"):
                grant(game.state, [{k: entry[k] for k in ("content_type", "content_id", "amount")}], game.catalog, game.user_id, game.now, game.progress)
                entry["accepted_at"] = game.now.isoformat()
        elif entry["content_type"] not in PRESENT_CONTENT_TYPES:
            raise GameplayError("Stored present has no native display representation", 503, 5036)
    return data["entries"]


def visible_presents(game):
    return [entry for entry in presents(game) if entry["content_type"] in PRESENT_CONTENT_TYPES]


def present_count(game):
    return sum(entry["accepted_at"] is None and available(entry, game.now, "begin_at", "end_at") for entry in visible_presents(game))


def enqueue(game, rewards, title="Inventory overflow"):
    entries = presents(game)
    for reward in rewards:
        if reward["content_type"] not in PRESENT_CONTENT_TYPES:
            raise GameplayError("Reward cannot be displayed in the native present box", 503, 5036)
        if len(entries) >= 10000:
            raise GameplayError("Present storage is full", 413, 4130)
        entries.append({"id": max((r["id"] for r in entries), default=0) + 1, **reward,
                        "begin_at": game.now.isoformat(), "end_at": None, "accepted_at": None,
                        "m_present_title_id": 0, "m_present_message_id": 0, "title": title,
                        "message": "Items are available here when inventory space is free.", "is_subscription": 0})


def present_list(game, body):
    category, offset = number(body["category"], 1, 99), number(body["offset"], 0, 1000000)
    if category not in (1, 2, 99):
        raise GameplayError("Unknown present category", 400, 4007)
    entries = visible_presents(game)
    if category == 99:
        history = [r for r in entries if r["accepted_at"] is not None]
        return {"present_list": [], "received_list": copy.deepcopy(history[offset:offset + 100])}
    pending = [r for r in entries if r["accepted_at"] is None and (r["end_at"] is None) == (category == 1) and available(r, game.now, "begin_at", "end_at")]
    return {"present_list": copy.deepcopy(pending[offset:offset + 100]), "received_list": []}


def present_receive(game, body):
    selected = identifiers(body["ids"], 1000)
    if not selected:
        raise GameplayError("No presents were selected", 400, 4007)
    entries = presents(game)
    by_id = {r["id"]: r for r in entries}
    if set(selected) - by_id.keys():
        raise GameplayError("Present is not owned")
    if any(by_id[i]["accepted_at"] is None and not available(by_id[i], game.now, "begin_at", "end_at") for i in selected):
        raise GameplayError("Present is outside its availability period")
    rewards = [{k: by_id[i][k] for k in ("content_type", "content_id", "amount")} for i in selected if by_id[i]["accepted_at"] is None]
    grant(game.state, rewards, game.catalog, game.user_id, game.now, game.progress)
    for identifier in selected:
        if by_id[identifier]["accepted_at"] is None:
            by_id[identifier]["accepted_at"] = game.now.isoformat()
    return {"present_list": None,
            "received_list": copy.deepcopy([by_id[i] for i in selected if by_id[i]["content_type"] in PRESENT_CONTENT_TYPES])}


PRESENT_ROUTES = {
    "api/present/list/{0}/{1}": route("GET", "category offset", present_list, "category", "offset"),
    "api/present/receive": route("POST", "ids", present_receive),
}
