from __future__ import annotations

from .gameplay_catalog import GameplayError, number, parse_timestamp
from .gameplay_delivery import deliver
from .gameplay_receipts import feature_data
from .gameplay_records import rows


def expire_tokens(game):
    rate = game.rules["gacha_expired_token_gold_per_token"]
    if not rate:
        return []
    expired = []
    for row in rows(game.state, "t_gacha_tokens"):
        definition = game.catalog.get("m_gacha_tokens", row["m_gacha_token_id"])
        if row["token_num"] and definition["end_at"] and game.now > parse_timestamp(definition["end_at"]):
            amount = number(row["token_num"], 1, 2 ** 31 - 1)
            expired.append((row, definition, amount, number(amount * rate, 1, 2 ** 31 - 1)))
    if not expired:
        return []
    data = feature_data(game, "gacha")
    receipts = data.setdefault("expiry_receipts", {})
    if not isinstance(receipts, dict):
        raise GameplayError("Stored gacha expiry receipts are invalid")
    result = []
    for row, definition, amount, gold in expired:
        key = f"{definition['id']}:{definition['end_at']}"
        receipt = receipts.get(key, {"token_num": 0, "gold": 0, "converted_at": game.now.isoformat()})
        if not isinstance(receipt, dict) or set(receipt) != {"token_num", "gold", "converted_at"}:
            raise GameplayError("Stored gacha expiry receipt is invalid")
        previous_amount = number(receipt["token_num"], 0)
        previous_gold = number(receipt["gold"], 0)
        try:
            parse_timestamp(receipt["converted_at"])
        except (ValueError, TypeError, AttributeError) as error:
            raise GameplayError("Stored gacha expiry timestamp is invalid") from error
        deliver(game, [{"content_type": 50, "content_id": 0, "amount": gold}])
        row["token_num"] = 0
        receipts[key] = {"token_num": number(previous_amount + amount), "gold": number(previous_gold + gold),
                         "converted_at": game.now.isoformat()}
        result.append({"m_gacha_token_id": definition["id"], "gacha_token_convert_amount": amount,
                       "converted_item_acquire_amount": gold})
    return result
