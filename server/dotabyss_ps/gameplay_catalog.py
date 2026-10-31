from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re

from .masterdata import MasterDataError
from .server_master_bindings import SERVER_TABLES, bound_fields
from .gameplay_event_tables import EVENT_TABLES

GAMEPLAY_TABLES = {
    *EVENT_TABLES,
    "m_items", "m_user_ranks", "m_stamina_recovery_costs", "m_missions", "m_mission_rewards", "m_mission_terms",
    "m_special_missions", "m_special_mission_days", "m_feature_locks", "m_feature_lock_conditions",
    "m_chapter_areas", "m_chapter_quests", "m_chapter_quest_sequences", "m_gachas", "m_gacha_groups",
    "m_exchange_categories", "m_exchange_lineups", "m_home_daily_greetings", "m_home_greeting_positions",
    "m_character_intimacy_levels", "m_novel_homes",
    "m_characters", "m_character_levels", "m_character_skins", "m_weapon_skins", "m_feature_tips", "m_degrees",
    "m_character_abilities", "m_abilities", "m_ability_effects", "m_ability_details", "m_ability_releases", "m_ability_level_up_costs",
    "m_character_limit_break_defines", "m_character_limit_breaks", "m_character_bond_parameter_groups", "m_character_bond_parameter_levels",
    "m_character_condition_resistance_levels", "m_intimacy_bond_point_rewards",
    "m_weapons", "m_armors", "m_accessories", "m_weapon_enchantment_groups", "m_armor_enchantment_groups", "m_accessory_enchantment_groups",
    "m_enchantment_groups", "m_enchantments", "m_enchantment_abilities", "m_mana_gems", "m_plans", "m_gacha_tokens",
    "m_gacha_steps", "m_gacha_lotteries", "m_gacha_contents", "m_gacha_mileage_rewards",
    "m_item_selectable_groups",
    "m_buildings", "m_building_levels", "m_building_skill_levels", "m_building_skill_params", "m_building_skill_costs",
    "m_building_skill_restricts", "m_plan_steps", "m_plan_restricts", "m_plan_restrict_rewards",
    "m_novel_characters", "m_novel_character_skins", "m_novel_others", "m_novel_mains", "m_novel_prologues",
    "m_novel_events", "m_novel_event_rewards", "m_events", "m_nethers", "m_nether_maps",
    "m_event_story_novels", "m_event_story_rewards",
    "m_dictionary_non_player_characters", "m_dictionary_enemies", "m_dictionary_worlds",
    "m_idle_exploration_maps", "m_idle_exploration_map_areas", "m_idle_exploration_map_area_difficulties",
    "m_idle_exploration_levels", "m_idle_exploration_drop_rules", "m_idle_exploration_explore_rewards", "m_idle_exploration_view_drops",
    "m_tavern_works", "m_tavern_work_levels", "m_tavern_card_levels", "m_tavern_cards", "m_tavern_character_cards",
    "m_tavern_card_effects", "m_tavern_reward_levels", "m_tavern_score_levels",
    "m_level_synchros", "m_level_synchro_upgrade_costs", "m_level_synchro_level_up_costs",
    "m_mission_passes", "m_mission_pass_levels", "m_visitors", "m_visitor_missions",
    "m_mileage_levels", "m_weapon_mileage_rewards", "m_armor_mileage_rewards", "m_accessory_mileage_rewards",
    "m_loop_weapon_mileage_rewards", "m_loop_armor_mileage_rewards", "m_loop_accessory_mileage_rewards",
    "m_mileage_reward_groups", "m_mileage_rewards",
    "m_payments", "m_payment_categories", "m_payment_lineups", "m_payment_recommends", "m_payment_subscriptions", "m_payment_subscription_effects",
    "m_payment_lineup_steps", "m_payment_packs", "m_payment_dmm_products", "m_payment_gachas",
    "m_vip_ranks", "m_vip_rank_bonuses", "m_vip_exchanges", "m_vip_exchange_contents",
    "m_login_bonuses", "m_login_bonus_sheets", "m_login_bonus_items", "m_limited_items", "m_event_currencies",
    "m_home_visitor_lotteries", "m_home_visitor_daily_greetings", *SERVER_TABLES,
    "m_chapters", "m_chapter_quest_battles", "m_chapter_quest_rewards", "m_chapter_characters",
    "m_chapter_quest_battle_rewards", "m_chapter_quest_battle_view_drops", "m_chapter_quest_battle_view_enemies", "m_chapter_repeat_quests",
    "m_dungeons", "m_dungeon_floor_parts", "m_dungeon_floor_part_enemies", "m_dungeon_floor_part_objects",
    "m_enemies", "m_enemy_parameters", "m_defend_stages", "m_enemy_disaster_groups",
    "m_defensive_units", "m_defensive_unit_levels", "m_recommended_defensive_unit_groups",
    "m_tutorial_quests", "m_tutorial_rewards", "m_fixed_parties", "m_allies",
    "m_party_restrictions", "m_party_restriction_groups",
    "m_disaster_bosses", "m_disaster_boss_phases", "m_disaster_boss_messages", "m_disaster_boss_restricts",
    "m_disaster_level_rewards", "m_disaster_levels", "m_disaster_quests", "m_disaster_quest_view_drops",
    "m_disaster_quest_view_enemies", "m_disaster_sp_level_rewards", "m_disaster_boss_appears",
    "m_disaster_sp_boss_appears", "m_disaster_level_restricts",
    "m_coaching_quests", "m_coaching_quest_restricts", "m_coaching_quest_rewards", "m_coaching_quest_view_enemies",
    "m_recollection_quests", "m_recollection_quest_restricts", "m_recollection_quest_view_enemies",
    "m_tryout_quests", "m_tryout_quest_restricts", "m_tryout_quest_view_enemies",
    "m_encounter_quests", "m_encounter_quest_view_enemies", "m_encounter_quest_view_drops", "m_encounter_quest_rewards",
    "m_idle_exploration_encounters", "m_idle_exploration_encounter_rules",
    "m_union_request_stages", "m_union_request_rewards", "m_union_request_opens", "m_union_request_view_enemies",
    "m_nether_map_floors", "m_nether_floor_events", "m_nether_floor_event_parts", "m_nether_floor_battles",
    "m_nether_battle_stages", "m_nether_floor_shops", "m_nether_floor_shop_contents", "m_nether_codes",
    "m_nether_result_reward_levels", "m_nether_code_category_skills", "m_nether_floor_restricts", "m_nether_floor_powers",
    "m_nether_spheres", "m_nether_sphere_boards", "m_nether_sphere_costs",
    "m_side_stories", "m_side_story_stages", "m_side_story_quests", "m_side_story_rewards", "m_side_story_restricts",
    "m_side_story_view_enemies", "m_side_story_view_drops", "m_side_story_top_characters", "m_novel_side_stories",
}
JST = timezone(timedelta(hours=9))
TABLE_KEYS = {"m_encounter_quest_view_drops": ("m_encounter_quest_id", "id")}


class GameplayError(ValueError):
    def __init__(self, message: str, status: int = 409, code: int = 4092):
        super().__init__(message)
        self.status, self.code = status, code


def number(value, minimum=1, maximum=2 ** 63 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise GameplayError("Invalid integer argument", 400, 4007)
    return value


def identifiers(value, maximum=1000):
    if not isinstance(value, list) or len(value) > maximum:
        raise GameplayError("Invalid identifier list", 400, 4007)
    values = [number(item) for item in value]
    if len(values) != len(set(values)):
        raise GameplayError("Duplicate identifiers", 400, 4007)
    return values


class GameplayCatalog:
    def __init__(self, tables: dict, schema: dict):
        self.tables = {}
        for name in GAMEPLAY_TABLES:
            if name not in tables or name not in schema["tables"] and name not in SERVER_TABLES:
                continue
            fields = [column["name"] for column in schema["tables"][name]] if name in schema["tables"] else bound_fields(name, tables[name])
            rows = {}
            for values in tables[name]:
                if len(values) != len(fields):
                    raise MasterDataError("Gameplay master row does not match its bound schema")
                row = dict(zip(fields, values))
                identifier = row.get("id")
                key_fields = TABLE_KEYS.get(name, ("id",))
                if any(type(row.get(field)) is not int for field in key_fields):
                    raise MasterDataError("Gameplay master identifiers are invalid: " + name)
                key = tuple(row[field] for field in key_fields) if name in TABLE_KEYS else identifier
                if key in rows:
                    raise MasterDataError("Gameplay master identifiers are duplicated: " + name)
                rows[key] = row
            self.tables[name] = rows

    def rows(self, name):
        if name not in self.tables:
            raise GameplayError("Required gameplay master table is not bound: " + name, 503, 5036)
        return list(self.tables[name].values())

    def get(self, name, identifier):
        self.rows(name)
        if name in TABLE_KEYS and not isinstance(identifier, tuple):
            matches = [row for row in self.tables[name].values() if row["id"] == identifier]
            if len(matches) > 1:
                raise GameplayError("Master identifier requires its composite key", 400, 4007)
            if matches:
                return matches[0]
        row = self.tables[name].get(identifier)
        if row is None:
            raise GameplayError("Unknown master identifier", 400, 4007)
        return row


def parse_timestamp(value):
    normalized = re.sub(r"^(\d{4}-\d{2}-\d{2}[ T])(\d):", lambda match: match[1] + match[2].zfill(2) + ":", value)
    stamp = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=JST)


def available(row, now: datetime, start="start_at", end="end_at"):
    try:
        return (not row.get(start) or parse_timestamp(row[start]) <= now) and (not row.get(end) or now <= parse_timestamp(row[end]))
    except (ValueError, TypeError) as error:
        raise GameplayError("Master availability date is invalid", 503, 5036) from error
