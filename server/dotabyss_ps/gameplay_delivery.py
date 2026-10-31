from __future__ import annotations

from .gameplay_content import EQUIPMENT
from .gameplay_catalog import number
from .gameplay_presents import enqueue
from .gameplay_records import find, grant, rows


def deliver(game, rewards):
    received, mailed = [], []
    for reward in rewards:
        kind, identifier, amount = reward["content_type"], reward["content_id"], reward["amount"]
        number(kind, 1, 2 ** 31 - 1)
        number(identifier, 0)
        number(amount, 1, 2 ** 31 - 1)
        accepted = amount
        if kind in EQUIPMENT:
            accepted = min(amount, max(0, 100 - sum(len(rows(game.state, "t_" + plural)) for _, plural in EQUIPMENT.values())))
        elif kind == 30:
            definition = game.catalog.get("m_items", identifier)
            item = find(game.state, "t_items", m_item_id=identifier)
            limit = definition.get("possession_limit", 2 ** 31 - 1) or 2 ** 31 - 1
            accepted = min(amount, max(0, limit - (item["amount"] if item else 0)))
        if accepted:
            received.extend(grant(game.state, [{**reward, "amount": accepted}], game.catalog, game.user_id, game.now, game.progress))
        if accepted < amount:
            overflow = {**reward, "amount": amount - accepted}
            enqueue(game, [overflow])
            mailed.append(overflow)
    return received, mailed
