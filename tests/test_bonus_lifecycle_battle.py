import copy
import json
from datetime import timedelta

import pytest

from dotabyss_ps.gameplay_catalog import GameplayError
from dotabyss_ps.gameplay_event_battle_common import context
from dotabyss_ps.gameplay_event_bonus import bonus_record, finish
from dotabyss_ps.gameplay_event_effects import context_characters
from dotabyss_ps.gameplay_event_quest_state import QUEST_KINDS, node_record, prepare_event_quests, saved_node
from dotabyss_ps.gameplay_battle_session import battle_state, create_session
from dotabyss_ps.gameplay_main_repeat import skipped_result
from dotabyss_ps.gameplay_nether import cancel, start
from dotabyss_ps.gameplay_nether_configuration import freeze_configuration, frozen_game, validate_configuration
from dotabyss_ps.gameplay_nether_events import resolve_floor
from dotabyss_ps.gameplay_nether_state import point_view, state
from dotabyss_ps.gameplay_records import put
from dotabyss_ps.storage import Store
from test_battle_rewards import reward_game, reward_master
from test_nether_growth import game_with_party


def begin_nether(game):
    start(game, {"m_nether_id": 1, "m_nether_map_id": 1, "party_no": 1,
                 "use_ticket": 0, "start_floor_level": 1})
    return state(game)["run"]


def test_nether_configuration_freezes_rules_master_and_preserves_live_context(reward_game):
    game = game_with_party(reward_game)
    expedition = begin_nether(game)
    floors = [next(row for row in expedition["floors"] if row["floor_level"] == level) for level in (1, 2)]
    resolve_floor(game, floors[0])
    assert expedition["base_score"] == 100
    game.rules = {**game.rules, "nether_score_per_floor": 900, "nether_initial_code_limit": 99}
    original = game.catalog.get("m_nether_spheres", 10021)
    game.catalog.tables["m_nether_spheres"] = {**game.catalog.tables["m_nether_spheres"],
                                               10021: {**original, "effect_parameter_1": 777}}
    state(game)["spheres"].append({"m_nether_sphere_board_id": 10, "type": 2, "level": 1})
    resolve_floor(game, floors[1])
    assert expedition["base_score"] == 200
    assert point_view(game)["max_nether_code"] == 8
    assert point_view(game)["extra_drop_ratio"] == original["effect_parameter_1"]
    assert game.rules["nether_score_per_floor"] == 900
    assert game.catalog.get("m_nether_spheres", 10021)["effect_parameter_1"] == 777
    assert len(json.dumps(expedition["configuration"]).encode()) < 256 * 1024
    Store._state_json(game.progress)
    cancel(game, {})
    state(game)["free_entries"] = {}
    following = begin_nether(game)
    resolve_floor(game, following["floors"][0])
    assert following["base_score"] == 900
    assert point_view(game)["max_nether_code"] == 99


def test_nether_legacy_run_migrates_once_without_rebuilding_party(reward_game):
    game = game_with_party(reward_game)
    assert freeze_configuration(game) is None
    expedition = begin_nether(game)
    del expedition["configuration"]
    party = copy.deepcopy(expedition["party"])
    game.rules = {**game.rules, "nether_score_per_floor": 250}
    configuration = freeze_configuration(game)
    assert configuration["rules"]["nether_score_per_floor"] == 250
    game.rules = {**game.rules, "nether_score_per_floor": 900}
    assert freeze_configuration(game) is configuration
    resolve_floor(game, expedition["floors"][0])
    assert expedition["base_score"] == 250
    assert expedition["party"] == party
    restored = copy.copy(game)
    restored.progress = json.loads(Store._state_json(game.progress))
    assert frozen_game(restored).rules["nether_score_per_floor"] == 250


def test_nether_rule_scope_does_not_leak_into_global_reconciliation(reward_game):
    game = game_with_party(reward_game)
    begin_nether(game)
    point_view(game)
    game.rules = {**game.rules, "idle_max_seconds": 72000, "nether_initial_code_limit": 99}
    game.now += timedelta(days=1)
    response = game.execute("api/nether", "GET", {})
    assert response["t_nether_point"]["max_nether_code"] == 8
    assert game.state["records"]["t_idle_exploration_statuses"]["up"][0]["explorable_duration"] == 72000
    assert game.rules["idle_max_seconds"] == 72000
    assert frozen_game(game).now == game.now


@pytest.mark.parametrize("damage", [
    lambda value: value["master"].update(bytes=1),
    lambda value: value["master"].update(data="not-base64"),
    lambda value: value["master"].update(data=value["master"]["data"][:-4]),
    lambda value: value["rules"].update(nether_score_per_floor=-1),
])
def test_nether_configuration_rejects_corrupt_snapshots(reward_game, damage):
    game = game_with_party(reward_game)
    configuration = copy.deepcopy(begin_nether(game)["configuration"])
    damage(configuration)
    with pytest.raises(GameplayError):
        validate_configuration(configuration)


def test_event_member_context_keeps_legacy_and_normalized_characters(reward_game):
    game = game_with_party(reward_game)
    metadata = context(game, game.catalog.get("m_events", 100001), 1)
    assert metadata["party_characters"] == list(map(int, metadata["members"]))
    legacy = {key: value for key, value in metadata.items() if key != "party_characters"}
    assert context_characters(legacy) == context_characters(metadata)
    assert context_characters({"party_characters": [], "members": {"1300035": 30}}) == []


@pytest.mark.parametrize("is_skip", [False, True])
def test_event_bonus_clear_rewards_apply_frozen_party_special_effect(reward_game, is_skip):
    game = game_with_party(reward_game)
    game.rules["battle_drop_probability_per_mille"] = 0
    event_id = 600002
    definition = game.catalog.get("m_event_bonus_quest_stages", 60000201)
    put(game.state, "t_events", {"m_event_id": event_id}, {"is_event_stage_effect_enabled": 1,
        "join_at": game.now.isoformat(), "created_at": game.now.isoformat(), "updated_at": game.now.isoformat()})
    bonus_record(game, event_id)["m_event_bonus_quest_stage_id"] = definition["id"]
    create_session(game, definition, kind="event_bonus", quest_id=definition["id"], party_no=1,
                   validate_members=False, context={"event_id": event_id, "kind": "event_bonus",
                   "party_characters": [1300035], "is_skip": is_skip}, result_schema="exploration")
    session = battle_state(game)["active"]
    results = skipped_result(session["stage_detail"], session["stage_type"])
    if session["stage_type"] == 2:
        results.update(total_damage=0, passed_floor_parts=[], clear_rank=0)
    response = finish(game, session, results, preserve_skip=is_skip)
    assert [(row["content_id"], row["amount"]) for row in response["clear_rewards"] if row["content_type"] == 54] == [(6001, 260)]


def test_event_skip_applies_character_bonus_to_random_drops(reward_game, monkeypatch):
    from dotabyss_ps import gameplay_event_quests as quests

    game = game_with_party(reward_game)
    event = game.catalog.get("m_events", 300001)
    kind = QUEST_KINDS["story"]
    node = game.catalog.get(kind.nodes, 6)
    character = game.state["records"]["t_characters"]["up"][0]["m_character_id"]
    game.catalog.tables["m_event_special_effect_drops"] = {1: {
        "id": 1, "m_event_id": event["id"], "m_character_id": character,
        "bonus_content_type": 30, "bonus_content_id": 50007, "bonus_ratio": 1000}}
    game.catalog.tables["m_event_story_view_drops"] = {1: {
        "id": 1, "m_event_story_stage_id": node["id"], "content_type": 30, "content_id": 50007, "is_rare_drop": 0}}
    put(game.state, "t_events", {"m_event_id": event["id"]}, {"is_event_stage_effect_enabled": 1,
        "join_at": game.now.isoformat(), "created_at": game.now.isoformat(), "updated_at": game.now.isoformat()})
    prepare_event_quests(game, event)
    node_record(game, kind, 2)["status"] = 99
    node_record(game, kind, node["id"])["status"] = 99
    saved_node(game, kind, node).update(first_claimed=True, party_characters=[character])
    original = quests.finish
    observed = []

    def check_manifest(game, kind, session, stage_results):
        drops = session["stage_detail"]["drops"]
        assert drops and all(row["content_id"] == 50007 and row["amount"] == 2 for row in drops)
        assert all(session["reward_index"][str(row["sid"])]["amount"] == 2 for row in drops)
        observed.extend(drops)
        return original(game, kind, session, stage_results)

    monkeypatch.setattr(quests, "finish", check_manifest)
    result = quests.skip(game, {kind.field: node["id"], "skip_count": 1}, kind)
    assert observed
    assert sum(row["amount"] for row in result["drops"] if row["content_id"] == 50007) == 2 * len(observed)
