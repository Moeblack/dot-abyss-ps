from __future__ import annotations

from datetime import datetime, timedelta

from .gameplay_catalog import GameplayError, number, parse_timestamp
from .gameplay_records import put, rows


def timestamp(value):
    try:
        parsed = parse_timestamp(value)
    except (ValueError, TypeError, AttributeError) as error:
        raise GameplayError("Stored timestamp is invalid") from error
    return parsed


def paid_lots(game):
    return sorted([r for r in rows(game.state, "t_payment_dmm_currencies") if r["rest_amount"] > 0 and timestamp(r["valid_at"]) > game.now],
                  key=lambda r: (timestamp(r["valid_at"]), r["id"]))


def expire_paid_lots(game):
    from .gameplay_shop import shop_data

    receipts = shop_data(game)["expired_paid_lots"]
    for identifier, receipt in receipts.items():
        if (not isinstance(identifier, str) or not 1 <= len(identifier) <= 19 or not identifier.isascii() or not identifier.isdecimal()
                or str(int(identifier)) != identifier or not 1 <= int(identifier) < 2 ** 63
                or not isinstance(receipt, dict) or set(receipt) != {"amount", "valid_at", "expired_at"}):
            raise GameplayError("Stored paid-currency expiration receipt is invalid")
        number(receipt["amount"], 1, 2 ** 31 - 1)
        if timestamp(receipt["expired_at"]) < timestamp(receipt["valid_at"]):
            raise GameplayError("Stored paid-currency expiration precedes its validity limit")
    expired = {str(lot["id"]): lot for lot in rows(game.state, "t_payment_dmm_currencies") if timestamp(lot["valid_at"]) <= game.now}
    for identifier, lot in expired.items():
        if lot["rest_amount"] <= 0:
            continue
        if identifier in receipts:
            raise GameplayError("An expired paid-currency lot has a remaining balance")
        receipts[identifier] = {"amount": lot["rest_amount"], "valid_at": lot["valid_at"], "expired_at": game.now.isoformat()}
        lot.update(rest_amount=0, updated_at=game.now.isoformat())
    if not expired:
        return
    pending = [receipt["response"] for receipt in game.progress["operation_receipts"].values()]
    pending.extend(receipt[key] for receipt in shop_data(game)["receipts"].values()
                   for key in ("response", "result") if key in receipt)
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            if {"id", "user_id", "rest_amount", "valid_at", "acquire_amount", "store_product_id"} <= value.keys():
                lot = expired.get(str(value["id"]))
                if lot and value["user_id"] == game.user_id and timestamp(value["valid_at"]) == timestamp(lot["valid_at"]):
                    value.update(rest_amount=0, updated_at=lot["updated_at"])
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)


def grant_paid(state, user_id, now, amount, *, product="local.credit", price=0, valid_days=180):
    amount, price = number(amount), number(price, 0, 2 ** 31 - 1)
    stamp = now.isoformat()
    return put(state, "t_payment_dmm_currencies", {"id": max((r["id"] for r in rows(state, "t_payment_dmm_currencies")), default=0) + 1},
               {"store_product_id": product, "price": price, "acquire_amount": amount, "rest_amount": amount,
                "paid_at": stamp, "valid_at": (now + timedelta(days=valid_days)).isoformat(), "created_at": now.strftime("%Y-%m-%d %H:%M:%S"), "updated_at": stamp}, user_id)
