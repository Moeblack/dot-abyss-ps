from __future__ import annotations

from .gameplay_catalog import GameplayError, available, identifiers, number
from .gameplay_economy import consume
from .gameplay_receipts import feature_data
from .gameplay_records import grant
from .gameplay_routes import route


def active_lineups(game):
    categories = {r["id"] for r in game.catalog.rows("m_exchange_categories") if available(r, game.now)}
    return [r for r in game.catalog.rows("m_exchange_lineups") if r["m_exchange_category_id"] in categories
            and available(r, game.now, "release_at", "close_at")]


def period_key(game, kind):
    if kind in (0, 1):
        return "once"
    if kind == 2:
        return game.now.date().isoformat()
    if kind == 3:
        year, week, _ = game.now.isocalendar()
        return f"{year}-W{week:02d}"
    if kind == 4:
        return game.now.strftime("%Y-%m")
    raise GameplayError("Unsupported exchange reset period", 503, 5036)


def counter(game, lineup):
    data = feature_data(game, "exchange")
    if not data:
        data["counts"] = {}
    if set(data) != {"counts"} or not isinstance(data["counts"], dict):
        raise GameplayError("Stored exchange counters are invalid")
    key = str(lineup["id"])
    period = period_key(game, lineup["available_type"])
    saved = data["counts"].get(key)
    if saved is not None:
        if not isinstance(saved, dict) or set(saved) != {"period", "count"} or not isinstance(saved["period"], str):
            raise GameplayError("Stored exchange counter is invalid")
        number(saved["count"], 0, 2 ** 31 - 1)
    if saved is None or saved["period"] != period:
        saved = data["counts"][key] = {"period": period, "count": 0}
    return saved


def listing(game, body):
    categories = {}
    seen = set(game.progress["read_lineups"])
    for row in sorted(active_lineups(game), key=lambda r: (r["display_order"], r["id"])):
        categories.setdefault(row["m_exchange_category_id"], []).append({
            "m_exchange_lineup_id": row["id"], "exchange_count": counter(game, row)["count"], "is_new": int(row["id"] not in seen)})
    return {"categories": [{"m_exchange_category_id": key, "lineups": value} for key, value in sorted(categories.items())]}


def read_lineups(game, body):
    selected = identifiers(body["m_exchange_lineup_ids"], 1000)
    if set(selected) - {r["id"] for r in active_lineups(game)}:
        raise GameplayError("Exchange lineup is not active")
    game.progress["read_lineups"] = sorted(set(game.progress["read_lineups"]) | set(selected))
    return {"read_m_exchange_lineup_ids": selected}


def execute(game, body):
    identifier = number(body["m_exchange_lineup_id"])
    amount = number(body["exchange_count"], 1, game.rules["exchange_max_batch"])
    lineup = next((r for r in active_lineups(game) if r["id"] == identifier), None)
    if lineup is None:
        raise GameplayError("Exchange lineup is not active")
    saved = counter(game, lineup)
    if lineup["available_type"] and saved["count"] + amount > lineup["available_count"]:
        raise GameplayError("Exchange purchase limit reached")
    category = game.catalog.get("m_exchange_categories", lineup["m_exchange_category_id"])
    consume(game, [{"content_type": category["cost_content_type"], "content_id": category["cost_content_id"], "amount": lineup["cost_num"] * amount}])
    acquired = grant(game.state, [{"content_type": lineup["content_type"], "content_id": lineup["content_id"], "amount": lineup["acquire_num"] * amount}],
                     game.catalog, game.user_id, game.now, game.progress)
    saved["count"] = number(saved["count"] + amount, 0, 2 ** 31 - 1)
    return {"result": {"update_lineups": {"m_exchange_lineup_id": identifier, "exchange_count": saved["count"]}, "acquire_contents": acquired}}


EXCHANGE_ROUTES = {
    "api/exchange/lineup": route("POST", "", listing),
    "api/exchange/read/lineup": route("POST", "m_exchange_lineup_ids", read_lineups),
    "api/exchange/execute": route("POST", "m_exchange_lineup_id exchange_count", execute),
}
