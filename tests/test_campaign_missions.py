import copy
from datetime import datetime, timedelta

import pytest

from dotabyss_ps.gameplay import Gameplay
from dotabyss_ps.gameplay_campaign_missions import CampaignMissions
from dotabyss_ps.gameplay_catalog import GameplayCatalog, GameplayError, JST
from dotabyss_ps.gameplay_encounter_campaign import campaign_data, record_clear
from dotabyss_ps.gameplay_event_missions import evaluator_for_mission
from dotabyss_ps.gameplay_records import put
from dotabyss_ps.gameplay_rules import load_rules
from dotabyss_ps.gameplay_statistics import record
from dotabyss_ps.newgame import build_initial_state
from test_gameplay import NOW, call, gameplay_master, party_body, setup_gameplay, snapshot
from test_training_encounter_modes import edit


@pytest.fixture
def campaign_game(gameplay_master):
    schema, tables = gameplay_master
    state = build_initial_state(tables, schema, 7, "Campaign missions", "2026-10-31 00:00:00", start_tutorial=False)
    game = Gameplay(state, {}, GameplayCatalog(tables, schema), 7, NOW, {**load_rules(), "event_open_all": 1})
    game.prepare()
    for feature in (2, 1009):
        put(state, "t_feature_locks", {"m_feature_lock_id": feature}, {"is_unlocked": True})
    return game


def join(game, identifier):
    game.execute("api/idle-exploration/campaign/join", "POST", {"m_encounter_campaign_id": identifier})


def missions(game, category=10000, request_id=""):
    return game.execute(f"api/event-mission/list/{category}", "GET", {}, request_id)


def values(response):
    return {row["m_mission_id"]: row for field in ("limited", "limited_daily") for row in response[field]}


def campaign_clear(game, identifier, encounter_id=1, *, job=None):
    data = campaign_data(game)
    data["origins"][str(encounter_id)] = {"campaign_id": identifier, "bonus": True}
    row = {"id": encounter_id, "m_encounter_quest_id": 10000001}
    session = result = None
    if job is not None:
        enemy = next(row for row in game.catalog.rows("m_enemies") if row["m_job_id"] == job)
        session = {"stage_detail": {"enemies": [{"sid": 1, "m_enemy_id": enemy["id"]}]}}
        result = {"destroyed_enemies": [1]}
    record_clear(game, row, session=session, stage_results=result)
    return row


def test_list_uses_category_instead_of_event_identity(campaign_game):
    game = campaign_game
    game.execute("api/event/join", "POST", {"m_event_id": 1100001})
    response = missions(game, 110001)
    assert response["limited"]
    with pytest.raises(GameplayError, match="Unknown event mission category"):
        missions(game, 1100001)


@pytest.mark.parametrize("effective_dates", [False, True])
def test_shared_category_tracks_selected_campaign_with_raw_or_effective_master(campaign_game, effective_dates):
    game = campaign_game
    if effective_dates:
        for term in game.catalog.rows("m_mission_terms"):
            if term["category_group"] == 10000:
                term.update(start_at="2020-01-01 00:00:00", end_at="2099-01-01 00:00:00")
    join(game, 10000)
    first = values(missions(game, request_id="same-category"))
    assert set(first) == set(range(60000101, 60000107))
    join(game, 10001)
    second = values(missions(game, request_id="same-category"))
    assert set(second) == set(range(60000201, 60000207))
    assert not set(first) & set(second)
    with pytest.raises(GameplayError, match="does not belong"):
        game.execute("api/event-mission/reward/receive", "POST", {"category_group": 10000, "m_mission_ids": [60000101]})


def test_ambiguous_old_shared_category_requires_selection(campaign_game):
    game = campaign_game
    join(game, 10000)
    join(game, 10001)
    join(game, 10003)
    with pytest.raises(GameplayError, match="Select the encounter campaign"):
        missions(game)


def test_campaign_mission_requires_join_and_available_period(campaign_game):
    game = campaign_game
    with pytest.raises(GameplayError, match="Join this encounter campaign"):
        missions(game)
    join(game, 10000)
    assert len(missions(game)["limited"]) == 6
    game.rules["event_open_all"] = 0
    with pytest.raises(GameplayError, match="period has ended"):
        missions(game)


def test_reopened_campaign_ignores_original_term_expiry_and_claims_once(campaign_game):
    game = campaign_game
    join(game, 10000)
    row = campaign_clear(game, 10000)
    record_clear(game, row)
    assert values(missions(game))[60000101]["value"] == 1
    badges = game.execute("api/quest/top", "GET", {})["receivable_event_missions"]
    assert {"m_event_id": 10000, "is_receivable_mission": 1} in badges
    body = {"category_group": 10000, "m_mission_ids": [60000101]}
    game.execute("api/event-mission/reward/receive", "POST", body)
    stable = copy.deepcopy((game.state, game.progress))
    game.execute("api/event-mission/reward/receive", "POST", body)
    assert (game.state, game.progress) == stable
    assert values(missions(game))[60000101]["is_received"] == 1


def test_previous_campaign_progress_does_not_credit_next_campaign(campaign_game):
    game = campaign_game
    join(game, 10000)
    campaign_clear(game, 10000)
    record(game.progress, game.now, 150100, 9999)
    join(game, 10001)
    assert values(missions(game))[60000201]["value"] == 0
    join(game, 10000)
    assert values(missions(game))[60000101]["value"] == 1


def test_campaign_kills_are_scoped_to_origin_when_selected_campaign_changes(campaign_game):
    game = campaign_game
    join(game, 10002)
    join(game, 10005)
    campaign_clear(game, 10002, job=2045000)
    record(game.progress, game.now, 140001, 9999, 2045000)
    current = values(missions(game, 10005))
    assert all(row["value"] == 0 for row in current.values())
    old = evaluator_for_mission(game, 60000301)
    assert old.value(game.catalog.get("m_missions", 60000301)) == 1
    assert old.campaign["id"] == 10002


@pytest.mark.parametrize("legacy_session", [False, True])
def test_regular_encounter_uses_departure_campaign_and_legacy_session_is_not_reassigned(campaign_game, legacy_session):
    from dotabyss_ps.gameplay_battle_session import battle_state
    from dotabyss_ps.gameplay_main_repeat import skipped_result

    game = campaign_game
    join(game, 10000)
    game.execute("api/party/exploration/edit", "POST", party_body())
    definition = next(row for row in game.catalog.rows("m_encounter_quests") if row["stage_type"] == 1)
    row = put(game.state, "t_idle_exploration_encounters", {"id": 1}, {
        "encount_type": 1, "encount_party_no": 1, "m_idle_exploration_map_area_id": 701,
        "m_encounter_quest_id": definition["id"], "begin_at": game.now.isoformat(),
        "end_at": (game.now + timedelta(minutes=10)).isoformat()}, game.user_id)
    body = {"t_idle_exploration_encounter_id": row["id"], "party_no": 1}
    game.execute("api/idle-exploration/encounter/start-quest", "POST", body)
    active = battle_state(game)["active"]
    assert active["context"]["mission_campaign_id"] == 10000
    if legacy_session:
        active["context"].pop("mission_campaign_id")
    original = copy.deepcopy(active["context"])
    join(game, 10001)
    game.execute("api/idle-exploration/encounter/start-quest", "POST", body)
    assert active["context"] == original
    result = {"stage_results": skipped_result(active["stage_detail"], active["stage_type"])}
    game.execute("api/idle-exploration/encounter/clear-quest", "POST", result)
    stable = copy.deepcopy((game.state, game.progress))
    game.execute("api/idle-exploration/encounter/clear-quest", "POST", result)
    assert (game.state, game.progress) == stable
    first = evaluator_for_mission(game, 60000101)
    assert first.value(game.catalog.get("m_missions", 60000101)) == (0 if legacy_session else 1)
    assert values(missions(game))[60000201]["value"] == 0


def test_legacy_clear_counts_and_claims_are_preserved_without_guessing_kills(campaign_game):
    game = campaign_game
    join(game, 10000)
    entry = campaign_data(game)["entries"]["10000"]
    entry.pop("mission_stats", None)
    entry["clear_counts"] = {"10000001": 3}
    game.progress["mission_claims"]["once:60000101"] = []
    response = values(missions(game))
    assert response[60000101]["value"] == 3
    assert response[60000101]["is_received"] == 1
    assert response[60000102]["is_received"] == 0
    join(game, 10002)
    record(game.progress, game.now, 140001, 100, 2045000)
    assert values(missions(game))[60000301]["value"] == 0


def test_non_reopened_campaign_respects_actual_term_window(campaign_game):
    game = campaign_game
    game.rules["event_open_all"] = 0
    game.now = datetime(2026, 6, 20, tzinfo=JST)
    join(game, 10000)
    campaign = game.catalog.get("m_encounter_campaigns", 10000)
    term = game.catalog.get("m_mission_terms", campaign["m_mission_term_id"])
    evaluator = CampaignMissions(game, campaign)
    term["start_at"] = "2026-06-21 00:00:00"
    assert not evaluator.listing()["limited"]
    game.now = datetime(2026, 6, 21, tzinfo=JST)
    assert len(CampaignMissions(game, campaign).listing()["limited"]) == 6


def test_reward_batch_with_an_unearned_task_does_not_partially_claim(setup_gameplay):
    context = setup_gameplay
    call(context, "api/user")
    edit(context, lambda state, _: put(state, "t_feature_locks", {"m_feature_lock_id": 2}, {"is_unlocked": True}))
    call(context, "api/idle-exploration/campaign/join", {"m_encounter_campaign_id": 10000})
    edit(context, lambda _, progress: progress["features"]["encounter"]["data"]["campaigns"]["entries"]["10000"].update(clear_counts={"10000001": 1}))
    call(context, "api/event-mission/list/10000", method="GET")
    before = snapshot(context)
    call(context, "api/event-mission/reward/receive", {"category_group": 10000, "m_mission_ids": [60000101, 60000106]}, expected=409)
    assert snapshot(context) == before
