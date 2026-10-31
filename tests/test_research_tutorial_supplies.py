import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from dotabyss_ps.app import create_app
from dotabyss_ps.gameplay_catalog import GameplayError
from dotabyss_ps.newgame import build_initial_state
from test_gameplay import call, gameplay_master, setup_gameplay, snapshot
from test_localstate import login
from test_permanent_features import seed


MATERIAL_IDS = (50001, 50004, 50007)


@pytest.fixture(autouse=True)
def legacy_profile(setup_gameplay):
    context = setup_gameplay
    context.store.game_state(context.identity["user_id"], context.record["revision"],
                             lambda identity, name, created_at: build_initial_state(
                                 context.tables, context.schema, identity, name, created_at, start_tutorial=False))


def configure(context, *, unlocked=1, amount=100):
    path = context.settings.data_dir / "gameplay-rules.json"
    path.write_text(json.dumps({"format_version": 1, "unlock_common_features": unlocked,
                               "research_tutorial_material_amount": amount}), encoding="utf-8")


def materials(records):
    amounts = {row["m_item_id"]: row["amount"] for row in records["t_items"]["up"]}
    return {identifier: amounts.get(identifier, 0) for identifier in MATERIAL_IDS}


def receipt(context):
    progress = json.loads(snapshot(context)[1])
    return progress["features"].get("research", {}).get("data", {}).get("tutorial_supplies")


def test_materials_are_added_once_and_survive_login_restart_and_rule_change(setup_gameplay):
    c = setup_gameplay
    seed(c, items={50001: 7, 50004: 11, 50007: 13})
    configure(c)
    expected = {50001: 107, 50004: 111, 50007: 113}
    assert materials(call(c, "api/user")["records"]) == expected
    original_receipt = receipt(c)
    assert original_receipt["amount"] == 100
    configure(c, amount=200)
    identity, headers = login(c.client)
    assert identity["user_id"] == c.identity["user_id"]
    assert materials(call(c, "api/mypage/login", headers=headers, method="GET")["records"]) == expected
    with TestClient(create_app(c.settings)) as restarted:
        response = restarted.get("/api/user", headers=headers)
        assert response.status_code == 200
        assert materials(response.json()["contents"]["records"]) == expected
    assert receipt(c) == original_receipt


@pytest.mark.parametrize("unlocked,amount,completed", [(0, 100, False), (1, 0, False), (1, 100, True)])
def test_supply_requires_early_unlock_enabled_amount_and_unfinished_tutorial(setup_gameplay, unlocked, amount, completed):
    c = setup_gameplay
    if completed:
        call(c, "api/tutorial/feature-exec", {"tutorial_type": 9})
    configure(c, unlocked=unlocked, amount=amount)
    result = call(c, "api/user")
    assert materials(result["records"]) == {50001: 0, 50004: 5 if unlocked else 0, 50007: 0}
    assert receipt(c) is None


def test_factory_upgrade_spends_supplies_normally_without_retry_or_login_refill(setup_gameplay):
    c = setup_gameplay
    configure(c)
    assert materials(call(c, "api/user")["records"]) == dict.fromkeys(MATERIAL_IDS, 100)
    before = call(c, "api/research")["building_skills"]
    assert next(row for row in before if row["m_building_skill_level_id"] == 500000)["is_upgradable"] == 1
    headers = {**c.headers, "x-request-id": "factory-tutorial-upgrade"}
    body = {"m_building_id": 500, "skill_type": 5000}
    result = call(c, "api/research/upgrade", body, headers=headers)
    assert call(c, "api/research/upgrade", body, headers=headers) == result
    records = call(c, "api/user")["records"]
    assert materials(records) == {50001: 100, 50004: 95, 50007: 100}
    skill = next(row for row in records["t_building_skills"]["up"] if row["skill_type"] == 5000)
    assert skill["skill_level"] == 1
    assert not any(row["completed_flags"] & 256 for row in records["t_feature_tutorials"]["up"])


def test_concurrent_first_visits_do_not_duplicate_supplies(setup_gameplay):
    c = setup_gameplay
    configure(c)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: call(c, "api/user"), range(2)))
    assert all(materials(result["records"]) == dict.fromkeys(MATERIAL_IDS, 100) for result in results)
    assert receipt(c)["amount"] == 100


def test_failed_supply_delivery_rolls_back_inventory_and_receipt(setup_gameplay, monkeypatch):
    import dotabyss_ps.gameplay_research as research

    c = setup_gameplay
    configure(c)
    before = snapshot(c)
    original_delivery = research.deliver

    def fail_after_delivery(game, rewards):
        original_delivery(game, rewards)
        raise GameplayError("Interrupted supply delivery", 503, 5036)

    with monkeypatch.context() as patch:
        patch.setattr(research, "deliver", fail_after_delivery)
        call(c, "api/user", expected=503)
    assert snapshot(c) == before
    assert materials(call(c, "api/user")["records"]) == dict.fromkeys(MATERIAL_IDS, 100)
    assert receipt(c)["amount"] == 100


def test_inventory_overflow_preserves_supplies_in_mail_without_duplication(setup_gameplay):
    c = setup_gameplay
    seed(c, items={50004: 999990})
    configure(c)
    assert materials(call(c, "api/user")["records"]) == {50001: 100, 50004: 999999, 50007: 100}
    before = call(c, "api/present/list/1/0", method="GET")["present_list"]
    overflow = [row for row in before if row["content_type"] == 30 and row["content_id"] == 50004]
    assert len(overflow) == 1 and overflow[0]["amount"] == 91
    call(c, "api/user")
    assert call(c, "api/present/list/1/0", method="GET")["present_list"] == before
