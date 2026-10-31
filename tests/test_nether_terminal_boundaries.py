import pytest

from dotabyss_ps.gameplay_catalog import GameplayCatalog
from dotabyss_ps.gameplay_records import put
from test_gameplay import NOW, call, gameplay_master, setup_gameplay, snapshot
from test_nether_modes import choose, clear_codes, coordinate, mutate, nether, progress, start, state, victory


def final_battle(context):
    start(context)
    run = progress(context)["features"]["nether"]["data"]["run"]
    level = max(row["floor_level"] for row in run["floors"])
    floor = next(row for row in run["floors"] if row["floor_level"] == level)
    mutate(context, lambda s, _: s["records"]["t_nethers"]["up"][0].update(floor_level=level - 1, m_nether_map_floor_id=0))
    call(context, "api/nether/update", coordinate(floor))
    call(context, "api/nether/start-battle", coordinate(floor))
    call(context, "api/nether/clear-battle", victory(context, floor))
    return floor


def test_final_boss_code_choice_precedes_result_and_restart(setup_gameplay):
    context = setup_gameplay
    final_battle(context)
    assert nether(context)["status"] == 8
    assert nether(context)["m_nether_code_ids"]
    before = snapshot(context)
    call(context, "api/nether/result", {"m_nether_id": 1, "m_nether_map_id": 1, "use_item_flag": 0}, expected=409)
    assert snapshot(context) == before
    clear_codes(context)
    result = call(context, "api/nether/result", {"m_nether_id": 1, "m_nether_map_id": 1, "use_item_flag": 0})
    assert result["result_score"] > 0
    stable = snapshot(context)
    call(context, "api/nether/result", {"m_nether_id": 1, "m_nether_map_id": 1, "use_item_flag": 0})
    assert snapshot(context) == stable
    mutate(context, lambda s, _: put(s, "t_items", {"m_item_id": 200002}, {"amount": 1}))
    previous = progress(context)["features"]["nether"]["data"]["run"]["id"]
    call(context, "api/nether/start", {"m_nether_id": 1, "m_nether_map_id": 1, "party_no": 1, "use_ticket": 1, "start_floor_level": 1})
    assert progress(context)["features"]["nether"]["data"]["run"]["id"] > previous


def test_final_floor_skip_keeps_pending_codes_usable(setup_gameplay):
    context = setup_gameplay
    start(context)
    level = max(row["floor_level"] for row in progress(context)["features"]["nether"]["data"]["run"]["floors"])
    def prepare(s, p):
        s["records"]["t_nethers"]["up"][0].update(floor_level=level - 10, m_nether_map_floor_id=0)
        put(s, "t_items", {"m_item_id": 200007}, {"amount": 1})
    mutate(context, prepare)
    call(context, "api/nether/skip", {"m_nether_id": 1, "m_nether_map_id": 1, "start_floor_level": level - 9,
                                      "is_rare": 0, "party_no": 1, "lock_items": []})
    assert nether(context)["floor_level"] == level
    assert nether(context)["status"] == 8
    clear_codes(context)
    call(context, "api/nether/result", {"m_nether_id": 1, "m_nether_map_id": 1, "use_item_flag": 0})


def test_completed_but_unclaimed_expedition_cannot_be_overwritten(setup_gameplay):
    context = setup_gameplay
    final_battle(context)
    clear_codes(context)
    mutate(context, lambda s, _: put(s, "t_items", {"m_item_id": 200002}, {"amount": 1}))
    before = snapshot(context)
    call(context, "api/nether/start", {"m_nether_id": 1, "m_nether_map_id": 1, "party_no": 1,
                                      "use_ticket": 1, "start_floor_level": 1}, expected=409)
    assert snapshot(context) == before


def test_settled_expedition_cannot_start_another_battle(setup_gameplay):
    context = setup_gameplay
    start(context)
    floor = choose(context, 1)
    call(context, "api/nether/update", coordinate(floor))
    call(context, "api/nether/result", {"m_nether_id": 1, "m_nether_map_id": 1, "use_item_flag": 0})
    before = snapshot(context)
    call(context, "api/nether/start-battle", coordinate(floor), expected=409)
    assert snapshot(context) == before


def test_protected_capacity_counts_items_locked_by_earlier_continue(setup_gameplay):
    context = setup_gameplay
    start(context)
    floor = choose(context, 1)
    call(context, "api/nether/update", coordinate(floor))
    catalog = GameplayCatalog(context.tables, context.schema)
    sphere = next(row for row in catalog.rows("m_nether_spheres") if row["effect_type"] == 10 and row["level"] == 1)
    def prepare(s, p):
        p["features"]["nether"]["data"]["spheres"] = [{"id": 1, "user_id": context.identity["user_id"],
            "m_nether_id": 1, "m_nether_sphere_board_id": sphere["m_nether_sphere_board_id"], "type": sphere["type"],
            "level": 1, "created_at": NOW.isoformat(), "updated_at": NOW.isoformat()}]
        put(s, "t_items", {"m_item_id": 200002}, {"amount": 2})
        for identifier in (200003, 200004):
            put(s, "t_nether_items", {"m_item_id": identifier}, {"m_nether_id": 1, "m_nether_map_id": 1, "amount": 1}, context.identity["user_id"])
    mutate(context, prepare)
    for identifier, expected in ((200003, 200), (200004, 409)):
        call(context, "api/nether/start-battle", coordinate(floor))
        call(context, "api/nether/close-battle", coordinate(floor))
        before = snapshot(context)
        call(context, "api/nether/continue", {"m_nether_id": 1, "m_nether_map_id": 1, "floor_level": 1,
            "use_ticket": 1, "lock_items": [{"m_item_id": identifier, "amount": 1}]}, expected=expected)
        if expected != 200:
            assert snapshot(context) == before
    assert len([row for row in state(context)["records"]["t_nether_lock_items"]["up"] if row["amount"]]) == 1


@pytest.mark.parametrize("unlock,expected", [(False, 409), (True, 200)])
def test_checkpoint_start_requires_recovery_sphere(setup_gameplay, unlock, expected):
    context = setup_gameplay
    start(context)
    call(context, "api/nether/cancel", {})
    catalog = GameplayCatalog(context.tables, context.schema)
    sphere = catalog.get("m_nether_spheres", 30101)
    def prepare(s, p):
        s["records"]["t_nethers"]["up"][0].update(max_cleared_floor_level=10)
        put(s, "t_items", {"m_item_id": 200002}, {"amount": 1})
        if unlock:
            p["features"]["nether"]["data"]["spheres"] = [{"id": 1, "user_id": context.identity["user_id"],
                "m_nether_id": 1, "m_nether_sphere_board_id": sphere["m_nether_sphere_board_id"],
                "type": sphere["type"], "level": sphere["level"], "created_at": NOW.isoformat(), "updated_at": NOW.isoformat()}]
    mutate(context, prepare)
    assert call(context, "api/nether")["t_nether_point"]["recovery_floor_level"] == (10 if unlock else 0)
    before = snapshot(context)
    call(context, "api/nether/start", {"m_nether_id": 1, "m_nether_map_id": 1, "party_no": 1,
        "use_ticket": 1, "start_floor_level": 11}, expected=expected)
    if not unlock:
        assert snapshot(context) == before
    else:
        assert nether(context)["floor_level"] == 10


def test_shop_late_invalid_item_rolls_back_earlier_purchase(setup_gameplay):
    context = setup_gameplay
    start(context)
    catalog = GameplayCatalog(context.tables, context.schema)
    level = min(row["min_order"] for row in catalog.rows("m_nether_map_floors") if row["type"] == 6)
    mutate(context, lambda s, _: s["records"]["t_nethers"]["up"][0].update(floor_level=level - 1, m_nether_map_floor_id=0))
    floor = choose(context, 6, level)
    call(context, "api/nether/update", coordinate(floor))
    item = next(row for row in catalog.rows("m_nether_floor_shop_contents") if row["m_nether_floor_shop_id"] == floor["extend_id"])
    mutate(context, lambda s, _: s["records"]["t_nethers"]["up"][0].update(nether_gold=100000))
    entry = {"m_nether_floor_shop_content_id": item["id"], "amount": 1}
    before = snapshot(context)
    call(context, "api/nether/update-shop", {"shop_item_amount": [entry, entry]}, expected=400)
    assert snapshot(context) == before
