from __future__ import annotations

import copy

from .gameplay_catalog import GameplayError, available, identifiers, number
from .gameplay_content import weighted_choice
from .gameplay_economy import consume
from .gameplay_receipts import feature_data
from .gameplay_records import find, grant, put, rows
from .gameplay_routes import route
from .gameplay_mileage import add_gacha_mileage
from .gameplay_gacha_tokens import expire_tokens


def gacha_state(game):
    data = feature_data(game, "gacha")
    if not data:
        data.update(periods={}, wishes={}, pity={})
    if not {"periods", "wishes", "pity"} <= set(data) or set(data) - {"periods", "wishes", "pity", "tutorial", "expiry_receipts"} or any(not isinstance(v, dict) for v in data.values()):
        raise GameplayError("Stored gacha state is invalid")
    return data


def active_group(game, identifier):
    group = game.catalog.get("m_gacha_groups", identifier)
    if not available(group, game.now, "begin_at", "end_at"):
        raise GameplayError("Gacha group is outside its availability period")
    return group


def gacha_record(game, definition):
    record = find(game.state, "t_gachas", m_gacha_id=definition["id"])
    if record is None:
        record = put(game.state, "t_gachas", {"m_gacha_id": definition["id"]}, {
            "m_gacha_group_id": definition["m_gacha_group_id"], "step": 1, "step_count": 0, "exec_count": 0})
    if definition["limit_reset_type"] == 1:
        periods = gacha_state(game)["periods"]
        day = game.now.date().isoformat()
        if periods.get(str(definition["id"])) != day:
            periods[str(definition["id"])] = day
            record.update(step=1, step_count=0, exec_count=0)
    elif definition["limit_reset_type"] != 0:
        raise GameplayError("Gacha reset period is not supported", 503, 5036)
    return record


def wishlist_subjects(game, group):
    numbers = {s["lottery_no"] for s in game.catalog.rows("m_gacha_steps") if s["m_gacha_group_id"] == group["id"]}
    contents = {r["content_no"] for r in game.catalog.rows("m_gacha_lotteries") if r["m_gacha_step_lottery_no"] in numbers and r["rarity"] == 3 and r["weight"] > 0}
    return sorted({r["content_id"] for r in game.catalog.rows("m_gacha_contents") if r["m_gacha_lottery_content_no"] in contents and r["content_type"] == 40})


def wishlist_view(game, group):
    if not group["wishlist_enabled"]:
        return {"wishlist": None, "wishlist_subject": None}
    wishes = gacha_state(game)["wishes"].get(str(group["id"]), [])
    identifiers(wishes, max(1, group["wishlist_slot_num"]))
    return {"wishlist": wishes + [0] * (group["wishlist_slot_num"] - len(wishes)), "wishlist_subject": wishlist_subjects(game, group)}


def tokens(game):
    return [{"m_gacha_token_id": r["m_gacha_token_id"], "token_num": r["token_num"]} for r in rows(game.state, "t_gacha_tokens")]


def listing(game, body):
    gacha_state(game)
    converted = expire_tokens(game)
    groups = []
    for group in sorted(game.catalog.rows("m_gacha_groups"), key=lambda r: (r["display_order"], r["id"])):
        if not available(group, game.now, "begin_at", "end_at"):
            continue
        entries = []
        for definition in sorted(game.catalog.rows("m_gachas"), key=lambda r: (r["display_order"], r["id"])):
            if definition["m_gacha_group_id"] == group["id"] and not definition["payment_id"]:
                record = gacha_record(game, definition)
                entries.append({"m_gacha_id": definition["id"], "exec_count": record["exec_count"]})
        if entries:
            groups.append({"m_gacha_group_id": group["id"], "gacha_list": entries, **wishlist_view(game, group)})
    return {"gacha_group_list": groups, "gacha_token_list": tokens(game), "converted_gacha_tokens": converted}


def wishlist_get(game, body):
    return wishlist_view(game, active_group(game, number(body["m_gacha_group_id"])))


def wishlist_update(game, body):
    group = active_group(game, number(body["m_gacha_group_id"]))
    if not group["wishlist_enabled"]:
        raise GameplayError("This gacha group has no wishlist")
    values = body["wishlist"]
    if not isinstance(values, list) or len(values) > group["wishlist_slot_num"]:
        raise GameplayError("Invalid wishlist size", 400, 4007)
    values = [number(value, 0) for value in values]
    selected = identifiers([value for value in values if value], group["wishlist_slot_num"])
    if set(selected) - set(wishlist_subjects(game, group)):
        raise GameplayError("Wishlist contains a character outside the pool")
    gacha_state(game)["wishes"][str(group["id"])] = selected
    return wishlist_view(game, group)


def execute(game, body):
    definition = game.catalog.get("m_gachas", number(body["m_gacha_id"]))
    group = active_group(game, definition["m_gacha_group_id"])
    if definition["type"] == 2 and gacha_state(game).get("tutorial", {}).get("pending"):
        raise GameplayError("Confirm the pending tutorial draw before using this pool")
    count = number(body["exec_count"], 1, game.rules["gacha_max_draws"])
    ticket = number(body["use_gacha_ticket"], 0, 1)
    draw_count = count * definition["lottery_num"]
    if not 1 <= draw_count <= game.rules["gacha_max_draws"]:
        raise GameplayError("Gacha draw batch exceeds its limit", 400, 4007)
    if definition["payment_id"]:
        raise GameplayError("Direct payment gacha requires a local entitlement")
    record = gacha_record(game, definition)
    if definition["limit_num"] > 0 and record["exec_count"] + count > definition["limit_num"]:
        raise GameplayError("Gacha execution limit reached")
    steps = sorted([r for r in game.catalog.rows("m_gacha_steps") if r["m_gacha_group_id"] == group["id"]], key=lambda r: r["step"])
    if not steps:
        raise GameplayError("Gacha step table is missing", 503, 5036)
    if ticket:
        cost = {"content_type": definition["gacha_ticket_content_type"], "content_id": definition["gacha_ticket_content_id"], "amount": definition["gacha_ticket_amount"] * count}
        if cost["content_type"] <= 0 or cost["amount"] <= 0:
            raise GameplayError("This gacha does not accept tickets")
    else:
        cost = {"content_type": definition["content_type"], "content_id": definition["content_id"], "amount": definition["use_amount"] * count}
    consume(game, [cost])
    data = gacha_state(game)
    wishes = set(data["wishes"].get(str(group["id"]), []))
    pity = number(data["pity"].get(str(group["id"]), 0), 0, 2 ** 31 - 1)
    content_groups = {}
    for content in game.catalog.rows("m_gacha_contents"):
        content_groups.setdefault(content["m_gacha_lottery_content_no"], []).append(content)
    received = []
    for _ in range(count):
        step = next((r for r in steps if r["step"] == record["step"]), steps[0])
        for draw in range(definition["lottery_num"]):
            suffix = "10_" if definition["lottery_num"] >= 10 and (draw + 1) % 10 == 0 else "_"
            rarity_weights = [{"rarity": rarity, "weight": step[f"weight{suffix}{name}"]} for rarity, name in ((1, "r"), (2, "sr"), (3, "ssr"))]
            if not any(r["weight"] for r in rarity_weights):
                rarity_weights = [{"rarity": rarity, "weight": step[f"weight_{name}"]} for rarity, name in ((1, "r"), (2, "sr"), (3, "ssr"))]
            pity_limit = game.rules["gacha_extra_pity_draws"]
            rarity = 3 if pity_limit and pity + 1 >= pity_limit else weighted_choice(rarity_weights)["rarity"]
            candidates = [dict(r) for r in game.catalog.rows("m_gacha_lotteries") if r["m_gacha_step_lottery_no"] == step["lottery_no"] and r["rarity"] == rarity]
            if wishes and group["wishlist_weight"] > 0:
                for candidate in candidates:
                    if any(c["content_type"] == 40 and c["content_id"] in wishes for c in content_groups.get(candidate["content_no"], [])):
                        candidate["weight"] = group["wishlist_weight"]
            selected = weighted_choice(candidates)
            contents = content_groups.get(selected["content_no"], [])
            if not contents:
                raise GameplayError("Gacha content group is empty", 503, 5036)
            for content in contents:
                original = {"content_type": content["content_type"], "content_id": content["content_id"], "amount": content["obtain_num"]}
                for reward in grant(game.state, [original], game.catalog, game.user_id, game.now, game.progress):
                    received.append({**reward, **({"origin_content": dict(original)} if reward != original else {})})
            pity = 0 if rarity == 3 else pity + 1
        record["exec_count"] += 1
        record["step_count"] += 1
        if step["limit_num"] > 0 and record["step_count"] >= step["limit_num"]:
            following = [r for r in steps if r["step"] > step["step"]]
            record["step"] = following[0]["step"] if following else steps[0]["step"]
            record["step_count"] = 0
    data["pity"][str(group["id"])] = pity
    if definition["m_gacha_token_id"] and definition["token_num"]:
        grant(game.state, [{"content_type": 170, "content_id": definition["m_gacha_token_id"], "amount": definition["token_num"] * count}], game.catalog, game.user_id, game.now, game.progress)
    mileage = add_gacha_mileage(game, definition["m_gacha_mileage_reward_group_no"], draw_count)
    return {"received_content_list": received, "gacha_token_list": tokens(game), "mileage_list": mileage or None, "dropped_data": None}


GACHA_ROUTES = {
    "api/gacha": route("GET", "", listing),
    "api/gacha/exec": route("POST", "m_gacha_id exec_count use_gacha_ticket", execute),
    "api/gacha/wishlist/{0}": route("GET", "m_gacha_group_id", wishlist_get, "m_gacha_group_id"),
    "api/gacha/wishlist/update": route("POST", "m_gacha_group_id wishlist", wishlist_update),
}
