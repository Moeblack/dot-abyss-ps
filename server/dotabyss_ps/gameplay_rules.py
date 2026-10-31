from __future__ import annotations

import copy
import re
from pathlib import Path

from .gameplay_catalog import GameplayError
from .protocol import decode_json

DEFAULT_RULES = {
    "format_version": 1,
    "character_level_cap": 30,
    "level_reset_refund_percent": 100,
    "bond_reset_refund_percent": 100,
    "resistance_reset_refund_percent": 100,
    "max_profile_name_length": 32,
    "max_profile_comment_length": 120,
    "max_party_name_length": 32,
    "starting_gold": 1000000,
    "starting_character_exp": 1000000,
    "starting_soul": 10000,
    "starting_free_currency": 30000,
    "research_tutorial_material_amount": 100,
    "battle_drop_probability_per_mille": 300,
    "battle_rare_drop_probability_per_mille": 50,
    "battle_player_exp_per_stamina": 1,
    "battle_defence_enemy_copies": 1,
    "battle_defence_enemy_energy": 300,
    "battle_dungeon_lottery_weighted": 1,
    "battle_first_reward_group_mode": 2,
    "nether_free_entries_per_day": 1,
    "nether_initial_code_limit": 8,
    "nether_initial_code_reloads": 1,
    "nether_starting_gold": 100,
    "nether_score_per_floor": 100,
    "nether_score_multiplier_per_extra_ticket": 1,
    "nether_points_per_code": 1,
    "nether_random_drop_probability_per_mille": 300,
    "encounter_daily_supply": 3,
    "encounter_daily_recoveries": 3,
    "encounter_recovery_cost": 50,
    "union_daily_attempts": 3,
    "disaster_free_summons_per_day": 5,
    "disaster_patrol_default_level": 1,
    "battle_treasure_s_seconds": 120,
    "battle_treasure_a_seconds": 180,
    "battle_treasure_b_drops": 1,
    "battle_treasure_a_drops": 2,
    "battle_treasure_s_drops": 3,
    "secondary_ability_unlock_level": 5,
    "union_gift_points": 100,
    "disassembly_gold_per_rank": 100,
    "appraisal_ticket_cost": 1,
    "appraisal_gold_cost": 1000,
    "unlock_common_features": 0,
    "party_slots": 1,
    "party_presets": 10,
    "idle_party_slots": 1,
    "idle_unlock_all_areas": 0,
    "idle_auto_start": 0,
    "idle_max_seconds": 43200,
    "idle_max_settle_ticks": 10000,
    "idle_max_boost_ticks": 4000,
    "tavern_staff_intimacy_level": 1,
    "comeback_inactive_days": 30,
    "recommendation_board_sheet_requirements": {"260099": 2, "260100": 4, "260101": 6, "260102": 8},
    "tavern_free_runs": 1,
    "tavern_base_score": 100,
    "tavern_combo_permille": 500,
    "tavern_use_client_score_parameters": 1,
    "tavern_max_skip": 100,
    "gacha_max_draws": 100,
    "gacha_extra_pity_draws": 0,
    "gacha_mileage_draw_interval": 10,
    "gacha_expired_token_gold_per_token": 1,
    "exchange_max_batch": 1000,
    "item_max_batch": 1000,
    "novel_read_free_currency": 50,
    "event_recollection_unlock_all": 0,
    "event_open_all": 1,
    "event_open_ids": [],
    "event_daily_supply": 3,
    "event_miner_battle_seconds": 60,
    "event_miner_recovery_limit": 3,
    "event_miner_recovery_cost": 50,
    "event_mutant_recovery_limit": 3,
    "event_mutant_recovery_cost": 50,
    "event_mutant_ranking_reward_on_query": 1,
    "event_bonus_character_rate_scale": 1000,
    "event_recollection_event_ids": [],
    "local_platform_missions_complete": 1,
    "friend_limit": 100,
    "friend_recommendation_limit": 30,
    "friend_daily_limit": 100,
    "friend_daily_points": 10,
    "local_paid_shop_enabled": 1,
    "local_shop_gold_per_price": 0,
    "local_vip_points_per_price": 1,
    "paid_currency_valid_days": 180,
    "subscription_days": 30,
    "home_greeting_limit": 3,
    "home_greeting_reset_hour": 5,
    "home_enable_visitors": 0,
    "local_serial_codes": {"LOCALWELCOME": [{"content_type": 50, "content_id": 0, "amount": 10000}]},
}
RULE_BOUNDS = {
    "character_level_cap": (1, 30),
    "level_reset_refund_percent": (0, 100),
    "bond_reset_refund_percent": (0, 100),
    "resistance_reset_refund_percent": (0, 100),
    "max_profile_name_length": (1, 64),
    "max_profile_comment_length": (1, 512),
    "max_party_name_length": (1, 64),
    "starting_gold": (0, 1000000000),
    "starting_character_exp": (0, 1000000000),
    "starting_soul": (0, 1000000000),
    "starting_free_currency": (0, 1000000000),
    "research_tutorial_material_amount": (0, 999999),
    "battle_drop_probability_per_mille": (0, 1000),
    "battle_rare_drop_probability_per_mille": (0, 1000),
    "battle_player_exp_per_stamina": (0, 10000),
    "battle_defence_enemy_copies": (1, 10),
    "battle_defence_enemy_energy": (0, 100000),
    "battle_dungeon_lottery_weighted": (0, 1),
    "battle_first_reward_group_mode": (0, 2),
    "nether_free_entries_per_day": (0, 100),
    "nether_initial_code_limit": (1, 100),
    "nether_initial_code_reloads": (0, 100),
    "nether_starting_gold": (0, 1000000),
    "nether_score_per_floor": (0, 100000),
    "nether_score_multiplier_per_extra_ticket": (0, 100),
    "nether_points_per_code": (0, 100000),
    "nether_random_drop_probability_per_mille": (0, 1000),
    "encounter_daily_supply": (0, 100),
    "encounter_daily_recoveries": (0, 100),
    "encounter_recovery_cost": (0, 100000),
    "union_daily_attempts": (0, 100),
    "disaster_free_summons_per_day": (0, 100),
    "disaster_patrol_default_level": (1, 36),
    "battle_treasure_s_seconds": (1, 86400),
    "battle_treasure_a_seconds": (1, 86400),
    "battle_treasure_b_drops": (0, 100),
    "battle_treasure_a_drops": (0, 100),
    "battle_treasure_s_drops": (0, 100),
    "secondary_ability_unlock_level": (1, 30),
    "union_gift_points": (1, 1000000),
    "disassembly_gold_per_rank": (1, 1000000),
    "appraisal_ticket_cost": (0, 1000),
    "appraisal_gold_cost": (0, 100000000),
    "unlock_common_features": (0, 1),
    "party_slots": (1, 10),
    "party_presets": (1, 100),
    "idle_party_slots": (1, 10),
    "idle_unlock_all_areas": (0, 1),
    "idle_auto_start": (0, 1),
    "idle_max_seconds": (900, 604800),
    "idle_max_settle_ticks": (1, 10000),
    "idle_max_boost_ticks": (1, 10000),
    "tavern_staff_intimacy_level": (0, 20),
    "comeback_inactive_days": (0, 3650),
    "tavern_free_runs": (0, 100),
    "tavern_base_score": (1, 10000),
    "tavern_combo_permille": (0, 10000),
    "tavern_use_client_score_parameters": (0, 1),
    "tavern_max_skip": (1, 100),
    "gacha_max_draws": (1, 1000),
    "gacha_extra_pity_draws": (0, 100000),
    "gacha_mileage_draw_interval": (1, 10000),
    "gacha_expired_token_gold_per_token": (0, 1000000),
    "exchange_max_batch": (1, 10000),
    "item_max_batch": (1, 10000),
    "novel_read_free_currency": (0, 1000000),
    "event_recollection_unlock_all": (0, 1),
    "event_open_all": (0, 1),
    "event_daily_supply": (0, 100000),
    "event_miner_battle_seconds": (1, 3600),
    "event_miner_recovery_limit": (0, 100),
    "event_miner_recovery_cost": (0, 1000000),
    "event_mutant_recovery_limit": (0, 100),
    "event_mutant_recovery_cost": (0, 1000000),
    "event_mutant_ranking_reward_on_query": (0, 1),
    "event_bonus_character_rate_scale": (0, 10000),
    "local_platform_missions_complete": (0, 1),
    "friend_limit": (1, 1000),
    "friend_recommendation_limit": (1, 100),
    "friend_daily_limit": (0, 1000),
    "friend_daily_points": (0, 10000),
    "local_paid_shop_enabled": (0, 1),
    "local_shop_gold_per_price": (0, 1000),
    "local_vip_points_per_price": (0, 100),
    "paid_currency_valid_days": (1, 3650),
    "subscription_days": (1, 365),
    "home_greeting_limit": (1, 12),
    "home_greeting_reset_hour": (0, 23),
    "home_enable_visitors": (0, 1),
}


def load_rules(path: Path | None = None) -> dict:
    settings = {}
    if path is not None and path.exists():
        if path.stat().st_size > 65536:
            raise GameplayError("Local gameplay rules exceed 64 KiB", 503, 5036)
        try:
            settings = decode_json(path.read_bytes())
        except (ValueError, TypeError) as error:
            raise GameplayError("Local gameplay rules contain invalid JSON", 503, 5036) from error
    return validate_rules(settings)


def validate_rules(settings: dict) -> dict:
    if not isinstance(settings, dict) or set(settings) - set(DEFAULT_RULES):
        raise GameplayError("Unknown local gameplay rule", 503, 5036)
    result = copy.deepcopy(DEFAULT_RULES)
    result.update(settings)
    if type(result["format_version"]) is not int or result["format_version"] != 1:
        raise GameplayError("Unsupported gameplay rules format", 503, 5036)
    for name, (minimum, maximum) in RULE_BOUNDS.items():
        if type(result[name]) is not int or not minimum <= result[name] <= maximum:
            raise GameplayError("Invalid local gameplay rule: " + name, 503, 5036)
    if result["battle_treasure_s_seconds"] > result["battle_treasure_a_seconds"]:
        raise GameplayError("Treasure S-rank time cannot exceed A-rank time", 503, 5036)
    for key in ("event_recollection_event_ids", "event_open_ids"):
        event_ids = result[key]
        if (not isinstance(event_ids, list) or len(event_ids) > 1000
                or any(type(value) is not int or not 1 <= value < 2 ** 31 for value in event_ids)
                or len(set(event_ids)) != len(event_ids)):
            raise GameplayError("Invalid event identifiers: " + key, 503, 5036)
    requirements = result["recommendation_board_sheet_requirements"]
    if (not isinstance(requirements, dict) or len(requirements) > 1000 or any(
            not isinstance(key, str) or not re.fullmatch(r"[1-9][0-9]{0,18}", key)
            or int(key) > 2 ** 63 - 1 or type(value) is not int or not 1 <= value <= 100000
            for key, value in requirements.items())):
        raise GameplayError("Invalid recommendation board sheet requirements", 503, 5036)
    codes = result["local_serial_codes"]
    if not isinstance(codes, dict) or len(codes) > 1000:
        raise GameplayError("Invalid local serial-code map", 503, 5036)
    for code, rewards in codes.items():
        if not isinstance(code, str) or not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{0,127}", code) or not isinstance(rewards, list) or not 1 <= len(rewards) <= 100:
            raise GameplayError("Invalid local serial-code definition", 503, 5036)
        for reward in rewards:
            if not isinstance(reward, dict) or set(reward) != {"content_type", "content_id", "amount"} or any(type(value) is not int for value in reward.values()) or not 1 <= reward["content_type"] <= 2 ** 31 - 1 or not 0 <= reward["content_id"] <= 2 ** 63 - 1 or not 1 <= reward["amount"] <= 2 ** 31 - 1:
                raise GameplayError("Invalid local serial-code reward", 503, 5036)
    return result
