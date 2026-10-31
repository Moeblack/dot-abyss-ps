import copy
import json
from types import SimpleNamespace

import pytest

from dotabyss_ps.gameplay import Gameplay
from dotabyss_ps.gameplay_catalog import GameplayCatalog
from dotabyss_ps.gameplay_content import create_equipment, roll_enchantments
from dotabyss_ps.gameplay_equipment import ensure_equipment_appraisal
from test_economy_features import give
from test_gameplay import NOW, call, gameplay_master, setup_gameplay, snapshot
from test_permanent_features import records, seed


def appraisal_gear(context):
    give(context, [{"content_type": 70, "content_id": 11010331, "amount": 1}])
    return records(context)["t_weapons"]["up"][0]


def prepare_appraisal(context):
    catalog = GameplayCatalog(context.tables, context.schema)
    ticket = next(row for row in catalog.rows("m_items") if row["type"] == 110)
    seed(context, items={ticket["id"]: 3}, gold=10000)
    return ticket


def test_appraisal_confirmation_updates_native_equipment_flag_and_replays(setup_gameplay, monkeypatch):
    context = setup_gameplay
    monkeypatch.setattr("dotabyss_ps.gameplay_content.secrets.randbelow", lambda limit: 0)
    gear = appraisal_gear(context)
    prepare_appraisal(context)
    assert gear["is_appraisal"] == 1
    body = {"content_type": 70, "t_equipment_id": gear["id"]}
    call(context, "api/equipment/appraisal/lottery", body)
    assert records(context)["t_weapons"]["up"][0]["is_appraisal"] == 1
    headers = {**context.headers, "x-request-id": "appraisal-native-flag"}
    result = call(context, "api/equipment/appraisal/execute", body, headers=headers)
    assert result["records"]["t_weapons"]["up"][0]["is_appraisal"] == 0
    before = snapshot(context)
    assert call(context, "api/equipment/appraisal/execute", body, headers=headers) == result
    assert snapshot(context) == before
    call(context, "api/equipment/appraisal/lottery", body)
    assert records(context)["t_weapons"]["up"][0]["is_appraisal"] == 0
    call(context, "api/equipment/appraisal/execute", body)
    assert records(context)["t_weapons"]["up"][0]["is_appraisal"] == 0


def test_reappraisal_does_not_duplicate_unchanged_enchantment(setup_gameplay, monkeypatch):
    context = setup_gameplay
    monkeypatch.setattr("dotabyss_ps.gameplay_content.secrets.randbelow", lambda limit: 0)
    gear = appraisal_gear(context)
    prepare_appraisal(context)
    catalog = GameplayCatalog(context.tables, context.schema)
    fixed = {row["id"] for row in catalog.rows("m_weapon_enchantment_groups")
             if row["m_weapon_id"] == gear["m_weapon_id"] and not row["is_appraisal"]}
    original = [row for row in records(context)["t_weapon_enchantments"]["up"]
                if row["m_weapon_enchantment_group_id"] in fixed]
    body = {"content_type": 70, "t_equipment_id": gear["id"]}
    call(context, "api/equipment/appraisal/lottery", body)
    pending = records(context)["t_appraisal_result_pools"]["up"]
    assert not {row["enchantment_no"] for row in original} & {row["enchantment_no"] for row in pending}
    call(context, "api/equipment/appraisal/execute", body)
    assert [row for row in records(context)["t_weapon_enchantments"]["up"]
            if row["m_weapon_enchantment_group_id"] in fixed] == original


def test_legacy_appraisal_flag_and_cached_response_are_reconciled_once(setup_gameplay, monkeypatch):
    context = setup_gameplay
    monkeypatch.setattr("dotabyss_ps.gameplay_content.secrets.randbelow", lambda limit: 0)
    gear = appraisal_gear(context)
    prepare_appraisal(context)
    body = {"content_type": 70, "t_equipment_id": gear["id"]}
    call(context, "api/equipment/appraisal/lottery", body)
    call(context, "api/equipment/appraisal/execute", body)
    state, progress = map(json.loads, snapshot(context))
    row = state["records"]["t_weapons"]["up"][0]
    row["is_appraisal"] = 1
    response = {"records": {"t_weapons": {"up": [copy.deepcopy(row)], "down": []}}, "value": 25}
    progress["features"]["equipment"]["data"].pop("appraisal_state_version", None)
    progress["operation_receipts"]["old-appraisal"] = {"digest": "a" * 64, "response": response}
    game = Gameplay(state, progress, GameplayCatalog(context.tables, context.schema), context.identity["user_id"], NOW)
    ensure_equipment_appraisal(game)
    assert row["is_appraisal"] == 0
    assert response["records"]["t_weapons"]["up"][0]["is_appraisal"] == 0
    assert response["value"] == 25
    assert progress["operation_receipts"]["old-appraisal"]["digest"] == "a" * 64
    before = copy.deepcopy((state, progress))
    ensure_equipment_appraisal(game)
    assert (state, progress) == before


@pytest.mark.parametrize("kind,singular,plural,identifier", [
    (70, "weapon", "weapons", 11010110), (80, "armor", "armors", 21010110),
    (90, "accessory", "accessories", 31010110)])
def test_equipment_without_appraisal_slots_does_not_stay_hidden(setup_gameplay, kind, singular, plural, identifier):
    context = setup_gameplay
    give(context, [{"content_type": kind, "content_id": identifier, "amount": 1}])
    state, progress = map(json.loads, snapshot(context))
    gear = state["records"]["t_" + plural]["up"][0]
    gear["is_appraisal"] = 1
    game = Gameplay(state, progress, GameplayCatalog(context.tables, context.schema), context.identity["user_id"], NOW)
    ensure_equipment_appraisal(game)
    assert gear["is_appraisal"] == 0


@pytest.mark.parametrize("pick,expected", [(lambda limit: 0, 10), (lambda limit: limit - 1, 30)])
def test_equipment_lottery_stores_native_parameter_value(monkeypatch, pick, expected):
    monkeypatch.setattr("dotabyss_ps.gameplay_content.secrets.randbelow", pick)
    master = {"min_parameter": 10, "max_parameter": 32, "lottery_interval": 5}
    catalog = SimpleNamespace(get=lambda table, identifier: master, rows=lambda table: [])
    state = {"records": {table: {"up": [], "down": []} for table in
                         ("t_weapons", "t_armors", "t_accessories", "t_weapon_enchantments")}}
    create_equipment(state, 70, 1, catalog, 2)
    assert state["records"]["t_weapons"]["up"][0]["up_count"] == expected


@pytest.mark.parametrize("identifier", [11010331, 11020331, 11030331])
def test_actual_master_reappraisal_respects_fixed_enchantment_rules(gameplay_master, monkeypatch, identifier):
    schema, tables = gameplay_master
    catalog = GameplayCatalog(tables, schema)
    monkeypatch.setattr("dotabyss_ps.gameplay_content.secrets.randbelow", lambda limit: 0)
    generated = roll_enchantments(catalog, 70, identifier)
    fixed = {row["id"] for row in catalog.rows("m_weapon_enchantment_groups")
             if row["m_weapon_id"] == identifier and not row["is_appraisal"]}
    retained = {row["enchantment_no"] for row in generated if row["m_weapon_enchantment_group_id"] in fixed}
    reappraised = roll_enchantments(catalog, 70, identifier, appraisal_only=True, excluded=retained)
    assert reappraised
    assert not retained & {row["enchantment_no"] for row in reappraised}


@pytest.mark.parametrize("identifier", [1100001, 1200003, 1300033])
def test_gift_bond_and_skill_unlocks_stay_consistent_across_roles(setup_gameplay, identifier):
    context = setup_gameplay
    give(context, [{"content_type": 40, "content_id": identifier, "amount": 1}])
    character = next(row for row in records(context)["t_characters"]["up"] if row["m_character_id"] == identifier)
    seed(context, items={140001: 14})
    headers = {**context.headers, "x-request-id": "role-gift"}
    body = {"t_character_id": character["id"], "use_items": [{"m_item_id": 140001, "amount": 14}]}
    gift = call(context, "api/character/gift/give", body, headers=headers)
    after = snapshot(context)
    assert call(context, "api/character/gift/give", body, headers=headers) == gift
    assert snapshot(context) == after
    data = records(context)
    character = next(row for row in data["t_characters"]["up"] if row["m_character_id"] == identifier)
    assert character["level"] == 1 and character["intimacy_point"] == 350 and character["bond_point"] == 1
    catalog = GameplayCatalog(context.tables, context.schema)
    skills = [row for row in data["t_character_abilities"]["up"] if row["t_character_id"] == character["id"]]
    assert skills and all(row["level"] == 1 for row in skills)
    gated = {row["m_character_ability_id"] for row in catalog.rows("m_ability_releases") if row["level"] == 6}
    assert all(row["possible_max_level"] == 5 for row in skills if row["m_character_ability_id"] in gated)
    call(context, "api/character/bond", {"t_character_id": character["id"], "orders": [{"up_parameter_type": 1, "bond_point": 1}]})
    data = records(context)
    assert any(row["t_character_id"] == character["id"] and row["up_parameter_type"] == 1 and row["level"] == 1
               for row in data["t_character_bond_parameters"]["up"])
    assert any(row["up_parameter_type"] == 1 and row["level"] == 1 and row["effect_value"] > 0
               for row in catalog.rows("m_character_bond_parameter_levels"))
    call(context, "api/character/bond/reset", {"t_character_id": character["id"]})
    before = snapshot(context)
    call(context, "api/character/bond/reset", {"t_character_id": character["id"]})
    assert snapshot(context) == before
    assert next(row for row in records(context)["t_characters"]["up"] if row["id"] == character["id"])["bond_point"] == 1


@pytest.mark.parametrize("identifier", [1100001, 1200003, 1300033])
def test_wrong_union_gift_rolls_back_for_each_role(setup_gameplay, identifier):
    context = setup_gameplay
    give(context, [{"content_type": 40, "content_id": identifier, "amount": 1}])
    catalog = GameplayCatalog(context.tables, context.schema)
    union = catalog.get("m_characters", identifier)["union_type"]
    wrong = next(row["id"] for row in catalog.rows("m_items") if row["type"] == 141 and row["value"] != union)
    character = next(row for row in records(context)["t_characters"]["up"] if row["m_character_id"] == identifier)
    seed(context, items={140001: 2, wrong: 1})
    before = snapshot(context)
    call(context, "api/character/gift/give", {"t_character_id": character["id"], "use_items": [
        {"m_item_id": 140001, "amount": 2}, {"m_item_id": wrong, "amount": 1}]}, expected=409)
    assert snapshot(context) == before
