from __future__ import annotations

import copy
import json
import secrets
from datetime import timedelta

from .gameplay_catalog import GameplayError, JST, available, identifiers, number
from .gameplay_delivery import deliver
from .gameplay_comeback import lineup_allowed
from .gameplay_economy import consume, text_argument
from .gameplay_exchange import period_key
from .gameplay_login_state import login_data
from .gameplay_receipts import feature_data
from .gameplay_records import find, put, rows
from .gameplay_routes import route
from .gameplay_statistics import record
from .gameplay_wallet import grant_paid, timestamp
from .protocol import decode_json
from .gameplay_effects import settle_before_effect_change, grant_vip_titles


def shop_data(game):
    data = feature_data(game, "shop")
    if not data:
        data.update(counts={}, released={}, steps={}, read_common=[], read_subscriptions=[], read_recommends=[], receipts={}, tokens={}, serial_claims=[], vip_claims={})
    data.setdefault("expired_paid_lots", {})
    expected = {"counts", "released", "steps", "read_common", "read_subscriptions", "read_recommends", "receipts", "tokens", "serial_claims", "vip_claims", "expired_paid_lots"}
    if set(data) != expected or any(not isinstance(data[k], dict) for k in ("counts", "released", "steps", "receipts", "tokens", "vip_claims", "expired_paid_lots")):
        raise GameplayError("Stored local shop data is invalid")
    for field in ("read_common", "read_subscriptions", "read_recommends"):
        identifiers(data[field], 10000)
    if not isinstance(data["serial_claims"], list) or len(data["serial_claims"]) > 10000:
        raise GameplayError("Stored serial receipts are invalid")
    return data


def vip_record(game):
    result = find(game.state, "t_vip_ranks", user_id=game.user_id)
    if result is None:
        result = put(game.state, "t_vip_ranks", {}, {"total_point": 0, "created_at": game.now.strftime("%Y-%m-%d %H:%M:%S"), "updated_at": game.now.isoformat()}, game.user_id)
    return result


def vip_week_start(now):
    shifted = now.astimezone(JST) - timedelta(hours=5)
    return (shifted - timedelta(days=shifted.weekday())).replace(hour=5, minute=0, second=0, microsecond=0)


def ensure_vip_weekly_counters(game):
    start = vip_week_start(game.now)
    claims = shop_data(game)["vip_claims"]
    legacy_weeks = {start.strftime("%G-W%V"), game.now.astimezone(JST).strftime("%G-W%V")}
    result = {}
    for kind, supply_type in ((1, 12), (2, 13)):
        key = f"weekly:{start.isoformat()}:{kind}"
        claimed = key in claims or any(f"{week}:{kind}" in claims for week in legacy_weeks)
        current = find(game.state, "t_timed_parameters", type=supply_type)
        if current and timestamp(current["last_recovery_at"]) >= start and current["amount"] == 0:
            claimed = True
        if claimed:
            claims[key] = True
        result[kind] = put(game.state, "t_timed_parameters", {"type": supply_type}, {
            "amount": int(not claimed), "capacity": 1, "recovery": 1, "duration_type": 3,
            "recovery_duration": 1, "last_recovery_at": start.isoformat()}, game.user_id)
    return result


def recommendation_eligible(game, definition):
    from .gameplay_recommendations import eligible

    return eligible(game, definition)


def recommendations(game):
    from .gameplay_recommendations import collect

    return collect(game, shop_data(game))


def count_entry(game, payment, kind=1):
    data = shop_data(game)
    kind = kind if kind not in (5, 6) else 1
    shifted = game.now.astimezone(JST) - timedelta(hours=5)
    period = {2: shifted.date().isoformat(), 3: shifted.strftime("%G-W%V"), 4: shifted.strftime("%Y-%m")}.get(kind, "once")
    if kind not in range(5):
        raise GameplayError("Unsupported shop reset period", 503, 5036)
    canonical = "reset05:" + period if kind in (2, 3, 4) else period
    key = str(payment["id"])
    entry = data["counts"].get(key)
    if entry is not None and (not isinstance(entry, dict) or set(entry) != {"period", "count"}):
        raise GameplayError("Stored shop purchase counter is invalid")
    if entry is not None:
        number(entry["count"], 0, 2 ** 31 - 1)
    if entry is not None and entry["period"] in {period, period_key(game, kind)}:
        if entry["period"] != period:
            return entry
        entry["period"] = canonical
    if entry is None or entry["period"] != canonical:
        entry = data["counts"][key] = {"period": canonical, "count": 0}
    return entry


def platform_product(game, payment, *, required=False):
    game.catalog.rows("m_payment_dmm_products")
    product = game.catalog.tables["m_payment_dmm_products"].get(payment["m_payment_dmm_product_id"])
    if product is None and required:
        raise GameplayError("This product is not available on the local DMM platform", 400, 4007)
    return product


def platform_payments(game, kind, identifier):
    step_ids = {r["m_payment_id"] for r in game.catalog.rows("m_payment_lineup_steps") if kind == 1 and r["m_payment_lineup_id"] == identifier}
    matches = [r for r in game.catalog.rows("m_payments") if r["payment_type"] == kind and (r["master_id"] == identifier or r["id"] in step_ids)]
    if not matches or platform_product(game, matches[0]) is None:
        return []
    return [r for r in matches if platform_product(game, r) is not None]


def lineup_payment(game, definition):
    payments = platform_payments(game, 1, definition["id"])
    if not payments:
        return None
    steps = sorted([r for r in game.catalog.rows("m_payment_lineup_steps") if r["m_payment_lineup_id"] == definition["id"]], key=lambda r: r["step"])
    if steps:
        step = shop_data(game)["steps"].get(str(definition["id"]), steps[0]["step"])
        match = next((r for r in steps if r["step"] == step), None)
        return next((r for r in payments if match and r["id"] == match["m_payment_id"]), None)
    return min(payments, key=lambda r: r["id"]) if payments else None


def payment_lineup(game, payment):
    steps = [r for r in game.catalog.rows("m_payment_lineup_steps") if r["m_payment_id"] == payment["id"]]
    if len(steps) > 1:
        raise GameplayError("Payment belongs to multiple step lineups", 503, 5036)
    identifier = steps[0]["m_payment_lineup_id"] if steps else payment["master_id"]
    return game.catalog.get("m_payment_lineups", identifier)


def validate_purchase(game, identifier):
    payment = game.catalog.get("m_payments", number(identifier))
    product = platform_product(game, payment, required=True)
    kind, master_id = payment["payment_type"], payment["master_id"]
    if kind == 1:
        definition = payment_lineup(game, payment)
        if not lineup_allowed(game, definition["id"]):
            raise GameplayError("Shop product requires an active comeback entitlement")
        if not available(definition, game.now):
            raise GameplayError("Shop product is not active")
        current = lineup_payment(game, definition)
        if current is None or current["id"] != payment["id"]:
            raise GameplayError("Shop purchase is not at the current step")
        counter = count_entry(game, payment, definition["available_type"])
        if definition["available_type"] and counter["count"] >= definition["available_count"]:
            raise GameplayError("Shop purchase limit reached")
    elif kind == 2:
        definition = next((r for r in recommendations(game) if r["id"] == master_id), None)
        if definition is None:
            raise GameplayError("Recommended product is not available")
        counter = count_entry(game, payment)
        if definition["recommend_type"] == 1 and counter["count"] >= 1:
            raise GameplayError("Recommended product has already been purchased")
    elif kind == 4:
        definition = game.catalog.get("m_payment_subscriptions", master_id)
        benefit = find(game.state, "t_payment_subscription_benefits", m_payment_subscription_id=master_id)
        if benefit and timestamp(benefit["expire_at"]) > game.now:
            raise GameplayError("Subscription is already active")
        counter = count_entry(game, payment)
    elif kind == 3:
        raise GameplayError("Direct-purchase gacha has no supported entitlement binding", 503, 5036)
    else:
        raise GameplayError("Unsupported shop product type", 503, 5036)
    return payment, definition, product, counter


def recommend_view(game):
    data = shop_data(game)
    result = []
    for definition in recommendations(game):
        payments = platform_payments(game, 2, definition["id"])
        if not payments:
            continue
        count = sum(count_entry(game, p)["count"] for p in payments)
        result.append({"m_payment_recommend_id": definition["id"], "purchase_count": count, "end_at": data["released"][str(definition["id"])],
                       "is_new": int(definition["id"] not in data["read_recommends"])})
    return result


def limited_login_view(game, bonus_type):
    entries = login_data(game.progress)["bonuses"]
    result = []
    for key, entry in entries.items():
        definition = game.catalog.get("m_login_bonuses", int(key))
        if definition["bonus_type"] == bonus_type and entry["paid_at"]:
            result.append({"id": int(key), "user_id": game.user_id, "m_login_bonus_id": int(key), "sheet_no": entry["sheet_no"],
                           "login_count": entry["index"], "login_total": entry["login_total"], "last_received_at": entry["last_received_at"],
                           "paid_at": entry["paid_at"], "limit_at": entry["limit_at"]})
    return result


def listing(game, body):
    ensure_vip_weekly_counters(game)
    data = shop_data(game)
    categories = {r["id"] for r in game.catalog.rows("m_payment_categories")}
    commons = []
    for definition in sorted(game.catalog.rows("m_payment_lineups"), key=lambda r: (r["display_order"], r["id"])):
        if not lineup_allowed(game, definition["id"]):
            continue
        if not available(definition, game.now) or definition["m_payment_category_id"] not in categories:
            continue
        payment = lineup_payment(game, definition)
        if payment:
            commons.append({"m_payment_lineup_id": definition["id"], "payment_count": count_entry(game, payment, definition["available_type"])["count"],
                            "end_at": definition["end_at"], "is_new": int(definition["id"] not in data["read_common"])})
    subscriptions = []
    for definition in game.catalog.rows("m_payment_subscriptions"):
        if not platform_payments(game, 4, definition["id"]):
            continue
        benefit = find(game.state, "t_payment_subscription_benefits", m_payment_subscription_id=definition["id"])
        subscriptions.append({"m_payment_subscription_id": definition["id"], "is_using": int(benefit is not None and timestamp(benefit["expire_at"]) > game.now),
                              "is_new": int(definition["id"] not in data["read_subscriptions"])})
    recommends = [{"m_payment_recommend_id": r["m_payment_recommend_id"], "payment_count": r["purchase_count"], "end_at": r["end_at"], "is_new": r["is_new"]} for r in recommend_view(game)]
    return {"categories": {"commons": commons, "recommends": recommends, "subscriptions": subscriptions},
            "t_login_thirty_days_bonuses": limited_login_view(game, 92),
            "t_payment_lineup_steps": [{"user_id": game.user_id, "m_payment_lineup_id": int(key), "step": value} for key, value in data["steps"].items()]}


def purchase(game, identifier):
    from .gameplay_idle import reconcile

    payment, definition, product, counter = validate_purchase(game, identifier)
    if not payment["is_free"] and not game.rules["local_paid_shop_enabled"]:
        raise GameplayError("Local simulated purchases are disabled")
    settle_before_effect_change(game)
    local_cost = product["price"] * game.rules["local_shop_gold_per_price"]
    if local_cost and not payment["is_free"]:
        consume(game, [{"content_type": 50, "content_id": 0, "amount": local_cost}])
    rewards = [{key: r[key] for key in ("content_type", "content_id", "amount")} for r in game.catalog.rows("m_payment_packs") if r["pack_id"] == product["m_payment_pack_pack_id"]]
    currency = product["currency_amount"]
    if currency:
        grant_paid(game.state, game.user_id, game.now, currency, product="local." + str(payment["id"]), valid_days=game.rules["paid_currency_valid_days"])
    if payment["payment_type"] == 4:
        previous = find(game.state, "t_payment_subscription_benefits", m_payment_subscription_id=definition["id"])
        count = previous["continue_count"] + 1 if previous else 1
        put(game.state, "t_payment_subscription_benefits", {"m_payment_subscription_id": definition["id"]}, {
            "expire_at": (game.now + timedelta(days=game.rules["subscription_days"])).isoformat(), "continue_count": count,
            "is_notify_grace_period": False, "is_notify_expired": False}, game.user_id)
        if count > 1:
            rewards.extend({key: r[key] for key in ("content_type", "content_id", "amount")} for r in game.catalog.rows("m_payment_subscription_effects")
                           if r["group_id"] == definition["m_payment_subscription_effect_group_id"] and r["effect_type"] == 2)
    received, mailed = deliver(game, rewards)
    received.extend(mailed)
    from .gameplay_login import collect_bonuses, subscription_rewards
    purchased_bonuses = {reward["content_id"] for reward in rewards if reward["content_type"] in (185, 186)}
    if purchased_bonuses:
        collect_bonuses(game, selected=purchased_bonuses, received_items=received)
    if payment["payment_type"] == 4:
        subscription_rewards(game, selected={definition["id"]}, received_items=received)
    if currency:
        received.insert(0, {"content_type": 20, "content_id": 0, "amount": currency})
    if payment["payment_type"] == 1 and definition["available_type"] == 6:
        key = str(definition["id"])
        shop_data(game)["steps"][key] = shop_data(game)["steps"].get(key, 1) + 1
    counter["count"] += 1
    vip = vip_record(game)
    vip["total_point"] = number(vip["total_point"] + product["price"] * game.rules["local_vip_points_per_price"], 0, 2 ** 31 - 1)
    vip["updated_at"] = game.now.isoformat()
    grant_vip_titles(game)
    reconcile(game, settle_pending=False)
    record(game.progress, game.now, 50000)
    return {"received_items": received, "exec_response": None}


def grant_free(game, body):
    return purchase(game, number(body["m_payment_id"]))


def validate(game, body):
    validate_purchase(game, body["m_payment_id"])
    return {}


def subscription_verify(game, body):
    payment = game.catalog.get("m_payments", number(body["m_payment_id"]))
    if payment["payment_type"] != 4:
        raise GameplayError("Payment is not a subscription", 400, 4007)
    return {"received_items": purchase(game, payment["id"])["received_items"]}


def read_common(game, body):
    data = shop_data(game)
    for field, table, state_field in (("m_payment_lineup_ids", "m_payment_lineups", "read_common"), ("m_payment_subscription_ids", "m_payment_subscriptions", "read_subscriptions")):
        selected = identifiers(body[field], 1000)
        for identifier in selected:
            game.catalog.get(table, identifier)
        data[state_field] = sorted(set(data[state_field]) | set(selected))
    return {}


def read_recommend(game, body):
    selected = identifiers(body["m_payment_recommend_ids"], 1000)
    if set(selected) - {r["id"] for r in recommendations(game)}:
        raise GameplayError("Recommended product is not available")
    data = shop_data(game)
    data["read_recommends"] = sorted(set(data["read_recommends"]) | set(selected))
    return {}


def create_token(game, body):
    data = shop_data(game)
    data["tokens"] = {k: v for k, v in data["tokens"].items() if timestamp(v) > game.now}
    if len(data["tokens"]) >= 32:
        raise GameplayError("Too many pending local payment tokens")
    token = "local-" + secrets.token_hex(16)
    expiry = (game.now + timedelta(minutes=5)).isoformat()
    data["tokens"][token] = expiry
    return {"token": token, "expired_at": expiry}


def verify_local(game, body):
    if "signature" in body and body["signature"] not in ("", "local"):
        raise GameplayError("External store signatures are not accepted", 400, 4007)
    if "transaction_id" in body:
        try:
            prefix, identifier, token = body["transaction_id"].split(":", 2)
            if prefix != "local":
                raise ValueError()
            receipt = {"m_payment_id": int(identifier), "token": token}
        except (ValueError, TypeError, AttributeError) as error:
            raise GameplayError("Only locally issued payment receipts are accepted", 400, 4007) from error
    else:
        try:
            receipt = decode_json(body["data"].encode("utf-8"))
        except (ValueError, TypeError, AttributeError) as error:
            raise GameplayError("Only locally issued payment receipts are accepted", 400, 4007) from error
    if not isinstance(receipt, dict) or set(receipt) != {"m_payment_id", "token"} or not isinstance(receipt["token"], str):
        raise GameplayError("Invalid local payment receipt", 400, 4007)
    identifier, token = number(receipt["m_payment_id"]), receipt["token"]
    if "purchase_token" in body and body["purchase_token"] != token:
        raise GameplayError("Local purchase token does not match the receipt", 400, 4007)
    data = shop_data(game)
    if token in data["receipts"]:
        saved = data["receipts"][token]
        if saved["payment_id"] != identifier:
            raise GameplayError("Local receipt was reused for another product")
        game.payment_receipt_token = token
        return copy.deepcopy(saved.get("response") or saved["result"])
    if token not in data["tokens"] or timestamp(data["tokens"][token]) <= game.now:
        raise GameplayError("Local payment token is missing or expired")
    result = purchase(game, identifier)
    data["tokens"].pop(token)
    data["receipts"][token] = {"payment_id": identifier, "result": copy.deepcopy(result)}
    game.payment_receipt_token = token
    while len(data["receipts"]) > 128:
        data["receipts"].pop(next(iter(data["receipts"])))
    return result


def capture_payment_response(game, response, *, refresh_expiry=False):
    if not game.payment_receipt_token:
        return
    receipts = shop_data(game)["receipts"]
    receipt = receipts[game.payment_receipt_token]
    if "response" not in receipt or refresh_expiry:
        receipt["response"] = copy.deepcopy(response)
    while len(json.dumps(receipts, ensure_ascii=False).encode("utf-8")) > 512 * 1024:
        if len(receipts) == 1:
            raise GameplayError("Local purchase response exceeds the receipt limit", 413, 4130)
        receipts.pop(next(iter(receipts)))


def serial(game, body):
    code = text_argument(body["serial_code"], 128).strip().upper()
    data = shop_data(game)
    reward = game.rules["local_serial_codes"].get(code)
    if reward is None:
        return {"status": 1, "input_disabled": 0, "message": "", "error_message": "Unknown local code.", "rewards": []}
    if code in data["serial_claims"]:
        return {"status": 1, "input_disabled": 0, "message": "", "error_message": "This code was already redeemed.", "rewards": []}
    received, mailed = deliver(game, reward)
    data["serial_claims"].append(code)
    return {"status": 0, "input_disabled": 0, "message": "Local rewards received.", "error_message": "",
            "rewards": [{**r, "additional_info": None, "origin_content": None} for r in received + mailed]}


def vip_exchange(game, body):
    kind = number(body["m_vip_exchange_type"], 1, 2)
    counter = ensure_vip_weekly_counters(game)[kind]
    vip = vip_record(game)
    rank = max(r["rank"] for r in game.catalog.rows("m_vip_ranks") if r["total_point"] <= vip["total_point"])
    definition = next((r for r in game.catalog.rows("m_vip_exchanges") if r["rank"] == rank and r["type"] == kind), None)
    if definition is None:
        raise GameplayError("VIP exchange definition is missing", 503, 5036)
    key = f"weekly:{vip_week_start(game.now).isoformat()}:{kind}"
    claims = shop_data(game)["vip_claims"]
    if not counter["amount"]:
        return {"acquire_contents": []}
    consume(game, [{"content_type": 10, "content_id": 0, "amount": definition["cost_amount"]}])
    rewards = [{k: r[k] for k in ("content_type", "content_id", "amount")} for r in game.catalog.rows("m_vip_exchange_contents")
               if r["group_id"] == definition["m_vip_exchange_content_group_id"]]
    received, mailed = deliver(game, rewards)
    claims[key] = True
    counter["amount"] = 0
    return {"acquire_contents": received + mailed}


SHOP_ROUTES = {
    "api/payment/lineup": route("POST", "", listing),
    "api/payment/lineup/recommend": route("GET", "", lambda g, b: {"available_recommends": recommend_view(g)}),
    "api/payment/read/common": route("POST", "m_payment_lineup_ids m_payment_subscription_ids", read_common),
    "api/payment/read/recommend": route("POST", "m_payment_recommend_ids", read_recommend),
    "api/payment/grant/free": route("POST", "m_payment_id", grant_free),
    "api/payment/validate": route("POST", "m_payment_id", validate),
    "api/payment-subscription/verify": route("POST", "m_payment_id", subscription_verify),
    "api/charge-center/create-token": route("POST", "", create_token),
    "api/payment/dmm/verify": route("POST", "data signature purchase_token", verify_local),
    "api/payment/googleplay/verify": route("POST", "data signature", verify_local),
    "api/payment/appstore/verify": route("POST", "transaction_id", verify_local),
    "api/license/get-view": route("GET", "", lambda g, b: {"view_text": "Local simulation. No real payments or external billing services.", "view_url": ""}),
    "api/serial/confirm": route("POST", "serial_code", serial),
    "api/vip/weekly-reward/exchange": route("POST", "m_vip_exchange_type", vip_exchange),
}
