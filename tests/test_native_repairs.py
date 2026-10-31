import json
from datetime import datetime, timedelta

from dotabyss_ps.gameplay_catalog import GameplayCatalog
from dotabyss_ps.gameplay_records import rows
from dotabyss_ps.protocol import envelope
from test_economy_features import update
from test_gameplay import call, gameplay_master, setup_gameplay, snapshot
from test_permanent_features import records, seed


def test_legacy_soul_mail_settles_once_and_never_reaches_native_lists(setup_gameplay):
    c = setup_gameplay
    home = call(c, "api/home", method="POST")
    assert home["records"]["t_statuses"]["up"][0]["soul"] == 10000
    assert home["badges"]["present_count"] == 3

    def legacy(state, progress):
        rows(state, "t_statuses")[0]["soul"] = 0
        for entry in progress["features"]["presents"]["data"]["entries"]:
            if entry["content_type"] == 51:
                entry["accepted_at"] = None

    update(c, legacy)
    before = snapshot(c)
    call(c, "api/present/receive", {"ids": [99999]}, expected=409)
    assert snapshot(c) == before
    listing = call(c, "api/present/list/1/0", method="GET")
    assert {r["content_type"] for r in listing["present_list"]} == {10, 50, 110}
    assert records(c)["t_statuses"]["up"][0]["soul"] == 10000
    progress = json.loads(snapshot(c)[1])
    legacy_id = next(r["id"] for r in progress["features"]["presents"]["data"]["entries"] if r["content_type"] == 51)
    response = call(c, "api/present/receive", {"ids": [legacy_id]})
    assert response["present_list"] is None
    assert response["received_list"] == []
    assert call(c, "api/present/list/99/0", method="GET")["received_list"] == []
    assert records(c)["t_statuses"]["up"][0]["soul"] == 10000


def test_shop_lists_only_products_the_native_dmm_lookup_can_resolve(setup_gameplay):
    c = setup_gameplay
    master = GameplayCatalog(c.tables, c.schema)
    categories = call(c, "api/payment/lineup")["categories"]
    products = {r["id"] for r in master.rows("m_payment_dmm_products")}
    category_ids = {r["id"] for r in master.rows("m_payment_categories")}
    for category, kind, field in (("commons", 1, "m_payment_lineup_id"), ("recommends", 2, "m_payment_recommend_id"), ("subscriptions", 4, "m_payment_subscription_id")):
        for item in categories[category]:
            native_first = next(r for r in master.rows("m_payments") if r["payment_type"] == kind and r["master_id"] == item[field])
            assert native_first["m_payment_dmm_product_id"] in products
            if kind == 1:
                assert master.get("m_payment_lineups", item[field])["m_payment_category_id"] in category_ids
    assert not set(range(80015, 80022)) & {r["m_payment_lineup_id"] for r in categories["commons"]}
    before = snapshot(c)
    for identifier in range(80015, 80022):
        call(c, "api/payment/grant/free", {"m_payment_id": identifier}, expected=400)
        assert snapshot(c) == before


def test_context_only_mission_pass_product_keeps_its_purchase_route(setup_gameplay):
    c = setup_gameplay
    assert 90006 not in {r["m_payment_lineup_id"] for r in call(c, "api/payment/lineup")["categories"]["commons"]}
    result = call(c, "api/payment/grant/free", {"m_payment_id": 90006})
    assert any(r["content_type"] == 180 for r in result["received_items"])


def test_successful_mail_claim_uses_null_warning_marker_with_other_mail_pending(setup_gameplay):
    c = setup_gameplay
    pending = call(c, "api/present/list/1/0", method="GET")["present_list"]
    result = call(c, "api/present/receive", {"ids": [pending[0]["id"]]})
    assert result["present_list"] is None
    assert [r["id"] for r in result["received_list"]] == [pending[0]["id"]]
    assert len(call(c, "api/present/list/1/0", method="GET")["present_list"]) == 2


def test_research_lists_every_master_tier_and_preserves_native_lookup_invariants(setup_gameplay):
    c = setup_gameplay
    seed(c, gold=1000000, items={50001: 100, 50004: 100, 50007: 100})
    master = GameplayCatalog(c.tables, c.schema)
    opened = {r["m_building_id"] for r in records(c)["t_buildings"]["up"]}
    expected = {r["id"]: r for r in master.rows("m_building_skill_levels") if r["m_building_id"] in opened}
    listing = call(c, "api/research")["building_skills"]
    assert {r["m_building_skill_level_id"] for r in listing} == expected.keys()
    for entry in listing:
        definition = expected[entry["m_building_skill_level_id"]]
        assert entry["skill_level"] > 0
        assert all(entry[k] == definition[k] for k in ("m_building_id", "skill_type", "skill_level"))
    call(c, "api/research/upgrade", {"m_building_id": 100, "skill_type": 1004})
    tiers = [r for r in call(c, "api/research")["building_skills"] if r["m_building_id"] == 100 and r["skill_type"] == 1004]
    assert tiers[0]["is_unlocked"] == 1 and tiers[0]["is_new"] == 1
    assert all(r["is_unlocked"] == 0 for r in tiers[1:])
    call(c, "api/research/upgrade/direction/check", {"m_building_id": 100, "skill_type": 1004})
    assert not any(r["is_new"] for r in call(c, "api/research")["building_skills"] if r["skill_type"] == 1004)


def test_zero_plan_overview_contains_owned_plan_missions_without_duplicates(setup_gameplay):
    c = setup_gameplay
    owned = [r["m_plan_id"] for r in records(c)["t_plans"]["up"]]
    assert owned
    individual = {r["m_mission_id"] for plan in owned for r in call(c, f"api/plan-mission/list/{plan}", method="GET")["limited"]}
    overview = call(c, "api/plan-mission/list/0", method="GET")["limited"]
    assert {r["m_mission_id"] for r in overview} == individual
    assert len(overview) == len(individual)
    before = snapshot(c)
    call(c, "api/plan-mission/reward/receive", {"m_plan_id": 0, "m_mission_ids": [next(iter(individual))]}, expected=400)
    assert snapshot(c) == before


def test_native_server_time_is_explicit_jst_and_matches_unix_timestamp():
    result = envelope({}, {})
    parsed = datetime.fromisoformat(result["server_time"])
    assert parsed.utcoffset() == timedelta(hours=9)
    assert int(parsed.timestamp()) == result["timestamp"]
