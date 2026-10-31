from __future__ import annotations

from .gameplay_catalog import GameplayError, identifiers, number
from .gameplay_content import EQUIPMENT, allocate_id, roll_enchantments
from .gameplay_economy import consume, owned
from .gameplay_records import find, grant, put, rows
from .gameplay_routes import route
from .gameplay_mileage import add_equipment_mileage
from .gameplay_effects import research_effects, boosted
from .gameplay_receipts import feature_data


def appraisal_groups(game, singular, identifier):
    return {row["id"] for row in game.catalog.rows(f"m_{singular}_enchantment_groups")
            if row[f"m_{singular}_id"] == identifier and row["is_appraisal"]}


def update_appraisal_state(game, singular, gear):
    required = appraisal_groups(game, singular, gear[f"m_{singular}_id"])
    completed = set()
    pending = set()
    for row in rows(game.state, f"t_{singular}_enchantments"):
        if row[f"t_{singular}_id"] == gear["id"]:
            (completed if row["is_appraised"] else pending).add(row[f"m_{singular}_enchantment_group_id"])
    gear["is_appraisal"] = int(bool(required - (completed - pending)))


def ensure_equipment_appraisal(game):
    current = {}
    for singular, plural in EQUIPMENT.values():
        if f"m_{singular}_enchantment_groups" not in game.catalog.tables:
            continue
        for gear in rows(game.state, "t_" + plural):
            update_appraisal_state(game, singular, gear)
            current[(singular, gear["id"])] = gear
    data = feature_data(game, "equipment")
    version = data.get("appraisal_state_version")
    if type(version) is int and version == 1:
        return
    if version is not None:
        raise GameplayError("Stored appraisal state version is invalid")
    cached = [receipt["response"] for receipt in game.progress["operation_receipts"].values()]
    payments = game.progress["features"].get("shop", {}).get("data", {}).get("receipts", {})
    cached.extend(receipt[key] for receipt in payments.values() for key in ("response", "result") if key in receipt)
    while cached:
        value = cached.pop()
        if isinstance(value, dict):
            for singular, _ in EQUIPMENT.values():
                master_key = f"m_{singular}_id"
                if {"id", master_key, "is_appraisal", "appraisal_count", "up_count"} <= value.keys():
                    gear = current.get((singular, value["id"]))
                    if gear and gear[master_key] == value[master_key]:
                        value["is_appraisal"] = gear["is_appraisal"]
            cached.extend(value.values())
        elif isinstance(value, list):
            cached.extend(value)
    data["appraisal_state_version"] = 1


def selection(game, body):
    selected = []
    for kind, (singular, plural) in EQUIPMENT.items():
        for identifier in identifiers(body[f"t_{singular}_ids"], 1000):
            selected.append((kind, singular, plural, owned(game, "t_" + plural, identifier)))
    if not selected:
        raise GameplayError("No equipment was selected", 400, 4007)
    return selected


def lock_toggle(game, body):
    for _, _, _, gear in selection(game, body):
        gear["is_lock"] = 1 - gear["is_lock"]
    return {}


def disassemble(game, body):
    selected = selection(game, body)
    rewards = []
    mileage = {}
    for _, singular, plural, gear in selected:
        if gear["is_lock"]:
            raise GameplayError("Locked equipment cannot be disassembled")
        if any(row.get(f"t_{singular}_id") == gear["id"] for table, container in game.state["records"].items()
               if table not in {f"t_{singular}_enchantments"} for row in container["up"]):
            raise GameplayError("Equipped equipment cannot be disassembled")
        master = game.catalog.get("m_" + plural, gear[f"m_{singular}_id"])
        mileage[singular] = mileage.get(singular, 0) + boosted(master["mileage"], sum(r["param_1"] for r in research_effects(game, 700)))
        rewards.append({"content_type": 50, "content_id": 0, "amount": max(1, master["rank"]) * game.rules["disassembly_gold_per_rank"]})
    grant(game.state, rewards, game.catalog, game.user_id, game.now, game.progress)
    for kind, singular, plural, gear in selected:
        rows(game.state, "t_" + plural).remove(gear)
        records = rows(game.state, f"t_{singular}_enchantments")
        records[:] = [r for r in records if r[f"t_{singular}_id"] != gear["id"]]
        pools = rows(game.state, "t_appraisal_result_pools")
        pools[:] = [r for r in pools if (r["content_type"], r["t_equipment_id"]) != (kind, gear["id"])]
    add_equipment_mileage(game, mileage)
    return {}


def selected_equipment(game, body):
    kind = number(body["content_type"], 1, 2 ** 31 - 1)
    if kind not in EQUIPMENT:
        raise GameplayError("Unsupported equipment content type", 400, 4007)
    singular, plural = EQUIPMENT[kind]
    gear = owned(game, "t_" + plural, body["t_equipment_id"])
    return kind, singular, plural, gear


def appraisal_lottery(game, body):
    kind, singular, _, gear = selected_equipment(game, body)
    replaceable = appraisal_groups(game, singular, gear[f"m_{singular}_id"])
    retained = {row["enchantment_no"] for row in rows(game.state, f"t_{singular}_enchantments")
                if row[f"t_{singular}_id"] == gear["id"] and row[f"m_{singular}_enchantment_group_id"] not in replaceable}
    rolled = roll_enchantments(game.catalog, kind, gear[f"m_{singular}_id"], appraisal_only=True, excluded=retained)
    if not rolled:
        raise GameplayError("This equipment has no appraisal slots")
    tickets = [r for r in game.catalog.rows("m_items") if r["type"] == 110]
    if not tickets:
        raise GameplayError("Appraisal ticket is missing", 503, 5036)
    ticket = min(tickets, key=lambda r: r["id"])
    consume(game, [{"content_type": 30, "content_id": ticket["id"], "amount": game.rules["appraisal_ticket_cost"]},
                   {"content_type": 50, "content_id": 0, "amount": game.rules["appraisal_gold_cost"]}])
    pools = rows(game.state, "t_appraisal_result_pools")
    pools[:] = [r for r in pools if (r["content_type"], r["t_equipment_id"]) != (kind, gear["id"])]
    for value in rolled:
        data = {k: v for k, v in value.items() if k not in (f"m_{singular}_enchantment_group_id", "is_appraised")}
        data.update(content_type=kind, t_equipment_id=gear["id"], m_equipment_enchantment_group_id=value[f"m_{singular}_enchantment_group_id"])
        put(game.state, "t_appraisal_result_pools", {"id": allocate_id(game.state, "t_appraisal_result_pools", game.progress)}, data, game.user_id)
    gear["appraisal_count"] += 1
    return {}


def appraisal_execute(game, body):
    kind, singular, _, gear = selected_equipment(game, body)
    pools = rows(game.state, "t_appraisal_result_pools")
    selected = [r for r in pools if (r["content_type"], r["t_equipment_id"]) == (kind, gear["id"])]
    groups = {r["m_equipment_enchantment_group_id"] for r in selected}
    enchants = rows(game.state, f"t_{singular}_enchantments")
    enchants[:] = [r for r in enchants if not (r[f"t_{singular}_id"] == gear["id"] and r[f"m_{singular}_enchantment_group_id"] in groups)]
    for pool in selected:
        values = {k: pool[k] for k in ("enchantment_group_no", "enchantment_no", "enchantment_ability_no", "m_ability_effect_id", "level")}
        values.update({f"t_{singular}_id": gear["id"], f"m_{singular}_enchantment_group_id": pool["m_equipment_enchantment_group_id"], "is_appraised": 1})
        put(game.state, f"t_{singular}_enchantments", {"id": allocate_id(game.state, f"t_{singular}_enchantments", game.progress)}, values, game.user_id)
    pools[:] = [r for r in pools if r not in selected]
    update_appraisal_state(game, singular, gear)
    return {}


EQUIPMENT_ROUTES = {
    "api/equipment/lock/toggle": route("POST", "t_weapon_ids t_armor_ids t_accessory_ids", lock_toggle),
    "api/equipment/disassembly/execute": route("POST", "t_weapon_ids t_armor_ids t_accessory_ids", disassemble),
    "api/equipment/appraisal/lottery": route("POST", "content_type t_equipment_id", appraisal_lottery),
    "api/equipment/appraisal/execute": route("POST", "content_type t_equipment_id", appraisal_execute),
}
