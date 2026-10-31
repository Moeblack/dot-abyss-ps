from __future__ import annotations

from collections import defaultdict

from .gameplay_catalog import GameplayError, available, number
from .gameplay_records import find, rows
from .gameplay_wallet import paid_lots, timestamp

BALANCE_FIELDS = {10: "free_virtual_money", 50: "gold", 51: "soul", 52: "disaster_coin", 53: "friend_point", 110: "character_exp"}


def combined_costs(costs):
    totals = defaultdict(int)
    for cost in costs:
        kind = number(cost["content_type"], 0, 2 ** 31 - 1)
        identifier = number(cost.get("content_id", 0), 0)
        amount = number(cost["amount"], 0, 2 ** 31 - 1)
        if amount:
            totals[kind, identifier] += amount
    return [{"content_type": kind, "content_id": identifier, "amount": number(amount, 1, 2 ** 31 - 1)}
            for (kind, identifier), amount in sorted(totals.items())]


def balance_location(game, kind, identifier):
    if kind in BALANCE_FIELDS:
        if identifier != 0:
            raise GameplayError("Currency identifiers must be zero", 503, 5036)
        return rows(game.state, "t_statuses")[0], BALANCE_FIELDS[kind]
    if kind == 30:
        game.catalog.get("m_items", identifier)
        return find(game.state, "t_items", m_item_id=identifier), "amount"
    if kind == 170:
        definition = game.catalog.get("m_gacha_tokens", identifier)
        if not available(definition, game.now, "begin_at", "end_at"):
            raise GameplayError("Gacha token is outside its availability period")
        return find(game.state, "t_gacha_tokens", m_gacha_token_id=identifier), "token_num"
    if kind == 54:
        game.catalog.get("m_event_currencies", identifier)
        return find(game.state, "t_event_currencies", m_event_currency_id=identifier), "amount"
    if kind == 56:
        game.catalog.get("m_event_boss_tickets", identifier)
        return find(game.state, "t_event_boss_tickets", m_event_boss_ticket_id=identifier), "amount"
    if kind == 120:
        from .gameplay_stamina import STAMINA_TYPE, settle_stamina

        record = settle_stamina(game.state, game.catalog, game.user_id, game.now) if identifier == STAMINA_TYPE else find(game.state, "t_timed_parameters", type=identifier)
        return record, "amount"
    raise GameplayError("This resource type is not spendable locally: " + str(kind), 501, 5010)


def consume(game, costs):
    totals = combined_costs(costs)
    changes, remaining = [], {}
    def available(row, field):
        return remaining.get((id(row), field), row[field])
    def reserve(row, field, amount):
        remaining[id(row), field] = available(row, field) - amount
        changes.append((row, field, amount))
    def reserve_paid(amount):
        for lot in paid_lots(game):
            spent = min(amount, available(lot, "rest_amount"))
            if spent:
                reserve(lot, "rest_amount", spent)
                amount -= spent
        if amount:
            raise GameplayError("Insufficient resources", 409, 4093)
    for cost in totals:
        if cost["content_type"] == 33:
            game.catalog.get("m_limited_items", cost["content_id"])
            amount = cost["amount"]
            lots = sorted([r for r in rows(game.state, "t_limited_items") if r["m_limited_item_id"] == cost["content_id"] and timestamp(r["expire_at"]) > game.now], key=lambda r: (timestamp(r["expire_at"]), r["id"]))
            for lot in lots:
                spent = min(amount, available(lot, "amount"))
                reserve(lot, "amount", spent)
                amount -= spent
            if amount:
                raise GameplayError("Insufficient limited items", 409, 4093)
            continue
        if cost["content_type"] in (10, 20):
            if cost["content_id"] != 0:
                raise GameplayError("Currency identifiers must be zero", 503, 5036)
            amount = cost["amount"]
            if cost["content_type"] == 10:
                status = rows(game.state, "t_statuses")[0]
                spent = min(available(status, "free_virtual_money"), amount)
                reserve(status, "free_virtual_money", spent)
                amount -= spent
            reserve_paid(amount)
            continue
        row, field = balance_location(game, cost["content_type"], cost["content_id"])
        if row is None or available(row, field) < cost["amount"]:
            raise GameplayError("Insufficient resources", 409, 4093)
        reserve(row, field, cost["amount"])
    for row, field, amount in changes:
        row[field] -= amount
        if field == "rest_amount":
            row["updated_at"] = game.now.isoformat()
    return totals


def owned(game, table, identifier):
    row = find(game.state, table, id=number(identifier))
    if row is None:
        raise GameplayError("Requested record is not owned")
    return row


def text_argument(value, maximum, *, empty=False, multiline=False):
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        raise GameplayError("Text length is invalid", 400, 4007)
    if any(ord(c) < 32 and not (multiline and c == "\n") or 0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise GameplayError("Text contains unsupported characters", 400, 4007)
    return value


def boolean_argument(value):
    if type(value) is not bool:
        raise GameplayError("Expected a boolean", 400, 4007)
    return value
