import copy
from datetime import timedelta

import pytest

from dotabyss_ps.gameplay import Gameplay
from dotabyss_ps.gameplay_battle_notifications import battle_notifications
from dotabyss_ps.gameplay_catalog import GameplayCatalog
from dotabyss_ps.gameplay_effects import settle_before_effect_change, vip_bonus
from dotabyss_ps.gameplay_idle import (
    assign, encounter_capacity, party_edit, progress, receive, reconcile, status, world,
)
from dotabyss_ps.gameplay_records import find, put, rows
from dotabyss_ps.gameplay_shop import purchase, vip_record
from dotabyss_ps.gameplay_story_progress import clear_quest, sequences, update_sequence
from test_gameplay import NOW, gameplay_master
from test_newgame import make_state


@pytest.fixture
def game(gameplay_master):
    schema, tables = gameplay_master
    result = Gameplay(make_state(gameplay_master), {}, GameplayCatalog(tables, schema), 42, NOW)
    result.prepare()
    return result


def dispatch(game, party_no=1):
    world(game)
    party_edit(game, {"party_no": party_no, "characters": [
        {"position_no": 1, "t_character_id": 1, "t_weapon_id": 0}]})
    assign(game, {"party_no": party_no, "assign_to": 101})
    return find(game.state, "t_idle_exploration_party_relations", party_no=party_no)


def set_capacity_research(game, level):
    put(game.state, "t_building_skills", {"m_building_id": 100, "skill_type": 1005},
        {"skill_level": level, "checked_skill_level": 0}, game.user_id)


@pytest.mark.parametrize("dispatched", [False, True])
def test_purchase_refreshes_vip_duration_without_opening_exploration(game, dispatched):
    relation = dispatch(game) if dispatched else None
    game.now = NOW + timedelta(hours=6)
    purchase(game, 80001)
    duration = game.rules["idle_max_seconds"] + vip_bonus(game, 2)
    assert duration > 13 * 3600
    assert status(game)["explorable_duration"] == duration
    if relation is None:
        assert not rows(game.state, "t_idle_exploration_party_relations")
        return
    assert relation["exploring_stop_at"] == (NOW + timedelta(seconds=duration)).isoformat()
    game.now = NOW + timedelta(hours=13)
    receive(game, {})
    assert relation["last_drop_at"] == game.now.isoformat()
    assert status(game)["exploring_state"] == 1
    assert receive(game, {})["grant_gold"] == 0


def test_vip_increase_does_not_restart_a_dispatch_that_already_stopped(game):
    relation = dispatch(game)
    stopped_at = relation["exploring_stop_at"]
    game.now = NOW + timedelta(hours=13)
    purchase(game, 80001)
    assert relation["exploring_stop_at"] == stopped_at
    assert relation["last_drop_at"] == stopped_at
    assert status(game)["exploring_state"] == 5


def test_vip_reduction_settles_old_duration_and_stops_at_change_time(game):
    vip_record(game)["total_point"] = 9000
    relation = dispatch(game)
    game.now = NOW + timedelta(hours=14)
    settle_before_effect_change(game)
    vip_record(game)["total_point"] = 0
    reconcile(game, settle_pending=False)
    assert relation["last_drop_at"] == game.now.isoformat()
    assert relation["exploring_stop_at"] == game.now.isoformat()
    assert status(game)["exploring_state"] == 5
    pending = copy.deepcopy(progress(game)["pending"])
    game.now += timedelta(hours=1)
    world(game)
    assert progress(game)["pending"] == pending


def test_capacity_reduction_withdraws_dispatch_preserving_team_and_remainder(game):
    set_capacity_research(game, 1)
    dispatch(game, 2)
    team = copy.deepcopy([row for row in rows(game.state, "t_idle_exploration_party_characters") if row["party_no"] == 2])
    game.now = NOW + timedelta(seconds=901)
    settle_before_effect_change(game)
    set_capacity_research(game, 0)
    reconcile(game, settle_pending=False)
    assert not rows(game.state, "t_idle_exploration_party_relations")
    assert status(game)["exploring_state"] == 0
    assert progress(game)["carry"]["2"]["seconds"] == 1
    assert [row for row in rows(game.state, "t_idle_exploration_party_characters") if row["party_no"] == 2] == team
    assert receive(game, {})["grant_gold"] == 166
    game.now += timedelta(hours=1)
    assert receive(game, {})["grant_gold"] == 0
    set_capacity_research(game, 1)
    reconcile(game, settle_pending=False)
    assign(game, {"party_no": 2, "assign_to": 101})
    relation = find(game.state, "t_idle_exploration_party_relations", party_no=2)
    assert relation["last_drop_at"] == (game.now - timedelta(seconds=1)).isoformat()


def story_before_level_up(game):
    quests = sorted((row for row in game.catalog.rows("m_chapter_quests") if row["m_chapter_area_id"] in (101, 102, 103)),
                    key=lambda row: (row["m_chapter_area_id"], row["progress_order"]))
    last = quests[-1]
    for quest in quests:
        area = game.catalog.get("m_chapter_areas", quest["m_chapter_area_id"])
        put(game.state, "t_chapter_quests", {"m_chapter_quest_id": quest["id"]}, {
            "m_chapter_id": area["m_chapter_id"], "m_chapter_area_id": area["id"],
            "sequence": len(sequences(game, quest["id"])) + int(quest != last)})
    return last["id"]


@pytest.mark.parametrize("entry", ["clear", "sequence", "world"])
def test_story_level_increase_keeps_old_ticks_and_uses_new_rewards_afterwards(game, entry):
    quest_id = story_before_level_up(game)
    dispatch(game)
    assert status(game)["level"] == 1
    game.now = NOW + timedelta(seconds=901)
    if entry == "clear":
        clear_quest(game, {"m_chapter_quest_id": quest_id})
    elif entry == "sequence":
        update_sequence(game, {"m_chapter_quest_id": quest_id, "sequence": 2})
    else:
        find(game.state, "t_chapter_quests", m_chapter_quest_id=quest_id)["sequence"] = 2
        world(game)
    assert status(game)["level"] == 2
    reward = receive(game, {})
    assert (reward["grant_gold"], reward["grant_character_exp"]) == (166, 208)
    assert next(row["amount"] for row in reward["drops"] if row["content_type"] == 30 and row["content_id"] == 180001) == 13
    game.now = NOW + timedelta(seconds=1800)
    reward = receive(game, {})
    assert (reward["grant_gold"], reward["grant_character_exp"]) == (222, 277)
    assert receive(game, {})["grant_gold"] == 0


def test_future_vip_encounter_capacity_is_cumulative(game):
    base = encounter_capacity(game)
    table = game.catalog.tables["m_vip_rank_bonuses"]
    for identifier, rank, amount in ((999991, 1, 2), (999992, 2, 1)):
        table[identifier] = {"id": identifier, "rank": rank, "bonus_type": 6, "bonus_param": amount}
    vip_record(game)["total_point"] = 0
    assert encounter_capacity(game) == base
    vip_record(game)["total_point"] = 9000
    assert encounter_capacity(game) == base + 3


def test_vip_notification_requires_an_actual_rank_increase(game):
    before = copy.deepcopy(game.state["records"])
    vip_record(game)["total_point"] = 9000
    after = copy.deepcopy(game.state["records"])
    assert battle_notifications(before, after, game.catalog)["vip_rank_up"] == [
        {"before_vip_rank": 0, "after_vip_rank": 5}]
    assert battle_notifications(after, after, game.catalog) is None
    assert battle_notifications(after, before, game.catalog) is None
