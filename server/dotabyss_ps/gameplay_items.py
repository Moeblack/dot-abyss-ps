from __future__ import annotations

from .gameplay_catalog import GameplayError, number
from .gameplay_economy import consume
from .gameplay_delivery import deliver
from .gameplay_routes import route


def use_selectable(game, body):
    item = game.catalog.get("m_items", number(body["m_item_id"]))
    selections = body["selects"]
    if item["type"] != 190:
        raise GameplayError("This item is not a selectable box")
    if not isinstance(selections, list) or not 1 <= len(selections) <= 100:
        raise GameplayError("Invalid selectable item batch", 400, 4007)
    choices = {r["id"]: r for r in game.catalog.rows("m_item_selectable_groups") if r["group_id"] == item["value"]}
    total, seen, rewards = 0, set(), []
    for selection in selections:
        if not isinstance(selection, dict) or set(selection) != {"choice", "amount"}:
            raise GameplayError("Invalid selectable item fields", 400, 4007)
        identifier = number(selection["choice"])
        amount = number(selection["amount"], 1, game.rules["item_max_batch"])
        if identifier in seen or identifier not in choices:
            raise GameplayError("Choice is duplicated or outside this box", 400, 4007)
        seen.add(identifier)
        total += amount
        choice = choices[identifier]
        rewards.append({"content_type": choice["content_type"], "content_id": choice["content_id"], "amount": choice["amount"] * amount})
    number(total, 1, game.rules["item_max_batch"])
    consume(game, [{"content_type": 30, "content_id": item["id"], "amount": total}])
    received, mailed = deliver(game, rewards)
    return {"receives": received, "presents": mailed}


ITEM_ROUTES = {"api/item/use/selectable": route("POST", "m_item_id selects", use_selectable)}
