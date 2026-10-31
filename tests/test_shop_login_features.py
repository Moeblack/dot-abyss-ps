import copy
import json
from datetime import timedelta

import pytest

from dotabyss_ps.gameplay import Gameplay
from dotabyss_ps.gameplay_catalog import GameplayCatalog, GameplayError
from dotabyss_ps.gameplay_economy import consume
from dotabyss_ps.gameplay_records import put
from dotabyss_ps.masterdata import MasterDataError
from dotabyss_ps.server_master_bindings import bound_fields
from test_economy_features import give, update
from test_gameplay import NOW, call, gameplay_master, setup_gameplay, snapshot
from test_localstate import login
from test_permanent_features import records, seed


def test_free_shop_limits_reset_by_period_and_retry_is_not_another_purchase(setup_gameplay, monkeypatch):
    c = setup_gameplay
    listing = call(c, "api/payment/lineup")
    assert {50001, 60001, 70001} <= {r["m_payment_lineup_id"] for r in listing["categories"]["commons"]}
    headers = {**c.headers, "x-request-id": "daily-shop"}
    result = call(c, "api/payment/grant/free", {"m_payment_id": 70001}, headers=headers)
    before = snapshot(c)
    assert call(c, "api/payment/grant/free", {"m_payment_id": 70001}, headers=headers) == result
    call(c, "api/payment/grant/free", {"m_payment_id": 70001}, expected=409)
    assert snapshot(c) == before
    for identifier in (50001, 60001):
        call(c, "api/payment/grant/free", {"m_payment_id": identifier})
    monkeypatch.setattr("test_gameplay.NOW", NOW + timedelta(days=1))
    for identifier in (50001, 70001):
        call(c, "api/payment/grant/free", {"m_payment_id": identifier})
    call(c, "api/payment/grant/free", {"m_payment_id": 60001}, expected=409)
    monkeypatch.setattr("test_gameplay.NOW", NOW + timedelta(days=2))
    call(c, "api/payment/grant/free", {"m_payment_id": 60001})


def test_local_paid_product_creates_currency_and_vip_without_external_payment(setup_gameplay):
    c = setup_gameplay
    call(c, "api/payment/grant/free", {"m_payment_id": 80001})
    data = records(c)
    assert sum(r["rest_amount"] for r in data["t_payment_dmm_currencies"]["up"]) == 15000
    assert data["t_statuses"]["up"][0]["free_virtual_money"] == 15000
    assert data["t_vip_ranks"]["up"][0]["total_point"] == 15000
    assert all(r["price"] == 0 and r["store_product_id"].startswith("local.") for r in data["t_payment_dmm_currencies"]["up"])
    call(c, "api/mission/reward/receive", {"m_mission_ids": [2010]})


def test_paid_lots_use_expiry_order_and_mixed_costs_are_reserved_atomically(setup_gameplay):
    c = setup_gameplay
    seed(c, free_virtual_money=2000)
    give(c, [{"content_type": 20, "content_id": 0, "amount": 8000}])
    catalog = GameplayCatalog(c.tables, c.schema)
    def spend(state, progress, costs):
        consume(Gameplay(state, progress, catalog, c.identity["user_id"], NOW), costs)
    before = snapshot(c)
    with pytest.raises(GameplayError, match="Insufficient"):
        update(c, lambda s, p: spend(s, p, [{"content_type": 10, "content_id": 0, "amount": 8000}, {"content_type": 20, "content_id": 0, "amount": 3000}]))
    assert snapshot(c) == before
    update(c, lambda s, p: spend(s, p, [{"content_type": 10, "content_id": 0, "amount": 8000}]))
    data = records(c)
    assert data["t_statuses"]["up"][0]["free_virtual_money"] == 0
    assert data["t_payment_dmm_currencies"]["up"][0]["rest_amount"] == 2000
    update(c, lambda s, p: s["records"]["t_payment_dmm_currencies"]["up"][0].update(valid_at=(NOW - timedelta(seconds=1)).isoformat()))
    call(c, "api/user")
    before = snapshot(c)
    with pytest.raises(GameplayError, match="Insufficient"):
        update(c, lambda s, p: spend(s, p, [{"content_type": 20, "content_id": 0, "amount": 1}]))
    assert snapshot(c) == before


def test_local_receipts_are_profile_bound_expire_and_replay_without_double_grant(setup_gameplay, monkeypatch):
    c = setup_gameplay
    token = call(c, "api/charge-center/create-token")["token"]
    _, other_headers = login(c.client, "other-shop-user")
    body = {"data": json.dumps({"m_payment_id": 80008, "token": token}), "signature": "", "purchase_token": token}
    call(c, "api/payment/dmm/verify", body, headers=other_headers, expected=409)
    result = call(c, "api/payment/dmm/verify", body)
    before = snapshot(c)
    assert call(c, "api/payment/dmm/verify", body) == result
    assert snapshot(c) == before
    call(c, "api/payment/dmm/verify", {**body, "data": "official-store-receipt"}, expected=400)
    token = call(c, "api/charge-center/create-token")["token"]
    monkeypatch.setattr("test_gameplay.NOW", NOW + timedelta(minutes=6))
    call(c, "api/payment/appstore/verify", {"transaction_id": f"local:80008:{token}"}, expected=409)


def test_login_bonuses_history_and_subscription_rewards_are_once_per_day(setup_gameplay, monkeypatch):
    c = setup_gameplay
    call(c, "api/payment-subscription/verify", {"m_payment_id": 20001})
    first = call(c, "api/mypage/login", method="GET")
    assert first["login_bonus_list"]["daily"]
    before = snapshot(c)
    second = call(c, "api/mypage/login", method="GET")
    assert all(not values for values in second["login_bonus_list"].values())
    assert snapshot(c) == before
    ids = [r["m_login_bonus_id"] for values in first["login_bonus_list"].values() for r in values]
    history = call(c, "api/mypage/login-bonus/history", {"m_login_bonus_ids": ids})
    assert history["login_bonus_histories"] == first["login_bonus_list"]
    claims = json.loads(snapshot(c)[1])["features"]["login"]["data"]["subscription_days"]
    assert claims and all(len(days) == 1 for days in claims.values())
    monkeypatch.setattr("test_gameplay.NOW", NOW + timedelta(days=1))
    call(c, "api/mypage/login", method="GET")
    claims = json.loads(snapshot(c)[1])["features"]["login"]["data"]["subscription_days"]
    assert all(len(days) == 2 for days in claims.values())


def test_limited_login_entitlement_and_expired_item_conversion(setup_gameplay):
    c = setup_gameplay
    give(c, [{"content_type": 186, "content_id": 10001, "amount": 1}])
    catalog = GameplayCatalog(c.tables, c.schema)
    definition = next(r for r in catalog.rows("m_limited_items") if r["m_limited_item_convert_content_group_id"] == 1)
    update(c, lambda s, p: put(s, "t_limited_items", {"m_limited_item_id": definition["id"]}, {"amount": 2, "expire_at": (NOW - timedelta(seconds=1)).isoformat()}))
    result = call(c, "api/mypage/login", method="GET")
    assert any(r["m_login_bonus_id"] == 10001 for r in result["login_bonus_list"]["special_condition"])
    expired = next(r for r in result["expired_limited_items"] if r["m_limited_item_id"] == definition["id"])
    assert expired["amount"] == 2 and expired["converted_contents"] == [{"content_type": 30, "content_id": 220001, "amount": 2}]
    assert not call(c, "api/mypage/login", method="GET")["expired_limited_items"]


def test_event_currency_uses_its_native_identity_field(setup_gameplay):
    c = setup_gameplay
    give(c, [{"content_type": 54, "content_id": 3000, "amount": 150}])
    assert records(c)["t_event_currencies"]["up"] == [{"m_event_currency_id": 3000, "amount": 150}]
    catalog = GameplayCatalog(c.tables, c.schema)
    update(c, lambda s, p: consume(Gameplay(s, p, catalog, c.identity["user_id"], NOW), [{"content_type": 54, "content_id": 3000, "amount": 25}]))
    assert records(c)["t_event_currencies"]["up"][0]["amount"] == 125


def test_serial_vip_and_shop_read_markers_persist(setup_gameplay):
    c = setup_gameplay
    call(c, "api/payment/read/common", {"m_payment_lineup_ids": [70001], "m_payment_subscription_ids": [20001]})
    listing = call(c, "api/payment/lineup")["categories"]
    assert next(r for r in listing["commons"] if r["m_payment_lineup_id"] == 70001)["is_new"] == 0
    assert next(r for r in listing["subscriptions"] if r["m_payment_subscription_id"] == 20001)["is_new"] == 0
    result = call(c, "api/serial/confirm", {"serial_code": "localwelcome"})
    assert result["status"] == 0 and result["rewards"]
    before = snapshot(c)
    assert call(c, "api/serial/confirm", {"serial_code": "LOCALWELCOME"})["status"] == 1
    assert snapshot(c) == before
    assert call(c, "api/vip/weekly-reward/exchange", {"m_vip_exchange_type": 1})["acquire_contents"]
    before = snapshot(c)
    assert call(c, "api/vip/weekly-reward/exchange", {"m_vip_exchange_type": 1})["acquire_contents"] == []
    assert snapshot(c) == before


def test_all_home_rewards_use_pinned_master_and_visitor_claims_are_separate(setup_gameplay, monkeypatch):
    c = setup_gameplay
    (c.settings.data_dir / "gameplay-rules.json").write_text(json.dumps({"home_enable_visitors": 1}), encoding="utf-8")
    monkeypatch.setattr("dotabyss_ps.gameplay_content.secrets.randbelow", lambda n: 0)
    result = call(c, "api/home")
    assert result["daily_visitors"] and result["records"]["t_visitors"]["up"]
    catalog = GameplayCatalog(c.tables, c.schema)
    for entry in result["daily_greetings"]:
        body = {k: entry[k] for k in ("m_home_daily_greeting_id", "m_home_visitor_daily_greeting_id")}
        reward = call(c, "api/home/daily-greeting/receive-reward", body)["rewards"]
        assert reward
        before = snapshot(c)
        assert call(c, "api/home/daily-greeting/receive-reward", body)["rewards"] == []
        assert snapshot(c) == before
    values = copy.deepcopy(c.tables["m_home_greeting_rewards"])
    assert bound_fields("m_home_greeting_rewards", values) == ["id", "group_id", "content_type", "content_id", "amount"]
    values[0][-1] += 1
    with pytest.raises(MasterDataError, match="requires review"):
        bound_fields("m_home_greeting_rewards", values)
