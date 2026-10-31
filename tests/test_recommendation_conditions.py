import copy
from datetime import timedelta

import pytest

from dotabyss_ps.event_master import reopened_tables
from dotabyss_ps.gameplay import Gameplay
from dotabyss_ps.gameplay_catalog import GameplayCatalog, GameplayError
from dotabyss_ps.gameplay_event_missions import record_clear
from dotabyss_ps.gameplay_events import event_data
from dotabyss_ps.gameplay_recommendations import collect, eligible, owner_context
from dotabyss_ps.gameplay_records import put
from dotabyss_ps.gameplay_rules import load_rules
from dotabyss_ps.gameplay_shop import shop_data, validate_purchase
from dotabyss_ps.gameplay_statistics import record
from dotabyss_ps.newgame import build_initial_state
from test_gameplay import NOW, gameplay_master


@pytest.fixture
def recommendation_game(gameplay_master):
    schema, tables = gameplay_master
    state = build_initial_state(tables, schema, 7, "Recommendation conditions", "2026-10-31 00:00:00", start_tutorial=False)
    game = Gameplay(state, {}, GameplayCatalog(tables, schema), 7, NOW, load_rules())
    game.prepare()
    return game


def offer(game, kind):
    return next(row for row in game.catalog.rows("m_payment_recommends") if row["condition_type"] == kind)


def joined(game, event_id):
    put(game.state, "t_events", {"m_event_id": event_id}, {
        "join_at": game.now.isoformat(), "is_event_stage_effect_enabled": 1,
        "created_at": game.now.isoformat(), "updated_at": game.now.isoformat(),
    })
    return event_data(game, event_id)


def test_specific_character_requirement_does_not_use_another_character(recommendation_game):
    game = recommendation_game
    definition = game.catalog.get("m_payment_recommends", 260003)
    target = definition["condition_id"]
    for character in game.state["records"]["t_characters"]["up"]:
        character["limit_break_count"] = 0
    other = next(row for row in game.state["records"]["t_characters"]["up"] if row["m_character_id"] != target)
    other["limit_break_count"] = 9
    assert not eligible(game, definition)
    assert eligible(game, game.catalog.get("m_payment_recommends", 200003))
    character = put(game.state, "t_characters", {"m_character_id": target}, {"limit_break_count": 3})
    assert not eligible(game, definition)
    character["limit_break_count"] = 4
    assert eligible(game, definition)


@pytest.mark.parametrize("kind,event_id,quest_id,situation", [
    (8, 300001, 4, 110001),
    (15, 500001, 101, 110002),
    (17, 900001, 90000101, 110004),
    (19, 100002, 100002101, 110000),
    (23, 1100001, 1100001101, 110201),
    (24, 800003, 80000301, 110003),
])
def test_event_progress_uses_its_own_namespace_and_correct_target(recommendation_game, kind, event_id, quest_id, situation):
    game = recommendation_game
    definition = offer(game, kind)
    assert owner_context(game.catalog, definition) == ("event", event_id)
    assert not eligible(game, definition)
    record(game.progress, game.now, situation, 100, definition["condition_id"])
    assert not eligible(game, definition)
    event = game.catalog.get("m_events", event_id)
    record_clear(game, event, quest_id, {"play_time": 30, "top_fc_damage": 0}, {})
    assert eligible(game, definition)


def test_story_stage_id_and_battle_id_are_distinct(recommendation_game):
    game = recommendation_game
    definition = offer(game, 8)
    assert definition["condition_id"] == 8
    record_clear(game, game.catalog.get("m_events", 300001), 8, {"play_time": 30}, {})
    assert not eligible(game, definition)
    record_clear(game, game.catalog.get("m_events", 300001), 4, {"play_time": 30}, {})
    assert eligible(game, definition)


def test_completed_native_records_and_private_counts_do_not_double_count(recommendation_game):
    game = recommendation_game
    definition = {**offer(game, 15), "condition_value": 2}
    entry = event_data(game, 500001)
    entry["quests"] = {"hunt:101": {"clears": 1}}
    put(game.state, "t_event_hunt_quests", {"m_event_hunt_quest_id": 101}, {"status": 99})
    record_clear(game, game.catalog.get("m_events", 500001), 101, {"play_time": 30}, {})
    assert not eligible(game, definition)
    entry["quests"]["hunt:101"]["clears"] = 2
    assert eligible(game, definition)


def test_mutant_uses_event_clear_count_instead_of_permanent_disaster(recommendation_game):
    game = recommendation_game
    definition = offer(game, 18)
    event_id = owner_context(game.catalog, definition)[1]
    record(game.progress, game.now, 160100, 20, definition["condition_id"])
    assert not eligible(game, definition)
    event_data(game, event_id)["mutant"] = {"quests": {str(definition["condition_id"]): {"clears": 1}}}
    assert eligible(game, definition)


def test_campaign_requires_its_own_clear_and_join_alone_is_insufficient(recommendation_game):
    from dotabyss_ps.gameplay_encounter_campaign import campaign_data

    game = recommendation_game
    definition = offer(game, 16)
    campaigns = campaign_data(game)
    target = str(definition["condition_id"])
    campaigns["entries"][target] = {"join_at": game.now.isoformat(), "bonus_progress": 0, "pending_bonus": None, "clear_counts": {}}
    campaigns["entries"][str(definition["condition_id"] + 1)] = {
        "join_at": game.now.isoformat(), "bonus_progress": 0, "pending_bonus": None, "clear_counts": {"different": 10},
    }
    assert not eligible(game, definition)
    campaigns["entries"][target]["clear_counts"]["first"] = 1
    assert eligible(game, definition)


def test_event_mission_recommendation_reads_completed_event_mission(recommendation_game):
    game = recommendation_game
    definition = offer(game, 22)
    joined(game, 900002)
    event = game.catalog.get("m_events", 900002)
    record_clear(game, event, 90000201, {"play_time": 30}, {}, count=2)
    assert not eligible(game, definition)
    record_clear(game, event, 90000201, {"play_time": 30}, {})
    assert eligible(game, definition)
    assert game.progress["mission_claims"] == {}


def test_box_opening_threshold_is_inclusive(recommendation_game):
    game = recommendation_game
    definition = offer(game, 14)
    data = event_data(game, definition["condition_id"])
    data["box"] = {"current_box_no": 1, "draws": {}}
    assert not eligible(game, definition)
    data["box"]["current_box_no"] = 2
    assert eligible(game, definition)


@pytest.mark.parametrize("identifier,threshold", [(260099, 2), (260100, 4), (260101, 6), (260102, 8)])
def test_board_milestones_use_explicit_local_thresholds(recommendation_game, identifier, threshold):
    game = recommendation_game
    definition = game.catalog.get("m_payment_recommends", identifier)
    assert definition["condition_value"] == 1
    game.rules["recommendation_board_sheet_requirements"] = {str(identifier): threshold}
    data = event_data(game, 700001)
    data["board"] = {"current_sheet": threshold - 1, "sheets": {}}
    assert not eligible(game, definition)
    data["board"]["current_sheet"] = threshold
    assert eligible(game, definition)


def test_all_current_offer_owners_resolve_without_aliasing(recommendation_game):
    game = recommendation_game
    definitions = game.catalog.rows("m_payment_recommends")
    owners = {row["id"]: owner_context(game.catalog, row) for row in definitions}
    assert len(owners) == 360
    assert sum(owner is not None for owner in owners.values()) == 144
    assert owners[260054] == ("event", 900001)
    assert owners[260106] == ("event", 900002)
    assert owners[260037] == ("campaign", 10003)


def test_corrected_eligibility_hides_previously_misreleased_offer_without_erasing_receipts(recommendation_game):
    game = recommendation_game
    definition = copy.deepcopy(game.catalog.get("m_payment_recommends", 260003))
    definition.update(start_at=(NOW - timedelta(days=1)).isoformat(), end_at=(NOW + timedelta(days=1)).isoformat())
    game.catalog.tables["m_payment_recommends"] = {definition["id"]: definition}
    data = shop_data(game)
    data["released"][str(definition["id"])] = (NOW + timedelta(minutes=5)).isoformat()
    data["counts"]["example"] = {"period": "once", "count": 1}
    original = copy.deepcopy(data)
    assert collect(game, data) == []
    assert data == original


def test_recommendation_timer_never_restarts_on_refresh_or_after_expiry(recommendation_game):
    game = recommendation_game
    definition = copy.deepcopy(offer(game, 5))
    game.catalog.tables["m_payment_recommends"] = {definition["id"]: definition}
    data = shop_data(game)
    assert collect(game, data) == [definition]
    deadline = data["released"][str(definition["id"])]
    game.now += timedelta(minutes=60)
    assert collect(game, data) == [definition]
    assert data["released"][str(definition["id"])] == deadline
    game.now += timedelta(minutes=61)
    assert collect(game, data) == []
    assert data["released"][str(definition["id"])] == deadline


def test_previously_released_global_milestone_keeps_its_original_window(recommendation_game):
    game = recommendation_game
    definition = copy.deepcopy(game.catalog.get("m_payment_recommends", 200003))
    game.catalog.tables["m_payment_recommends"] = {definition["id"]: definition}
    characters = game.state["records"]["t_characters"]["up"]
    characters[0]["limit_break_count"] = 1
    data = shop_data(game)
    assert collect(game, data) == [definition]
    deadline = data["released"][str(definition["id"])]
    for character in characters:
        character["limit_break_count"] = 0
    assert collect(game, data) == [definition]
    assert data["released"][str(definition["id"])] == deadline


def test_event_master_projection_reopens_only_owned_event_offers(gameplay_master):
    schema, tables = gameplay_master
    rules = {**load_rules(), "event_open_all": 0, "event_open_ids": [600002]}
    projected, _ = reopened_tables(tables, schema, rules)
    before, after = GameplayCatalog(tables, schema), GameplayCatalog(projected, schema)
    opened = after.get("m_payment_recommends", 260033)
    assert opened["end_at"].startswith("2099")
    assert after.get("m_payment_recommends", 260003) == before.get("m_payment_recommends", 260003)
    assert after.get("m_payment_recommends", 260037) == before.get("m_payment_recommends", 260037)


def test_campaign_selection_does_not_start_another_campaign_offer_timer(recommendation_game):
    from dotabyss_ps.gameplay_encounter_campaign import campaign_data

    game = recommendation_game
    definition = copy.deepcopy(offer(game, 16))
    definition.update(start_at="2020-01-01 00:00:00", end_at="2099-12-31 23:59:59")
    game.catalog.tables["m_payment_recommends"] = {definition["id"]: definition}
    campaigns = campaign_data(game)
    identifier = definition["condition_id"]
    campaigns["entries"][str(identifier)] = {
        "join_at": game.now.isoformat(), "bonus_progress": 0, "pending_bonus": None, "clear_counts": {"first": 1},
    }
    campaigns["current_id"] = identifier + 1
    data = shop_data(game)
    assert collect(game, data) == []
    assert data["released"] == {}
    campaigns["current_id"] = identifier
    assert collect(game, data) == [definition]


def test_reopened_offer_expiry_and_purchase_limit_survive_event_reset(recommendation_game):
    game = recommendation_game
    definition = copy.deepcopy(offer(game, 14))
    definition.update(start_at="2020-01-01 00:00:00", end_at="2099-12-31 23:59:59")
    game.catalog.tables["m_payment_recommends"] = {definition["id"]: definition}
    data = joined(game, definition["condition_id"])
    data["box"] = {"current_box_no": 2, "draws": {}}
    shop = shop_data(game)
    assert collect(game, shop) == [definition]
    payment = next(row for row in game.catalog.rows("m_payments") if row["payment_type"] == 2 and row["master_id"] == definition["id"])
    shop["counts"][str(payment["id"])] = {"period": "once", "count": 1}
    with pytest.raises(GameplayError, match="already been purchased"):
        validate_purchase(game, payment["id"])
    data["epoch"] += 1
    deadline = shop["released"][str(definition["id"])]
    game.now += timedelta(minutes=definition["valid_minutes"] + 1)
    assert collect(game, shop) == []
    assert shop["released"][str(definition["id"])] == deadline
    assert shop["counts"][str(payment["id"])]["count"] == 1
