from __future__ import annotations

import hashlib

from .masterdata import MasterDataError, canonical_json

SERVER_TABLES = {
    "m_encounter_campaign_bonus_quests": ("id group_id m_encounter_quest_id bonus_quest_time_limit m_encounter_campaign_bonus_quest_appear_group_id", "da3c98b4f5a03d4a20a788dab79e286b3196b76b6966e53ef72094b3a470158f"),
    "m_encounter_campaign_bonus_quest_appears": ("id group_id m_encounter_quest_id", "2e5400936c85bfc872a3a59d0d0b1c368cba813d1528f04491b63ea0f08dd5ea"),
    "m_event_miners": ("id m_event_id initial_supply capacity_supply daily_recovery_supply", "a150401c727e838508116d76c74b586123f8bf6b1d2c4f5856500e28f5cd21c4"),
    "m_dungeon_floor_part_enemy_lotteries": ("id group_id weight option_1 option_2 enemy_group_id", "9e5ca13dd4ed9b0341a00b55c17747aac9ffd2eed5b59e11c0d3e109958de6ab"),
    "m_fixed_forts": ("id m_defensive_unit_id level option_1", "225474c2800582ee771f96458e83a053d2c254b3d25576d113cf60180f6b2ca7"),
    "m_fixed_defensive_units": ("id m_fixed_fort_id m_defensive_unit_id level placement_line placement_index", "2af48d41d47ce51c7f4b1b71af8350ecf0abe8c6a090b4159644b09764795fc7"),
    "m_home_greeting_rewards": ("id group_id content_type content_id amount", "160132bd99b456b35afedb132a16c2a3aded57440177c7df07d55bad05f69aab"),
    "m_limited_item_convert_contents": ("id group_id content_type content_id amount", "555d2b2817493c793cfe770b61442c3588fb3c27aea04b6a83b09353b3b930db"),
}


def bound_fields(name, values):
    fields, expected = SERVER_TABLES[name]
    if hashlib.sha256(canonical_json(values)).hexdigest() != expected:
        raise MasterDataError("Server-only master binding requires review for the changed table: " + name)
    return fields.split()
