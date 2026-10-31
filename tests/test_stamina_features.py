from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from dotabyss_ps.gameplay_records import find, put
from test_economy_features import update
from test_gameplay import NOW, call, gameplay_master, setup_gameplay, snapshot
from test_permanent_features import records, seed


def stamina(context):
    return next(r for r in records(context)["t_timed_parameters"]["up"] if r["type"] == 5)


def body(kind, identifier, amount):
    return {"use_items": [{"content_type": kind, "content_id": identifier, "amount": amount}]}


def test_missing_stamina_is_initialized_and_natural_recovery_keeps_fractional_time(setup_gameplay, monkeypatch):
    c = setup_gameplay
    assert stamina(c)["amount"] == stamina(c)["capacity"] == 240
    update(c, lambda s, p: find(s, "t_timed_parameters", type=5).update(amount=0, last_recovery_at=(NOW - timedelta(seconds=370)).isoformat()))
    assert stamina(c)["amount"] == 2
    assert stamina(c)["last_recovery_at"] == (NOW - timedelta(seconds=10)).isoformat()
    monkeypatch.setattr("test_gameplay.NOW", NOW + timedelta(seconds=170))
    assert stamina(c)["amount"] == 3
    update(c, lambda s, p: find(s, "t_timed_parameters", type=5).update(amount=270, last_recovery_at=(NOW - timedelta(days=1)).isoformat()))
    assert stamina(c)["amount"] == 270


def test_item_recovery_and_concurrent_retry_consume_exactly_once(setup_gameplay):
    c = setup_gameplay
    seed(c, items={150000: 5})
    request = body(30, 150000, 1)
    headers = {**c.headers, "x-request-id": "stamina-item"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: call(c, "api/main-story/recovery", request, headers=headers), range(2)))
    assert responses[0] == responses[1]
    assert stamina(c)["amount"] == 300
    assert next(r for r in records(c)["t_items"]["up"] if r["m_item_id"] == 150000)["amount"] == 4
    assert records(c)["t_stamina_recoveries"]["up"][0]["recovery_count"] == 0


def test_gem_amount_is_a_price_quote_and_daily_tiers_reset_at_five_jst(setup_gameplay, monkeypatch):
    c = setup_gameplay
    seed(c, free_virtual_money=1000)
    call(c, "api/main-story/recovery", body(10, 0, 150))
    assert stamina(c)["amount"] == 600
    assert records(c)["t_stamina_recoveries"]["up"][0]["recovery_count"] == 3
    before = snapshot(c)
    call(c, "api/main-story/recovery", body(10, 0, 50), expected=400)
    assert snapshot(c) == before
    call(c, "api/main-story/recovery", body(10, 0, 100))
    assert stamina(c)["amount"] == 720
    next_day = NOW.replace(hour=4, minute=59) + timedelta(days=1)
    monkeypatch.setattr("test_gameplay.NOW", next_day)
    assert records(c)["t_stamina_recoveries"]["up"][0]["recovery_count"] == 4
    monkeypatch.setattr("test_gameplay.NOW", next_day + timedelta(minutes=1))
    assert records(c)["t_stamina_recoveries"]["up"][0]["recovery_count"] == 0
    call(c, "api/main-story/recovery", body(10, 0, 50))
    assert stamina(c)["amount"] == 840


def test_limited_items_use_unexpired_lots_and_reject_mixed_failure_atomically(setup_gameplay):
    c = setup_gameplay
    seed(c, free_virtual_money=100)
    def lots(state, progress):
        for expiry in (NOW - timedelta(seconds=1), NOW + timedelta(hours=1)):
            put(state, "t_limited_items", {"m_limited_item_id": 150000, "expire_at": expiry.isoformat()}, {"amount": 1})
    update(c, lots)
    call(c, "api/main-story/recovery", body(33, 150000, 1))
    assert stamina(c)["amount"] == 300
    values = records(c)["t_limited_items"]["up"]
    assert [r["amount"] for r in values] == [1, 0]
    before = snapshot(c)
    request = {"use_items": body(33, 150000, 1)["use_items"] + body(10, 0, 50)["use_items"]}
    call(c, "api/main-story/recovery", request, expected=409)
    assert snapshot(c) == before


@pytest.mark.parametrize("payload,status", [
    ({"use_items": []}, 400),
    (body(51, 0, 1), 400),
    (body(30, 150000, 0), 400),
    (body(30, 150000, True), 400),
    (body(30, 150000, 20), 409),
    ({"use_items": body(30, 150000, 1)["use_items"] * 2}, 400),
    (body(10, 0, 51), 400),
])
def test_recovery_rejects_invalid_or_over_capacity_requests_without_charging(setup_gameplay, payload, status):
    c = setup_gameplay
    seed(c, free_virtual_money=1000, items={150000: 30})
    before = snapshot(c)
    call(c, "api/main-story/recovery", payload, expected=status)
    assert snapshot(c) == before
