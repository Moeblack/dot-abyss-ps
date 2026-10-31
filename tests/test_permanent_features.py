import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from dotabyss_ps.gameplay_catalog import GameplayCatalog
from dotabyss_ps.gameplay_records import put
from dotabyss_ps.newgame import validate_saved_state
from test_gameplay import call, gameplay_master, setup_gameplay, snapshot


def seed(context, *, items=None, **balances):
    call(context, "api/user")
    def update(state, progress):
        state["records"]["t_statuses"]["up"][0].update(balances)
        for identifier, amount in (items or {}).items():
            put(state, "t_items", {"m_item_id": identifier}, {"amount": amount})
        validate_saved_state(state)
        return {}
    context.store.game_state(context.identity["user_id"], context.record["revision"], lambda *_: None, update)


def records(context):
    return call(context, "api/user")["records"]


def test_profile_changes_are_persistent_and_validate_ownership(setup_gameplay):
    c = setup_gameplay
    call(c, "api/profile/edit/name", {"name": "Local Tester", "is_sync_dmm": 0})
    call(c, "api/profile/edit/comment", {"comment": "Hello\nWorld"})
    data = records(c)
    assert data["t_statuses"]["up"][0]["name"] == "Local Tester"
    assert data["t_profiles"]["up"][0]["comment"] == "Hello\nWorld"
    before = snapshot(c)
    call(c, "api/profile/edit/degree", {"m_degree_id": 9999999}, expected=409)
    call(c, "api/profile/edit/favorite-character", {"m_character_id": 1200016, "m_character_skin_id": 120000201}, expected=409)
    assert snapshot(c) == before
    call(c, "api/profile/edit/favorite-character", {"m_character_id": 1200002, "m_character_skin_id": 120000201})
    assert records(c)["t_statuses"]["up"][0]["favorite_t_character_id"] == 2


@pytest.mark.parametrize("value", ["", " " * 4, "a" * 33, "bad\x00name", "bad\nname", 123])
def test_invalid_names_do_not_create_save(setup_gameplay, value):
    c = setup_gameplay
    call(c, "api/profile/edit/name", {"name": value, "is_sync_dmm": 0}, expected=400)
    assert snapshot(c) == (None, None)


def test_favorite_and_skin_selection_require_owned_compatible_content(setup_gameplay):
    c = setup_gameplay
    call(c, "api/character/favorite/toggle", {"t_character_id": 1, "is_favorite": True})
    assert records(c)["t_characters"]["up"][0]["is_favorite"] == 1
    before = snapshot(c)
    call(c, "api/character/favorites/reset", {"t_character_ids": [1, 999]}, expected=409)
    call(c, "api/character/skin/set", {"t_character_id": 1, "m_character_skin_id": 120000201}, expected=409)
    call(c, "api/character/weapon_skin/set", {"t_character_id": 1, "m_weapon_skin_id": 100016001}, expected=409)
    assert snapshot(c) == before
    call(c, "api/character/favorites/reset", {"t_character_ids": []})
    call(c, "api/character/skin/set", {"t_character_id": 1, "m_character_skin_id": 120001601})
    assert records(c)["t_characters"]["up"][0]["is_favorite"] == 0


def test_config_and_feature_tip_return_native_records(setup_gameplay):
    c = setup_gameplay
    result = call(c, "api/user/config/update", {"type": 1, "value": 1})
    assert result["t_config"] == {"id": 1, "user_id": c.identity["user_id"], "type": 1, "value": 1}
    assert records(c)["t_configs"]["up"] == [result["t_config"]]
    result = call(c, "api/feature-tip/read", {"m_feature_tip_id": 1})
    assert result["version"] >= 1
    assert records(c)["t_feature_tips"]["up"][0]["read_version"] == result["version"]
    before = snapshot(c)
    call(c, "api/user/config/update", {"type": 2, "value": 1}, expected=400)
    call(c, "api/user/config/update", {"type": 1, "value": True}, expected=400)
    assert snapshot(c) == before


def test_level_costs_retries_unlocks_and_resets_are_consistent(setup_gameplay):
    c = setup_gameplay
    seed(c, gold=1000000, character_exp=1000000)
    before_abilities = records(c)["t_character_abilities"]["up"]
    headers = {**c.headers, "x-request-id": "level-test-1"}
    body = {"t_character_id": 1, "exec_count": 4}
    first = call(c, "api/character/level/up", body, headers=headers)
    after = snapshot(c)
    assert call(c, "api/character/level/up", body, headers=headers) == first
    assert snapshot(c) == after
    data = records(c)
    assert data["t_characters"]["up"][0]["level"] == 5
    catalog = GameplayCatalog(c.tables, c.schema)
    for kind, field in ((50, "gold"), (110, "character_exp")):
        spent = sum(r["require_amount"] for r in catalog.rows("m_character_levels") if 1 < r["level"] <= 5 and r["content_type"] == kind)
        assert data["t_statuses"]["up"][0][field] == 1000000 - spent
    assert data["t_character_abilities"]["up"] == before_abilities
    call(c, "api/character/level/reset", {"t_character_id": 1})
    data = records(c)
    assert data["t_characters"]["up"][0]["level"] == 1
    assert data["t_statuses"]["up"][0]["gold"] == data["t_statuses"]["up"][0]["character_exp"] == 1000000
    before = snapshot(c)
    call(c, "api/character/level/reset", {"t_character_id": 1})
    assert snapshot(c) == before


def test_cost_failures_and_reused_ids_roll_back(setup_gameplay):
    c = setup_gameplay
    seed(c, character_exp=1000000, gold=0)
    before = snapshot(c)
    call(c, "api/character/level/up", {"t_character_id": 1, "exec_count": 2}, expected=409)
    assert snapshot(c) == before
    seed(c, gold=1000000)
    headers = {**c.headers, "x-request-id": "same-id"}
    call(c, "api/character/level/up", {"t_character_id": 1, "exec_count": 1}, headers=headers)
    before = snapshot(c)
    call(c, "api/character/level/up", {"t_character_id": 1, "exec_count": 2}, headers=headers, expected=409)
    call(c, "api/character/level/up", {"t_character_id": 1, "exec_count": 30}, expected=409)
    assert snapshot(c) == before


def test_concurrent_costed_request_runs_once(setup_gameplay):
    c = setup_gameplay
    seed(c, character_exp=1000000, gold=1000000)
    headers = {**c.headers, "x-request-id": "concurrent-level"}
    with ThreadPoolExecutor(max_workers=4) as pool:
        replies = list(pool.map(lambda _: call(c, "api/character/level/up", {"t_character_id": 1, "exec_count": 1}, headers=headers), range(4)))
    assert all(r == replies[0] for r in replies)
    assert records(c)["t_characters"]["up"][0]["level"] == 2


def test_limit_break_and_resistance_points_use_master_costs(setup_gameplay):
    c = setup_gameplay
    catalog = GameplayCatalog(c.tables, c.schema)
    core = next(r for r in catalog.rows("m_items") if r["type"] == 10 and r["value"] == 1200016)
    master = catalog.get("m_characters", 1200016)
    definition = next(r for r in catalog.rows("m_character_limit_break_defines") if r["rarity"] == master["rarity"])
    seed(c, items={core["id"]: 10})
    call(c, "api/character/limit-break", {"t_character_id": 1, "exec_count": 2})
    data = records(c)
    assert data["t_characters"]["up"][0]["limit_break_count"] == 2
    assert data["t_characters"]["up"][0]["limit_break_core_point"] == definition["limit_break_core_point"] * 2
    kind = next(r["up_parameter_type"] for r in catalog.rows("m_character_condition_resistance_levels") if r["rarity"] == master["rarity"])
    call(c, "api/character/condition_resistance", {"t_character_id": 1, "orders": [{"up_parameter_type": kind, "limit_break_core_point": 1}]})
    assert records(c)["t_character_condition_resistances"]["up"][0]["level"] == 1
    call(c, "api/character/condition_resistance/reset", {"t_character_id": 1})
    assert records(c)["t_characters"]["up"][0]["limit_break_core_point"] == definition["limit_break_core_point"] * 2


def test_gifts_bond_allocation_and_failed_batches_are_atomic(setup_gameplay):
    c = setup_gameplay
    seed(c, items={140001: 10, 41001: 10})
    call(c, "api/character/gift/give", {"t_character_id": 1, "use_items": [{"m_item_id": 140001, "amount": 2}]})
    assert records(c)["t_characters"]["up"][0]["intimacy_point"] == 50
    call(c, "api/character/bond-point/add", {"t_character_id": 1, "use_items": [{"m_item_id": 41001, "amount": 5}]})
    assert records(c)["t_characters"]["up"][0]["bond_point"] == 5
    call(c, "api/character/bond", {"t_character_id": 1, "orders": [{"up_parameter_type": 1, "bond_point": 2}]})
    assert records(c)["t_characters"]["up"][0]["bond_point"] == 3
    before = snapshot(c)
    call(c, "api/character/bond", {"t_character_id": 1, "orders": [{"up_parameter_type": 1, "bond_point": 2}, {"up_parameter_type": 999, "bond_point": 1}]}, expected=400)
    assert snapshot(c) == before
    call(c, "api/character/bond/reset", {"t_character_id": 1})
    assert records(c)["t_characters"]["up"][0]["bond_point"] == 5


def test_ability_upgrade_uses_owned_row_and_costs(setup_gameplay):
    c = setup_gameplay
    data = records(c)
    ability = next(r for r in data["t_character_abilities"]["up"] if r["t_character_id"] == 1 and r["level"] == 1)
    master = GameplayCatalog(c.tables, c.schema)
    character = master.get("m_characters", 1200016)
    costs = [r for r in master.rows("m_ability_level_up_costs") if r["element_type"] == character["element_type"] and r["party_position"] == character["party_position"] and r["level"] == 2]
    seed(c, items={r["content_id"]: 10000 for r in costs if r["content_type"] == 30}, soul=10000, gold=1000000)
    call(c, "api/character/ability/level/up", {"t_character_id": 1, "orders": [{"t_character_ability_id": ability["id"], "exec_count": 1}]})
    current = next(r for r in records(c)["t_character_abilities"]["up"] if r["id"] == ability["id"])
    assert current["level"] == 2
    before = snapshot(c)
    call(c, "api/character/ability/level/up", {"t_character_id": 2, "orders": [{"t_character_ability_id": ability["id"], "exec_count": 1}]}, expected=409)
    assert snapshot(c) == before


def test_progress_upgrade_preserves_game_state_and_invalid_policy_rolls_back(setup_gameplay):
    c = setup_gameplay
    call(c, "api/user")
    old_state, raw = snapshot(c)
    progress = json.loads(raw)
    progress.pop("operation_receipts")
    progress.pop("features")
    progress["format_version"] = 1
    with c.store.connection() as db:
        db.execute("UPDATE gameplay_progress SET contents=? WHERE profile_id=?", (json.dumps(progress), c.identity["user_id"]))
    call(c, "api/user")
    state, raw = snapshot(c)
    assert state == old_state and json.loads(raw)["format_version"] == 2
    assert json.loads(raw)["features"]["presents"]["data"] == {"seeded": True, "entries": []}
    call(c, "api/home", method="POST")
    assert snapshot(c)[0] == old_state
    (c.settings.data_dir / "gameplay-rules.json").write_text('{"character_level_cap":1000}', encoding="utf-8")
    before = snapshot(c)
    call(c, "api/character/level/up", {"t_character_id": 1, "exec_count": 1}, expected=503)
    assert snapshot(c) == before
