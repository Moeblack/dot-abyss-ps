from __future__ import annotations

import secrets
from datetime import timedelta

from .gameplay_catalog import GameplayError, number
from .gameplay_records import find, put, rows
from .gameplay_statistics import record_grant

EQUIPMENT = {70: ("weapon", "weapons"), 80: ("armor", "armors"), 90: ("accessory", "accessories")}
BALANCES = {10: "free_virtual_money", 50: "gold", 51: "soul", 52: "disaster_coin", 53: "friend_point", 110: "character_exp"}


def weighted_choice(values, key="weight"):
    weighted = [(row, number(row[key], 0, 2 ** 63 - 1)) for row in values]
    total = sum(weight for _, weight in weighted)
    if total <= 0:
        raise GameplayError("Lottery has no positive weights", 503, 5036)
    pick = secrets.randbelow(total)
    for row, weight in weighted:
        if pick < weight:
            return row
        pick -= weight
    raise AssertionError("Weighted choice exhausted its range")


def one(values, name):
    values = list(values)
    if len(values) != 1:
        raise GameplayError(name + " must have exactly one master match", 503, 5036)
    return values[0]


def create_character(state, identifier, catalog, user_id, now, progress=None):
    master = catalog.get("m_characters", identifier)
    existing = find(state, "t_characters", m_character_id=identifier)
    if existing:
        core = one((r for r in catalog.rows("m_items") if r["type"] == 10 and r["value"] == identifier), "Duplicate character core")
        return apply_rewards(state, [{"content_type": 30, "content_id": core["id"], "amount": 1}], catalog, user_id, now, progress)
    skins = [one((r for r in catalog.rows("m_character_skins") if r["m_character_id"] == identifier and r["type"] == kind and r["is_default"]), "Default character skin") for kind in (1, 2)]
    weapon = one((r for r in catalog.rows("m_weapon_skins") if r["weapon_type"] == master["weapon_type"] and r["is_default"] and not r["is_paid"] and r["m_character_id"] == 0), "Default weapon skin")
    for skin in skins:
        put(state, "t_character_skins", {"m_character_skin_id": skin["id"]}, {}, user_id)
    put(state, "t_weapon_skins", {"m_weapon_skin_id": weapon["id"]}, {}, user_id)
    put(state, "t_characters", {"m_character_id": identifier}, {
        "level": 1, "exp": 0, "limit_break_count": 0, "is_favorite": 0, "is_new": 1, "is_unread": 1,
        "love_point": 0, "bond_point": 0, "limit_break_core_point": 0, "intimacy_point": 0,
        "battle_m_character_skin_id": skins[0]["id"], "tavern_m_character_skin_id": skins[1]["id"],
        "battle_m_weapon_skin_id": weapon["id"], "created_at": now.strftime("%Y-%m-%d %H:%M:%S")}, user_id)
    record_grant(progress, now, 40, identifier, 1, catalog)
    return [{"content_type": 40, "content_id": identifier, "amount": 1}]


def roll_enchantments(catalog, kind, identifier, *, appraisal_only=False, excluded=()):
    singular, plural = EQUIPMENT[kind]
    groups = [r for r in catalog.rows(f"m_{singular}_enchantment_groups") if r[f"m_{singular}_id"] == identifier
              and (not appraisal_only or r["is_appraisal"])]
    result, selected = [], set(excluded)
    for group in sorted(groups, key=lambda r: (r["order"], r["id"])):
        options = [r for r in catalog.rows("m_enchantment_groups") if r["enchantment_group_no"] == group["enchantment_group_no"]
                   and (r["can_duplication"] or r["enchantment_no"] not in selected)]
        chosen = weighted_choice(options)
        selected.add(chosen["enchantment_no"])
        definitions = [r for r in catalog.rows("m_enchantments") if r["enchantment_no"] == chosen["enchantment_no"]]
        if not definitions:
            raise GameplayError("Equipment enchantment definition is missing", 503, 5036)
        for definition in definitions:
            ability = weighted_choice([r for r in catalog.rows("m_enchantment_abilities") if r["enchantment_ability_no"] == definition["enchantment_ability_no"]])
            result.append({f"m_{singular}_enchantment_group_id": group["id"], "enchantment_group_no": group["enchantment_group_no"],
                           "enchantment_no": chosen["enchantment_no"], "enchantment_ability_no": definition["enchantment_ability_no"],
                           "m_ability_effect_id": ability["m_ability_effect_id"], "level": ability["level"], "is_appraised": int(not group["is_appraisal"])})
    return result


def allocate_id(state, table, progress=None):
    minimum = max((r["id"] for r in rows(state, table)), default=0) + 1
    if progress is None:
        return minimum
    data = progress["features"].setdefault("equipment", {"version": 1, "data": {}})["data"]
    cursors = data.setdefault("next_ids", {})
    if not isinstance(cursors, dict):
        raise GameplayError("Stored record counters are invalid")
    value = max(minimum, number(cursors.get(table, minimum)))
    cursors[table] = number(value + 1)
    return value


def create_equipment(state, kind, identifier, catalog, user_id, progress=None):
    singular, plural = EQUIPMENT[kind]
    master = catalog.get("m_" + plural, identifier)
    if sum(len(rows(state, "t_" + names[1])) for names in EQUIPMENT.values()) >= 100:
        raise GameplayError("Equipment inventory is full")
    enchants = roll_enchantments(catalog, kind, identifier)
    minimum = number(master["min_parameter"], 0, 2 ** 32 - 1)
    maximum = number(master["max_parameter"], minimum, 2 ** 32 - 1)
    interval = number(master["lottery_interval"], 1, 2 ** 32 - 1)
    steps = (maximum - minimum) // interval
    gear = put(state, "t_" + plural, {"id": allocate_id(state, "t_" + plural, progress)},
               {f"m_{singular}_id": identifier, "up_count": minimum + secrets.randbelow(steps + 1) * interval, "quality_type": 1,
                "is_lock": 0, "is_appraisal": int(any(not r["is_appraised"] for r in enchants)), "appraisal_count": 0}, user_id)
    for enchant in enchants:
        put(state, f"t_{singular}_enchantments", {"id": allocate_id(state, f"t_{singular}_enchantments", progress)},
            {f"t_{singular}_id": gear["id"], **enchant}, user_id)


def apply_rewards(state, rewards, catalog, user_id, now, progress=None):
    received = []
    skin_change = False
    status = rows(state, "t_statuses")[0]
    for reward in rewards:
        kind, identifier, amount = reward["content_type"], reward["content_id"], reward["amount"]
        skin_change = skin_change or kind in (40, 140, 141)
        number(kind, 1, 2 ** 31 - 1)
        number(identifier, 0)
        number(amount, 1, 2 ** 31 - 1)
        if kind in BALANCES:
            field = BALANCES[kind]
            status[field] = number(status[field] + amount, 0, 2 ** 31 - 1)
        elif kind == 20:
            from .gameplay_wallet import grant_paid
            grant_paid(state, user_id, now, amount)
        elif kind == 30:
            master = catalog.get("m_items", identifier)
            item = put(state, "t_items", {"m_item_id": identifier}, {})
            maximum = master.get("possession_limit", 2 ** 31 - 1) or 2 ** 31 - 1
            if item.get("amount", 0) + amount > maximum:
                raise GameplayError("Item possession limit exceeded")
            item["amount"] = number(item.get("amount", 0) + amount, 0, 2 ** 31 - 1)
        elif kind == 33:
            from .gameplay_wallet import timestamp
            definition = catalog.get("m_limited_items", identifier)
            expiry = timestamp(definition["expire_at"]) if definition["expire_at"] else now + timedelta(days=definition["expire_days"])
            if expiry <= now:
                raise GameplayError("Limited item has expired")
            existing = sum(r["amount"] for r in rows(state, "t_limited_items") if r["m_limited_item_id"] == identifier and timestamp(r["expire_at"]) > now)
            if existing + amount > (definition["possession_limit"] or 2 ** 31 - 1):
                raise GameplayError("Limited item possession limit exceeded")
            item = put(state, "t_limited_items", {"m_limited_item_id": identifier, "expire_at": expiry.isoformat()}, {})
            item["amount"] = number(item.get("amount", 0) + amount, 0, 2 ** 31 - 1)
        elif kind in (54, 56):
            table, field, master = ("t_event_currencies", "m_event_currency_id", "m_event_currencies") if kind == 54 else ("t_event_boss_tickets", "m_event_boss_ticket_id", "m_event_boss_tickets")
            catalog.get(master, identifier)
            row = put(state, table, {field: identifier}, {})
            row["amount"] = number(row.get("amount", 0) + amount, 0, 2 ** 31 - 1)
        elif kind == 120:
            from .gameplay_stamina import STAMINA_LIMIT, STAMINA_TYPE, settle_stamina

            row = settle_stamina(state, catalog, user_id, now) if identifier == STAMINA_TYPE else find(state, "t_timed_parameters", type=identifier)
            if row is None:
                raise GameplayError("Timed reward resource is not initialized", 503, 5036)
            row["amount"] = min(STAMINA_LIMIT if identifier == STAMINA_TYPE else 2 ** 31 - 1, row["amount"] + amount)
        elif kind == 40:
            if amount > 1000:
                raise GameplayError("Character grant batch is too large", 400, 4007)
            for _ in range(amount):
                received.extend(create_character(state, identifier, catalog, user_id, now, progress))
            continue
        elif kind == 41:
            character = find(state, "t_characters", m_character_id=identifier)
            if character is None:
                raise GameplayError("Character core recipient is not owned")
            character["limit_break_core_point"] = number(character["limit_break_core_point"] + amount, 0, 2 ** 31 - 1)
        elif kind in EQUIPMENT:
            if amount > 100:
                raise GameplayError("Equipment grant batch is too large", 400, 4007)
            for _ in range(amount):
                create_equipment(state, kind, identifier, catalog, user_id, progress)
            record_grant(progress, now, kind, identifier, amount, catalog)
        elif kind in (60, 131, 140, 141, 190):
            table, field, master_table = {
                60: ("t_mana_gems", "m_mana_gem_id", "m_mana_gems"),
                131: ("t_plans", "m_plan_id", "m_plans"),
                140: ("t_character_skins", "m_character_skin_id", "m_character_skins"),
                141: ("t_weapon_skins", "m_weapon_skin_id", "m_weapon_skins"),
                190: ("t_degrees", "m_degree_id", "m_degrees"),
            }[kind]
            catalog.get(master_table, identifier)
            if find(state, table, **{field: identifier}) is None:
                put(state, table, {field: identifier}, {"step": 0} if kind == 131 else {}, None if kind == 131 else user_id)
        elif kind == 111:
            from .gameplay_stamina import settle_stamina, STAMINA_LIMIT
            stamina = settle_stamina(state, catalog, user_id, now)
            old_rank = status["user_rank"]
            status["player_exp"] = number(status["player_exp"] + amount, 0, 2 ** 31 - 1)
            ranks = sorted(catalog.rows("m_user_ranks"), key=lambda r: r["rank"])
            current = max((r for r in ranks if r["total_exp"] <= status["player_exp"]), key=lambda r: r["rank"])
            status["user_rank"] = current["rank"]
            raised = [r for r in ranks if old_rank < r["rank"] <= current["rank"]]
            if raised:
                stamina["capacity"] = current["stamina_max"]
                stamina["amount"] = min(STAMINA_LIMIT, stamina["amount"] + sum(r["stamina_max"] for r in raised))
                if stamina["amount"] >= stamina["capacity"]:
                    stamina["last_recovery_at"] = now.isoformat()
        elif kind == 170:
            catalog.get("m_gacha_tokens", identifier)
            row = put(state, "t_gacha_tokens", {"m_gacha_token_id": identifier}, {})
            row["token_num"] = number(row.get("token_num", 0) + amount, 0, 2 ** 31 - 1)
        elif kind in (180, 181):
            from .gameplay_pass_state import pass_state
            catalog.get("m_mission_passes", identifier)
            if progress is None:
                raise GameplayError("Pass rewards require persistent gameplay progress")
            record = pass_state(progress, identifier)
            if kind == 180:
                record["premium"] = 1
            else:
                record["points"] = number(record["points"] + amount, 0, 2 ** 31 - 1)
        elif kind == 45:
            catalog.get("m_visitors", identifier)
            put(state, "t_visitors", {"m_visitor_id": identifier}, {"is_temporary": 0}, user_id)
        elif kind in (185, 186):
            from .gameplay_login_state import grant_limited_login
            if progress is None or amount != 1:
                raise GameplayError("Limited login rewards require one persistent entitlement")
            grant_limited_login(progress, catalog, identifier, now)
        else:
            raise GameplayError("Reward type has not been implemented: " + str(kind), 501, 5010)
        received.append(dict(reward))
    if skin_change and progress is not None:
        from types import SimpleNamespace
        from .gameplay_skin_bonds import reconcile_skin_bonds
        reconcile_skin_bonds(SimpleNamespace(state=state, progress=progress, catalog=catalog))
    return received
