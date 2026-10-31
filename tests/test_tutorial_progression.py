import copy
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from dotabyss_ps.app import create_app
from dotabyss_ps.gameplay import Gameplay
from dotabyss_ps.gameplay_catalog import GameplayCatalog, GameplayError
from dotabyss_ps.gameplay_records import put
from dotabyss_ps.gameplay_tutorial import can_unlock_feature, validate_tutorial_battle
from dotabyss_ps.newgame import NewGameError, build_initial_state, validate_saved_state
from test_gameplay import NOW, call, gameplay_master, setup_gameplay, snapshot
from test_newgame import PC_CLIENT, PLAY_CLIENT, minimal_master


def mutate(context, action):
    def operation(state, progress):
        action(state, progress)
        validate_saved_state(state)
        return {}

    context.store.game_state(context.identity["user_id"], context.record["revision"], lambda *_: None, operation)


def prologue(context):
    return call(context, "api/user")["records"]["t_prologue_tutorials"]["up"][0]["status"]


@pytest.mark.parametrize("client,expected", [(PC_CLIENT, 0), (PLAY_CLIENT, 99), (None, 99)])
def test_new_profiles_use_the_platform_tutorial_policy(client, expected):
    schema, tables = minimal_master(client)
    state = build_initial_state(tables, schema, 7, "New profile", "2026-10-31 00:00:00")
    assert state["records"]["t_prologue_tutorials"]["up"][0]["status"] == expected
    validate_saved_state(state)


def test_explicit_legacy_profile_and_invalid_tutorial_policy():
    schema, tables = minimal_master(PC_CLIENT)
    state = build_initial_state(tables, schema, 7, "Legacy", "2026-10-31 00:00:00", start_tutorial=False)
    assert state["records"]["t_prologue_tutorials"]["up"][0]["status"] == 99
    with pytest.raises(NewGameError, match="boolean"):
        build_initial_state(tables, schema, 7, "Invalid", "2026-10-31 00:00:00", start_tutorial=1)


def test_new_profile_keeps_guided_rules_across_restart(setup_gameplay):
    c = setup_gameplay
    configured = {"unlock_common_features": 1, "party_slots": 10, "idle_party_slots": 10,
                  "idle_auto_start": 1, "idle_unlock_all_areas": 1}
    settings = c.settings.data_dir / "gameplay-rules.json"
    settings.write_text(json.dumps(configured), encoding="utf-8")
    result = call(c, "api/user")["records"]
    assert result["t_prologue_tutorials"]["up"][0]["status"] == 0
    assert result["t_feature_locks"]["up"] == []
    assert result["t_statuses"]["up"][0]["max_party_count"] == 1
    assert result["t_idle_exploration_party_relations"]["up"] == []
    progress = json.loads(snapshot(c)[1])
    assert progress["features"]["tutorial"]["data"] == {"mode": "guided", "reward_claims": []}
    with TestClient(create_app(c.settings)) as restarted:
        result = restarted.get("/api/user", headers=c.headers).json()["contents"]["records"]
    assert result["t_prologue_tutorials"]["up"][0]["status"] == 0
    assert result["t_feature_locks"]["up"] == []
    assert json.loads(settings.read_text()) == configured


@pytest.mark.parametrize("saved_status", [3, 99])
def test_existing_profile_retains_tutorial_progress_and_shortcuts(setup_gameplay, saved_status):
    c = setup_gameplay

    def create(identity, name, created_at):
        state = build_initial_state(c.tables, c.schema, identity, name, created_at, start_tutorial=False)
        state["records"]["t_prologue_tutorials"]["up"][0]["status"] = saved_status
        return state

    c.store.game_state(c.identity["user_id"], c.record["revision"], create)
    (c.settings.data_dir / "gameplay-rules.json").write_text(
        json.dumps({"unlock_common_features": 1, "party_slots": 10}), encoding="utf-8")
    result = call(c, "api/user")["records"]
    assert result["t_prologue_tutorials"]["up"][0]["status"] == saved_status
    assert result["t_statuses"]["up"][0]["max_party_count"] == 10
    assert {row["m_feature_lock_id"] for row in result["t_feature_locks"]["up"]} == {1, 2, 6, 7, 8}
    assert "tutorial" not in json.loads(snapshot(c)[1])["features"]


def test_prologue_rejects_skips_and_replays_completed_steps(setup_gameplay):
    c = setup_gameplay
    call(c, "api/user")
    before = snapshot(c)
    call(c, "api/tutorial/prologue-update", {"status": 99}, expected=409)
    assert snapshot(c) == before
    call(c, "api/tutorial/prologue-update", {"status": 1})
    before = snapshot(c)
    assert call(c, "api/tutorial/prologue-update", {"status": 1})["records"] == {}
    assert snapshot(c) == before
    call(c, "api/tutorial/prologue-update", {"status": 0}, expected=409)
    call(c, "api/tutorial/prologue-update", {"status": 2})
    before = snapshot(c)
    call(c, "api/tutorial/prologue-update", {"status": 3}, expected=409)
    assert snapshot(c) == before


def test_prologue_ends_after_state_thirteen_and_preserves_guided_mode(setup_gameplay):
    c = setup_gameplay
    call(c, "api/user")
    mutate(c, lambda state, progress: state["records"]["t_prologue_tutorials"]["up"][0].update(status=13))
    call(c, "api/tutorial/prologue-update", {"status": 14}, expected=409)
    call(c, "api/tutorial/prologue-update", {"status": 99})
    before = snapshot(c)
    assert call(c, "api/tutorial/prologue-update", {"status": 99})["records"] == {}
    assert snapshot(c) == before
    assert json.loads(before[1])["features"]["tutorial"]["data"]["mode"] == "guided"


def test_prologue_requires_confirmed_tutorial_gacha(setup_gameplay):
    c = setup_gameplay
    call(c, "api/user")
    mutate(c, lambda state, progress: state["records"]["t_prologue_tutorials"]["up"][0].update(status=7))
    before = snapshot(c)
    call(c, "api/tutorial/prologue-update", {"status": 8}, expected=409)
    assert snapshot(c) == before
    call(c, "api/gacha/tutorial/exec")
    call(c, "api/gacha/tutorial/confirm")
    call(c, "api/tutorial/prologue-update", {"status": 8})
    assert prologue(c) == 8


def test_feature_tutorial_rewards_are_delivered_once_by_master_policy(setup_gameplay):
    c = setup_gameplay
    call(c, "api/user")
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda _: call(c, "api/tutorial/feature-exec", {"tutorial_type": 5}), range(3)))
    records = call(c, "api/user")["records"]
    items = {row["m_item_id"]: row["amount"] for row in records["t_items"]["up"]}
    assert items[140001] == 1 and items[20000] == 3
    call(c, "api/tutorial/feature-exec", {"tutorial_type": 11})
    before = snapshot(c)
    call(c, "api/tutorial/feature-exec", {"tutorial_type": 11})
    assert snapshot(c) == before
    progress = json.loads(before[1])
    rewards = [entry for entry in progress["features"]["presents"]["data"]["entries"] if entry["title"] == "Tutorial rewards"]
    assert len(rewards) == 1 and rewards[0]["content_id"] == 140001 and rewards[0]["amount"] == 20
    assert progress["features"]["tutorial"]["data"]["reward_claims"] == [1, 2, 3]


def test_completed_feature_does_not_retroactively_grant_tutorial_rewards(setup_gameplay):
    c = setup_gameplay
    call(c, "api/user")
    mutate(c, lambda state, progress: put(state, "t_feature_tutorials", {}, {"completed_flags": 1 << 4}, c.identity["user_id"]))
    before = snapshot(c)
    call(c, "api/tutorial/feature-exec", {"tutorial_type": 5})
    assert snapshot(c) == before


def test_tutorial_reward_failure_rolls_back_grants_flags_and_receipts(setup_gameplay):
    c = setup_gameplay
    call(c, "api/user")
    before = snapshot(c)
    catalog = GameplayCatalog(c.tables, c.schema)
    catalog.tables = copy.deepcopy(catalog.tables)
    catalog.tables["m_tutorial_rewards"][2]["content_id"] = 999999999

    def execute(state, progress):
        return Gameplay(state, progress, catalog, c.identity["user_id"], NOW).execute(
            "api/tutorial/feature-exec", "POST", {"tutorial_type": 5})

    with pytest.raises(GameplayError, match="Unknown master identifier"):
        c.store.game_state(c.identity["user_id"], c.record["revision"], lambda *_: None, execute)
    assert snapshot(c) == before


def test_main_story_unlock_conditions_follow_quest_sequence_and_staff(setup_gameplay):
    c = setup_gameplay
    call(c, "api/user")
    for feature in (2, 7, 8):
        call(c, "api/feature-lock/unlock", {"m_feature_lock_id": feature}, expected=409)

    def completed_battle(state, progress):
        put(state, "t_chapter_quests", {"m_chapter_quest_id": 1010101},
            {"m_chapter_id": 1, "m_chapter_area_id": 101, "sequence": 3})

    mutate(c, completed_battle)
    call(c, "api/feature-lock/unlock", {"m_feature_lock_id": 8})
    call(c, "api/feature-lock/unlock", {"m_feature_lock_id": 2}, expected=409)
    mutate(c, lambda state, progress: state["records"]["t_chapter_quests"]["up"][0].update(sequence=4))
    call(c, "api/feature-lock/unlock", {"m_feature_lock_id": 2})

    def staff_registered(state, progress):
        for character in state["records"]["t_characters"]["up"]:
            character["intimacy_point"] = 50

    mutate(c, staff_registered)
    call(c, "api/feature-lock/unlock", {"m_feature_lock_id": 7})
    before = snapshot(c)
    call(c, "api/feature-lock/unlock", {"m_feature_lock_id": 7})
    assert snapshot(c) == before


def test_tutorial_quest_requires_the_corresponding_prologue_state(setup_gameplay):
    c = setup_gameplay
    call(c, "api/user")
    state, progress = map(json.loads, snapshot(c))
    game = Gameplay(state, progress, GameplayCatalog(c.tables, c.schema), c.identity["user_id"], NOW)
    for identifier, expected in ((1, 2), (2, 10), (3, 99)):
        with pytest.raises(GameplayError, match="not available"):
            validate_tutorial_battle(game, identifier)
        state["records"]["t_prologue_tutorials"]["up"][0]["status"] = expected
        assert validate_tutorial_battle(game, identifier)["id"] == identifier
        state["records"]["t_prologue_tutorials"]["up"][0]["status"] = 0


def test_unknown_feature_conditions_do_not_unlock(setup_gameplay):
    c = setup_gameplay
    call(c, "api/user")
    state, progress = map(json.loads, snapshot(c))
    catalog = GameplayCatalog(c.tables, c.schema)
    catalog.tables = copy.deepcopy(catalog.tables)
    catalog.tables["m_feature_lock_conditions"][1]["condition_type"] = 99
    game = Gameplay(state, progress, catalog, c.identity["user_id"], NOW)
    with pytest.raises(GameplayError, match="Unsupported feature unlock"):
        can_unlock_feature(game, 1)


def test_complete_native_tutorial_flow_builds_both_battles_and_resumes(setup_gameplay):
    c = setup_gameplay
    initial = call(c, "api/user")["records"]
    assert initial["t_prologue_tutorials"]["up"][0]["status"] == 0
    stamina = copy.deepcopy(initial["t_timed_parameters"])
    for state in (1, 2):
        call(c, "api/tutorial/prologue-update", {"status": state})
    first = call(c, "api/tutorial/start-quest", {"m_tutorial_quest_id": 1, "party_no": 0})
    assert (first["status"], first["quest_type"], first["quest_id"], first["stage_type"]) == (1, 7, 1, 1)
    assert json.loads(first["stage_detail"])["floor_parts"]
    assert json.loads(first["party_detail"])["party_no"] == 0
    repeated = call(c, "api/tutorial/start-quest", {"m_tutorial_quest_id": 1, "party_no": 0})
    assert repeated["stage_detail"] == first["stage_detail"]
    with TestClient(create_app(c.settings)) as restarted:
        resumed = restarted.get("/api/battle-session/status", headers=c.headers).json()["contents"]
    assert resumed["stage_detail"] == first["stage_detail"]
    for state in (3, 4, 5, 6, 7):
        call(c, "api/tutorial/prologue-update", {"status": state})
    assert call(c, "api/battle-session/status")["status"] == 0
    call(c, "api/gacha/tutorial/exec")
    call(c, "api/gacha/tutorial/confirm")
    for state in (8, 9, 10):
        call(c, "api/tutorial/prologue-update", {"status": state})
    second = call(c, "api/tutorial/start-quest", {"m_tutorial_quest_id": 2, "party_no": 0})
    assert (second["status"], second["quest_id"], second["stage_type"]) == (1, 2, 2)
    defensive_stage = json.loads(second["stage_detail"])
    assert defensive_stage["boss_enemy"]["m_enemy_id"] > 0
    assert defensive_stage["defensive_object"]["m_defensive_unit_id"] > 0
    for state in (11, 12, 13, 99):
        call(c, "api/tutorial/prologue-update", {"status": state})
    completed = call(c, "api/user")["records"]
    assert completed["t_prologue_tutorials"]["up"][0]["status"] == 99
    assert completed["t_timed_parameters"] == stamina
    assert call(c, "api/battle-session/status")["status"] == 0
    battle = json.loads(snapshot(c)[1])["features"]["battle"]["data"]
    assert battle["tutorial_completed"] == [1, 2]
