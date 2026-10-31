import json

from dotabyss_ps.gameplay_catalog import GameplayCatalog
from test_economy_features import give, update
from test_gameplay import call, gameplay_master, setup_gameplay, snapshot
from test_permanent_features import records


def test_tutorial_rerolls_keep_inventory_unchanged_until_confirmation(setup_gameplay, monkeypatch):
    c = setup_gameplay
    monkeypatch.setattr("dotabyss_ps.gameplay_content.secrets.randbelow", lambda n: 0)
    call(c, "api/user")
    initial = snapshot(c)[0]
    result = call(c, "api/gacha/tutorial/exec")
    assert len(result["received_content_list"]) == 10
    assert snapshot(c)[0] == initial
    assert call(c, "api/gacha/tutorial/keep-list")["is_confirmed"] == 0
    call(c, "api/gacha/tutorial/exec")
    assert snapshot(c)[0] == initial
    assert not json.loads(snapshot(c)[1])["features"]["statistics"]["data"]["totals"].get("250000:0:0")
    before = snapshot(c)
    call(c, "api/gacha/exec", {"m_gacha_id": 10101, "exec_count": 1, "use_gacha_ticket": 0}, expected=409)
    assert snapshot(c) == before
    call(c, "api/gacha/tutorial/confirm")
    after = snapshot(c)
    assert after[0] != initial
    assert json.loads(after[1])["features"]["statistics"]["data"]["totals"]["250000:0:0"] == 10
    call(c, "api/gacha/tutorial/confirm")
    assert snapshot(c) == after
    assert call(c, "api/gacha/tutorial/keep-list")["is_confirmed"] == 1
    call(c, "api/gacha/tutorial/exec", expected=409)


def test_tutorial_requires_a_pending_draw(setup_gameplay):
    c = setup_gameplay
    call(c, "api/user")
    before = snapshot(c)
    call(c, "api/gacha/tutorial/confirm", expected=409)
    assert snapshot(c) == before


def test_disassembly_mileage_crosses_normal_and_loop_boundaries_once(setup_gameplay):
    c = setup_gameplay
    give(c, [{"content_type": 70, "content_id": 11010110, "amount": 3}])
    gears = [r["id"] for r in records(c)["t_weapons"]["up"]]
    def set_mileage(state, progress, normal, loop):
        state["records"]["t_equipment_mileages"]["up"][0].update(weapon_mileage=normal, loop_weapon_mileage=loop)
    update(c, lambda s, p: set_mileage(s, p, 9900, 0))
    body = {"t_weapon_ids": [gears[0]], "t_armor_ids": [], "t_accessory_ids": []}
    headers = {**c.headers, "x-request-id": "mileage-threshold"}
    reply = call(c, "api/equipment/disassembly/execute", body, headers=headers)
    data = records(c)
    assert data["t_equipment_mileages"]["up"][0]["weapon_mileage"] == 10000
    assert next(r["amount"] for r in data["t_items"]["up"] if r["m_item_id"] == 230001) == 5
    before = snapshot(c)
    assert call(c, "api/equipment/disassembly/execute", body, headers=headers) == reply
    assert snapshot(c) == before
    update(c, lambda s, p: set_mileage(s, p, 250000, 279900))
    call(c, "api/equipment/disassembly/execute", {**body, "t_weapon_ids": [gears[1]]})
    data = records(c)
    assert data["t_equipment_mileages"]["up"][0]["loop_weapon_mileage"] == 0
    catalog = GameplayCatalog(c.tables, c.schema)
    final_group = max(catalog.rows("m_loop_weapon_mileage_rewards"), key=lambda r: r["mileage"])["group_no"]
    reward_numbers = {r["reward_no"] for r in catalog.rows("m_mileage_reward_groups") if r["group_no"] == final_group}
    expected = next(r for r in catalog.rows("m_mileage_rewards") if r["reward_no"] in reward_numbers)
    quantity = next(r["amount"] for r in data["t_items"]["up"] if r["m_item_id"] == expected["content_id"])
    assert quantity == expected["amount"]
    call(c, "api/equipment/disassembly/execute", {**body, "t_weapon_ids": [gears[2]]})
    data = records(c)
    assert data["t_equipment_mileages"]["up"][0]["loop_weapon_mileage"] == 100
    assert next(r["amount"] for r in data["t_items"]["up"] if r["m_item_id"] == expected["content_id"]) == quantity
