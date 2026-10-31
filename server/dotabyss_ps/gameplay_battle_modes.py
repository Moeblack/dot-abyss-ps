from __future__ import annotations

from dataclasses import dataclass

from .gameplay_catalog import GameplayError


@dataclass(frozen=True)
class BattleMode:
    quest_type: int
    master: str
    drop_table: str = ""
    drop_key: str = ""
    enemy_table: str = ""
    enemy_key: str = ""
    defensive_type: int = 1
    result_schema: str | None = None
    grant_experience: bool = True
    reference_field: str = "id"


BATTLE_MODES = {
    "main": BattleMode(2, "m_chapter_quest_battles", "m_chapter_quest_battle_view_drops", "m_chapter_quest_battle_id",
                       "m_chapter_quest_battle_view_enemies", "m_chapter_quest_battle_id"),
    "repeat": BattleMode(3, "m_chapter_quest_battles", "m_chapter_quest_battle_view_drops", "m_chapter_quest_battle_id",
                         "m_chapter_quest_battle_view_enemies", "m_chapter_quest_battle_id"),
    "tutorial": BattleMode(7, "m_tutorial_quests", grant_experience=False),
    "side_story": BattleMode(8, "m_side_story_quests", "m_side_story_view_drops", "m_side_story_quest_id",
                             "m_side_story_view_enemies", "m_side_story_quest_id", result_schema="exploration"),
    "disaster": BattleMode(4, "m_disaster_quests", "m_disaster_quest_view_drops", "m_disaster_quest_id",
                           "m_disaster_quest_view_enemies", "m_disaster_quest_id", 2),
    "encounter": BattleMode(5, "m_encounter_quests", "m_encounter_quest_view_drops", "m_encounter_quest_id",
                            "m_encounter_quest_view_enemies", "m_encounter_quest_id"),
    "union": BattleMode(6, "m_union_request_stages", enemy_table="m_union_request_view_enemies", enemy_key="m_union_request_stage_id"),
    "coaching": BattleMode(9, "m_coaching_quests", enemy_table="m_coaching_quest_view_enemies", enemy_key="m_coaching_quest_id",
                           defensive_type=2, result_schema="exploration", grant_experience=False),
    "recollection": BattleMode(10, "m_recollection_quests", enemy_table="m_recollection_quest_view_enemies", enemy_key="m_recollection_quest_id",
                               defensive_type=2, result_schema="exploration", grant_experience=False),
    "tryout": BattleMode(11, "m_tryout_quests", enemy_table="m_tryout_quest_view_enemies", enemy_key="m_tryout_quest_id",
                         defensive_type=2, result_schema="exploration", grant_experience=False),
    "nether": BattleMode(110, "m_nether_battle_stages", result_schema="exploration", grant_experience=False),
    "event_miner": BattleMode(100, "m_event_miner_quests", "m_event_miner_quest_view_drops", "m_event_miner_quest_id",
                               "m_event_miner_quest_view_enemies", "m_event_miner_quest_id", result_schema="exploration"),
    "event_story": BattleMode(101, "m_event_story_quests", "m_event_story_view_drops", "m_event_story_stage_id",
                               "m_event_story_view_enemies", "m_event_story_stage_id", result_schema="exploration", reference_field="m_event_story_stage_id"),
    "event_mutant": BattleMode(102, "m_event_disaster_quests", "m_event_disaster_quest_view_drops", "m_event_disaster_quest_id",
                                "m_event_disaster_quest_view_enemies", "m_event_disaster_quest_id", defensive_type=3, result_schema="disaster"),
    "event_hunt": BattleMode(103, "m_event_hunt_quests", "m_event_hunt_quest_view_drops", "m_event_hunt_quest_id",
                              "m_event_hunt_quest_view_enemies", "m_event_hunt_quest_id", result_schema="exploration"),
    "event_commission": BattleMode(104, "m_event_commission_quests", "m_event_commission_quest_view_drops", "m_event_commission_quest_id",
                                    "m_event_commission_quest_view_enemies", "m_event_commission_quest_id", result_schema="exploration"),
    "event_training": BattleMode(105, "m_event_training_quests", "m_event_training_view_drops", "m_event_training_stage_id",
                                  "m_event_training_view_enemies", "m_event_training_stage_id", result_schema="exploration", reference_field="m_event_training_stage_id"),
    "event_bonus": BattleMode(106, "m_event_bonus_quest_stages", "m_event_bonus_quest_view_drops", "m_event_bonus_quest_stage_id",
                               "m_event_bonus_quest_view_enemies", "m_event_bonus_quest_stage_id", result_schema="exploration"),
    "event_speedrun": BattleMode(107, "m_event_speedrun_quests", "m_event_speedrun_quest_view_drops", "m_event_speedrun_quest_id",
                                  "m_event_speedrun_quest_view_enemies", "m_event_speedrun_quest_id", result_schema="exploration"),
}


def battle_mode(kind):
    try:
        return BATTLE_MODES[kind]
    except (KeyError, TypeError) as error:
        raise GameplayError("Unsupported battle mode", 400, 4007) from error
