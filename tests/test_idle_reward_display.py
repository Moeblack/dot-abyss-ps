import copy
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from dotabyss_ps.gameplay import Gameplay
from dotabyss_ps.gameplay_catalog import GameplayCatalog, GameplayError
from dotabyss_ps.gameplay_content import EQUIPMENT, create_equipment
from dotabyss_ps.gameplay_idle import progress as idle_progress
from dotabyss_ps.gameplay_presents import presents
from dotabyss_ps.gameplay_records import find, grant, rows
from test_economy_features import update
from test_gameplay import NOW, call, gameplay_master, setup_gameplay, snapshot
from test_idle_effect_lifecycle import dispatch
from test_newgame import make_state


@pytest.fixture(scope="module")
def idle_display_template(gameplay_master):
    schema, tables = gameplay_master
    game = Gameplay(make_state(gameplay_master), {}, GameplayCatalog(tables, schema), 42, NOW)
    game.prepare()
    return game


@pytest.fixture
def display_game(idle_display_template):
    template = idle_display_template
    return Gameplay(copy.deepcopy(template.state), copy.deepcopy(template.progress), template.catalog,
                    template.user_id, template.now, copy.deepcopy(template.rules))


def equipment_reward(game, kind, amount):
    _, plural = EQUIPMENT[kind]
    identifier = min(row["id"] for row in game.catalog.rows("m_" + plural))
    return {"content_type": kind, "content_id": identifier, "amount": amount}


def queue(game, rewards):
    idle_progress(game)["pending"] = copy.deepcopy(rewards)


def receive(game, operation_id=""):
    return game.execute("api/idle-exploration/receive", "POST", {}, operation_id)


def assert_equipment_links(game, response):
    seen = set()
    for reward in response["drops"]:
        kind = reward["content_type"]
        if kind not in EQUIPMENT:
            continue
        singular, plural = EQUIPMENT[kind]
        identifier = reward["additional_info"]["t_" + singular + "_id"]
        assert reward["amount"] == 1
        assert (kind, identifier) not in seen
        seen.add((kind, identifier))
        owned = find(game.state, "t_" + plural, id=identifier)
        assert owned is not None
        assert owned["m_" + singular + "_id"] == reward["content_id"]
    return seen


def old_response(response):
    result = copy.deepcopy(response)
    grouped = {}
    ordinary = []
    for reward in result["drops"]:
        if reward["content_type"] not in EQUIPMENT:
            ordinary.append(reward)
            continue
        key = (reward["content_type"], reward["content_id"])
        if key not in grouped:
            grouped[key] = {**reward, "amount": 0, "additional_info": None}
        grouped[key]["amount"] += reward["amount"]
    result["drops"] = ordinary + list(grouped.values())
    return result


@pytest.mark.parametrize("kind", [70, 80, 90])
def test_idle_equipment_drops_bind_each_new_instance_and_keep_master_id(display_game, kind):
    game = display_game
    reward = equipment_reward(game, kind, 3)
    _, plural = EQUIPMENT[kind]
    create_equipment(game.state, kind, reward["content_id"], game.catalog, game.user_id, game.progress)
    before = {row["id"] for row in rows(game.state, "t_" + plural)}
    queue(game, [reward])
    response = receive(game)
    links = assert_equipment_links(game, response)
    created = {row["id"] for row in rows(game.state, "t_" + plural)} - before
    assert links == {(kind, identifier) for identifier in created}
    assert len(created) == 3
    assert {row["content_id"] for row in response["drops"]} == {reward["content_id"]}
    assert {row["id"] for row in response["records"]["t_" + plural]["up"]} == created
    assert receive(game)["drops"] == []


@pytest.mark.parametrize("occupied", [98, 100])
def test_idle_full_inventory_mails_overflow_without_unowned_equipment_icons(display_game, occupied):
    game = display_game
    existing = equipment_reward(game, 70, 1)
    for _ in range(occupied):
        create_equipment(game.state, 70, existing["content_id"], game.catalog, game.user_id, game.progress)
    reward = equipment_reward(game, 80, 3)
    queue(game, [reward])
    response = receive(game)
    links = assert_equipment_links(game, response)
    assert len(links) == 100 - occupied
    assert sum(len(rows(game.state, "t_" + plural)) for _, plural in EQUIPMENT.values()) == 100
    overflow = [row for row in presents(game) if row["title"] == "Inventory overflow"]
    assert sum(row["amount"] for row in overflow) == 3 - (100 - occupied)
    assert all(row["content_id"] == reward["content_id"] and row["accepted_at"] is None for row in overflow)


def test_idle_ordinary_rewards_and_mixed_equipment_keep_their_original_amounts(display_game):
    game = display_game
    pending = [equipment_reward(game, kind, 2) for kind in EQUIPMENT]
    pending += [{"content_type": 50, "content_id": 0, "amount": 1020},
                {"content_type": 110, "content_id": 0, "amount": 1278},
                {"content_type": 30, "content_id": 180001, "amount": 84}]
    queue(game, pending)
    response = receive(game, "mixed-idle-reward")
    assert response["grant_gold"] == 1020
    assert response["grant_character_exp"] == 1278
    assert len(assert_equipment_links(game, response)) == 6
    ordinary = [row for row in response["drops"] if row["content_type"] == 30]
    assert len(ordinary) == 1 and ordinary[0]["amount"] == 84 and ordinary[0]["additional_info"] is None
    before = copy.deepcopy((game.state, game.progress))
    assert receive(game, "mixed-idle-reward") == response
    assert (game.state, game.progress) == before


def test_boost_binds_equipment_and_retries_do_not_consume_or_grant_twice(display_game):
    game = display_game
    dispatch(game)
    grant(game.state, [{"content_type": 30, "content_id": 60001, "amount": 2}],
          game.catalog, game.user_id, game.now, game.progress)
    queue(game, [equipment_reward(game, 70, 2)])
    body = {"boosts": [{"party_no": 1, "use_items": [{"m_item_id": 60001, "amount": 1}]}]}
    response = game.execute("api/idle-exploration/boost", "POST", body, "equipment-boost")
    assert len(assert_equipment_links(game, response)) >= 2
    assert find(game.state, "t_items", m_item_id=60001)["amount"] == 1
    before = copy.deepcopy((game.state, game.progress))
    repeated = game.execute("api/idle-exploration/boost", "POST", body, "equipment-boost")
    assert repeated == response
    assert (game.state, game.progress) == before


@pytest.mark.parametrize("kind", [70, 80, 90])
def test_old_idle_receipt_recovers_unique_instance_links_without_regrant(display_game, kind):
    game = display_game
    queue(game, [equipment_reward(game, kind, 2)])
    first = receive(game, "legacy-idle-equipment")
    legacy = old_response(first)
    game.progress["operation_receipts"]["legacy-idle-equipment"]["response"] = legacy
    before = copy.deepcopy(game.state)
    pending = copy.deepcopy(idle_progress(game)["pending"])
    repeated = receive(game, "legacy-idle-equipment")
    assert assert_equipment_links(game, repeated) == assert_equipment_links(game, first)
    assert game.state == before
    assert idle_progress(game)["pending"] == pending == []


def test_replaying_an_old_receipt_skips_equipment_already_removed(display_game):
    game = display_game
    queue(game, [equipment_reward(game, 70, 2)])
    first = receive(game, "sold-idle-equipment")
    game.progress["operation_receipts"]["sold-idle-equipment"]["response"] = old_response(first)
    removed = first["drops"][0]["additional_info"]["t_weapon_id"]
    rows(game.state, "t_weapons")[:] = [row for row in rows(game.state, "t_weapons") if row["id"] != removed]
    rows(game.state, "t_weapon_enchantments")[:] = [row for row in rows(game.state, "t_weapon_enchantments") if row["t_weapon_id"] != removed]
    before = copy.deepcopy(game.state)
    response = receive(game, "sold-idle-equipment")
    links = assert_equipment_links(game, response)
    assert len(links) == 1 and (70, removed) not in links
    assert game.state == before


def test_ambiguous_old_equipment_receipt_does_not_guess_or_issue_items(display_game):
    game = display_game
    queue(game, [equipment_reward(game, 70, 2)])
    first = receive(game, "ambiguous-idle-equipment")
    legacy = old_response(first)
    legacy["drops"][0]["amount"] = 1
    game.progress["operation_receipts"]["ambiguous-idle-equipment"]["response"] = legacy
    before = copy.deepcopy(game.state)
    with pytest.raises(GameplayError):
        receive(game, "ambiguous-idle-equipment")
    assert game.state == before


def test_concurrent_idle_receives_only_deliver_each_equipment_instance_once(setup_gameplay):
    context = setup_gameplay
    reward = {"content_type": 70, "content_id": 11010110, "amount": 2}
    call(context, "api/idle-exploration", {"sequence": ""})
    update(context, lambda state, progress: progress["features"]["idle"]["data"].update(pending=[reward]))
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: call(context, "api/idle-exploration/receive"), range(4)))
    delivered = [drop for response in responses for drop in response["drops"] if drop["content_type"] == 70]
    assert len(delivered) == 2
    identifiers = {drop["additional_info"]["t_weapon_id"] for drop in delivered}
    assert len(identifiers) == 2
    state, progress = map(json.loads, snapshot(context))
    assert identifiers <= {row["id"] for row in rows(state, "t_weapons")}
    assert all(drop["amount"] == 1 and drop["content_id"] == 11010110 for drop in delivered)
    assert progress["features"]["idle"]["data"]["pending"] == []
