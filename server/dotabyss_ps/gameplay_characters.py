from __future__ import annotations

import copy

from .gameplay_catalog import GameplayError, identifiers, number
from .gameplay_economy import boolean_argument, consume, owned
from .gameplay_library import intimacy_level
from .gameplay_receipts import feature_data
from .gameplay_records import find, grant, put, rows
from .gameplay_routes import route


def favorite(game, body):
    character = owned(game, "t_characters", body["t_character_id"])
    character["is_favorite"] = int(boolean_argument(body["is_favorite"]))
    return {}


def favorites_reset(game, body):
    selected = [owned(game, "t_characters", value) for value in identifiers(body["t_character_ids"], 1000)]
    for character in selected:
        character["is_favorite"] = 0
    return {}


def character_skin(game, body):
    character = owned(game, "t_characters", body["t_character_id"])
    identifier = number(body["m_character_skin_id"])
    skin = game.catalog.get("m_character_skins", identifier)
    if skin["m_character_id"] != character["m_character_id"] or find(game.state, "t_character_skins", m_character_skin_id=identifier) is None:
        raise GameplayError("Character skin is not owned by this character")
    fields = {1: "battle_m_character_skin_id", 2: "tavern_m_character_skin_id"}
    if skin["type"] not in fields:
        raise GameplayError("Unsupported character skin category", 400, 4007)
    character[fields[skin["type"]]] = identifier
    return {}


def weapon_skin(game, body):
    character = owned(game, "t_characters", body["t_character_id"])
    identifier = number(body["m_weapon_skin_id"])
    skin = game.catalog.get("m_weapon_skins", identifier)
    master = game.catalog.get("m_characters", character["m_character_id"])
    if find(game.state, "t_weapon_skins", m_weapon_skin_id=identifier) is None or skin["weapon_type"] != master["weapon_type"] or skin["m_character_id"] not in (0, master["id"]):
        raise GameplayError("Weapon skin is not owned or is incompatible")
    character["battle_m_weapon_skin_id"] = identifier
    return {}


def level_costs(game, current, target):
    costs = [r for r in game.catalog.rows("m_character_levels") if current < r["level"] <= target]
    if {r["level"] for r in costs} != set(range(current + 1, target + 1)):
        raise GameplayError("Character level cost table is incomplete", 503, 5036)
    return [{"content_type": r["content_type"], "content_id": r["content_id"], "amount": r["require_amount"]} for r in costs]


def level_up(game, body):
    character = owned(game, "t_characters", body["t_character_id"])
    count = number(body["exec_count"], 1, game.rules["character_level_cap"])
    target = character["level"] + count
    if target > game.rules["character_level_cap"]:
        raise GameplayError("Character level cap exceeded")
    consume(game, level_costs(game, character["level"], target))
    character["level"] = target
    return {}


def level_reset(game, body):
    character = owned(game, "t_characters", body["t_character_id"])
    if character["level"] <= 1:
        return {}
    percent = game.rules["level_reset_refund_percent"]
    refunds = [{**r, "amount": r["amount"] * percent // 100} for r in level_costs(game, 1, character["level"])]
    grant(game.state, [r for r in refunds if r["amount"]], game.catalog, game.user_id, game.now, game.progress)
    character.update(level=1, exp=0)
    return {}


def ability_release_limit(game, character, releases, maximum):
    for release in releases:
        if release["condition_type"] != 2:
            raise GameplayError("Ability release condition is not bound", 503, 5036)
        if intimacy_level(game, character) < release["condition_value"]:
            maximum = min(maximum, release["level"] - 1)
    return maximum


def ability_release_policy(game):
    data = feature_data(game, "character")
    policy = data.get("ability_releases")
    if policy is None:
        policy = {"version": 1, "retained_levels": {}}
        data["ability_releases"] = policy
        return policy, True
    if not isinstance(policy, dict) or set(policy) != {"version", "retained_levels"} or type(policy["version"]) is not int or policy["version"] != 1:
        raise GameplayError("Stored ability release policy is invalid")
    retained = policy["retained_levels"]
    if not isinstance(retained, dict):
        raise GameplayError("Stored retained ability levels are invalid")
    for key, level in retained.items():
        if not isinstance(key, str) or len(key.split(":")) != 3 or any(not part.isascii() or not part.isdecimal() or int(part) < 1 for part in key.split(":")):
            raise GameplayError("Stored retained ability identifier is invalid")
        number(level, 2, 100)
    return policy, False


def ensure_abilities(game):
    if not {"m_character_abilities", "m_ability_details"} <= game.catalog.tables.keys():
        return
    maximums, releases, abilities = {}, {}, {}
    for detail in game.catalog.rows("m_ability_details"):
        maximums[detail["ability_no"]] = max(maximums.get(detail["ability_no"], 0), detail["level"])
    for release in game.catalog.rows("m_ability_releases"):
        releases.setdefault(release["m_character_ability_id"], []).append(release)
    for master in game.catalog.rows("m_character_abilities"):
        abilities.setdefault(master["m_character_id"], []).append(master)
    policy, migrate = ability_release_policy(game)
    for character in rows(game.state, "t_characters"):
        for master in abilities.get(character["m_character_id"], []):
            maximum = ability_release_limit(game, character, releases.get(master["id"], []), maximums.get(master["ability_no"], 0))
            record = find(game.state, "t_character_abilities", t_character_id=character["id"], m_character_ability_id=master["id"])
            if record is None:
                put(game.state, "t_character_abilities", {"t_character_id": character["id"], "m_character_ability_id": master["id"]},
                    {"level": 1 if maximum else 0, "possible_max_level": maximum}, game.user_id)
                continue
            key = f"{record['id']}:{character['id']}:{master['id']}"
            if migrate and record["level"] > max(1, maximum):
                policy["retained_levels"][key] = number(record["level"], 2, 100)
            maximum = max(maximum, policy["retained_levels"].get(key, 0))
            if record["level"] > max(1, maximum):
                raise GameplayError("Stored ability level exceeds its release limit")
            record["possible_max_level"] = maximum
            record["level"] = max(record["level"], 1) if maximum else 0


def order_list(value, fields):
    if not isinstance(value, list) or not 1 <= len(value) <= 100 or any(not isinstance(row, dict) or set(row) != set(fields.split()) for row in value):
        raise GameplayError("Invalid enhancement orders", 400, 4007)
    return value


def power_items(game, kinds):
    masters = {r["id"] for r in game.catalog.rows("m_items") if r["type"] in kinds}
    return [copy.deepcopy(r) for r in rows(game.state, "t_items") if r["m_item_id"] in masters]


def ability_level_up(game, body):
    character = owned(game, "t_characters", body["t_character_id"])
    master = game.catalog.get("m_characters", character["m_character_id"])
    changes, costs, seen = [], [], set()
    for order in order_list(body["orders"], "t_character_ability_id exec_count"):
        ability = owned(game, "t_character_abilities", order["t_character_ability_id"])
        if ability["t_character_id"] != character["id"] or ability["id"] in seen:
            raise GameplayError("Ability does not belong to this character or is duplicated")
        seen.add(ability["id"])
        target = ability["level"] + number(order["exec_count"], 1, 100)
        if target > ability["possible_max_level"]:
            raise GameplayError("Ability level cap exceeded")
        for level in range(ability["level"] + 1, target + 1):
            required = [r for r in game.catalog.rows("m_ability_level_up_costs") if r["element_type"] == master["element_type"] and r["party_position"] == master["party_position"] and r["level"] == level]
            if not required:
                raise GameplayError("Ability level costs are missing", 503, 5036)
            costs.extend({"content_type": r["content_type"], "content_id": r["content_id"], "amount": r["require_amount"]} for r in required)
        changes.append((ability, target))
    consume(game, costs)
    for ability, target in changes:
        ability["level"] = target
    return {"t_character_ability": copy.deepcopy(changes[-1][0]), "power_up_items": power_items(game, {20})}


def limit_break(game, body):
    character = owned(game, "t_characters", body["t_character_id"])
    count = number(body["exec_count"], 1, 100)
    master = game.catalog.get("m_characters", character["m_character_id"])
    rules = [r for r in game.catalog.rows("m_character_limit_break_defines") if r["rarity"] == master["rarity"]]
    cores = [r for r in game.catalog.rows("m_items") if r["type"] == 10 and r["value"] == master["id"]]
    if len(rules) != 1 or len(cores) != 1:
        raise GameplayError("Limit break rule or material is not uniquely bound", 503, 5036)
    rule = rules[0]
    if character["limit_break_count"] + count > rule["max_limit_break_count"]:
        raise GameplayError("Limit break cap exceeded")
    consume(game, [{"content_type": 30, "content_id": cores[0]["id"], "amount": rule["require_amount"] * count}])
    character["limit_break_count"] += count
    character["limit_break_core_point"] += rule["limit_break_core_point"] * count
    return {"power_up_items": power_items(game, {10})}


def add_points(game, body, destination, item_types, *, owned_item_ids=False):
    character = owned(game, "t_characters", body["t_character_id"])
    character_master = game.catalog.get("m_characters", character["m_character_id"])
    item_key = "t_item_id" if owned_item_ids else "m_item_id"
    costs, total, seen = [], 0, set()
    for request in order_list(body["use_items"], item_key + " amount"):
        identifier = number(request[item_key])
        if identifier in seen:
            raise GameplayError("Gift item is duplicated", 400, 4007)
        seen.add(identifier)
        if owned_item_ids:
            identifier = owned(game, "t_items", identifier)["m_item_id"]
        master = game.catalog.get("m_items", identifier)
        if master["type"] not in item_types:
            raise GameplayError("Item is not valid for this enhancement", 400, 4007)
        amount = number(request["amount"], 1, 1000000)
        value = master["value"]
        if master["type"] in (31, 141):
            if value != character_master["union_type"]:
                raise GameplayError("Gift does not match the character's union")
            value = game.rules["union_gift_points"]
        if value <= 0:
            raise GameplayError("Gift point value is not bound", 503, 5036)
        total += value * amount
        costs.append({"content_type": 30, "content_id": identifier, "amount": amount})
    old = character[destination]
    target = old + total
    if destination == "intimacy_point":
        maximum = max(r["total_point"] for r in game.catalog.rows("m_character_intimacy_levels"))
        if old >= maximum:
            raise GameplayError("Character intimacy is already at its cap")
        target = min(target, maximum)
    number(target, 0, 2 ** 31 - 1)
    consume(game, costs)
    character[destination] = target
    if destination == "intimacy_point":
        character["bond_point"] += sum(r["add_bond_point"] for r in game.catalog.rows("m_intimacy_bond_point_rewards") if old < r["intimacy_point"] <= target)
    return {}


def gift(game, body):
    return add_points(game, body, "intimacy_point", {140, 141})


def love_gift(game, body):
    return add_points(game, body, "love_point", {30, 31}, owned_item_ids=True)


def bond_points(game, body):
    return add_points(game, body, "bond_point", {40})


def enhance_parameters(game, body, resistance=False):
    character = owned(game, "t_characters", body["t_character_id"])
    master = game.catalog.get("m_characters", character["m_character_id"])
    table = "t_character_condition_resistances" if resistance else "t_character_bond_parameters"
    field = "limit_break_core_point" if resistance else "bond_point"
    if resistance:
        maximums = {r["up_parameter_type"]: r["level_max"] for r in game.catalog.rows("m_character_condition_resistance_levels") if r["rarity"] == master["rarity"]}
    else:
        allowed = {r["up_parameter_type"] for r in game.catalog.rows("m_character_bond_parameter_groups") if r["party_position"] == master["party_position"]}
        maximums = {kind: max(r["level"] for r in game.catalog.rows("m_character_bond_parameter_levels") if r["up_parameter_type"] == kind) for kind in allowed}
    changes, total, seen = [], 0, set()
    for order in order_list(body["orders"], "up_parameter_type " + field):
        kind, points = number(order["up_parameter_type"], 1, 2 ** 31 - 1), number(order[field], 1, 2 ** 31 - 1)
        if kind in seen or kind not in maximums:
            raise GameplayError("Invalid or duplicated parameter type", 400, 4007)
        seen.add(kind)
        row = find(game.state, table, t_character_id=character["id"], up_parameter_type=kind)
        target = (row["level"] if row else 0) + points
        if target > maximums[kind]:
            raise GameplayError("Character parameter cap exceeded")
        total += points
        changes.append((kind, target))
    if total > character[field]:
        raise GameplayError("Insufficient enhancement points", 409, 4093)
    character[field] -= total
    for kind, target in changes:
        put(game.state, table, {"t_character_id": character["id"], "up_parameter_type": kind}, {"level": target}, game.user_id)
    return {}


def reset_parameters(game, body, resistance=False):
    character = owned(game, "t_characters", body["t_character_id"])
    table = "t_character_condition_resistances" if resistance else "t_character_bond_parameters"
    field = "limit_break_core_point" if resistance else "bond_point"
    percent = game.rules["resistance_reset_refund_percent" if resistance else "bond_reset_refund_percent"]
    selected = [r for r in rows(game.state, table) if r["t_character_id"] == character["id"]]
    character[field] += sum(r["level"] for r in selected) * percent // 100
    for row in selected:
        row["level"] = 0
    return {}


CHARACTER_ROUTES = {
    "api/character/favorite/toggle": route("POST", "t_character_id is_favorite", favorite),
    "api/character/favorites/reset": route("POST", "t_character_ids", favorites_reset),
    "api/character/skin/set": route("POST", "t_character_id m_character_skin_id", character_skin),
    "api/character/weapon_skin/set": route("POST", "t_character_id m_weapon_skin_id", weapon_skin),
    "api/character/level/up": route("POST", "t_character_id exec_count", level_up),
    "api/character/level/reset": route("POST", "t_character_id", level_reset),
    "api/character/ability/level/up": route("POST", "t_character_id orders", ability_level_up),
    "api/character/limit-break": route("POST", "t_character_id exec_count", limit_break),
    "api/character/gift/give": route("POST", "t_character_id use_items", gift),
    "api/character/gift": route("POST", "t_character_id use_items", love_gift),
    "api/character/bond-point/add": route("POST", "t_character_id use_items", bond_points),
    "api/character/bond": route("POST", "t_character_id orders", enhance_parameters),
    "api/character/bond/reset": route("POST", "t_character_id", reset_parameters),
    "api/character/condition_resistance": route("POST", "t_character_id orders", lambda g, b: enhance_parameters(g, b, True)),
    "api/character/condition_resistance/reset": route("POST", "t_character_id", lambda g, b: reset_parameters(g, b, True)),
}
