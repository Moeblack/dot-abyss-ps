from __future__ import annotations

from collections import Counter

from .gameplay_catalog import GameplayError, number
from .gameplay_delivery import deliver
from .gameplay_event_rewards import event_state, randomizer, spend_currency
from .gameplay_records import find, put
from .gameplay_routes import route


def box(game, event_id):
    event, data = event_state(game, event_id, "box", {6})
    settings = [row for row in game.catalog.rows("m_event_box_gachas") if row["m_event_id"] == event["id"]]
    boxes = sorted([row for row in game.catalog.rows("m_event_box_gacha_boxes") if row["m_event_id"] == event["id"]], key=lambda row: row["box_no"])
    if len(settings) != 1 or not boxes or not boxes[-1]["is_loop"]:
        raise GameplayError("Event box gacha settings are incomplete", 503, 5036)
    existing = find(game.state, "t_event_box_gachas", m_event_id=event["id"])
    if not data:
        data.update(current_box_no=max(1, existing["current_box_no"] if existing else 1), draws={})
    if set(data) != {"current_box_no", "draws"} or not isinstance(data["draws"], dict):
        raise GameplayError("Stored event box gacha is invalid")
    logical = number(data["current_box_no"], 1)
    definition = next((row for row in boxes if row["box_no"] == logical), boxes[-1])
    contents = [row for row in game.catalog.rows("m_event_box_gacha_contents") if row["m_event_box_gacha_box_id"] == definition["id"]]
    known = {str(row["id"]): row for row in contents}
    if not contents or set(data["draws"]) - set(known):
        raise GameplayError("Stored box inventory does not match the event")
    for key, amount in data["draws"].items():
        number(amount, 0, known[key]["count_in_box"])
    put(game.state, "t_event_box_gachas", {"m_event_id": event["id"]}, {"current_box_no": logical}, game.user_id)
    return event, settings[0], definition, contents, data


def emission(counts):
    return [{"m_event_box_gacha_content_id": int(key), "emission_count": amount}
            for key, amount in sorted(counts.items(), key=lambda pair: int(pair[0])) if amount]


def status(game, body):
    _, _, definition, _, data = box(game, body["m_event_id"])
    return {"m_event_box_gacha_box_id": definition["id"], "emission_data": emission(data["draws"])}


def execute(game, body):
    event, settings, definition, contents, data = box(game, body["m_event_id"])
    count = number(body["exec_count"], 1, 1000)
    remaining = {row["id"]: row["count_in_box"] - data["draws"].get(str(row["id"]), 0) for row in contents}
    if count > sum(remaining.values()):
        raise GameplayError("The requested draws exceed the remaining box inventory")
    spend_currency(game, event["id"], number(settings["cost"]) * count)
    chooser = randomizer(game, event["id"], "box", data["current_box_no"], sum(data["draws"].values()))
    draws = Counter()
    for _ in range(count):
        identifier = chooser.choices(list(remaining), weights=list(remaining.values()), k=1)[0]
        remaining[identifier] -= 1
        draws[str(identifier)] += 1
    rewards = []
    for row in contents:
        amount = draws.get(str(row["id"]), 0)
        if amount:
            rewards.append({"content_type": row["content_type"], "content_id": row["content_id"], "amount": row["amount"] * amount})
            data["draws"][str(row["id"])] = data["draws"].get(str(row["id"]), 0) + amount
    deliver(game, rewards)
    return {"emission_data": emission(draws)}


def renew(game, body):
    event, _, definition, contents, data = box(game, body["m_event_id"])
    complete = all(data["draws"].get(str(row["id"]), 0) == row["count_in_box"] for row in contents)
    picked_up = not definition["is_loop"] and any(row["sp_content"] and data["draws"].get(str(row["id"]), 0) == row["count_in_box"] for row in contents)
    if not complete and not picked_up:
        raise GameplayError("Obtain the box pickup or empty its inventory before renewing")
    data.update(current_box_no=data["current_box_no"] + 1, draws={})
    put(game.state, "t_event_box_gachas", {"m_event_id": event["id"]}, {"current_box_no": data["current_box_no"]}, game.user_id)
    return {}


EVENT_BOX_ROUTES = {
    "api/event/box-gacha/status": route("POST", "m_event_id", status),
    "api/event/box-gacha/exec": route("POST", "m_event_id exec_count", execute),
    "api/event/box-gacha/renew-box": route("POST", "m_event_id", renew),
}
