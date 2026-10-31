import copy
import json

import pytest

from dotabyss_ps.gameplay import Gameplay
from dotabyss_ps.gameplay_catalog import GameplayCatalog, GameplayError
from dotabyss_ps.gameplay_characters import ensure_abilities
from dotabyss_ps.gameplay_records import find, grant, put, rows
from dotabyss_ps.gameplay_research import ensure_research_rewards
from test_economy_features import give, update
from test_gameplay import NOW, call, gameplay_master, setup_gameplay, snapshot
from test_permanent_features import records, seed


def character(context, identifier):
    give(context, [{"content_type": 40, "content_id": identifier, "amount": 1}])
    return next(row for row in records(context)["t_characters"]["up"] if row["m_character_id"] == identifier)


def ability(context, identifier):
    return next(row for row in records(context)["t_character_abilities"]["up"] if row["m_character_ability_id"] == identifier)


def set_intimacy(context, owned_id, points):
    update(context, lambda state, progress: find(state, "t_characters", id=owned_id).update(intimacy_point=points))


def local_game(context):
    call(context, "api/user")
    state, progress = map(json.loads, snapshot(context))
    return Gameplay(state, progress, GameplayCatalog(context.tables, context.schema), context.identity["user_id"], NOW)


def test_unconditional_secondary_ability_starts_open(setup_gameplay):
    c = setup_gameplay
    character(c, 1300033)
    row = ability(c, 130003302)
    assert (row["level"], row["possible_max_level"]) == (1, 10)
    locked = ability(c, 130003303)
    assert (locked["level"], locked["possible_max_level"]) == (0, 0)


@pytest.mark.parametrize("points,level,maximum", [(0, 0, 0), (349, 0, 0), (350, 1, 5), (2649, 1, 5), (2650, 1, 10)])
def test_release_limits_follow_intimacy_thresholds(setup_gameplay, points, level, maximum):
    c = setup_gameplay
    owned = character(c, 1300033)
    set_intimacy(c, owned["id"], points)
    row = ability(c, 130003303)
    assert (row["level"], row["possible_max_level"]) == (level, maximum)


def test_normal_character_level_does_not_unlock_intimacy_skill(setup_gameplay):
    c = setup_gameplay
    owned = character(c, 1200003)
    seed(c, character_exp=1000000, gold=1000000)
    call(c, "api/character/level/up", {"t_character_id": owned["id"], "exec_count": 4})
    row = ability(c, 120000302)
    assert (row["level"], row["possible_max_level"]) == (0, 0)


def test_gift_updates_ability_and_bond_in_same_response_once(setup_gameplay):
    c = setup_gameplay
    owned = character(c, 1200003)
    seed(c, items={140001: 14})
    headers = {**c.headers, "x-request-id": "gift-open-ability"}
    body = {"t_character_id": owned["id"], "use_items": [{"m_item_id": 140001, "amount": 14}]}
    result = call(c, "api/character/gift/give", body, headers=headers)
    changed = result["records"]
    skill = next(row for row in changed["t_character_abilities"]["up"] if row["m_character_ability_id"] == 120000302)
    assert skill["level"] == 1 and skill["possible_max_level"] > 0
    changed_character = next(row for row in changed["t_characters"]["up"] if row["id"] == owned["id"])
    assert changed_character["intimacy_point"] == 350 and changed_character["bond_point"] == 1
    after = snapshot(c)
    assert call(c, "api/character/gift/give", body, headers=headers) == result
    assert snapshot(c) == after


def test_legacy_free_unlocks_are_corrected_and_invested_levels_preserved(setup_gameplay):
    c = setup_gameplay
    owned = character(c, 1300033)
    game = local_game(c)
    free = find(game.state, "t_character_abilities", m_character_ability_id=130003302)
    invested = find(game.state, "t_character_abilities", m_character_ability_id=130003303)
    free.update(level=0, possible_max_level=0)
    invested.update(level=7, possible_max_level=10)
    game.progress["features"]["character"]["data"].pop("ability_releases")
    ensure_abilities(game)
    assert (free["level"], free["possible_max_level"]) == (1, 10)
    assert (invested["level"], invested["possible_max_level"]) == (7, 7)
    policy = copy.deepcopy(game.progress["features"]["character"]["data"]["ability_releases"])
    body = {"t_character_id": owned["id"], "orders": [{"t_character_ability_id": invested["id"], "exec_count": 1}]}
    with pytest.raises(GameplayError, match="Ability level cap exceeded"):
        game.execute("api/character/ability/level/up", "POST", body)
    ensure_abilities(game)
    assert game.progress["features"]["character"]["data"]["ability_releases"] == policy
    find(game.state, "t_characters", id=owned["id"]).update(intimacy_point=2650)
    ensure_abilities(game)
    assert (invested["level"], invested["possible_max_level"]) == (7, 10)


def test_legacy_unearned_free_level_is_removed_without_future_grandfathering(setup_gameplay):
    c = setup_gameplay
    character(c, 1300033)
    game = local_game(c)
    row = find(game.state, "t_character_abilities", m_character_ability_id=130003303)
    row.update(level=1, possible_max_level=10)
    game.progress["features"]["character"]["data"].pop("ability_releases")
    ensure_abilities(game)
    assert (row["level"], row["possible_max_level"]) == (0, 0)
    assert game.progress["features"]["character"]["data"]["ability_releases"]["retained_levels"] == {}
    grant(game.state, [{"content_type": 40, "content_id": 1200003, "amount": 1}], game.catalog, game.user_id, game.now, game.progress)
    ensure_abilities(game)
    added = find(game.state, "t_character_abilities", m_character_ability_id=120000302)
    assert (added["level"], added["possible_max_level"]) == (0, 0)
    row.update(level=2, possible_max_level=10)
    with pytest.raises(GameplayError, match="exceeds its release limit"):
        ensure_abilities(game)


def test_locked_sixth_level_does_not_consume_upgrade_materials(setup_gameplay):
    c = setup_gameplay
    owned = character(c, 1300033)
    set_intimacy(c, owned["id"], 350)
    row = ability(c, 130003303)
    update(c, lambda state, progress: find(state, "t_character_abilities", id=row["id"]).update(level=5))
    before = snapshot(c)
    call(c, "api/character/ability/level/up", {"t_character_id": owned["id"], "orders": [
        {"t_character_ability_id": row["id"], "exec_count": 1}]}, expected=409)
    assert snapshot(c) == before


def prepare_mana_research(context):
    catalog = GameplayCatalog(context.tables, context.schema)
    seed(context, items={50003: 50, 50006: 50, 50009: 50, 50013: 30, 50011: 30, 50012: 30})
    def ready(state, progress):
        put(state, "t_plans", {"m_plan_id": 10011}, {"step": 2})
        for step in catalog.rows("m_plan_steps"):
            if step["m_plan_id"] == 10011 and step["step"] <= 2:
                progress["mission_claims"][f"once:{step['m_mission_id']}"] = []
    update(context, ready)


def test_research_mana_rewards_arrive_once_and_obey_costs_and_maximum(setup_gameplay):
    c = setup_gameplay
    prepare_mana_research(c)
    body = {"m_building_id": 1000, "skill_type": 12001}
    headers = {**c.headers, "x-request-id": "research-mana-one"}
    result = call(c, "api/research/upgrade", body, headers=headers)
    assert [row["m_mana_gem_id"] for row in result["records"]["t_mana_gems"]["up"]] == [304]
    after = snapshot(c)
    assert call(c, "api/research/upgrade", body, headers=headers) == result
    assert snapshot(c) == after
    result = call(c, "api/research/upgrade", body)
    assert [row["m_mana_gem_id"] for row in result["records"]["t_mana_gems"]["up"]] == [204]
    data = records(c)
    amounts = {row["m_item_id"]: row["amount"] for row in data["t_items"]["up"]}
    assert all(amounts.get(identifier, 0) == 0 for identifier in (50003, 50006, 50009, 50013, 50011, 50012))
    assert sum(row["m_mana_gem_id"] == 304 for row in data["t_mana_gems"]["up"]) == 1
    assert sum(row["m_mana_gem_id"] == 204 for row in data["t_mana_gems"]["up"]) == 1
    before = snapshot(c)
    call(c, "api/research/upgrade", body, expected=409)
    assert snapshot(c) == before


def test_research_backfills_already_reached_rewards_idempotently(setup_gameplay):
    game = local_game(setup_gameplay)
    put(game.state, "t_building_skills", {"m_building_id": 1000, "skill_type": 12001},
        {"skill_level": 2, "checked_skill_level": 2}, game.user_id)
    assert not any(row["m_mana_gem_id"] in (304, 204) for row in rows(game.state, "t_mana_gems"))
    ensure_research_rewards(game)
    before = copy.deepcopy(game.state)
    ensure_research_rewards(game)
    assert game.state == before
    assert {row["m_mana_gem_id"] for row in rows(game.state, "t_mana_gems")} >= {304, 204}


def test_research_failed_costs_do_not_grant_mana(setup_gameplay):
    c = setup_gameplay
    prepare_mana_research(c)
    seed(c, items={50003: 49})
    before = snapshot(c)
    call(c, "api/research/upgrade", {"m_building_id": 1000, "skill_type": 12001}, expected=409)
    assert snapshot(c) == before
