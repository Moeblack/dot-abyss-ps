import copy
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from dotabyss_ps.app import create_app
from dotabyss_ps.gameplay import Gameplay
from dotabyss_ps.gameplay_catalog import GameplayCatalog, GameplayError, available
from dotabyss_ps.gameplay_economy import consume
from dotabyss_ps.gameplay_login import collect_bonuses, subscription_rewards
from dotabyss_ps.gameplay_records import find, rows
from dotabyss_ps.gameplay_shop import purchase
from dotabyss_ps.gameplay_wallet import expire_paid_lots, grant_paid
from test_economy_features import give, update
from test_gameplay import NOW, call, gameplay_master, setup_gameplay, snapshot
from test_permanent_features import records, seed


def local_game(context, now=NOW):
    state, progress = map(json.loads, snapshot(context))
    return Gameplay(state, progress, GameplayCatalog(context.tables, context.schema), context.identity["user_id"], now)


def test_paid_currency_expiration_updates_native_total_once_and_preserves_audit(setup_gameplay, monkeypatch):
    context = setup_gameplay
    seed(context, free_virtual_money=300)
    def add_lots(state, progress):
        grant_paid(state, context.identity["user_id"], NOW, 100, valid_days=1)
        grant_paid(state, context.identity["user_id"], NOW, 200, valid_days=2)
        state["records"]["t_payment_dmm_currencies"]["up"][0]["rest_amount"] = 70
    update(context, add_lots)
    monkeypatch.setattr("test_gameplay.NOW", NOW + timedelta(days=1, microseconds=-1))
    assert sum(row["rest_amount"] for row in records(context)["t_payment_dmm_currencies"]["up"]) == 270
    monkeypatch.setattr("test_gameplay.NOW", NOW + timedelta(days=1))
    response = call(context, "api/payment/lineup")
    expired = response["records"]["t_payment_dmm_currencies"]["up"][0]
    assert expired["rest_amount"] == 0 and expired["acquire_amount"] == 100
    assert sum(row["rest_amount"] for row in records(context)["t_payment_dmm_currencies"]["up"]) == 200
    assert records(context)["t_statuses"]["up"][0]["free_virtual_money"] == 300
    progress = json.loads(snapshot(context)[1])
    assert progress["features"]["shop"]["data"]["expired_paid_lots"]["1"]["amount"] == 70
    before = snapshot(context)
    assert "t_payment_dmm_currencies" not in call(context, "api/payment/lineup")["records"]
    assert snapshot(context) == before


def test_paid_currency_expiration_survives_restart_without_reissuing_it(setup_gameplay, monkeypatch):
    context = setup_gameplay
    call(context, "api/user")
    update(context, lambda state, progress: grant_paid(state, context.identity["user_id"], NOW, 100, valid_days=1))
    monkeypatch.setattr("test_gameplay.NOW", NOW + timedelta(days=1))
    call(context, "api/user")
    before = snapshot(context)
    with TestClient(create_app(context.settings)) as client:
        response = client.get("/api/user", headers=context.headers)
        assert response.status_code == 200
        assert response.json()["contents"]["records"]["t_payment_dmm_currencies"]["up"][0]["rest_amount"] == 0
    assert snapshot(context) == before


def test_paid_currency_expiration_rejects_restored_balance_with_existing_receipt(setup_gameplay):
    context = setup_gameplay
    call(context, "api/user")
    game = local_game(context, NOW + timedelta(days=1))
    grant_paid(game.state, game.user_id, NOW, 100, valid_days=1)
    expire_paid_lots(game)
    rows(game.state, "t_payment_dmm_currencies")[0]["rest_amount"] = 1
    with pytest.raises(GameplayError, match="remaining balance"):
        expire_paid_lots(game)


@pytest.mark.parametrize("receipt_type", ["request", "payment"])
def test_expired_paid_currency_cannot_return_through_old_purchase_receipt(setup_gameplay, monkeypatch, receipt_type):
    context = setup_gameplay
    if receipt_type == "request":
        path = "api/payment/grant/free"
        body = {"m_payment_id": 80008}
        headers = {**context.headers, "x-request-id": "paid-currency-original-order"}
    else:
        token = call(context, "api/charge-center/create-token")["token"]
        path = "api/payment/dmm/verify"
        body = {"data": json.dumps({"m_payment_id": 80008, "token": token}), "signature": "", "purchase_token": token}
        headers = context.headers
    first = call(context, path, body, headers=headers)
    first_rows = first["records"]["t_payment_dmm_currencies"]["up"]
    assert first_rows[0]["rest_amount"] > 0
    monkeypatch.setattr("test_gameplay.NOW", NOW + timedelta(days=181))
    repeated = call(context, path, body, headers=headers)
    assert repeated["received_items"] == first["received_items"]
    assert repeated["records"]["t_payment_dmm_currencies"]["up"][0]["rest_amount"] == 0
    state = json.loads(snapshot(context)[0])
    assert len(state["records"]["t_payment_dmm_currencies"]["up"]) == len(first_rows)
    assert all(row["rest_amount"] == 0 for row in state["records"]["t_payment_dmm_currencies"]["up"])


def test_replayed_response_without_currency_receives_new_expiration_delta(setup_gameplay, monkeypatch):
    context = setup_gameplay
    call(context, "api/payment/grant/free", {"m_payment_id": 80008})
    headers = {**context.headers, "x-request-id": "quest-overview-before-expiry"}
    first = call(context, "api/quest/top", headers=headers)
    assert "t_payment_dmm_currencies" not in first["records"]
    monkeypatch.setattr("test_gameplay.NOW", NOW + timedelta(days=181))
    repeated = call(context, "api/quest/top", headers=headers)
    assert repeated["records"]["t_payment_dmm_currencies"]["up"][0]["rest_amount"] == 0
    assert repeated["receivable_event_missions"] == first["receivable_event_missions"]
    assert call(context, "api/quest/top", headers=headers) == repeated


def test_expired_spent_lot_does_not_reappear_in_replayed_purchase(setup_gameplay, monkeypatch):
    context = setup_gameplay
    headers = {**context.headers, "x-request-id": "spent-lot-original-order"}
    first = call(context, "api/payment/grant/free", {"m_payment_id": 80008}, headers=headers)
    amount = first["records"]["t_payment_dmm_currencies"]["up"][0]["rest_amount"]
    catalog = GameplayCatalog(context.tables, context.schema)
    update(context, lambda state, progress: consume(
        Gameplay(state, progress, catalog, context.identity["user_id"], NOW),
        [{"content_type": 20, "content_id": 0, "amount": amount}]))
    monkeypatch.setattr("test_gameplay.NOW", NOW + timedelta(days=181))
    result = call(context, "api/payment/grant/free", {"m_payment_id": 80008}, headers=headers)
    assert result["records"]["t_payment_dmm_currencies"]["up"][0]["rest_amount"] == 0
    assert not json.loads(snapshot(context)[1])["features"]["shop"]["data"]["expired_paid_lots"]


def test_payment_token_replay_keeps_expiration_delta_for_another_lot(setup_gameplay, monkeypatch):
    context = setup_gameplay
    token = call(context, "api/charge-center/create-token")["token"]
    body = {"data": json.dumps({"m_payment_id": 80008, "token": token}), "signature": "", "purchase_token": token}
    first = call(context, "api/payment/dmm/verify", body)
    assert {row["id"] for row in first["records"]["t_payment_dmm_currencies"]["up"]} == {1}
    update(context, lambda state, progress: grant_paid(state, context.identity["user_id"], NOW, 100, valid_days=1))
    monkeypatch.setattr("test_gameplay.NOW", NOW + timedelta(days=1))
    result = call(context, "api/payment/dmm/verify", body, headers={**context.headers, "x-request-id": "fresh-token-replay"})
    balances = {row["id"]: row["rest_amount"] for row in result["records"]["t_payment_dmm_currencies"]["up"]}
    assert balances[1] > 0 and balances[2] == 0
    assert result["received_items"] == first["received_items"]
    assert call(context, "api/payment/dmm/verify", body,
                headers={**context.headers, "x-request-id": "fresh-token-replay-again"}) == result


def test_subscription_daily_effect_limits_and_exact_expiry_are_enforced(setup_gameplay):
    context = setup_gameplay
    call(context, "api/user")
    game = local_game(context)
    purchase(game, 20001)
    for day in range(1, 30):
        game.now = NOW + timedelta(days=day)
        subscription_rewards(game)
        before = copy.deepcopy((game.state, game.progress))
        subscription_rewards(game)
        assert (game.state, game.progress) == before
    claims = game.progress["features"]["login"]["data"]["subscription_days"]
    assert len(claims["20001:1:1"]) == 20
    assert len(claims["20001:1:2"]) == 7
    game.now = NOW + timedelta(days=30)
    before = copy.deepcopy((game.state, game.progress))
    subscription_rewards(game)
    assert (game.state, game.progress) == before
    purchase(game, 20001)
    assert find(game.state, "t_payment_subscription_benefits", m_payment_subscription_id=20001)["continue_count"] == 2
    assert len(claims["20001:2:1"]) == len(claims["20001:2:2"]) == 1
    assert len(claims["20001:1:1"]) == 20


def test_subscription_early_renewal_and_concurrent_purchase_do_not_grant_twice(setup_gameplay):
    context = setup_gameplay
    call(context, "api/user")
    def attempt(_):
        return context.client.post("/api/payment-subscription/verify", headers=context.headers, json={"m_payment_id": 20001}).status_code
    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(attempt, range(4)))
    assert sorted(statuses) == [200, 409, 409, 409]
    benefits = records(context)["t_payment_subscription_benefits"]["up"]
    assert len(benefits) == 1 and benefits[0]["continue_count"] == 1
    before = snapshot(context)
    call(context, "api/payment-subscription/verify", {"m_payment_id": 20001}, expected=409)
    assert snapshot(context) == before


def test_subscription_purchase_retry_remains_same_order_after_expiry(setup_gameplay, monkeypatch):
    context = setup_gameplay
    headers = {**context.headers, "x-request-id": "subscription-original-order"}
    result = call(context, "api/payment-subscription/verify", {"m_payment_id": 20001}, headers=headers)
    monkeypatch.setattr("test_gameplay.NOW", NOW + timedelta(days=30))
    assert call(context, "api/payment-subscription/verify", {"m_payment_id": 20001}, headers=headers) == result
    assert records(context)["t_payment_subscription_benefits"]["up"][0]["continue_count"] == 1


def test_limited_login_stops_exactly_at_entitlement_expiry(setup_gameplay):
    context = setup_gameplay
    call(context, "api/payment/grant/free", {"m_payment_id": 110045})
    game = local_game(context)
    entry = game.progress["features"]["login"]["data"]["bonuses"]["10045"]
    game.now = NOW + timedelta(days=10)
    before = copy.deepcopy((game.state, game.progress))
    assert not any(collect_bonuses(game, selected={10045}).values())
    assert (game.state, game.progress) == before
    assert entry["index"] == 1


@pytest.mark.parametrize("route,body", [
    ("api/mission-pass/top", {}),
    ("api/mission-pass/level/up", {"level_up_count": 1}),
    ("api/mission-pass/level/reward/receive", {"levels": [{"level": 1, "receive_normal": 1, "receive_premium": 1}]}),
    ("api/mission-pass/loop-reward/receive", {}),
])
def test_ended_pass_rejects_new_actions_without_mutation(setup_gameplay, monkeypatch, route, body):
    context = setup_gameplay
    call(context, "api/user")
    catalog = GameplayCatalog(context.tables, context.schema)
    definition = next(row for row in catalog.rows("m_mission_passes") if available(row, NOW))
    give(context, [{"content_type": 180, "content_id": definition["id"], "amount": 1},
                   {"content_type": 181, "content_id": definition["id"], "amount": 5200}])
    from dotabyss_ps.gameplay_catalog import parse_timestamp
    monkeypatch.setattr("test_gameplay.NOW", parse_timestamp(definition["end_at"]) + timedelta(seconds=1))
    before = snapshot(context)
    call(context, route, {"m_mission_pass_id": definition["id"], **body}, expected=409)
    assert snapshot(context) == before


def test_pass_invalid_late_selection_does_not_claim_earlier_valid_reward(setup_gameplay):
    context = setup_gameplay
    seed(context, free_virtual_money=1000)
    catalog = GameplayCatalog(context.tables, context.schema)
    identifier = next(row["id"] for row in catalog.rows("m_mission_passes") if available(row, NOW))
    call(context, "api/mission-pass/level/up", {"m_mission_pass_id": identifier, "level_up_count": 1})
    before = snapshot(context)
    call(context, "api/mission-pass/level/reward/receive", {"m_mission_pass_id": identifier, "levels": [
        {"level": 1, "receive_normal": 1, "receive_premium": 0},
        {"level": 2, "receive_normal": 1, "receive_premium": 0}]}, expected=409)
    assert snapshot(context) == before


def test_pass_loop_reward_concurrent_claim_is_single_credit(setup_gameplay):
    context = setup_gameplay
    call(context, "api/user")
    catalog = GameplayCatalog(context.tables, context.schema)
    identifier = next(row["id"] for row in catalog.rows("m_mission_passes") if available(row, NOW))
    give(context, [{"content_type": 181, "content_id": identifier, "amount": 5200}])
    before_gold = records(context)["t_statuses"]["up"][0]["gold"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: call(context, "api/mission-pass/loop-reward/receive", {"m_mission_pass_id": identifier}), range(4)))
    record = json.loads(snapshot(context)[1])["features"]["mission_pass"]["data"][str(identifier)]
    assert record["loop_received"] == 2
    assert records(context)["t_statuses"]["up"][0]["gold"] - before_gold == 2000
    before = snapshot(context)
    call(context, "api/mission-pass/loop-reward/receive", {"m_mission_pass_id": identifier})
    assert snapshot(context) == before
