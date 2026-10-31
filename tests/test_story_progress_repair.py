import copy

import pytest

from dotabyss_ps.gameplay import Gameplay
from dotabyss_ps.gameplay_catalog import GameplayCatalog, GameplayError
from dotabyss_ps.gameplay_records import find, put
from dotabyss_ps.gameplay_rules import load_rules
from dotabyss_ps.gameplay_story_progress import completed, prepare_story_state
from dotabyss_ps.gameplay_tutorial import can_unlock_feature
from dotabyss_ps.newgame import build_initial_state
from test_gameplay import NOW, gameplay_master


@pytest.fixture
def story_game(gameplay_master):
    schema, tables = gameplay_master
    state = build_initial_state(tables, schema, 7, "Story repair", "2026-11-01 00:00:00", start_tutorial=False)
    game = Gameplay(state, {}, GameplayCatalog(tables, schema), 7, NOW, {**load_rules(), "unlock_common_features": 0})
    game.prepare()
    return game


def old_progress(game, sequence, claimed=False):
    put(game.state, "t_chapter_quests", {"m_chapter_quest_id": 1010101},
        {"m_chapter_id": 1, "m_chapter_area_id": 101, "sequence": sequence})
    prepare_story_state(game).pop("completion_version")
    game.progress["features"]["battle_rewards"] = {"version": 1, "data": {
        "first_claims": {"1010101:2": {"legacy": True}},
        "quest_claims": {"1010101": {"legacy": True}} if claimed else {},
    }}


def test_claimed_old_completion_is_migrated_and_returned_in_response(story_game):
    game = story_game
    old_progress(game, 4, claimed=True)
    ledger = copy.deepcopy(game.progress["features"]["battle_rewards"])
    response = game.execute("api/quest/top", "GET", {})
    assert completed(game, 1010101)
    assert response["records"]["t_chapter_quests"]["up"][0]["sequence"] == 99
    assert can_unlock_feature(game, 2)
    assert game.progress["features"]["battle_rewards"] == ledger
    stable = copy.deepcopy((game.state, game.progress))
    game.execute("api/quest/top", "GET", {})
    assert (game.state, game.progress) == stable


def test_battle_win_does_not_skip_unread_final_story(story_game):
    game = story_game
    old_progress(game, 3)
    game.execute("api/quest/top", "GET", {})
    assert find(game.state, "t_chapter_quests", m_chapter_quest_id=1010101)["sequence"] == 3
    assert not completed(game, 1010101)
    assert not can_unlock_feature(game, 2)
    assert can_unlock_feature(game, 8)
    assert game.progress["features"]["battle_rewards"]["data"]["quest_claims"] == {}


def test_pending_final_story_settles_once_and_releases_next_quest(story_game):
    game = story_game
    old_progress(game, 4)
    prepare_story_state(game)["pending_releases"]["1010101"] = [1010101]
    response = game.execute("api/main-story/clear", "POST", {"m_chapter_quest_id": 1010101})
    assert completed(game, 1010101)
    assert response["release_data"]["release_m_chapter_quest_ids"]
    assert response["receive_content_list"]
    stable = copy.deepcopy((game.state, game.progress))
    game.execute("api/main-story/clear", "POST", {"m_chapter_quest_id": 1010101})
    assert (game.state, game.progress) == stable


def test_cached_unrelated_request_includes_migrated_progress(story_game):
    game = story_game
    route, body = "api/character/new/check", {"t_character_ids": []}
    game.execute(route, "POST", body, "unchanged-request")
    old_progress(game, 4, claimed=True)
    response = game.execute(route, "POST", body, "unchanged-request")
    assert response["records"]["t_chapter_quests"]["up"][0]["sequence"] == 99


def test_imported_legacy_completion_and_explicit_later_edits_are_preserved(story_game):
    game = story_game
    old_progress(game, 4, claimed=True)
    game.progress["features"].pop("main_story")
    prepare_story_state(game)
    record = find(game.state, "t_chapter_quests", m_chapter_quest_id=1010101)
    assert record["sequence"] == 99
    record["sequence"] = 0
    prepare_story_state(game)
    assert record["sequence"] == 0


def test_unknown_completion_policy_is_not_silently_rewritten(story_game):
    game = story_game
    prepare_story_state(game)["completion_version"] = 2
    with pytest.raises(GameplayError):
        prepare_story_state(game)
