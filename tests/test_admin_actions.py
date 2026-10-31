import copy
import json

import pytest

from dotabyss_ps.admin_actions import action_specs, apply_action
from dotabyss_ps.gameplay import EXTRA_ROUTES, Gameplay
from dotabyss_ps.gameplay_catalog import GameplayCatalog, GameplayError
from dotabyss_ps.gameplay_library import eligible_novel
from dotabyss_ps.gameplay_records import find, put, rows
from dotabyss_ps.newgame import validate_saved_state
from test_gameplay import NOW, call, gameplay_master, setup_gameplay, snapshot


@pytest.fixture
def game(setup_gameplay):
    context = setup_gameplay
    call(context, "api/user")
    state, progress = map(json.loads, snapshot(context))
    return Gameplay(state, progress, GameplayCatalog(context.tables, context.schema), context.identity["user_id"], NOW)


def test_action_catalog_is_unique_typed_and_all_wrapped_routes_exist():
    specs = action_specs()
    assert len(specs) >= 100
    assert len({row["id"] for row in specs}) == len(specs)
    for row in specs:
        assert row["label"] and row["group"]
        assert row["timing"] in {"live", "page", "next_battle", "next_round", "master", "relogin"}
        assert len({item["name"] for item in row["fields"]}) == len(row["fields"])
        if row.get("route") in EXTRA_ROUTES:
            assert {item["name"] for item in row["fields"]} == EXTRA_ROUTES[row["route"]].fields
    skin = next(row for row in specs if row["id"] == "skin.unlock")
    assert skin["fields"][1]["source_by"]["values"]["weapon"] == "m_weapon_skins"


def test_balances_reject_bool_unknown_fields_and_overdraft(game):
    for args in ({"kind": 50, "amount": True}, {"kind": 50, "amount": 1, "user_id": 2}, {"kind": 999, "amount": 1}):
        with pytest.raises(GameplayError):
            apply_action(game, "currency.balance", args)
    apply_action(game, "currency.balance", {"kind": 50, "amount": 1234})
    apply_action(game, "currency.balance", {"kind": 50, "amount": 34, "mode": "subtract"})
    assert rows(game.state, "t_statuses")[0]["gold"] == 1200
    with pytest.raises(GameplayError):
        apply_action(game, "currency.balance", {"kind": 50, "amount": 1201, "mode": "subtract"})
    assert rows(game.state, "t_statuses")[0]["gold"] == 1200


def test_character_unlock_idempotence_and_growth_coupling(game):
    args = {"ids": [1300033]}
    apply_action(game, "character.unlock", args)
    character = find(game.state, "t_characters", m_character_id=1300033)
    before_items = copy.deepcopy(rows(game.state, "t_items"))
    apply_action(game, "character.unlock", args)
    assert rows(game.state, "t_items") == before_items
    apply_action(game, "character.edit", {"ids": [1300033], "level": 30, "intimacy_point": 2650, "max_skills": True})
    assert character["level"] == 30 and character["intimacy_point"] == 2650
    assert character["bond_point"] > 0
    abilities = [row for row in rows(game.state, "t_character_abilities") if row["t_character_id"] == character["id"]]
    assert abilities and all(row["level"] == row["possible_max_level"] for row in abilities)
    before = character["bond_point"]
    apply_action(game, "character.edit", {"ids": [1300033], "intimacy_point": 2650})
    assert character["bond_point"] == before
    result = apply_action(game, "character.edit", {"ids": [1300033], "intimacy_point": 0})
    assert result["warnings"]
    validate_saved_state(game.state)


def test_story_completion_suppresses_rewards_and_is_idempotent(game):
    quest = min(game.catalog.rows("m_chapter_quests"), key=lambda row: row["id"])
    before = rows(game.state, "t_statuses")[0]["free_virtual_money"]
    apply_action(game, "story.complete", {"ids": [quest["id"]]})
    ledger = game.progress["features"]["battle_rewards"]["data"]
    assert str(quest["id"]) in ledger["quest_claims"]
    assert rows(game.state, "t_statuses")[0]["free_virtual_money"] == before
    apply_action(game, "story.complete", {"ids": [quest["id"]], "grant_unclaimed_rewards": True})
    assert rows(game.state, "t_statuses")[0]["free_virtual_money"] == before
    with pytest.raises(GameplayError):
        apply_action(game, "story.reset_rewards", {"ids": [quest["id"]]})


def test_novel_unlock_fulfills_affection_and_keeps_read_claims(game):
    novel = next(row for row in game.catalog.rows("m_novel_characters") if row["m_character_id"] == 1300033 and row["condition_value"] >= 5)
    apply_action(game, "novel.unlock", {"kind": 3, "ids": [novel["id"]], "read_state": "read"})
    assert eligible_novel(game, 3, novel)
    assert game.progress["features"]["library"]["data"]["read_novels"][f"3:{novel['id']}"]
    apply_action(game, "novel.unlock", {"kind": 3, "ids": [novel["id"]], "read_state": "unread"})
    record = find(game.state, "t_novel_characters", m_novel_character_id=novel["id"])
    assert record["is_unread"] == 1
    assert game.progress["features"]["library"]["data"]["read_novels"][f"3:{novel['id']}"]


def test_equipment_edit_rejects_bad_parameter_and_delete_cleans_pool(game):
    identifier = game.catalog.rows("m_weapons")[0]["id"]
    apply_action(game, "equipment.create", {"kind": 70, "master_id": identifier, "amount": 1})
    gear = rows(game.state, "t_weapons")[-1]
    master = game.catalog.get("m_weapons", identifier)
    apply_action(game, "equipment.edit", {"kind": 70, "equipment_id": gear["id"], "parameter": master["max_parameter"], "appraised": True})
    assert gear["up_count"] == master["max_parameter"]
    with pytest.raises(GameplayError):
        apply_action(game, "equipment.edit", {"kind": 70, "equipment_id": gear["id"], "parameter": master["max_parameter"] + 1})
    apply_action(game, "equipment.delete", {"kind": 70, "equipment_id": gear["id"]})
    assert find(game.state, "t_weapons", id=gear["id"]) is None
    assert not any(row["t_weapon_id"] == gear["id"] for row in rows(game.state, "t_weapon_enchantments"))


def test_subscription_pass_and_paid_batches_keep_ownership_ledgers(game):
    subscription = game.catalog.rows("m_payment_subscriptions")[0]
    apply_action(game, "subscription.set", {"master_id": subscription["id"], "days": 90})
    benefit = find(game.state, "t_payment_subscription_benefits", m_payment_subscription_id=subscription["id"])
    assert benefit["continue_count"] == 1
    pass_id = game.catalog.rows("m_mission_passes")[0]["id"]
    apply_action(game, "pass.set", {"master_id": pass_id, "points": 400, "premium": True})
    entry = game.progress["features"]["mission_pass"]["data"][str(pass_id)]
    entry["rewards"]["1"] = {"normal": True, "premium": True}
    apply_action(game, "pass.set", {"master_id": pass_id, "premium": False})
    assert entry["rewards"]["1"]["premium"] is True
    apply_action(game, "currency.paid_grant", {"amount": 100, "days": 30})
    lot = rows(game.state, "t_payment_dmm_currencies")[-1]
    with pytest.raises(GameplayError):
        apply_action(game, "currency.paid_edit", {"lot_id": lot["id"], "remaining": 101})
    apply_action(game, "currency.paid_edit", {"lot_id": lot["id"], "remaining": 40})
    assert lot["acquire_amount"] == 100 and lot["rest_amount"] == 40


def test_event_reset_scopes_epoch_quest_and_claims(game):
    other_id = next(row["id"] for row in game.catalog.rows("m_events") if row["type"] == 4)
    apply_action(game, "event.join", {"ids": [300001, other_id]})
    apply_action(game, "event.complete", {"event_id": 300001})
    novel_ids = {row["m_novel_event_id"] for row in game.catalog.rows("m_event_story_novels")
                 if game.catalog.get("m_event_story_stages", row["m_event_story_stage_id"])["m_event_id"] == 300001}
    claims = game.progress["features"]["library"]["data"]["event_reward_claims"]
    assert novel_ids and all(str(identifier) in claims for identifier in novel_ids)
    other = copy.deepcopy(game.progress["features"]["events"]["data"]["instances"][str(other_id)])
    with pytest.raises(GameplayError):
        apply_action(game, "event.reset", {"event_id": 300001})
    apply_action(game, "event.reset", {"event_id": 300001, "allow_duplicate_rewards": True})
    data = game.progress["features"]["events"]["data"]["instances"]
    assert data["300001"]["epoch"] == 2
    assert data[str(other_id)] == other
    targets = {row["id"] for row in game.catalog.rows("m_event_story_stages") if row["m_event_id"] == 300001}
    assert all(row["status"] != 99 for row in rows(game.state, "t_event_story_stages") if row["m_event_story_stage_id"] in targets)


def test_battle_guard_rejects_before_mutating(game):
    game.progress["features"]["battle"] = {"version": 1, "data": {"active": {"id": 1}}}
    before = copy.deepcopy(game.state)
    with pytest.raises(GameplayError, match="战斗"):
        apply_action(game, "currency.balance", {"kind": 50, "amount": 1})
    assert game.state == before


def test_bulk_unlock_research_and_character_delete_validate_all_references(game):
    apply_action(game, "character.unlock", {"all": True})
    apply_action(game, "skin.unlock", {"kind": "character", "all": True})
    apply_action(game, "skin.unlock", {"kind": "weapon", "all": True})
    building_ids = [row["m_building_id"] for row in rows(game.state, "t_buildings")]
    apply_action(game, "research.maximize", {"ids": building_ids})
    target = rows(game.state, "t_characters")[0]
    apply_action(game, "party.layout", {"party_no": 1, "characters": [{"position_no": 1, "t_character_id": target["id"], "t_weapon_id": 0, "t_armor_id": 0, "t_accessory_id": 0}], "mana_gems": []})
    apply_action(game, "idle.party", {"party_no": 1, "characters": [{"position_no": 1, "t_character_id": target["id"], "t_weapon_id": 0}]})
    apply_action(game, "character.delete", {"character_id": target["id"]})
    assert find(game.state, "t_characters", id=target["id"]) is None
    assert all(row["t_character_id"] != target["id"] for row in rows(game.state, "t_character_abilities"))
    assert all(row["t_character_id"] != target["id"] for row in rows(game.state, "t_exploration_party_characters"))
    assert all(row["t_character_id"] != target["id"] for row in rows(game.state, "t_idle_exploration_party_characters"))
    validate_saved_state(game.state)


def test_gacha_exchange_mail_and_mode_editors(game):
    gacha = game.catalog.rows("m_gachas")[0]
    step = min(row["step"] for row in game.catalog.rows("m_gacha_steps") if row["m_gacha_group_id"] == gacha["m_gacha_group_id"])
    apply_action(game, "gacha.set", {"master_id": gacha["id"], "step": step, "step_count": 0, "pity": 17})
    exchange = game.catalog.rows("m_exchange_lineups")[0]
    apply_action(game, "shop.reset_counter", {"kind": "exchange", "master_id": exchange["id"]})
    apply_action(game, "mail.create", {"kind": 50, "amount": 5, "title": "本地邮件"})
    mail = game.progress["features"]["presents"]["data"]["entries"][-1]
    apply_action(game, "mail.withdraw", {"mail_id": mail["id"]})
    stage = game.catalog.rows("m_union_request_stages")[0]
    apply_action(game, "union.progress", {"stage_id": stage["id"], "completed": True})
    apply_action(game, "disaster.status", {"level": 1, "points": 10})
    apply_action(game, "nether.points", {"sphere_attack_point": 8})
    apply_action(game, "tutorial.set", {"all": True, "complete_prologue": True})
    validate_saved_state(game.state)


def test_board_box_and_mining_editors(game):
    kinds = {kind: next(row["id"] for row in game.catalog.rows("m_events") if row["type"] == kind) for kind in (1, 4, 6)}
    apply_action(game, "event.join", {"ids": list(kinds.values())})
    apply_action(game, "event.miner_complete", {"event_id": kinds[1]})
    apply_action(game, "event.board_select", {"event_id": kinds[4], "number": 2})
    with pytest.raises(GameplayError):
        apply_action(game, "event.box_reset", {"event_id": kinds[6], "number": 2})
    apply_action(game, "event.box_reset", {"event_id": kinds[6], "number": 2, "allow_duplicate_rewards": True})
    validate_saved_state(game.state)


def test_finishing_guided_tutorial_applies_configured_rules_in_same_action(game):
    game.rules = {**game.rules, "party_slots": 8, "unlock_common_features": 1}
    game.progress["features"]["tutorial"]["data"]["mode"] = "guided"
    result = apply_action(game, "tutorial.set", {"complete_prologue": True})
    assert result["timing"] == "relogin" and result["warnings"]
    assert game.rules["party_slots"] == 8
    assert rows(game.state, "t_statuses")[0]["max_party_count"] == 8


def test_tavern_finish_uses_current_round_and_gacha_mileage_stays_in_period(game):
    apply_action(game, "tavern.staff", {"m_tavern_work_id": 100, "position": 1, "m_character_id": 1200002})
    daily = rows(game.state, "t_tavern_daily_cards")[0]
    apply_action(game, "tavern.start", {"t_tavern_daily_card_id": daily["id"]})
    result = apply_action(game, "tavern.finish", {})
    assert result["response"]["is_work_end"] == 1
    assert game.progress["features"]["tavern"]["data"]["active"] is None
    assert rows(game.state, "t_tavern_shops")[0]["total_play_count"] == 1
    with pytest.raises(GameplayError):
        apply_action(game, "tavern.finish", {})
    group = game.catalog.rows("m_gacha_mileage_rewards")[0]["group_no"]
    apply_action(game, "gacha.mileage", {"group": group, "value": 1})
    assert find(game.state, "t_gacha_mileages", m_gacha_mileage_reward_group_no=group)["mileage"] == 1
    with pytest.raises(GameplayError):
        apply_action(game, "gacha.mileage", {"group": group, "value": 1000000})
