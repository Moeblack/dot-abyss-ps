from __future__ import annotations

from datetime import timedelta

from .gameplay_catalog import GameplayError, available, identifiers
from .gameplay_delivery import deliver
from .gameplay_login_state import bonus_entry, login_data
from .gameplay_records import rows
from .gameplay_routes import route
from .gameplay_shop import limited_login_view, listing as shop_listing
from .gameplay_stamina import recovery_day
from .gameplay_wallet import timestamp


def empty_bonus_list():
    return {"daily": [], "special": [], "special_condition": []}


def eligible(game, definition, entry):
    if not available(definition, game.now, "begin_at", "end_at"):
        return False
    if definition["condition_type"] != 0 or definition["login_type"] != 1:
        raise GameplayError("Login bonus condition requires a new binding", 503, 5036)
    if definition["bonus_type"] in (91, 92) and not entry["paid_at"]:
        return False
    return not entry["limit_at"] or timestamp(entry["limit_at"]) > game.now


def login_day(game):
    return "reset05:" + recovery_day(game.now).isoformat()


def already_collected(game, values):
    return bool(set(values) & {login_day(game), recovery_day(game.now).isoformat(), game.now.date().isoformat()})


def collect_bonuses(game, *, selected=None, received_items=None):
    data = login_data(game.progress)
    result = empty_bonus_list()
    today = login_day(game)
    for definition in sorted(game.catalog.rows("m_login_bonuses"), key=lambda r: (r["display_priority"], r["display_order"], r["id"])):
        if selected is not None and definition["id"] not in selected:
            continue
        if not available(definition, game.now, "begin_at", "end_at"):
            continue
        entry = bonus_entry(game.progress, definition["id"])
        if not eligible(game, definition, entry) or already_collected(game, [entry["last_day"]]):
            continue
        sheets = {r["sheet_no"]: r for r in game.catalog.rows("m_login_bonus_sheets") if r["m_login_bonus_id"] == definition["id"]}
        sheet = sheets.get(entry["sheet_no"])
        if sheet is None:
            raise GameplayError("Login bonus sheet is missing", 503, 5036)
        if entry["index"] >= sheet["limit_point"]:
            if sheet["next_sheet_no"] == 0:
                continue
            sheet = sheets.get(sheet["next_sheet_no"])
            if sheet is None:
                raise GameplayError("Next login bonus sheet is missing", 503, 5036)
            entry.update(sheet_no=sheet["sheet_no"], index=0)
        index = entry["index"] + 1
        rewards = [{k: r[k] for k in ("content_type", "content_id", "amount")} for r in game.catalog.rows("m_login_bonus_items")
                   if r["m_login_bonus_sheet_id"] == sheet["id"] and r["index"] == index]
        if not rewards:
            raise GameplayError("Login bonus rewards are missing", 503, 5036)
        received, mailed = deliver(game, rewards)
        if received_items is not None:
            received_items.extend(received + mailed)
        entry.update(index=index, login_total=entry["login_total"] + 1, last_day=today, last_received_at=game.now.isoformat())
        if definition["expire_days"] and not entry["limit_at"]:
            entry["limit_at"] = (game.now + timedelta(days=definition["expire_days"])).isoformat()
        view = {"m_login_bonus_id": definition["id"], "m_login_bonus_sheet_id": sheet["id"], "index": index}
        category = {1: "daily", 2: "special", 91: "special_condition", 92: "special_condition"}.get(definition["bonus_type"])
        if category is None:
            raise GameplayError("Unknown login bonus category", 503, 5036)
        result[category].append(view)
        data["history"].append({"day": today, "category": category, **view})
    data["history"] = data["history"][-2000:]
    return result


def subscription_rewards(game, *, selected=None, received_items=None):
    claims = login_data(game.progress)["subscription_days"]
    day = login_day(game)
    for benefit in rows(game.state, "t_payment_subscription_benefits"):
        if selected is not None and benefit["m_payment_subscription_id"] not in selected:
            continue
        if timestamp(benefit["expire_at"]) <= game.now:
            continue
        definition = game.catalog.get("m_payment_subscriptions", benefit["m_payment_subscription_id"])
        effects = [r for r in game.catalog.rows("m_payment_subscription_effects") if r["group_id"] == definition["m_payment_subscription_effect_group_id"] and r["effect_type"] == 1]
        for effect in effects:
            key = f"{definition['id']}:{benefit['continue_count']}:{effect['id']}"
            dates = claims.setdefault(key, [])
            if not isinstance(dates, list) or len(dates) != len(set(dates)):
                raise GameplayError("Stored subscription reward receipts are invalid")
            if not already_collected(game, dates) and (effect["effect_days"] == 0 or len(dates) < effect["effect_days"]):
                received, mailed = deliver(game, [{k: effect[k] for k in ("content_type", "content_id", "amount")}])
                if received_items is not None:
                    received_items.extend(received + mailed)
                dates.append(day)


def expire_items(game):
    expired, soon = [], []
    for item in rows(game.state, "t_limited_items"):
        expiry = timestamp(item["expire_at"])
        if item["amount"] and expiry <= game.now:
            definition = game.catalog.get("m_limited_items", item["m_limited_item_id"])
            contents = [{"content_type": r["content_type"], "content_id": r["content_id"], "amount": r["amount"] * item["amount"]}
                        for r in game.catalog.rows("m_limited_item_convert_contents") if r["group_id"] == definition["m_limited_item_convert_content_group_id"]]
            received, mailed = deliver(game, contents)
            expired.append({"m_limited_item_id": item["m_limited_item_id"], "amount": item["amount"], "converted_contents": received + mailed})
            item["amount"] = 0
        elif item["amount"] and expiry <= game.now + timedelta(days=1):
            soon.append(item["id"])
    return expired, soon


def collect(game):
    expired, soon = expire_items(game)
    bonuses = collect_bonuses(game)
    subscription_rewards(game)
    friends = len(game.community.related_ids()) if game.community is not None else 0
    data = login_data(game.progress)
    awarded = 0
    today = game.now.date().isoformat()
    if data["friend_day"] != today:
        awarded = min(friends, game.rules["friend_daily_limit"]) * game.rules["friend_daily_points"]
        if awarded:
            deliver(game, [{"content_type": 53, "content_id": 0, "amount": awarded}])
        data["friend_day"] = today
    shop = shop_listing(game, {})
    has_new = any(r["is_new"] for entries in shop["categories"].values() for r in entries)
    return {"login_bonus_list": bonuses, "friend_point_data": {"follow_count": friends, "get_friend_point": awarded},
            "expired_limited_items": expired, "expires_soon_t_limited_item_ids": soon, "has_new_shop": int(has_new),
            "t_login_thirty_days_bonuses": limited_login_view(game, 92), "t_login_limited_bonuses": limited_login_view(game, 91)}


def history(game, body):
    selected = set(identifiers(body["m_login_bonus_ids"], 1000))
    for identifier in selected:
        game.catalog.get("m_login_bonuses", identifier)
    result = empty_bonus_list()
    latest = {}
    for entry in login_data(game.progress)["history"]:
        if entry["m_login_bonus_id"] in selected:
            latest[entry["m_login_bonus_id"]] = entry
    for entry in latest.values():
        result[entry["category"]].append({k: entry[k] for k in ("m_login_bonus_id", "m_login_bonus_sheet_id", "index")})
    return {"login_bonus_histories": result, "t_login_thirty_days_bonuses": limited_login_view(game, 92), "t_login_limited_bonuses": limited_login_view(game, 91)}


LOGIN_ROUTES = {"api/mypage/login-bonus/history": route("POST", "m_login_bonus_ids", history)}
