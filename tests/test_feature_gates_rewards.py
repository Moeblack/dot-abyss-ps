import copy

import pytest

from dotabyss_ps.admin_actions import apply_action
from dotabyss_ps.admin_validation import validate_import
from dotabyss_ps.gameplay import Gameplay
from dotabyss_ps.gameplay_access import feature_available, reconcile_feature_rewards, staff_registered
from dotabyss_ps.gameplay_catalog import GameplayCatalog, GameplayError
from dotabyss_ps.gameplay_main_repeat import skipped_result
from dotabyss_ps.gameplay_records import find, grant, put, rows
from dotabyss_ps.gameplay_research import ensure_tutorial_supplies
from dotabyss_ps.gameplay_rules import load_rules
from dotabyss_ps.gameplay_tavern import staff, state as tavern_state, works
from dotabyss_ps.gameplay_tutorial import can_unlock_feature, unlock_feature
from dotabyss_ps.newgame import build_initial_state
from test_gameplay import NOW, call, gameplay_master, party_body, setup_gameplay, snapshot


@pytest.fixture
def game(gameplay_master):
    schema, tables = gameplay_master
    state = build_initial_state(tables, schema, 7, "Feature gates", "2026-11-01 00:00:00", start_tutorial=False)
    context = Gameplay(state, {}, GameplayCatalog(tables, schema), 7, NOW,
                       {**load_rules(), "unlock_common_features": 0})
    context.prepare()
    context.execute("api/party/exploration/edit", "POST", party_body())
    return context


def unlocked(game, feature, enabled=True):
    put(game.state, "t_feature_locks", {"m_feature_lock_id": feature}, {"is_unlocked": enabled})


def amount(game, item):
    record = find(game.state, "t_items", m_item_id=item)
    return record["amount"] if record else 0


@pytest.mark.parametrize("path,method,body", [
    ("api/nether", "GET", {}),
    ("api/nether/start", "POST", {"m_nether_id": 1, "m_nether_map_id": 1, "party_no": 1, "use_ticket": 0, "start_floor_level": 0}),
    ("api/nether-mission/list", "GET", {}),
    ("api/nether-mission/reward/receive", "POST", {"m_mission_ids": [93000001]}),
    ("api/nether/upgrade-sphere", "POST", {"m_nether_id": 1, "m_nether_map_id": 1, "m_nether_sphere_board_id": 10, "m_nether_sphere_id": 10011, "current_level": 0}),
    ("api/union-request/start-quest", "POST", {"m_union_request_stage_id": 10001, "party_no": 1}),
    ("api/union-request/repeat-quest", "POST", {"union_type": 1, "exec_count": 1}),
    ("api/side-story/list", "GET", {}),
    ("api/side-story/top", "POST", {"m_side_story_id": 1022001}),
    ("api/side-story/clear-story", "POST", {"m_side_story_stage_id": 102200101}),
    ("api/side-story/start-quest", "POST", {"m_side_story_stage_id": 102200103, "party_no": 1}),
    ("api/side-story/skip-quest", "POST", {"m_side_story_stage_id": 102200112, "skip_count": 1}),
])
def test_locked_mode_entry_points_reject_unmet_requirements(game, path, method, body):
    with pytest.raises(GameplayError, match="Feature unlock requirements"):
        game.execute(path, method, body)


@pytest.mark.parametrize("path,body", [("api/level-synchro/level-up", {"exec_count": 1}),
                                       ("api/level-synchro/upgrade", {})])
def test_synchronization_rejects_before_spending_without_research(game, path, body):
    before = copy.deepcopy(game.state["records"])
    with pytest.raises(GameplayError, match="Synchronization research"):
        game.execute(path, "POST", body)
    assert game.state["records"] == before


@pytest.mark.parametrize("feature,quest", [(4, 1030503), (5, 1060505), (9, 1010203)])
def test_native_completion_and_explicit_console_override_both_unlock(game, feature, quest):
    assert not feature_available(game, feature)
    unlocked(game, feature)
    assert feature_available(game, feature)
    unlocked(game, feature, False)
    definition = game.catalog.get("m_chapter_quests", quest)
    area = game.catalog.get("m_chapter_areas", definition["m_chapter_area_id"])
    put(game.state, "t_chapter_quests", {"m_chapter_quest_id": quest},
        {"m_chapter_id": area["m_chapter_id"], "m_chapter_area_id": area["id"], "sequence": 99})
    assert feature_available(game, feature)


def test_synchronization_unlocked_research_uses_normal_costs(game):
    put(game.state, "t_building_skills", {"m_building_id": 300, "skill_type": 11001},
        {"skill_level": 1, "checked_skill_level": 0}, game.user_id)
    current = rows(game.state, "t_level_synchros")[0]
    target = current["level"] + 1
    costs = [{field: entry[field] for field in ("content_type", "content_id", "amount")}
             for entry in game.catalog.rows("m_level_synchro_level_up_costs") if entry["level"] == target]
    grant(game.state, costs, game.catalog, game.user_id, game.now, game.progress)
    game.execute("api/level-synchro/level-up", "POST", {"exec_count": 1})
    assert current["level"] == target


def test_union_resume_settlement_and_exit_remain_available_after_manual_lock(game):
    unlocked(game, 4)
    request = {"m_union_request_stage_id": 10001, "party_no": 1}
    game.execute("api/union-request/start-quest", "POST", request)
    session = game.progress["features"]["battle"]["data"]["active"]
    unlocked(game, 4, False)
    resumed = game.execute("api/union-request/start-quest", "POST", request)
    assert resumed["quest_id"] == session["quest_id"]
    response = game.execute("api/union-request/clear-quest", "POST",
                            {"stage_results": skipped_result(session["stage_detail"], session["stage_type"])})
    assert response["first_rewards"]
    assert game.execute("api/union-request/close-quest", "POST", {"play_time": 0})["status"] == 0


def test_existing_nether_can_be_viewed_and_returned_after_manual_lock(game):
    unlocked(game, 5)
    game.execute("api/nether/start", "POST", {"m_nether_id": 1, "m_nether_map_id": 1, "party_no": 1,
                                               "use_ticket": 0, "start_floor_level": 0})
    unlocked(game, 5, False)
    assert game.execute("api/nether", "GET", {})["party_detail"]
    game.execute("api/nether/result", "POST", {"m_nether_id": 1, "m_nether_map_id": 1, "use_item_flag": 0})
    assert game.progress["features"]["nether"]["data"]["run"]["settled"]
    game.execute("api/nether/cancel", "POST", {})


def test_side_story_active_battle_settles_after_manual_lock(game):
    unlocked(game, 9)
    for identifier in (102200101, 102200102):
        game.execute("api/side-story/clear-story", "POST", {"m_side_story_stage_id": identifier})
    game.execute("api/side-story/start-quest", "POST", {"m_side_story_stage_id": 102200103, "party_no": 1})
    session = game.progress["features"]["battle"]["data"]["active"]
    unlocked(game, 9, False)
    result = game.execute("api/side-story/clear-quest", "POST",
                          {"stage_results": skipped_result(session["stage_detail"], session["stage_type"])})
    assert result["first_rewards"]
    assert game.execute("api/side-story/close-quest", "POST", {"play_time": 0})["status"] == 0


@pytest.mark.parametrize("configured", [0, 1, 5])
def test_tavern_registration_matches_native_level_one(game, configured):
    game.rules["tavern_staff_intimacy_level"] = configured
    characters = rows(game.state, "t_characters")
    for character in characters:
        character["intimacy_point"] = 0
    assert not staff(game)
    assert not staff_registered(game, find(game.state, "t_characters", m_character_id=1200002))
    for character, points in zip(characters, (50, 49, 50)):
        character["intimacy_point"] = points
    assert len(staff(game)) == 2
    assert not can_unlock_feature(game, 7)
    characters[1]["intimacy_point"] = 50
    assert len(staff(game)) == 3 and can_unlock_feature(game, 7)


def test_deregistered_staff_is_removed_only_after_active_work_finishes(game):
    character = rows(game.state, "t_characters")[0]
    character["intimacy_point"] = 0
    data = tavern_state(game)
    data["assignments"] = {"100": [character["m_character_id"]]}
    data["active"] = {}
    assert tavern_state(game)["assignments"]["100"] == [character["m_character_id"]]
    data["active"] = None
    assert tavern_state(game)["assignments"]["100"] == [0]
    assert works(game)[0]["t_character_id_1"] == 0


def test_unlock_rewards_are_delivered_once_and_survive_relocking(game):
    assert "m_feature_lock_rewards" in game.catalog.tables
    put(game.state, "t_chapter_quests", {"m_chapter_quest_id": 1010202},
        {"m_chapter_id": 1, "m_chapter_area_id": 102, "sequence": 99})
    before = amount(game, 140001)
    unlock_feature(game, {"m_feature_lock_id": 6})
    assert amount(game, 140001) == before + 12
    claims = game.progress["features"]["feature_unlock"]["data"]["reward_claims"]
    assert claims["2"]["source"] == "unlock"
    unlock_feature(game, {"m_feature_lock_id": 6})
    unlocked(game, 6, False)
    reconcile_feature_rewards(game)
    unlock_feature(game, {"m_feature_lock_id": 6})
    assert amount(game, 140001) == before + 12


@pytest.mark.parametrize("legacy_amount,grant_amount", [(0, 5), (2, 3), (5, 0), (20, 0)])
def test_legacy_unlock_backfill_accounts_for_proven_material_grants(game, legacy_amount, grant_amount):
    unlocked(game, 1)
    unlocked(game, 6)
    if legacy_amount:
        game.progress["features"].setdefault("research", {"version": 1, "data": {}})["data"]["tutorial_supplies"] = {
            "amount": legacy_amount, "granted_at": game.now.isoformat()}
    bones, gifts = amount(game, 50004), amount(game, 140001)
    reconcile_feature_rewards(game)
    assert amount(game, 50004) == bones + grant_amount
    assert amount(game, 140001) == gifts + 12
    claims = game.progress["features"]["feature_unlock"]["data"]["reward_claims"]
    assert claims["1"]["covered_amount"] == 5 - grant_amount
    assert claims["1"]["source"] == "legacy-backfill"
    stable = copy.deepcopy((game.state, game.progress))
    reconcile_feature_rewards(game)
    assert (game.state, game.progress) == stable


def test_existing_reward_receipt_does_not_refill_spent_items(game):
    unlocked(game, 6)
    reconcile_feature_rewards(game)
    find(game.state, "t_items", m_item_id=140001)["amount"] = 0
    reconcile_feature_rewards(game)
    assert amount(game, 140001) == 0


@pytest.mark.parametrize("supply_amount", [2, 5, 100])
@pytest.mark.parametrize("feature_first", [False, True])
def test_research_and_feature_rewards_share_material_credit_in_either_order(game, supply_amount, feature_first):
    game.rules.update(unlock_common_features=1, research_tutorial_material_amount=supply_amount)
    unlocked(game, 1)
    before = {item: amount(game, item) for item in (50001, 50004, 50007)}
    actions = [reconcile_feature_rewards, ensure_tutorial_supplies] if feature_first else [ensure_tutorial_supplies, reconcile_feature_rewards]
    for action in actions:
        action(game)
    assert {item: amount(game, item) - before[item] for item in before} == {
        50001: supply_amount, 50004: max(5, supply_amount), 50007: supply_amount}
    supply = game.progress["features"]["research"]["data"]["tutorial_supplies"]
    claim = game.progress["features"]["feature_unlock"]["data"]["reward_claims"]["1"]
    assert supply["feature_reward_bone_credit"] == (min(5, supply_amount) if feature_first else 0)
    assert claim["covered_amount"] == (0 if feature_first else min(5, supply_amount))
    find(game.state, "t_items", m_item_id=50004)["amount"] = 0
    game.rules["research_tutorial_material_amount"] = 200
    stable = copy.deepcopy((game.state, game.progress))
    for action in reversed(actions):
        action(game)
    assert (game.state, game.progress) == stable


def test_feature_reward_overflow_is_mailed_once(game):
    unlocked(game, 1)
    limit = game.catalog.get("m_items", 50004)["possession_limit"]
    grant(game.state, [{"content_type": 30, "content_id": 50004, "amount": limit - 2}],
          game.catalog, game.user_id, game.now, game.progress)
    reconcile_feature_rewards(game)
    assert amount(game, 50004) == limit
    entries = game.progress["features"]["presents"]["data"]["entries"]
    overflow = [entry for entry in entries if entry["content_type"] == 30 and entry["content_id"] == 50004]
    assert len(overflow) == 1 and overflow[0]["amount"] == 3
    stable = copy.deepcopy((game.state, game.progress))
    reconcile_feature_rewards(game)
    assert (game.state, game.progress) == stable


def test_invalid_research_credit_cannot_cover_feature_reward(game):
    unlocked(game, 1)
    game.progress["features"]["research"] = {"version": 1, "data": {"tutorial_supplies": {
        "amount": 2, "granted_at": game.now.isoformat(), "feature_reward_bone_credit": 3}}}
    with pytest.raises(GameplayError):
        reconcile_feature_rewards(game)


@pytest.mark.parametrize("feature_first", [False, True])
def test_feature_reward_admin_import_preserves_valid_ledgers_and_source(game, feature_first):
    game.rules.update(unlock_common_features=1, research_tutorial_material_amount=100)
    unlocked(game, 1)
    unlocked(game, 6)
    actions = [reconcile_feature_rewards, ensure_tutorial_supplies] if feature_first else [ensure_tutorial_supplies, reconcile_feature_rewards]
    for action in actions:
        action(game)
    before = copy.deepcopy((game.state, game.progress, game.rules))
    validate_import(game)
    assert (game.state, game.progress, game.rules) == before


@pytest.mark.parametrize("ledger,field,value", [
    ("claim", "covered_amount", 1),
    ("claim", "granted_amount", 4),
    ("claim", "covered_amount", -1),
    ("supply", "feature_reward_bone_credit", -1),
    ("supply", "feature_reward_bone_credit", 101),
])
def test_feature_reward_admin_import_rejects_invalid_accounting_without_mutation(game, ledger, field, value):
    game.rules.update(unlock_common_features=1, research_tutorial_material_amount=100)
    unlocked(game, 1)
    reconcile_feature_rewards(game)
    ensure_tutorial_supplies(game)
    target = (game.progress["features"]["feature_unlock"]["data"]["reward_claims"]["1"] if ledger == "claim"
              else game.progress["features"]["research"]["data"]["tutorial_supplies"])
    target[field] = value
    before = copy.deepcopy((game.state, game.progress, game.rules))
    with pytest.raises(GameplayError):
        validate_import(game)
    assert (game.state, game.progress, game.rules) == before


def test_feature_reward_admin_action_grants_once_across_relocking_and_import(game):
    before = {item: amount(game, item) for item in (50004, 140001)}
    args = {"ids": [1, 6], "unlocked": True}
    apply_action(game, "feature.set", args)
    assert {item: amount(game, item) - before[item] for item in before} == {50004: 5, 140001: 12}
    claims = copy.deepcopy(game.progress["features"]["feature_unlock"]["data"]["reward_claims"])
    apply_action(game, "feature.set", args)
    apply_action(game, "feature.set", {**args, "unlocked": False})
    find(game.state, "t_items", m_item_id=140001)["amount"] = 0
    apply_action(game, "feature.set", args)
    assert amount(game, 50004) == before[50004] + 5
    assert amount(game, 140001) == 0
    assert game.progress["features"]["feature_unlock"]["data"]["reward_claims"] == claims
    validate_import(game)


def test_legacy_reward_failure_rolls_back_save(setup_gameplay, monkeypatch):
    import dotabyss_ps.gameplay_access as access

    context = setup_gameplay
    call(context, "api/user")
    def change(state, progress):
        put(state, "t_feature_locks", {"m_feature_lock_id": 6}, {"is_unlocked": True})
        return {}
    context.store.game_state(context.identity["user_id"], context.record["revision"], lambda *_: None, change)
    before = snapshot(context)
    original = access.deliver
    def fail(game, rewards):
        original(game, rewards)
        raise GameplayError("Injected feature reward failure")
    monkeypatch.setattr(access, "deliver", fail)
    call(context, "api/user", expected=409)
    assert snapshot(context) == before
