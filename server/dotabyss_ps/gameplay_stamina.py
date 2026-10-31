from __future__ import annotations

from datetime import timedelta

from .gameplay_catalog import GameplayError, JST, number, parse_timestamp
from .gameplay_economy import consume
from .gameplay_records import find, put, rows
from .gameplay_routes import route

STAMINA_TYPE = 5
STAMINA_LIMIT = 999
RECOVERY_SECONDS = 180
GEM_RECOVERY = 120


def recovery_day(now):
    return (now.astimezone(JST) - timedelta(hours=5)).date()


def settle_stamina(state, catalog, user_id, now):
    status = rows(state, "t_statuses")[0]
    rank = next((r for r in catalog.rows("m_user_ranks") if r["rank"] == status["user_rank"]), None)
    if rank is None or "stamina_max" not in rank:
        raise GameplayError("Stamina capacity is missing from the rank master", 503, 5036)
    capacity = number(rank["stamina_max"], 1, STAMINA_LIMIT)
    stamina = find(state, "t_timed_parameters", type=STAMINA_TYPE)
    if stamina is None:
        return put(state, "t_timed_parameters", {"type": STAMINA_TYPE}, {
            "amount": capacity, "capacity": capacity, "recovery": 1, "duration_type": 1,
            "recovery_duration": RECOVERY_SECONDS, "last_recovery_at": now.isoformat()}, user_id)
    stamina.update(capacity=capacity, recovery=1, duration_type=1, recovery_duration=RECOVERY_SECONDS)
    try:
        cursor = parse_timestamp(stamina["last_recovery_at"])
    except (TypeError, ValueError) as error:
        raise GameplayError("Stored stamina recovery time is invalid") from error
    if cursor > now:
        return stamina
    if stamina["amount"] >= capacity:
        stamina["last_recovery_at"] = now.isoformat()
        return stamina
    ticks = int((now - cursor).total_seconds()) // RECOVERY_SECONDS
    if ticks:
        stamina["amount"] = min(capacity, stamina["amount"] + ticks)
        stamina["last_recovery_at"] = (now if stamina["amount"] == capacity else cursor + timedelta(seconds=ticks * RECOVERY_SECONDS)).isoformat()
    return stamina


def recovery_record(game):
    record = find(game.state, "t_stamina_recoveries")
    if record is None:
        return put(game.state, "t_stamina_recoveries", {}, {"recovery_count": 0, "last_recovery_at": game.now.isoformat()}, game.user_id)
    try:
        previous = parse_timestamp(record["last_recovery_at"])
    except (TypeError, ValueError) as error:
        raise GameplayError("Stored stamina purchase time is invalid") from error
    if recovery_day(previous) < recovery_day(game.now):
        record.update(recovery_count=0, last_recovery_at=game.now.isoformat())
    return record


def gem_recoveries(game, amount, previous_count, maximum):
    prices = sorted(game.catalog.rows("m_stamina_recovery_costs"), key=lambda r: r["recovery_count"])
    spent = 0
    for count in range(1, maximum + 1):
        tier = next((r for r in reversed(prices) if r["recovery_count"] <= previous_count + count), None)
        if tier is None:
            raise GameplayError("Stamina recovery price tier is missing", 503, 5036)
        spent += number(tier["cost_amount"], 1, 2 ** 31 - 1)
        if spent == amount:
            return count
        if spent > amount:
            raise GameplayError("Stamina recovery price does not match the current tier", 400, 4007)
    raise GameplayError("Stamina recovery exceeds the capacity limit")


def recover(game, body):
    selected = body["use_items"]
    if not isinstance(selected, list) or not 1 <= len(selected) <= 100:
        raise GameplayError("Invalid stamina recovery batch", 400, 4007)
    stamina = settle_stamina(game.state, game.catalog, game.user_id, game.now)
    counter = recovery_record(game)
    seen, costs, recovered, gem_cost = set(), [], 0, 0
    for item in selected:
        if not isinstance(item, dict) or set(item) != {"content_type", "content_id", "amount"}:
            raise GameplayError("Invalid stamina recovery content", 400, 4007)
        kind, identifier, amount = number(item["content_type"]), number(item["content_id"], 0), number(item["amount"], 1, 2 ** 31 - 1)
        if (kind, identifier) in seen:
            raise GameplayError("Duplicate stamina recovery content", 400, 4007)
        seen.add((kind, identifier))
        if kind == 10 and identifier == 0:
            gem_cost = amount
        elif kind in (30, 33):
            definition = game.catalog.get("m_items" if kind == 30 else "m_limited_items", identifier)
            if definition["type"] != 150:
                raise GameplayError("Item does not restore stamina", 400, 4007)
            number(amount, 1, game.rules["item_max_batch"])
            recovered += number(definition["value"], 1, STAMINA_LIMIT) * amount
        else:
            raise GameplayError("Unsupported stamina recovery content", 400, 4007)
        costs.append(dict(item))
    remaining = STAMINA_LIMIT - stamina["amount"] - recovered
    if remaining < 0:
        raise GameplayError("Stamina recovery exceeds the capacity limit")
    purchases = gem_recoveries(game, gem_cost, counter["recovery_count"], remaining // GEM_RECOVERY) if gem_cost else 0
    recovered += purchases * GEM_RECOVERY
    consume(game, costs)
    stamina["amount"] += recovered
    if stamina["amount"] >= stamina["capacity"]:
        stamina["last_recovery_at"] = game.now.isoformat()
    if purchases:
        counter.update(recovery_count=number(counter["recovery_count"] + purchases, 0, 2 ** 31 - 1), last_recovery_at=game.now.isoformat())
    return {}


STAMINA_ROUTES = {"api/main-story/recovery": route("POST", "use_items", recover)}
