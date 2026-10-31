import copy
import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from dotabyss_ps.gameplay_battle_rewards import validate_stage_results
from dotabyss_ps.gameplay_battle_notifications import battle_notifications
from dotabyss_ps.gameplay_battle_session import battle_state, create_session, session_view
from dotabyss_ps.gameplay_catalog import GameplayCatalog, GameplayError, JST
from dotabyss_ps.gameplay_receipts import refresh_replay_records
from dotabyss_ps.gameplay_records import delta, put
from dotabyss_ps.gameplay_rules import load_rules
from dotabyss_ps.newgame import NewGameError, build_initial_state, validate_saved_state
from test_newgame import PC_CLIENT, minimal_master


def initial_state():
    schema, tables = minimal_master(PC_CLIENT)
    return build_initial_state(tables, schema, 7, "Local", "2026-10-31 12:00:00", start_tutorial=False)


def test_encounter_catalog_preserves_composite_ids():
    fields = ("id", "m_encounter_quest_id", "content_type", "content_id", "is_rare_drop", "is_pickup")
    schema = {"tables": {"m_encounter_quest_view_drops": [{"name": key} for key in fields]}}
    catalog = GameplayCatalog({"m_encounter_quest_view_drops": [[31100, 1010307, 70, 13020520, 0, 0], [31100, 10000084, 30, 50001, 0, 0]]}, schema)
    assert len(catalog.rows("m_encounter_quest_view_drops")) == 2
    assert catalog.get("m_encounter_quest_view_drops", (1010307, 31100))["content_type"] == 70
    with pytest.raises(GameplayError, match="composite"):
        catalog.get("m_encounter_quest_view_drops", 31100)


def test_nether_code_records_use_native_key_and_replay_current_values():
    state = initial_state()
    before = copy.deepcopy(state["records"])
    code = put(state, "t_nether_codes", {"m_nether_code_id": 101}, {"m_nether_id": 1, "m_nether_map_id": 1, "amount": 1}, 7)
    assert "id" not in code
    validate_saved_state(state)
    response = {"records": delta(before, state["records"])}
    code["amount"] = 3
    assert refresh_replay_records(state, response)["records"]["t_nether_codes"]["up"][0]["amount"] == 3
    state["records"]["t_nether_codes"]["up"].clear()
    assert refresh_replay_records(state, response)["records"]["t_nether_codes"] == {"up": [], "down": [101]}


def test_empty_disaster_slots_accept_zero_master_ids():
    state = initial_state()
    for slot in range(4):
        put(state, "t_disaster_slots", {"slot": slot}, {"is_special_boss": int(slot == 0), "disaster_level": 0,
            "m_disaster_boss_id": 0, "m_disaster_quest_id": 0, "last_appear_at": "2026-10-31T12:00:00+09:00"}, 7)
    validate_saved_state(state)
    state["records"]["t_disaster_slots"]["up"][0]["m_disaster_boss_id"] = -1
    with pytest.raises(NewGameError):
        validate_saved_state(state)


def test_disaster_report_shape_and_depth_are_bounded():
    state = initial_state()
    report = {"message_id": 1, "replies": [{"message_id": 2, "replies": []}]}
    record = put(state, "t_disaster_statuses", {}, {"disaster_level": 1, "disaster_point": 0,
        "disaster_level_record_high": 1, "skippable_disaster_level": 0, "skip_extra_drop_probability": 0,
        "skip_additional_drop_rate": 0, "last_appear_at": "", "last_disaster_defeat_at": "", "reports": [report]}, 7)
    validate_saved_state(state)
    record["reports"][0]["message_id"] = True
    with pytest.raises(NewGameError):
        validate_saved_state(state)
    report = {"message_id": 1, "replies": []}
    for _ in range(10):
        report = {"message_id": 1, "replies": [report]}
    record["reports"] = [report]
    with pytest.raises(NewGameError):
        validate_saved_state(state)


def test_custom_nether_session_freezes_party_and_floor_context():
    game = SimpleNamespace(state=initial_state(), progress={"features": {}}, now=datetime(2026, 10, 31, 12, tzinfo=JST),
                           user_id=7, rules=load_rules())
    stage = {"floor_parts": [], "enemies": [], "resources": [], "drops": [], "mining": None}
    party = {"characters": [{"m_character_id": 1, "hp": 100}]}
    definition = {"id": 1, "stage_id": 1, "stage_type": 1, "time_limit": 0}
    response = create_session(game, definition, kind="nether", quest_id=1, party_no=1, context={"floor": 2},
                              stage_detail=stage, reward_index={}, party_detail=party, session_detail={"floor": 2}, validate_members=False)
    party["characters"][0]["hp"] = 1
    assert response["quest_type"] == 110
    assert json.loads(response["party_detail"])["characters"][0]["hp"] == 100
    active = battle_state(game)["active"]
    assert session_view(active) == response
    assert json.loads(response["session_detail"]) == {"floor": 2}
    with pytest.raises(GameplayError, match="current battle"):
        create_session(game, definition, kind="disaster", quest_id=1, party_no=1)


@pytest.mark.parametrize("floor_ids", [[], None])
def test_defence_training_uses_exploration_request_schema(floor_ids):
    enemy = {"sid": 11, "m_enemy_id": 1, "drops": [], "is_target": 1}
    stage = {"enemies": [], "boss_enemy": enemy, "drops": []}
    result = {"play_time": 1, "destroyed_enemies": [11], "drop_items": [], "mana_gem_count": 0,
              "top_fc_damage": 0, "total_damage": 100, "passed_floor_parts": floor_ids, "clear_rank": 0}
    assert validate_stage_results(stage, result, 2, result_schema="exploration")["destroyed_enemies"] == [11]
    with pytest.raises(GameplayError):
        validate_stage_results(stage, result, 2)


def test_mode_notifications_follow_actual_transitions_only():
    state = initial_state()
    records = state["records"]
    records["t_disaster_statuses"]["up"] = [{"disaster_level": 1}]
    records["t_coaching_quests"]["up"] = [{"m_coaching_quest_id": 12, "status": 0}]
    before = copy.deepcopy(records)
    assert battle_notifications(before, records) is None
    records["t_disaster_statuses"]["up"][0]["disaster_level"] = 2
    records["t_disaster_bosses"]["up"].append({"m_disaster_boss_id": 3})
    records["t_coaching_quests"]["up"][0]["status"] = 1
    result = battle_notifications(before, records)
    assert result["disaster_level_up"] == [{"before_disaster_level": 1, "after_disaster_level": 2}]
    assert result["disaster_boss_unlock"] == [{"m_disaster_boss_id": 3}]
    assert result["coaching_quest_open"] == [{"m_coaching_quest_id": 12}]
    assert result["player_exp_up"] == []
    assert battle_notifications(copy.deepcopy(records), records) is None
