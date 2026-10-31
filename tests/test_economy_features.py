import copy
import json
from datetime import datetime

import pytest

from dotabyss_ps.gameplay_catalog import GameplayCatalog, JST
from dotabyss_ps.gameplay_records import grant, put
from dotabyss_ps.newgame import validate_saved_state
from test_gameplay import NOW, call, gameplay_master, setup_gameplay, snapshot
from test_permanent_features import records, seed


def update(context, operation):
    call(context, "api/user")
    def apply(state, progress):
        operation(state, progress)
        validate_saved_state(state)
        return {}
    context.store.game_state(context.identity["user_id"], context.record["revision"], lambda *_: None, apply)


def give(context, rewards):
    catalog = GameplayCatalog(context.tables, context.schema)
    update(context, lambda state, progress: grant(state, rewards, catalog, context.identity["user_id"], NOW, progress))


def test_exchange_spends_tokens_limits_retries_and_resets_monthly(setup_gameplay, monkeypatch):
    c = setup_gameplay
    give(c, [{"content_type": 170, "content_id": 1, "amount": 1000}])
    headers = {**c.headers, "x-request-id": "exchange-101"}
    body = {"m_exchange_lineup_id": 101, "exchange_count": 2}
    result = call(c, "api/exchange/execute", body, headers=headers)
    assert result["result"]["update_lineups"] == {"m_exchange_lineup_id": 101, "exchange_count": 2}
    before = snapshot(c)
    assert call(c, "api/exchange/execute", body, headers=headers) == result
    call(c, "api/exchange/execute", {**body, "exchange_count": 1}, expected=409)
    assert snapshot(c) == before
    assert records(c)["t_gacha_tokens"]["up"][0]["token_num"] == 600
    monkeypatch.setattr("test_gameplay.NOW", datetime(2026, 11, 1, tzinfo=JST))
    result = call(c, "api/exchange/execute", {**body, "exchange_count": 1})
    assert result["result"]["update_lineups"]["exchange_count"] == 1
    assert records(c)["t_gacha_tokens"]["up"][0]["token_num"] == 400


def test_exchange_rejects_expired_and_unaffordable_without_save_changes(setup_gameplay):
    c = setup_gameplay
    call(c, "api/exchange/lineup")
    before = snapshot(c)
    call(c, "api/exchange/execute", {"m_exchange_lineup_id": 101, "exchange_count": 1}, expected=409)
    call(c, "api/exchange/execute", {"m_exchange_lineup_id": 201, "exchange_count": 1}, expected=409)
    assert snapshot(c) == before


def test_selectable_box_uses_choice_row_and_rejects_foreign_choice_atomically(setup_gameplay):
    c = setup_gameplay
    seed(c, items={190003: 4})
    master = GameplayCatalog(c.tables, c.schema)
    box = master.get("m_items", 190003)
    choices = [r for r in master.rows("m_item_selectable_groups") if r["group_id"] == box["value"]]
    choice = choices[0]
    foreign = next(r for r in master.rows("m_item_selectable_groups") if r["group_id"] != box["value"])
    before = snapshot(c)
    call(c, "api/item/use/selectable", {"m_item_id": box["id"], "selects": [{"choice": choice["id"], "amount": 1}, {"choice": foreign["id"], "amount": 1}]}, expected=400)
    assert snapshot(c) == before
    result = call(c, "api/item/use/selectable", {"m_item_id": box["id"], "selects": [{"choice": choice["id"], "amount": 2}]})
    assert result["receives"] == [{"content_type": choice["content_type"], "content_id": choice["content_id"], "amount": choice["amount"] * 2}]
    assert next(r["amount"] for r in records(c)["t_items"]["up"] if r["m_item_id"] == box["id"]) == 2


def test_equipment_grant_lock_disassembly_and_ids_survive_recreation(setup_gameplay):
    c = setup_gameplay
    give(c, [{"content_type": 70, "content_id": 11010110, "amount": 1}])
    gear = records(c)["t_weapons"]["up"][0]
    body = {"t_weapon_ids": [gear["id"]], "t_armor_ids": [], "t_accessory_ids": []}
    call(c, "api/equipment/lock/toggle", body)
    before = snapshot(c)
    call(c, "api/equipment/disassembly/execute", body, expected=409)
    assert snapshot(c) == before
    call(c, "api/equipment/lock/toggle", body)
    gold = records(c)["t_statuses"]["up"][0]["gold"]
    result = call(c, "api/equipment/disassembly/execute", body)
    assert result["records"]["t_weapons"]["down"] == [gear["id"]]
    data = records(c)
    assert not data["t_weapons"]["up"] and not data["t_weapon_enchantments"]["up"]
    assert data["t_statuses"]["up"][0]["gold"] > gold
    give(c, [{"content_type": 70, "content_id": 11010110, "amount": 1}])
    assert records(c)["t_weapons"]["up"][0]["id"] > gear["id"]


def test_equipment_appraisal_charges_once_and_confirmation_persists(setup_gameplay, monkeypatch):
    c = setup_gameplay
    monkeypatch.setattr("dotabyss_ps.gameplay_content.secrets.randbelow", lambda n: 0)
    master = GameplayCatalog(c.tables, c.schema)
    group = next(r for r in master.rows("m_weapon_enchantment_groups") if r["is_appraisal"])
    ticket = next(r for r in master.rows("m_items") if r["type"] == 110)
    seed(c, items={ticket["id"]: 2}, gold=5000)
    give(c, [{"content_type": 70, "content_id": group["m_weapon_id"], "amount": 1}])
    gear = records(c)["t_weapons"]["up"][0]
    body = {"content_type": 70, "t_equipment_id": gear["id"]}
    headers = {**c.headers, "x-request-id": "appraisal"}
    first = call(c, "api/equipment/appraisal/lottery", body, headers=headers)
    assert call(c, "api/equipment/appraisal/lottery", body, headers=headers) == first
    data = records(c)
    assert data["t_statuses"]["up"][0]["gold"] == 4000
    pools = copy.deepcopy(data["t_appraisal_result_pools"]["up"])
    assert pools
    call(c, "api/equipment/appraisal/execute", body)
    data = records(c)
    assert not data["t_appraisal_result_pools"]["up"]
    for pending in pools:
        assert any(r["m_weapon_enchantment_group_id"] == pending["m_equipment_enchantment_group_id"] and r["m_ability_effect_id"] == pending["m_ability_effect_id"] and r["level"] == pending["level"] and r["is_appraised"] == 1 for r in data["t_weapon_enchantments"]["up"])
    before = snapshot(c)
    call(c, "api/equipment/appraisal/execute", body)
    assert snapshot(c) == before


def test_welcome_presents_claim_once_and_history_survives_reload(setup_gameplay):
    c = setup_gameplay
    call(c, "api/home", method="POST")
    pending = call(c, "api/present/list/1/0", method="GET")["present_list"]
    assert len(pending) == 3
    ids = [r["id"] for r in pending]
    before = snapshot(c)
    call(c, "api/present/receive", {"ids": ids + [999]}, expected=409)
    assert snapshot(c) == before
    call(c, "api/present/receive", {"ids": ids})
    before = snapshot(c)
    call(c, "api/present/receive", {"ids": ids})
    assert snapshot(c) == before
    assert call(c, "api/present/list/1/0", method="GET")["present_list"] == []
    assert len(call(c, "api/present/list/99/0", method="GET")["received_list"]) == 3
    assert call(c, "api/home", method="POST")["badges"]["present_count"] == 0


def test_tutorial_gacha_grants_guarantee_and_retries_do_not_redraw(setup_gameplay, monkeypatch):
    c = setup_gameplay
    monkeypatch.setattr("dotabyss_ps.gameplay_content.secrets.randbelow", lambda n: 0)
    headers = {**c.headers, "x-request-id": "tutorial-draw"}
    body = {"m_gacha_id": 10101, "exec_count": 1, "use_gacha_ticket": 0}
    result = call(c, "api/gacha/exec", body, headers=headers)
    assert sum(r["amount"] for r in result["received_content_list"]) == 10
    data = records(c)
    catalog = GameplayCatalog(c.tables, c.schema)
    assert any(catalog.get("m_characters", r["m_character_id"])["rarity"] == 3 for r in data["t_characters"]["up"])
    assert any(r["content_type"] == 30 for r in result["received_content_list"])
    assert all(r["origin_content"]["content_type"] == 40 for r in result["received_content_list"] if r["content_type"] == 30)
    before = snapshot(c)
    assert call(c, "api/gacha/exec", body, headers=headers) == result
    call(c, "api/gacha/exec", body, expected=409)
    assert snapshot(c) == before


def test_research_upgrades_costs_experience_and_read_flags_persist(setup_gameplay):
    c = setup_gameplay
    seed(c, items={50001: 100, 50004: 100, 50007: 100})
    view = call(c, "api/research")
    entry = next(r for r in view["building_skills"] if r["m_building_id"] == 100 and r["skill_type"] == 1004)
    assert entry["skill_level"] == 1 and entry["is_unlocked"] == 0 and entry["is_upgradable"] == 1
    headers = {**c.headers, "x-request-id": "research-upgrade"}
    body = {"m_building_id": 100, "skill_type": 1004}
    result = call(c, "api/research/upgrade", body, headers=headers)
    assert result["building"]["exp"] == 10
    assert call(c, "api/research/upgrade", body, headers=headers) == result
    data = records(c)
    assert next(r["amount"] for r in data["t_items"]["up"] if r["m_item_id"] == 50001) == 95
    call(c, "api/research/upgrade/direction/check", body)
    skill = next(r for r in records(c)["t_building_skills"]["up"] if (r["m_building_id"], r["skill_type"]) == (100, 1004))
    assert skill["skill_level"] == skill["checked_skill_level"] == 1


def test_research_plan_and_intimacy_requirements_do_not_consume_materials(setup_gameplay):
    c = setup_gameplay
    seed(c, items={51001: 100})
    call(c, "api/research")
    before = snapshot(c)
    call(c, "api/research/upgrade", {"m_building_id": 100, "skill_type": 1000}, expected=409)
    call(c, "api/research/upgrade", {"m_building_id": 200, "skill_type": 2000}, expected=409)
    assert snapshot(c) == before


def test_selectable_box_overflow_goes_to_presents_without_losing_items(setup_gameplay):
    c = setup_gameplay
    master = GameplayCatalog(c.tables, c.schema)
    box = master.get("m_items", 190003)
    choice = next(r for r in master.rows("m_item_selectable_groups") if r["group_id"] == box["value"] and r["content_type"] == 30)
    limit = master.get("m_items", choice["content_id"])["possession_limit"]
    seed(c, items={190003: 1, choice["content_id"]: limit})
    result = call(c, "api/item/use/selectable", {"m_item_id": 190003, "selects": [{"choice": choice["id"], "amount": 1}]})
    assert result["receives"] == [] and result["presents"] == [{"content_type": 30, "content_id": choice["content_id"], "amount": choice["amount"]}]
    entries = call(c, "api/present/list/1/0", method="GET")["present_list"]
    assert any(r["content_id"] == choice["content_id"] and r["amount"] == choice["amount"] for r in entries)
    assert next(r["amount"] for r in records(c)["t_items"]["up"] if r["m_item_id"] == 190003) == 0
