import copy
import json
from datetime import datetime, timedelta

import pytest

from dotabyss_ps.gameplay import Gameplay
from dotabyss_ps.gameplay_catalog import GameplayCatalog, GameplayError, JST, available
from dotabyss_ps.gameplay_login import collect_bonuses, subscription_rewards
from dotabyss_ps.gameplay_records import find, rows
from dotabyss_ps.gameplay_shop import count_entry, ensure_vip_weekly_counters, listing, purchase, shop_data
from dotabyss_ps.newgame import validate_saved_state
from test_gameplay import NOW, call, gameplay_master, setup_gameplay, snapshot
from test_permanent_features import records, seed


def local_game(context, now=NOW):
    state, progress = map(json.loads, snapshot(context))
    return Gameplay(state, progress, GameplayCatalog(context.tables, context.schema), context.identity["user_id"], now)


def test_vip_counters_initialize_claim_once_and_recover_at_monday_five(setup_gameplay, monkeypatch):
    c = setup_gameplay
    seed(c, free_virtual_money=1000)
    first = records(c)["t_timed_parameters"]["up"]
    assert {r["type"]: r["amount"] for r in first if r["type"] in (12, 13)} == {12: 1, 13: 1}
    for kind in (1, 2):
        response = call(c, "api/vip/weekly-reward/exchange", {"m_vip_exchange_type": kind})
        assert response["acquire_contents"]
        assert next(r for r in response["records"]["t_timed_parameters"]["up"] if r["type"] == kind + 11)["amount"] == 0
        before = snapshot(c)
        assert call(c, "api/vip/weekly-reward/exchange", {"m_vip_exchange_type": kind})["acquire_contents"] == []
        assert snapshot(c) == before
    monkeypatch.setattr("test_gameplay.NOW", datetime(2026, 11, 2, 4, 59, 59, tzinfo=JST))
    assert call(c, "api/vip/weekly-reward/exchange", {"m_vip_exchange_type": 1})["acquire_contents"] == []
    monkeypatch.setattr("test_gameplay.NOW", datetime(2026, 11, 2, 5, tzinfo=JST))
    recovered = records(c)["t_timed_parameters"]["up"]
    assert all(r["amount"] == 1 and r["last_recovery_at"] == "2026-11-02T05:00:00+09:00" for r in recovered if r["type"] in (12, 13))
    assert call(c, "api/vip/weekly-reward/exchange", {"m_vip_exchange_type": 1})["acquire_contents"]


def test_vip_legacy_claims_and_captured_spent_counters_cannot_duplicate_rewards(setup_gameplay):
    c = setup_gameplay
    call(c, "api/user")
    game = local_game(c, datetime(2026, 11, 2, 4, tzinfo=JST))
    shop_data(game)["vip_claims"]["2026-W45:1"] = True
    counters = ensure_vip_weekly_counters(game)
    assert counters[1]["amount"] == 0 and counters[2]["amount"] == 1
    game.now += timedelta(hours=1)
    assert ensure_vip_weekly_counters(game)[1]["amount"] == 0
    counters = ensure_vip_weekly_counters(game)
    counters[2]["amount"] = 0
    assert ensure_vip_weekly_counters(game)[2]["amount"] == 0
    assert "2026-W45:1" in shop_data(game)["vip_claims"]
    game.now += timedelta(days=7)
    assert all(row["amount"] == 1 for row in ensure_vip_weekly_counters(game).values())


@pytest.mark.parametrize("kind,boundary", [(2, datetime(2026, 11, 1, 5, tzinfo=JST)), (3, datetime(2026, 11, 2, 5, tzinfo=JST)), (4, datetime(2026, 11, 1, 5, tzinfo=JST))])
def test_shop_period_limits_reset_at_five_not_midnight(setup_gameplay, kind, boundary):
    c = setup_gameplay
    call(c, "api/user")
    game = local_game(c, boundary - timedelta(hours=6))
    payment = {"id": 1}
    count_entry(game, payment, kind)["count"] = 1
    game.now = boundary - timedelta(seconds=1)
    assert count_entry(game, payment, kind)["count"] == 1
    game.now = boundary
    assert count_entry(game, payment, kind)["count"] == 0


def test_vip_failed_cost_is_atomic(setup_gameplay):
    c = setup_gameplay
    seed(c, free_virtual_money=0)
    before = snapshot(c)
    call(c, "api/vip/weekly-reward/exchange", {"m_vip_exchange_type": 2}, expected=409)
    assert snapshot(c) == before
    assert next(r for r in records(c)["t_timed_parameters"]["up"] if r["type"] == 13)["amount"] == 1


def test_purchased_login_bonus_awards_first_day_immediately_then_once_per_reset(setup_gameplay):
    c = setup_gameplay
    result = call(c, "api/payment/grant/free", {"m_payment_id": 110045})
    assert {r["content_type"] for r in result["received_items"]} >= {10, 20, 30, 186}
    game = local_game(c)
    entry = game.progress["features"]["login"]["data"]["bonuses"]["10045"]
    assert entry["index"] == 1
    assert not any(collect_bonuses(game, selected={10045}).values())
    game.now = datetime(2026, 11, 1, 4, 59, 59, tzinfo=JST)
    assert not any(collect_bonuses(game, selected={10045}).values())
    game.now += timedelta(seconds=1)
    assert collect_bonuses(game, selected={10045})["special_condition"]
    assert entry["index"] == 2
    before = snapshot(c)
    call(c, "api/payment/grant/free", {"m_payment_id": 110045}, expected=409)
    assert snapshot(c) == before


def test_subscription_purchase_grants_today_once_and_preserves_entitlement(setup_gameplay):
    c = setup_gameplay
    result = call(c, "api/payment/grant/free", {"m_payment_id": 20001})
    assert any(r["content_type"] == 10 and r["amount"] == 120 for r in result["received_items"])
    game = local_game(c)
    before = copy.deepcopy(game.state)
    subscription_rewards(game)
    assert game.state == before
    with pytest.raises(GameplayError, match="already active"):
        purchase(game, 20001)
    game.now += timedelta(days=31)
    second = purchase(game, 20001)
    assert any(r["content_id"] == 80004 for r in second["received_items"])
    assert find(game.state, "t_payment_subscription_benefits", m_payment_subscription_id=20001)["continue_count"] == 2


@pytest.mark.parametrize("payment_type", [1, 2, 4])
def test_every_current_dmm_product_grants_its_master_pack_and_paid_currency(setup_gameplay, payment_type, monkeypatch):
    c = setup_gameplay
    call(c, "api/user")
    base = local_game(c)
    catalog = base.catalog
    if payment_type == 2:
        monkeypatch.setattr("dotabyss_ps.gameplay_recommendations.eligible", lambda *_: True)
        monkeypatch.setattr("dotabyss_ps.gameplay_recommendations.active", lambda game, definition, _: available(definition, game.now))
    tested = []
    for payment in catalog.rows("m_payments"):
        if payment["payment_type"] != payment_type or not payment["m_payment_dmm_product_id"]:
            continue
        table = {1: "m_payment_lineups", 2: "m_payment_recommends", 4: "m_payment_subscriptions"}[payment_type]
        definition = catalog.tables[table].get(payment["master_id"])
        if definition is None or not available(definition, base.now):
            continue
        game = Gameplay(copy.deepcopy(base.state), copy.deepcopy(base.progress), catalog, base.user_id, base.now)
        if payment_type == 1 and definition["available_type"] == 6:
            step = next(r["step"] for r in catalog.rows("m_payment_lineup_steps") if r["m_payment_id"] == payment["id"])
            shop_data(game)["steps"][str(definition["id"])] = step
        if payment_type == 2:
            shop_data(game)["released"][str(definition["id"])] = (game.now + timedelta(minutes=1)).isoformat()
        result = purchase(game, payment["id"])
        product = catalog.get("m_payment_dmm_products", payment["m_payment_dmm_product_id"])
        paid = sum(r["rest_amount"] for r in rows(game.state, "t_payment_dmm_currencies"))
        assert paid == product["currency_amount"], payment["id"]
        expected_pack = [{k: r[k] for k in ("content_type", "content_id", "amount")} for r in catalog.rows("m_payment_packs") if r["pack_id"] == product["m_payment_pack_pack_id"]]
        for reward in expected_pack:
            assert reward in result["received_items"], payment["id"]
            if reward["content_type"] in (140, 141):
                table, key = ("t_character_skins", "m_character_skin_id") if reward["content_type"] == 140 else ("t_weapon_skins", "m_weapon_skin_id")
                assert find(game.state, table, **{key: reward["content_id"]})
            elif reward["content_type"] == 180:
                assert game.progress["features"]["mission_pass"]["data"]
        validate_saved_state(game.state)
        tested.append(payment["id"])
    assert tested
    if payment_type == 1:
        assert {90006, 110045, 80001, 10261} <= set(tested)


def test_step_lineup_follows_its_mapping_and_never_repeats_completed_steps(setup_gameplay):
    c = setup_gameplay
    call(c, "api/user")
    game = local_game(c, datetime(2026, 9, 22, 14, tzinfo=JST))
    for identifier in range(120001, 120010):
        assert purchase(game, identifier)["received_items"]
        with pytest.raises(GameplayError, match="current step"):
            purchase(game, identifier)
    assert shop_data(game)["steps"]["120001"] == 10
    assert not any(row["m_payment_lineup_id"] == 120001 for row in listing(game, {})["categories"]["commons"])


def test_legacy_purchase_after_midnight_is_not_granted_again_at_new_reset(setup_gameplay):
    c = setup_gameplay
    call(c, "api/user")
    game = local_game(c, datetime(2026, 11, 2, 4, tzinfo=JST))
    shop_data(game)["counts"]["1"] = {"period": "2026-W45", "count": 1}
    assert count_entry(game, {"id": 1}, 3)["count"] == 1
    game.now += timedelta(hours=1)
    assert count_entry(game, {"id": 1}, 3)["count"] == 1
    game.now += timedelta(days=7)
    assert count_entry(game, {"id": 1}, 3)["count"] == 0
