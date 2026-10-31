from __future__ import annotations

import copy
import random

from .gameplay_catalog import GameplayError, identifiers, number
from .gameplay_nether_configuration import frozen_nether
from .gameplay_economy import consume
from .gameplay_records import find, put, rows
from .gameplay_statistics import record as statistic
from .gameplay_nether_state import POINT_FIELDS, add_item, ensure, floor_key, point_view, record, reveal_routes, run, selected_floor, state


@frozen_nether
def code_rows(game):
    return rows(game.state, "t_nether_codes")


@frozen_nether
def offer_codes(game, count=1):
    data, current = run(game), ensure(game)
    data["code_draws"] += count
    if current["m_nether_code_ids"]:
        return
    candidates = [row["id"] for row in game.catalog.rows("m_nether_codes") if row["m_nether_id"] == current["m_nether_id"]
                  and not any(owned["m_nether_code_id"] == row["id"] and owned["amount"] > 0 for owned in code_rows(game))]
    rng = random.Random(f"nether-code:{data['id']}:{data['code_roll']}")
    data["code_roll"] += 1
    current["m_nether_code_ids"] = rng.sample(candidates, min(3, len(candidates)))
    if not candidates:
        data["code_draws"] = 0


@frozen_nether
def add_code(game, identifier):
    definition = game.catalog.get("m_nether_codes", number(identifier))
    current = ensure(game)
    if definition["m_nether_id"] != current["m_nether_id"]:
        raise GameplayError("Nether code belongs to another expedition")
    existing = find(game.state, "t_nether_codes", m_nether_code_id=identifier)
    amount = existing["amount"] if existing else 0
    if amount:
        raise GameplayError("Nether code is already owned in this expedition")
    owned = sum(row["amount"] > 0 for row in code_rows(game))
    if not amount and owned >= point_view(game)["max_nether_code"]:
        raise GameplayError("Nether code capacity is full")
    result = put(game.state, "t_nether_codes", {"m_nether_code_id": identifier}, {
        "m_nether_id": current["m_nether_id"], "m_nether_map_id": current["m_nether_map_id"], "amount": amount + 1}, game.user_id)
    statistic(game.progress, game.now, 120001, 1, definition["category"], identifier, mode="max")
    return result


@frozen_nether
def random_code(game, excluded=0):
    current, data = ensure(game), run(game)
    owned = {row["m_nether_code_id"]: row["amount"] for row in code_rows(game)}
    full = sum(value > 0 for value in owned.values()) >= point_view(game)["max_nether_code"]
    candidates = [row["id"] for row in game.catalog.rows("m_nether_codes") if row["m_nether_id"] == current["m_nether_id"]
                  and row["id"] != excluded and not owned.get(row["id"], 0) and not full]
    if not candidates:
        raise GameplayError("No eligible Nether code can be granted")
    rng = random.Random(f"nether-event-code:{data['id']}:{current['floor_level']}:{current['floor_index']}")
    return add_code(game, rng.choice(candidates))


@frozen_nether
def fix_code(game, body):
    current = record(game, body["m_nether_id"], body["m_nether_map_id"])
    data = run(game)
    identifier, removed = number(body["m_nether_code_id"]), number(body["remove_m_nether_code_id"], 0)
    if identifier not in current["m_nether_code_ids"]:
        raise GameplayError("Nether code was not offered")
    old = None
    if removed:
        old = find(game.state, "t_nether_codes", m_nether_code_id=removed)
        if old is None or old["amount"] <= 0 or removed == identifier:
            raise GameplayError("Nether replacement code is not owned")
        old["amount"] = 0
    result = add_code(game, identifier)
    current["m_nether_code_ids"] = []
    data["code_draws"] = max(0, data["code_draws"] - 1)
    if data["code_draws"]:
        offer_codes(game, 0)
    return {"t_nether_code": copy.deepcopy(result), "remove_t_nether_code": copy.deepcopy(old)}


@frozen_nether
def reload_code(game, body):
    current = ensure(game)
    run(game)
    if not current["m_nether_code_ids"] or current["code_reload"] >= point_view(game)["max_code_reload"]:
        raise GameplayError("No Nether code redraw is available")
    current["m_nether_code_ids"] = []
    current["code_reload"] += 1
    offer_codes(game, 0)
    return {"t_nether_point": point_view(game)}


@frozen_nether
def grant_local(game, kind, identifier, amount):
    number(amount, 0, 2 ** 31 - 1)
    current, data = ensure(game), run(game)
    if not amount or kind == 0:
        return None
    if kind in (30, 31):
        add_item(game, identifier, amount)
    elif kind == 160:
        if amount != 1:
            raise GameplayError("Nether event code amount is unsupported", 503, 5036)
        return add_code(game, identifier) if identifier else random_code(game)
    elif kind in (165, 166):
        key = "nether_gold" if kind == 165 else "treasure_key"
        current[key] = number(current[key] + amount, 0, 2 ** 31 - 1)
    elif 161 <= kind <= 164:
        key = POINT_FIELDS[kind - 161]
        state(game)["points"][key] = number(state(game)["points"][key] + amount, 0, 2 ** 31 - 1)
    else:
        data["rewards"].append({"content_type": kind, "content_id": identifier, "amount": amount})
    return None


@frozen_nether
def erosion(game, amount):
    current = ensure(game)
    modifier = 0
    categories = {}
    effects = []
    for owned in code_rows(game):
        if not owned["amount"]:
            continue
        definition = game.catalog.get("m_nether_codes", owned["m_nether_code_id"])
        categories[definition["category"]] = categories.get(definition["category"], 0) + owned["amount"]
        effects.append(definition)
    effects.extend(definition for definition in game.catalog.rows("m_nether_code_category_skills")
                   if definition["m_nether_id"] == current["m_nether_id"]
                   and categories.get(definition["category"], 0) >= definition["counter"])
    for definition in effects:
        if definition["effect_type"] in (6, 7):
            modifier += definition["effect_parameter_1"] * (1 if definition["effect_type"] == 6 else -1)
    current["erosion_point"] = min(100, max(0, current["erosion_point"] + amount + modifier))


@frozen_nether
def apply_event_effect(game, kind, amount):
    data, current = run(game), ensure(game)
    if kind == 0:
        return
    if kind in (1, 2):
        sign = 1 if kind == 1 else -1
        data["hp"] = {key: min(1000, max(0, value + sign * amount)) for key, value in data["hp"].items()}
    elif kind in (3, 4):
        erosion(game, amount * (1 if kind == 3 else -1))
    elif kind in (5, 6):
        key = "nether_gold" if kind == 5 else "treasure_key"
        if current[key] < amount:
            raise GameplayError("Insufficient Nether event resources")
        current[key] -= amount
    elif kind == 7:
        return
    elif kind == 8:
        data["event_battle"] = amount
    else:
        raise GameplayError("Nether event effect is not supported", 503, 5036)


@frozen_nether
def resolve_floor(game, floor):
    current, data = ensure(game), run(game)
    identity = floor_key(floor)
    if identity not in data["resolved"]:
        data["resolved"].append(identity)
        data["cleared_count"] += 1
        score = game.rules["nether_score_per_floor"]
        data["base_score"] = number(data["base_score"] + score, 0, 2 ** 31 - 1)
        data["bonus_score"] = number(data["bonus_score"] + score * (data["score_multiplier"] - 1), 0, 2 ** 31 - 1)
    current["max_cleared_floor_level"] = max(current["max_cleared_floor_level"], floor["floor_level"])
    current["max_floor_level"] = max(current["max_floor_level"], floor["floor_level"])
    maximum = game.catalog.get("m_nether_maps", current["m_nether_map_id"])["max_floor_num"]
    current["status"] = 8 if floor["floor_level"] >= maximum else 3 if floor["floor_level"] % 10 == 0 else 2
    if floor["floor_level"] % 10 == 0:
        current["continuance_floor_level"] = floor["floor_level"]
    statistic(game.progress, game.now, 120000, current["max_cleared_floor_level"], mode="max")
    if not any(data["hp"].values()):
        current["status"] = 7


@frozen_nether
def update_event(game, body):
    from .gameplay_nether_state import hp_view

    current, data, floor = selected_floor(game, body)
    if (current["floor_level"], current["floor_index"]) != (floor["floor_level"], floor["floor_index"]):
        raise GameplayError("Enter the Nether event floor before selecting an option")
    if floor_key(floor) in data["resolved"]:
        return {"t_nether_characters": hp_view(game), "t_nether_map_floor": None, "t_nether_code": None}
    definition = game.catalog.get("m_nether_map_floors", floor["m_nether_map_floor_id"])
    if definition["type"] not in (4, 5, 7):
        raise GameplayError("Selected Nether floor is not an event")
    event = game.catalog.get("m_nether_floor_events", floor["extend_id"])
    choice = number(body["select_number"], 1, 4)
    part_id = event[f"m_nether_floor_event_part_id_{choice}"]
    if not part_id:
        raise GameplayError("Nether event option does not exist")
    part = game.catalog.get("m_nether_floor_event_parts", part_id)
    code_id = number(body["m_nether_code_id"], 0)
    replacing = any(part[f"target_type_{index}"] == 7 for index in range(1, 4))
    replacing_reward = part["content_type"] == 160 and code_id > 0
    if replacing or replacing_reward:
        owned = find(game.state, "t_nether_codes", m_nether_code_id=code_id)
        if owned is None or owned["amount"] <= 0:
            raise GameplayError("Select an owned Nether code to transform")
        owned["amount"] = 0
    elif code_id:
        raise GameplayError("This Nether event does not transform a code", 400, 4007)
    for index in range(1, 4):
        apply_event_effect(game, part[f"target_type_{index}"], part[f"select_parameter_{index}"])
    code = grant_local(game, part["content_type"], part["content_id"], part["amount"])
    if replacing:
        code = random_code(game, code_id)
    current["latest_event_number"] = choice
    if data.get("event_battle"):
        target = game.catalog.get("m_nether_map_floors", data["event_battle"])
        candidates = [row for row in game.catalog.rows("m_nether_floor_battles") if row["m_nether_map_floor_id"] == target["id"]]
        if not candidates:
            raise GameplayError("Nether event battle floor has no battle binding", 503, 5036)
        previous_id = floor["m_nether_map_floor_id"]
        for following in data["floors"]:
            if following["floor_level"] == floor["floor_level"] + 1:
                following["m_nether_map_floor_prev_ids"] = [target["id"] if identifier == previous_id else identifier
                                                          for identifier in following["m_nether_map_floor_prev_ids"]]
        floor.update(m_nether_map_floor_id=target["id"], extend_id=min(candidates, key=lambda row: row["id"])["id"])
        current["m_nether_map_floor_id"] = target["id"]
        data["event_battle"] = 0
    else:
        resolve_floor(game, floor)
    return {"t_nether_characters": hp_view(game), "t_nether_map_floor": reveal_routes(game), "t_nether_code": copy.deepcopy(code)}


@frozen_nether
def update_shop(game, body):
    current, data = ensure(game), run(game)
    floor = next((row for row in data["floors"] if row["floor_level"] == current["floor_level"] and row["floor_index"] == current["floor_index"]), None)
    if floor is None or game.catalog.get("m_nether_map_floors", floor["m_nether_map_floor_id"])["type"] != 6:
        raise GameplayError("No Nether shop is open")
    requested = body["shop_item_amount"]
    if not isinstance(requested, list) or not 1 <= len(requested) <= 100:
        raise GameplayError("Invalid Nether shop basket", 400, 4007)
    seen = set()
    for selected in requested:
        if not isinstance(selected, dict) or set(selected) != {"m_nether_floor_shop_content_id", "amount"}:
            raise GameplayError("Invalid Nether shop item", 400, 4007)
        identifier, amount = number(selected["m_nether_floor_shop_content_id"]), number(selected["amount"], 1, 100)
        if identifier in seen:
            raise GameplayError("Duplicate Nether shop item", 400, 4007)
        seen.add(identifier)
        item = game.catalog.get("m_nether_floor_shop_contents", identifier)
        if item["m_nether_floor_shop_id"] != floor["extend_id"]:
            raise GameplayError("Item does not belong to the current Nether shop")
        key = f"{floor_key(floor)}:{identifier}"
        bought = data["shop_purchases"].get(key, 0)
        if bought + amount > 1:
            raise GameplayError("Nether shop item has already been purchased")
        currency = {165: "nether_gold", 166: "treasure_key"}.get(item["consume_content_type"])
        if currency is None:
            raise GameplayError("Unsupported Nether shop currency", 503, 5036)
        cost = item["consume_amount"] * amount
        if current[currency] < cost:
            raise GameplayError("Insufficient Nether shop currency")
        current[currency] -= cost
        grant_local(game, item["content_type"], item["content_id"], item["amount"] * amount)
        data["shop_purchases"][key] = bought + amount
    return {"shop_histories": shop_history(game)}


@frozen_nether
def shop_history(game):
    data = state(game)["run"]
    if not data:
        return []
    current = ensure(game)
    prefix = f"{current['floor_level']}:{current['floor_index']}:"
    return [{"m_nether_floor_shop_content_id": int(key.rsplit(":", 1)[1]), "amount": amount}
            for key, amount in data["shop_purchases"].items() if key.startswith(prefix)]


@frozen_nether
def upgrade_sphere(game, body):
    from .gameplay_access import require_feature

    require_feature(game, 5)
    current = record(game, body["m_nether_id"], body["m_nether_map_id"])
    definition = game.catalog.get("m_nether_spheres", number(body["m_nether_sphere_id"]))
    board_id = number(body["m_nether_sphere_board_id"])
    board = game.catalog.get("m_nether_sphere_boards", board_id)
    if definition["m_nether_sphere_board_id"] != board_id:
        raise GameplayError("Nether sphere belongs to another board")
    data = state(game)
    existing = next((row for row in data["spheres"] if row["m_nether_sphere_board_id"] == board_id and row["type"] == definition["type"]), None)
    level = existing["level"] if existing else 0
    if number(body["current_level"], 0) != level or definition["level"] != level + 1:
        raise GameplayError("Nether sphere level changed or cannot be skipped")
    acquired = {(row["m_nether_sphere_board_id"], row["type"]): row["level"] for row in data["spheres"]}
    if definition["type"] == 10:
        branches = [row for row in game.catalog.rows("m_nether_spheres")
                    if row["m_nether_sphere_board_id"] == board_id and row["type"] != 10]
        if any(acquired.get((board_id, row["type"]), 0) < row["level"] for row in branches):
            raise GameplayError("Complete the Nether sphere board before unlocking its center")
    elif level == 0:
        previous = [row for row in game.catalog.rows("m_nether_sphere_boards")
                    if row["display_order"] < board["display_order"]]
        if previous and not acquired.get((max(previous, key=lambda row: row["display_order"])["id"], 10), 0):
            raise GameplayError("Unlock the previous Nether sphere board center first")
    costs = [row for row in game.catalog.rows("m_nether_sphere_costs") if row["m_nether_sphere_id"] == definition["id"]]
    ordinary = []
    for cost in costs:
        kind, amount = cost["content_type"], cost["require_amount"]
        if 161 <= kind <= 164:
            field = POINT_FIELDS[kind - 161]
            if data["points"][field] < amount:
                raise GameplayError("Insufficient Nether research points")
            data["points"][field] -= amount
        else:
            ordinary.append({"content_type": kind, "content_id": cost["content_id"], "amount": amount})
    consume(game, ordinary)
    if existing is None:
        existing = {"id": max((row["id"] for row in data["spheres"]), default=0) + 1, "user_id": game.user_id,
                    "m_nether_id": current["m_nether_id"], "m_nether_sphere_board_id": board_id,
                    "type": definition["type"], "created_at": game.now.isoformat()}
        data["spheres"].append(existing)
    existing.update(level=definition["level"], updated_at=game.now.isoformat())
    return {"t_nether_sphere_level": copy.deepcopy(existing), "t_nether_point": point_view(game)}
