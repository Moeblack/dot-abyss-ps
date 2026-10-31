from __future__ import annotations

from .gameplay_content import EQUIPMENT
from .gameplay_catalog import GameplayError, number
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


def deliver_for_display(game, rewards, *, include_mailed=True):
    displayed = []
    for reward in rewards:
        kind = reward["content_type"]
        table = "t_" + EQUIPMENT[kind][1] if kind in EQUIPMENT else None
        before = {row["id"] for row in rows(game.state, table)} if table else set()
        content = {key: reward[key] for key in ("content_type", "content_id", "amount")}
        received, mailed = deliver(game, [content])
        created = sorted(row["id"] for row in rows(game.state, table) if row["id"] not in before) if table else []
        if table and len(created) != sum(row["amount"] for row in received):
            raise GameplayError("Granted equipment does not match its display records", 503, 5036)
        entries = [(row, True) for row in received]
        if include_mailed:
            # Equipment animations require an inventory instance, which mailed equipment does not have.
            entries.extend((row, False) for row in mailed if row["content_type"] not in EQUIPMENT)
        for granted, is_received in entries:
            content = {key: granted[key] for key in ("content_type", "content_id", "amount")}
            extras = {"bonus_type": reward.get("bonus_type", 0), "is_rare_drop": reward.get("is_rare_drop", 0)}
            if table and is_received:
                for _ in range(granted["amount"]):
                    displayed.append({**content, "amount": 1, **extras,
                                      "additional_info": {"t_" + EQUIPMENT[kind][0] + "_id": created.pop(0)}})
            else:
                displayed.append({**content, **extras, "additional_info": None})
            if (content["content_type"], content["content_id"]) != (reward["content_type"], reward["content_id"]):
                displayed[-1]["origin_content"] = {key: reward[key] for key in ("content_type", "content_id", "amount")}
    return displayed


def replay_display_rewards(game, rewards, records):
    missing, used, candidates = {}, {}, {}
    for reward in rewards:
        kind = reward["content_type"]
        if kind not in EQUIPMENT:
            continue
        identity = kind, reward["content_id"]
        field = "t_" + EQUIPMENT[kind][0] + "_id"
        info = reward.get("additional_info")
        if info is None:
            missing[identity] = missing.get(identity, 0) + number(reward["amount"], 1, 100)
        elif isinstance(info, dict) and reward["amount"] == 1:
            used.setdefault(identity, set()).add(number(info.get(field)))
        else:
            raise GameplayError("Stored equipment reward display is invalid")
    for (kind, identifier), amount in missing.items():
        singular, plural = EQUIPMENT[kind]
        ids = {row["id"] for row in records.get("t_" + plural, {}).get("up", []) if row.get("m_" + singular + "_id") == identifier}
        ids -= used.get((kind, identifier), set())
        if len(ids) != amount:
            raise GameplayError("Stored equipment reward instances cannot be recovered", 409, 4092)
        candidates[kind, identifier] = sorted(ids)
    owned = {kind: {row["id"]: row["m_" + singular + "_id"] for row in rows(game.state, "t_" + plural)}
             for kind, (singular, plural) in EQUIPMENT.items()}
    displayed = []
    for reward in rewards:
        kind, identifier = reward["content_type"], reward["content_id"]
        if kind not in EQUIPMENT:
            displayed.append(dict(reward))
            continue
        field = "t_" + EQUIPMENT[kind][0] + "_id"
        info = reward.get("additional_info")
        ids = [info[field]] if info is not None else [candidates[kind, identifier].pop(0) for _ in range(reward["amount"])]
        for instance in ids:
            if owned[kind].get(instance) == identifier:
                displayed.append({**reward, "amount": 1, "additional_info": {field: instance}})
    return displayed
