from __future__ import annotations

import copy

from .gameplay_catalog import GameplayError
from .gameplay_event_records import EVENT_ROW_FIELDS

OPTIONAL_ROW_FIELDS = {
    **EVENT_ROW_FIELDS,
    "t_coaching_quests": {"long": "id user_id m_coaching_quest_id", "int": "status is_exempted"},
    "t_recollection_quests": {"long": "id user_id m_recollection_quest_id", "int": "status"},
    "t_tryout_quests": {"long": "id user_id m_tryout_quest_id", "int": "status high_score"},
    "t_union_requests": {"long": "id m_union_type_id", "int": "last_party_no stage"},
    "t_idle_exploration_encounters": {"long": "id user_id m_idle_exploration_map_area_id m_encounter_quest_id", "int": "encount_type encount_party_no", "string": "begin_at end_at"},
    "t_idle_exploration_encounter_skip_configs": {"long": "id user_id m_encounter_quest_id", "int": "skip_extra_drop_probability skip_additional_drop_rate", "long_ids": "skip_party_characters"},
    "t_disaster_statuses": {"long": "id user_id", "int": "disaster_level disaster_point disaster_level_record_high skippable_disaster_level skip_extra_drop_probability skip_additional_drop_rate", "string": "last_appear_at last_disaster_defeat_at", "disaster_reports": "reports"},
    "t_disaster_bosses": {"long": "id user_id m_disaster_boss_id", "int": "is_new"},
    "t_disaster_slots": {"long": "id user_id m_disaster_boss_id m_disaster_quest_id", "int": "slot is_special_boss disaster_level", "string": "last_appear_at"},
    "t_disaster_parties": {"long": "id user_id m_disaster_quest_id m_disaster_boss_id t_exploration_party_id", "int": "disaster_type"},
    "t_disaster_party_defensive_units": {"long": "id user_id t_disaster_party_id t_defensive_unit_id", "int": "placement_line placement_index"},
    "t_defensive_units": {"long": "id user_id m_defensive_unit_id", "int": "level"},
    "t_nether_items": {"long": "id user_id m_nether_id m_nether_map_id m_item_id", "int": "amount"},
    "t_nether_lock_items": {"long": "id user_id m_nether_id m_nether_map_id m_item_id", "int": "amount"},
    "t_nether_codes": {"long": "user_id m_nether_id m_nether_map_id m_nether_code_id", "int": "amount"},
    "t_limited_items": {"long": "id m_limited_item_id", "int": "amount", "string": "expire_at"},
    "t_event_currencies": {"long": "m_event_currency_id", "int": "amount"},
    "t_payment_dmm_currencies": {"long": "id user_id", "int": "price acquire_amount rest_amount", "string": "store_product_id paid_at valid_at created_at updated_at"},
    "t_payment_subscription_benefits": {"long": "id user_id m_payment_subscription_id", "int": "continue_count", "string": "expire_at", "bool": "is_notify_grace_period is_notify_expired"},
    "t_vip_ranks": {"long": "id user_id", "int": "total_point", "string": "updated_at created_at"},
    "t_level_synchros": {"long": "id user_id", "int": "grade level"},
    "t_visitors": {"long": "id user_id m_visitor_id", "int": "is_temporary"},
    "t_tavern_shops": {"long": "id user_id", "string": "name", "int": "work_level card_level reward_level total_play_count max_score"},
    "t_idle_exploration_maps": {"long": "id user_id m_idle_exploration_map_id"},
    "t_idle_exploration_map_areas": {"long": "id user_id m_idle_exploration_map_area_id", "int": "difficulty"},
    "t_idle_exploration_parties": {"long": "id", "int": "party_no icon_1 icon_2 icon_3", "string": "name"},
    "t_idle_exploration_party_characters": {"long": "id user_id t_character_id t_weapon_id", "int": "party_no position_no"},
    "t_idle_exploration_party_relations": {"long": "id m_idle_exploration_map_area_id", "int": "party_no drop_guaranty_count", "string": "exploring_stop_at last_drop_at last_encount_at"},
    "t_novel_characters": {"long": "id user_id m_novel_character_id", "int": "status is_unread"},
    "t_novel_character_skins": {"long": "id user_id m_novel_character_skin_id", "int": "is_unread"},
    "t_novel_others": {"long": "id m_novel_other_id", "int": "is_unread"},
    "t_novel_events": {"long": "id user_id", "ulong": "m_novel_event_id", "int": "is_unread"},
    "t_nethers": {"long": "id user_id m_nether_id m_nether_map_id m_nether_map_floor_id",
                  "int": "status floor_level floor_index max_floor_level max_cleared_floor_level continuance_floor_level code_reload erosion_point nether_gold treasure_key hidden_count strong_enemy_count used_ticket used_ticket_day latest_event_number",
                  "long_ids": "m_nether_code_ids"},
    "t_building_skills": {"long": "id user_id m_building_id", "int": "skill_type skill_level checked_skill_level"},
    "t_weapons": {"long": "id user_id", "ulong": "m_weapon_id", "uint": "up_count", "ushort": "quality_type", "int": "is_lock is_appraisal appraisal_count"},
    "t_armors": {"long": "id user_id", "ulong": "m_armor_id", "uint": "up_count", "ushort": "quality_type", "int": "is_lock is_appraisal appraisal_count"},
    "t_accessories": {"long": "id user_id", "ulong": "m_accessory_id", "uint": "up_count", "ushort": "quality_type", "int": "is_lock is_appraisal appraisal_count"},
    "t_gachas": {"long": "id m_gacha_group_id m_gacha_id", "int": "step step_count exec_count"},
    "t_gacha_tokens": {"long": "id m_gacha_token_id", "int": "token_num"},
    "t_gacha_mileages": {"long": "id", "int": "m_gacha_mileage_reward_group_no mileage"},
    "t_equipment_mileages": {"long": "id user_id", "ulong": "weapon_mileage armor_mileage accessory_mileage loop_weapon_mileage loop_armor_mileage loop_accessory_mileage"},
    "t_appraisal_result_pools": {"long": "id user_id t_equipment_id m_equipment_enchantment_group_id enchantment_group_no enchantment_no enchantment_ability_no m_ability_effect_id", "int": "content_type level"},
    "t_configs": {"long": "id user_id", "int": "type value"},
    "t_feature_tips": {"long": "id user_id m_feature_tip_id", "int": "read_version"},
    "t_character_abilities": {"long": "id user_id t_character_id m_character_ability_id", "int": "level possible_max_level"},
    "t_character_bond_parameters": {"long": "id user_id t_character_id", "int": "up_parameter_type level"},
    "t_character_condition_resistances": {"long": "id user_id t_character_id", "int": "up_parameter_type level"},
    "t_feature_tutorials": {"long": "id user_id", "int": "completed_flags"},
    "t_feature_locks": {"long": "id m_feature_lock_id", "bool": "is_unlocked"},
    "t_exploration_parties": {"long": "id user_id", "int": "restriction_value party_no icon_1 icon_2 icon_3", "string": "name updated_at"},
    "t_preset_exploration_parties": {"long": "id user_id", "int": "preset_no party_no icon", "string": "name"},
    "t_exploration_party_characters": {"long": "id user_id t_exploration_party_id t_character_id t_weapon_id t_armor_id t_accessory_id",
                                       "int": "party_type party_order party_position position_no"},
    "t_exploration_party_mana_gems": {"long": "id user_id t_exploration_party_id m_mana_gem_id", "int": "slot_no"},
    "t_chapter_quests": {"long": "id m_chapter_id m_chapter_area_id m_chapter_quest_id", "int": "sequence"},
    "t_items": {"long": "id m_item_id", "int": "amount"},
    "t_novel_homes": {"long": "id user_id m_novel_home_id"},
    "t_timed_parameters": {"long": "id user_id", "int": "type amount capacity recovery duration_type recovery_duration", "string": "last_recovery_at"},
    "t_stamina_recoveries": {"long": "id user_id", "int": "recovery_count", "string": "last_recovery_at"},
    "t_special_missions": {"long": "id user_id m_special_mission_id", "string": "start_at end_at completed_at created_at updated_at"},
}
for _singular in ("weapon", "armor", "accessory"):
    OPTIONAL_ROW_FIELDS[f"t_{_singular}_enchantments"] = {
        "long": f"id user_id t_{_singular}_id m_{_singular}_enchantment_group_id enchantment_group_no enchantment_no enchantment_ability_no m_ability_effect_id",
        "int": "level is_appraised",
    }
for _plural, _singular in (("non_player_characters", "non_player_character"), ("enemies", "enemy"), ("worlds", "world")):
    OPTIONAL_ROW_FIELDS["t_dictionary_" + _plural] = {"long": "id user_id", "ulong": f"m_dictionary_{_singular}_id", "int": "is_unread"}


def rows(state, table):
    return state["records"][table]["up"]


def find(state, table, **keys):
    return next((row for row in rows(state, table) if all(row.get(k) == v for k, v in keys.items())), None)


def identity_field(table):
    return {"t_event_currencies": "m_event_currency_id", "t_nether_codes": "m_nether_code_id",
            "t_events": "m_event_id", "t_event_disaster_quests": "m_event_disaster_quest_id"}.get(table, "id")


def put(state, table, keys, values, user_id=None):
    row = find(state, table, **keys)
    if row is None:
        field = identity_field(table)
        row = {field: max((r[field] for r in rows(state, table)), default=0) + 1, **keys}
        if user_id is not None:
            row["user_id"] = user_id
        rows(state, table).append(row)
    row.update(values)
    return row


def delta(before, after):
    result = {}
    for table, container in after.items():
        field = identity_field(table)
        old = {row[field]: row for row in before[table]["up"]}
        new = {row[field]: row for row in container["up"]}
        up = [copy.deepcopy(row) for identity, row in new.items() if old.get(identity) != row]
        down = sorted(set(old) - set(new))
        if up or down:
            result[table] = {"up": up, "down": down}
    return result


def validate_optional_records(state):
    if len(rows(state, "t_nethers")) > 1 or any(row["status"] not in (1, 2, 3, 5, 6, 7, 8) for row in rows(state, "t_nethers")):
        raise ValueError("Stored Nether state is invalid")
    if any(r["rest_amount"] > r["acquire_amount"] for r in rows(state, "t_payment_dmm_currencies")):
        raise ValueError("Stored paid currency exceeds its issued amount")
    if len(rows(state, "t_vip_ranks")) > 1:
        raise ValueError("Stored VIP rank must be a singleton")
    if len(rows(state, "t_level_synchros")) > 1 or any(r["grade"] < 1 or r["level"] < 1 for r in rows(state, "t_level_synchros")):
        raise ValueError("Stored synchronization grade or level is invalid")
    if any(r["is_temporary"] not in (0, 1) for r in rows(state, "t_visitors")):
        raise ValueError("Stored visitor flags are invalid")
    if len(rows(state, "t_tavern_shops")) > 1 or any(r["work_level"] < 1 or r["card_level"] < 1 or r["reward_level"] < 1 for r in rows(state, "t_tavern_shops")):
        raise ValueError("Stored tavern shop levels are invalid")
    for table in ("t_novel_characters", "t_novel_character_skins", "t_novel_others", "t_novel_events", "t_dictionary_non_player_characters", "t_dictionary_enemies", "t_dictionary_worlds"):
        if any(r["is_unread"] not in (0, 1) for r in rows(state, table)):
            raise ValueError("Stored library read flag is invalid")
    buildings = {r["m_building_id"] for r in rows(state, "t_buildings")}
    skills = rows(state, "t_building_skills")
    if len({(r["m_building_id"], r["skill_type"]) for r in skills}) != len(skills) or any(
            r["m_building_id"] not in buildings or r["checked_skill_level"] > r["skill_level"] for r in skills):
        raise ValueError("Stored building skill references or levels are invalid")
    parties = {row["id"]: row for row in rows(state, "t_exploration_parties")}
    if len({row["party_no"] for row in parties.values()}) != len(parties) or any(not 1 <= row["party_no"] <= 100 for row in parties.values()):
        raise ValueError("Stored party numbers are invalid")
    presets = {row["id"]: row for row in rows(state, "t_preset_exploration_parties")}
    if parties.keys() & presets.keys() or len({(r["preset_no"], r["party_no"]) for r in presets.values()}) != len(presets):
        raise ValueError("Stored preset identities are duplicated")
    if any(not 1 <= r["preset_no"] <= 100 or not 1 <= r["party_no"] <= 100 for r in presets.values()):
        raise ValueError("Stored preset numbers are invalid")
    all_parents = parties.keys() | presets.keys()
    characters = {row["id"] for row in rows(state, "t_characters")}
    idle_parties = rows(state, "t_idle_exploration_parties")
    idle_numbers = {r["party_no"] for r in idle_parties}
    if len(idle_numbers) != len(idle_parties) or any(not 1 <= n <= 10 for n in idle_numbers):
        raise ValueError("Stored idle party numbers are invalid")
    idle_members = rows(state, "t_idle_exploration_party_characters")
    if len({(r["party_no"], r["position_no"]) for r in idle_members}) != len(idle_members):
        raise ValueError("Stored idle party positions are duplicated")
    idle_characters, idle_weapons = set(), set()
    weapon_ids = {r["id"] for r in rows(state, "t_weapons")}
    for member in idle_members:
        if member["party_no"] not in idle_numbers or not 1 <= member["position_no"] <= 10:
            raise ValueError("Stored idle party position is invalid")
        character, weapon = member["t_character_id"], member["t_weapon_id"]
        if character and (character not in characters or character in idle_characters):
            raise ValueError("Stored idle character is not owned or assigned twice")
        if weapon and (not character or weapon not in weapon_ids or weapon in idle_weapons):
            raise ValueError("Stored idle weapon is not owned or assigned twice")
        if character:
            idle_characters.add(character)
        if weapon:
            idle_weapons.add(weapon)
    idle_relations = rows(state, "t_idle_exploration_party_relations")
    areas = {r["m_idle_exploration_map_area_id"] for r in rows(state, "t_idle_exploration_map_areas")}
    if len({r["party_no"] for r in idle_relations}) != len(idle_relations):
        raise ValueError("Stored idle assignments are duplicated")
    if any(r["party_no"] not in idle_numbers or r["m_idle_exploration_map_area_id"] not in areas or not any(
            m["party_no"] == r["party_no"] and m["t_character_id"] for m in idle_members) for r in idle_relations):
        raise ValueError("Stored idle assignment refers to an empty party or locked area")
    mana = {row["m_mana_gem_id"] for row in rows(state, "t_mana_gems")}
    occupied, assigned, slots, equipment, gems = set(), set(), set(), set(), set()
    for row in rows(state, "t_exploration_party_characters"):
        parent = row["t_exploration_party_id"]
        position = (parent, row["position_no"])
        if parent not in all_parents or position in occupied or not 1 <= row["position_no"] <= 10:
            raise ValueError("Stored party position is invalid")
        if (parent in presets and row["party_type"] != 2) or (parent in parties and row["party_type"] not in (0, 1)):
            raise ValueError("Stored party kind does not match its parent")
        occupied.add(position)
        character = row["t_character_id"]
        if character not in characters or (parent, character) in assigned:
            raise ValueError("Stored party character is invalid")
        assigned.add((parent, character))
        for field, table in (("t_weapon_id", "t_weapons"), ("t_armor_id", "t_armors"), ("t_accessory_id", "t_accessories")):
            key = (parent, field, row[field])
            if row[field] and (not character or row[field] not in {r["id"] for r in rows(state, table)} or key in equipment):
                raise ValueError("Stored party equipment is not owned")
            if row[field]:
                equipment.add(key)
    for row in rows(state, "t_exploration_party_mana_gems"):
        key = (row["t_exploration_party_id"], row["slot_no"])
        gem = (key[0], row["m_mana_gem_id"])
        if key[0] not in all_parents or key in slots or not 1 <= key[1] <= 3 or row["m_mana_gem_id"] not in mana or gem in gems:
            raise ValueError("Stored party mana gem is invalid")
        slots.add(key)
        gems.add(gem)
    if len(rows(state, "t_feature_tutorials")) > 1:
        raise ValueError("Stored tutorial flags must be a singleton")
    for row in rows(state, "t_feature_tutorials"):
        if row["completed_flags"] > (1 << 19) - 1:
            raise ValueError("Stored tutorial flags contain unknown bits")
    timed = rows(state, "t_timed_parameters")
    if len({row["type"] for row in timed}) != len(timed):
        raise ValueError("Stored timed parameter types are duplicated")
    if len(rows(state, "t_stamina_recoveries")) > 1:
        raise ValueError("Stored stamina recovery counter must be a singleton")
    configs = rows(state, "t_configs")
    if len(configs) > 1 or any(r["type"] != 1 or r["value"] not in (0, 1) for r in configs):
        raise ValueError("Stored user configuration is invalid")
    for table, key in (("t_character_abilities", "m_character_ability_id"), ("t_character_bond_parameters", "up_parameter_type"), ("t_character_condition_resistances", "up_parameter_type")):
        records = rows(state, table)
        if any(r["t_character_id"] not in characters for r in records) or len({(r["t_character_id"], r[key]) for r in records}) != len(records):
            raise ValueError("Stored character enhancement references are invalid")
    if any(r["level"] > r["possible_max_level"] for r in rows(state, "t_character_abilities")):
        raise ValueError("Stored ability exceeds its level cap")
    for singular, plural in (("weapon", "weapons"), ("armor", "armors"), ("accessory", "accessories")):
        equipment = rows(state, "t_" + plural)
        identifiers = {r["id"] for r in equipment}
        if any(r["is_lock"] not in (0, 1) or r["is_appraisal"] not in (0, 1) for r in equipment):
            raise ValueError("Stored equipment flags are invalid")
        if any(r[f"t_{singular}_id"] not in identifiers or r["is_appraised"] not in (0, 1) for r in rows(state, f"t_{singular}_enchantments")):
            raise ValueError("Stored enchantment owner or flags are invalid")
    if len(rows(state, "t_equipment_mileages")) > 1:
        raise ValueError("Equipment mileage must be a singleton")
    for pool in rows(state, "t_appraisal_result_pools"):
        table = {70: "t_weapons", 80: "t_armors", 90: "t_accessories"}.get(pool["content_type"])
        if table is None or find(state, table, id=pool["t_equipment_id"]) is None:
            raise ValueError("Stored appraisal references missing equipment")


def grant(state, rewards, catalog, user_id, now, progress=None):
    from .gameplay_content import apply_rewards
    return apply_rewards(state, rewards, catalog, user_id, now, progress)
