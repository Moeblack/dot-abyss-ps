import copy
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from dotabyss_ps.app import create_app
from dotabyss_ps.gameplay_battle_stages import build_stage
from dotabyss_ps.gameplay_catalog import JST
from dotabyss_ps.gameplay_missions import Missions
from dotabyss_ps.gameplay_records import put
from dotabyss_ps.gameplay_stamina import STAMINA_TYPE
from test_battle_stages import assert_links, battle_catalog, game_for
from test_gameplay import call, gameplay_master, setup_gameplay, snapshot
from test_training_encounter_modes import active, clock, edit, prepare as prepare_party, records, table, victory

STORY = 1022001
FIRST = 102200101
FINAL = 102200112


def prepare(context, chapters=False):
    prepare_party(context, chapters)
    edit(context, lambda state, _: put(state, "t_feature_locks", {"m_feature_lock_id": 9}, {"is_unlocked": True}))


def ordered_stages(context):
    return sorted(table(context, "m_side_story_stages"), key=lambda row: row["no"])


def top(context, **kwargs):
    return call(context, "api/side-story/top", {"m_side_story_id": STORY}, **kwargs)["side_story_stages"]


def clear_stage(context, definition):
    identifier = definition["id"]
    if definition["type"] == 1:
        return call(context, "api/side-story/clear-story", {"m_side_story_stage_id": identifier})
    result = call(context, "api/side-story/start-quest", {"m_side_story_stage_id": identifier, "party_no": 1})
    assert result["quest_type"] == 8 and result["quest_id"] == definition["master_id"]
    return call(context, "api/side-story/clear-quest", victory(context))


def advance(context, count=12):
    for stage in ordered_stages(context)[:count]:
        clear_stage(context, stage)


def feature(context):
    return json.loads(snapshot(context)[1])["features"]["side_story"]["data"]


def stamina(context):
    return next(row["amount"] for row in records(context)["t_timed_parameters"]["up"] if row["type"] == STAMINA_TYPE)


def mission_values(context):
    from dotabyss_ps.gameplay_catalog import GameplayCatalog
    from dotabyss_ps.gameplay_rules import DEFAULT_RULES

    state, saved = map(json.loads, snapshot(context))
    missions = Missions(state, saved, GameplayCatalog(context.tables, context.schema),
                        datetime(2026, 10, 31, 14, tzinfo=JST), context.identity["user_id"], DEFAULT_RULES)
    return {row["id"]: missions.value(row) for row in table(context, "m_missions") if row["situation_type"] == 280000}


def test_side_story_campaign_status_native_bindings_and_missions(setup_gameplay):
    context = setup_gameplay
    prepare(context)
    assert call(context, "api/side-story/list")["side_stories"] == [{"m_side_story_id": STORY, "is_new": 1, "is_completed": 0}]
    stage_rows = top(context)
    assert len(stage_rows) == 12
    assert [row["status"] for row in stage_rows] == [1] + [0] * 11
    assert top(context)[0]["status"] == 2
    rewards = []
    for index, stage in enumerate(ordered_stages(context)):
        if index == 11:
            assert call(context, "api/side-story/list")["side_stories"][0] == {"m_side_story_id": STORY, "is_new": 0, "is_completed": 1}
        result = clear_stage(context, stage)
        rewards.extend(result["first_rewards"])
        result_rows = top(context)
        assert all(row["status"] == 99 and row["is_reward_received"] == 1 for row in result_rows[:index + 1])
        if index < 11:
            assert result_rows[index + 1]["status"] == 1
    assert sum(row["amount"] for row in rewards if row["content_type"] == 10) == 600
    assert mission_values(context) == {70103: 1, 70123: 1, 70144: 1, 70165: 1}
    final = top(context)[-1]
    characters = {row["id"]: row["m_character_id"] for row in records(context)["t_characters"]["up"]}
    assert final["skip_party_characters"] == [characters[identifier] for identifier in (1, 2, 3)]


def test_side_story_rejects_locked_stages_wrong_types_and_missing_novel(setup_gameplay, monkeypatch):
    from dotabyss_ps.gameplay_catalog import GameplayCatalog

    context = setup_gameplay
    prepare(context)
    before = snapshot(context)
    call(context, "api/side-story/clear-story", {"m_side_story_stage_id": FIRST + 1}, expected=409)
    call(context, "api/side-story/start-quest", {"m_side_story_stage_id": FIRST, "party_no": 1}, expected=409)
    call(context, "api/side-story/skip-quest", {"m_side_story_stage_id": FINAL, "skip_count": 1}, expected=409)
    assert snapshot(context) == before
    original = GameplayCatalog.rows
    monkeypatch.setattr(GameplayCatalog, "rows", lambda self, name: [] if name == "m_novel_side_stories" else original(self, name))
    call(context, "api/side-story/clear-story", {"m_side_story_stage_id": FIRST}, expected=503)
    assert snapshot(context) == before


def test_side_story_replay_is_reward_safe_and_top_receipts_do_not_rewind(setup_gameplay):
    context = setup_gameplay
    prepare(context)
    headers = {**context.headers, "X-Request-Id": "side-story-top"}
    top(context, headers=headers)
    call(context, "api/side-story/clear-story", {"m_side_story_stage_id": FIRST})
    before = snapshot(context)
    assert not call(context, "api/side-story/clear-story", {"m_side_story_stage_id": FIRST})["first_rewards"]
    assert snapshot(context) == before
    assert top(context, headers=headers)[0]["status"] == 99
    assert feature(context)["stages"][str(FIRST)]["clears"] == 1


@pytest.mark.parametrize("endpoint", ["close-quest", "surrender-quest"])
def test_side_story_failed_battles_do_not_advance_or_grant_rewards(setup_gameplay, endpoint):
    context = setup_gameplay
    prepare(context)
    advance(context, 2)
    call(context, "api/side-story/start-quest", {"m_side_story_stage_id": FIRST + 2, "party_no": 1})
    state = feature(context)
    assert call(context, "api/side-story/" + endpoint, {"play_time": 3})["status"] == 0
    assert feature(context) == state
    assert top(context)[2]["status"] in (1, 2)
    assert not top(context)[2]["is_reward_received"]
    assert mission_values(context)[70103] == 0


def test_side_story_resume_validation_and_battle_receipt_isolation(setup_gameplay):
    context = setup_gameplay
    prepare(context)
    advance(context, 2)
    body = {"m_side_story_stage_id": FIRST + 2, "party_no": 1}
    started = call(context, "api/side-story/start-quest", body)
    assert call(context, "api/side-story/start-quest", body)["stage_detail"] == started["stage_detail"]
    with TestClient(create_app(context.settings)) as client:
        resumed = client.get("/api/battle-session/status", headers=context.headers).json()["contents"]
        assert resumed["quest_id"] == 1022001001 and resumed["stage_detail"] == started["stage_detail"]
    result = victory(context)
    invalid = copy.deepcopy(result)
    invalid["stage_results"]["destroyed_enemies"].append(987654321)
    before = snapshot(context)
    call(context, "api/side-story/clear-quest", invalid, expected=400)
    call(context, "api/main-story/close-battle", {"play_time": 0}, expected=409)
    assert snapshot(context) == before
    first = call(context, "api/side-story/clear-quest", result)
    assert first["first_rewards"]
    call(context, "api/side-story/start-quest", body)
    before = snapshot(context)
    assert call(context, "api/side-story/clear-quest", result)["first_rewards"] == first["first_rewards"]
    assert snapshot(context) == before and active(context) is not None
    assert not call(context, "api/side-story/clear-quest", victory(context))["first_rewards"]
    assert mission_values(context)[70103] == 2


def test_side_story_skip_requires_first_win_and_settles_count_and_stamina_once(setup_gameplay):
    context = setup_gameplay
    prepare(context)
    advance(context, 11)
    call(context, "api/side-story/skip-quest", {"m_side_story_stage_id": FINAL, "skip_count": 1}, expected=409)
    clear_stage(context, ordered_stages(context)[-1])
    before = stamina(context)
    previous_status = records(context)["t_statuses"]["up"][0]
    headers = {**context.headers, "X-Request-Id": "side-story-skip"}
    body = {"m_side_story_stage_id": FINAL, "skip_count": 2}
    result = call(context, "api/side-story/skip-quest", body, headers=headers)
    assert result["skip_count"] == 2 and not result["first_rewards"]
    assert result["grant_character_exp"] == 0
    current_status = records(context)["t_statuses"]["up"][0]
    restored = sum(row["stamina_max"] for row in table(context, "m_user_ranks")
                   if previous_status["user_rank"] < row["rank"] <= current_status["user_rank"])
    assert current_status["player_exp"] == previous_status["player_exp"] + 80
    assert stamina(context) == before - 80 + restored
    assert feature(context)["stages"][str(FINAL)]["clears"] == 3
    assert mission_values(context)[70165] == 3
    before = snapshot(context)
    call(context, "api/side-story/skip-quest", body, headers=headers)
    assert snapshot(context) == before
    call(context, "api/side-story/skip-quest", {"m_side_story_stage_id": FINAL, "skip_count": 100}, expected=409)
    call(context, "api/side-story/skip-quest", {"m_side_story_stage_id": FIRST + 2, "skip_count": 1}, expected=409)
    assert snapshot(context) == before


def test_side_story_concurrent_story_completion_only_grants_once(setup_gameplay):
    context = setup_gameplay
    prepare(context)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: call(context, "api/side-story/clear-story", {"m_side_story_stage_id": FIRST}), range(2)))
    assert sorted(len(response["first_rewards"]) for response in responses) == [0, 1]
    assert feature(context)["stages"][str(FIRST)]["clears"] == 1


def test_side_story_expiry_blocks_entry_but_preserves_active_settlement(setup_gameplay, monkeypatch):
    context = setup_gameplay
    prepare(context)
    advance(context, 2)
    call(context, "api/side-story/start-quest", {"m_side_story_stage_id": FIRST + 2, "party_no": 1})
    body = victory(context)
    clock(monkeypatch, datetime(2100, 1, 1, tzinfo=JST))
    assert not call(context, "api/side-story/list")["side_stories"]
    call(context, "api/side-story/top", {"m_side_story_id": STORY}, expected=409)
    call(context, "api/side-story/start-quest", {"m_side_story_stage_id": FIRST + 2, "party_no": 1}, expected=409)
    assert call(context, "api/side-story/clear-quest", body)["first_rewards"]


def test_side_story_state_rejects_corrupt_counts_without_resetting_progress(setup_gameplay):
    context = setup_gameplay
    prepare(context)
    advance(context, 1)
    edit(context, lambda _, saved: saved["features"]["side_story"]["data"]["stages"][str(FIRST)].update(clears=-1))
    before = snapshot(context)
    call(context, "api/side-story/list", expected=400)
    assert snapshot(context) == before


def test_all_side_story_dungeons_have_closed_native_references(battle_catalog):
    game = game_for(battle_catalog)
    quests = battle_catalog.rows("m_side_story_quests")
    assert len(quests) == 4
    for seed, quest in enumerate(quests, 1):
        stage = build_stage(game, quest["stage_type"], quest["stage_id"], seed, kind="side_story", quest_id=quest["id"])
        assert_links(stage)
        assert stage["enemies"]
        for enemy in stage["enemies"]:
            definition = battle_catalog.get("m_enemies", enemy["m_enemy_id"])
            assert battle_catalog.get("m_enemy_parameters", definition["m_enemy_parameter_id"])["hp"] > 0
