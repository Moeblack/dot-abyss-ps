import copy
import json

import pytest

from dotabyss_ps.admin_actions import apply_action
from dotabyss_ps.admin_validation import validate_import
from dotabyss_ps.gameplay import Gameplay
from dotabyss_ps.gameplay_catalog import GameplayCatalog, GameplayError
from test_gameplay import NOW, call, gameplay_master, setup_gameplay, snapshot


@pytest.fixture
def game(setup_gameplay):
    context = setup_gameplay
    call(context, "api/user")
    state, progress = map(json.loads, snapshot(context))
    return Gameplay(state, progress, GameplayCatalog(context.tables, context.schema), context.identity["user_id"], NOW)


def test_valid_import_is_read_only_and_accepts_empty_legacy_features(game):
    before = copy.deepcopy((game.state, game.progress, game.rules))
    validate_import(game)
    assert (game.state, game.progress, game.rules) == before
    game.progress["features"]["events"] = {"version": 1, "data": {}}
    game.progress["features"]["nether"] = {"version": 1, "data": {}}
    before = copy.deepcopy((game.state, game.progress, game.rules))
    validate_import(game)
    assert (game.state, game.progress, game.rules) == before


def test_import_rejects_malformed_feature_interiors_without_mutation(game):
    cases = {
        "battle": {"next_id": 0},
        "nether": {"run": {}},
        "tavern": {"day": "", "free_used": 0, "assignments": {}, "active": {}},
        "encounter": {"next_id": 1, "campaigns": {"current_id": 0, "entries": {"1": {}}, "origins": {}}},
        "equipment": {"next_ids": {"t_weapons": -1}},
        "party": {"next_ids": {"not_a_table": 1}},
        "research": {"tutorial_supplies": {"amount": 1, "granted_at": "not-a-date"}},
        "events": {"instances": {"300001": {"epoch": 0}}},
        "friends": {"unknown_future_state": True},
        "statistics": {"initialized": True, "totals": {"1:0:0": -1}, "days": {}},
    }
    for name, data in cases.items():
        candidate = copy.copy(game)
        candidate.state = copy.deepcopy(game.state)
        candidate.progress = copy.deepcopy(game.progress)
        candidate.progress["features"][name] = {"version": 1, "data": data}
        before = copy.deepcopy((candidate.state, candidate.progress))
        with pytest.raises(GameplayError):
            validate_import(candidate)
        assert (candidate.state, candidate.progress) == before


def test_import_checks_rewards_and_subscription_receipts(game):
    game.progress["features"]["login"] = {"version": 1, "data": {
        "bonuses": {}, "subscription_days": {"1:1:1": ["reset05:2026-10-30"]}, "friend_day": "reset05:2026-10-30", "history": []}}
    validate_import(game)
    game.progress["features"]["login"]["data"]["subscription_days"]["1:1:1"] = ["reset05:not-a-date"]
    with pytest.raises(GameplayError):
        validate_import(game)
    game.progress["features"].pop("login")
    game.progress["features"]["idle"] = {"version": 1, "data": {"pending": [{"content_type": 50, "content_id": 0, "amount": -1}],
        "logs": [], "next_sequence": 1, "carry": {}}}
    with pytest.raises(GameplayError):
        validate_import(game)


def test_import_validates_event_board_box_and_cross_event_nodes(game):
    story_id = next(row["id"] for row in game.catalog.rows("m_events") if row["type"] == 3)
    board_id = next(row["id"] for row in game.catalog.rows("m_events") if row["type"] == 4)
    apply_action(game, "event.join", {"ids": [story_id, board_id]})
    apply_action(game, "event.complete", {"event_id": story_id})
    validate_import(game)
    data = game.progress["features"]["events"]["data"]["instances"]
    data[str(board_id)]["board"] = {"current_sheet": 1, "sheets": {"1": {"template": 1, "contents": [], "received": []}}}
    with pytest.raises(GameplayError):
        validate_import(game)
    data[str(board_id)].pop("board")
    data[str(story_id)]["unknown_mode"] = {}
    with pytest.raises(GameplayError):
        validate_import(game)


def test_import_rejects_foreign_owner_and_incomplete_nether_snapshot(game):
    source = copy.deepcopy(game.state)
    game.state["records"]["t_statuses"]["up"][0]["user_id"] += 1
    with pytest.raises(GameplayError):
        validate_import(game)
    game.state = source
    original_master_id = game.state["records"]["t_characters"]["up"][0]["m_character_id"]
    game.state["records"]["t_characters"]["up"][0]["m_character_id"] = 999999999999
    with pytest.raises(GameplayError):
        validate_import(game)
    game.state["records"]["t_characters"]["up"][0]["m_character_id"] = original_master_id
    from dotabyss_ps.gameplay_nether_state import state

    data = state(game)
    data["run"] = {"id": 1, "map_id": 1, "party_no": 1, "party": {}, "hp": {}, "floors": [], "visited": [], "resolved": [],
        "rewards": [], "code_draws": 0, "code_roll": 0, "shop_purchases": {}, "cleared_count": 0, "event_battle": 0,
        "last_battle": None, "last_continue": None, "settled": False}
    with pytest.raises(GameplayError):
        validate_import(game)


def test_import_accepts_complete_nether_expedition_and_rejects_bad_hp(setup_gameplay):
    from test_nether_modes import start

    context = setup_gameplay
    start(context)
    state, progress = map(json.loads, snapshot(context))
    game = Gameplay(state, progress, GameplayCatalog(context.tables, context.schema), context.identity["user_id"], NOW)
    before = copy.deepcopy((state, progress))
    validate_import(game)
    assert (state, progress) == before
    run = progress["features"]["nether"]["data"]["run"]
    run["hp"][next(iter(run["hp"]))] = 1001
    with pytest.raises(GameplayError):
        validate_import(game)


def test_import_accepts_active_tavern_and_rejects_missing_worker(setup_gameplay):
    from test_tavern_features import configure

    context = setup_gameplay
    identifier = configure(context)
    call(context, "api/tavern/exec/work", {"use_ticket": 0, "t_tavern_daily_card_id": identifier})
    state, progress = map(json.loads, snapshot(context))
    game = Gameplay(state, progress, GameplayCatalog(context.tables, context.schema), context.identity["user_id"], NOW)
    before = copy.deepcopy((state, progress))
    validate_import(game)
    assert (state, progress) == before
    progress["features"]["tavern"]["data"]["active"]["workers"][0].pop("work")
    with pytest.raises(GameplayError):
        validate_import(game)
