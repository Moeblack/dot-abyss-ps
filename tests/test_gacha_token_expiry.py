import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest

from dotabyss_ps.gameplay import Gameplay
from dotabyss_ps.gameplay_catalog import GameplayCatalog, GameplayError, JST
from dotabyss_ps.gameplay_economy import consume
from test_economy_features import give, update
from test_gameplay import NOW, call, gameplay_master, setup_gameplay, snapshot
from test_permanent_features import records, seed


def token_count(context, identifier):
    return next(row["token_num"] for row in records(context)["t_gacha_tokens"]["up"]
                if row["m_gacha_token_id"] == identifier)


def test_expired_tokens_convert_to_gold_once_and_preserve_active_tokens(setup_gameplay):
    context = setup_gameplay
    give(context, [{"content_type": 170, "content_id": 3, "amount": 12},
                   {"content_type": 170, "content_id": 1, "amount": 7}])
    before = records(context)["t_statuses"]["up"][0]["gold"]
    headers = {**context.headers, "x-request-id": "expired-token-conversion"}
    result = call(context, "api/gacha", headers=headers)
    assert result["converted_gacha_tokens"] == [{"m_gacha_token_id": 3, "gacha_token_convert_amount": 12,
                                                  "converted_item_acquire_amount": 12}]
    assert token_count(context, 3) == 0 and token_count(context, 1) == 7
    assert records(context)["t_statuses"]["up"][0]["gold"] == before + 12
    saved = snapshot(context)
    assert call(context, "api/gacha", headers=headers) == result
    assert snapshot(context) == saved
    assert call(context, "api/gacha")["converted_gacha_tokens"] == []


@pytest.mark.parametrize("rate", [0, 7])
def test_expiry_rate_is_configurable_and_zero_preserves_balance(setup_gameplay, rate):
    context = setup_gameplay
    (context.settings.data_dir / "gameplay-rules.json").write_text(
        json.dumps({"gacha_expired_token_gold_per_token": rate}), encoding="utf-8")
    give(context, [{"content_type": 170, "content_id": 3, "amount": 4}])
    before = records(context)["t_statuses"]["up"][0]["gold"]
    call(context, "api/gacha")
    assert token_count(context, 3) == (4 if rate == 0 else 0)
    assert records(context)["t_statuses"]["up"][0]["gold"] == before + 4 * rate


def test_expiry_respects_the_master_end_time(setup_gameplay, monkeypatch):
    context = setup_gameplay
    cutoff = datetime(2026, 10, 5, 4, 59, 59, tzinfo=JST)
    monkeypatch.setattr("test_gameplay.NOW", cutoff)
    give(context, [{"content_type": 170, "content_id": 3, "amount": 4}])
    assert call(context, "api/gacha")["converted_gacha_tokens"] == []
    assert token_count(context, 3) == 4
    monkeypatch.setattr("test_gameplay.NOW", cutoff + timedelta(seconds=1))
    assert call(context, "api/gacha")["converted_gacha_tokens"][0]["gacha_token_convert_amount"] == 4


def test_held_expired_tokens_cannot_be_spent(setup_gameplay):
    context = setup_gameplay
    give(context, [{"content_type": 170, "content_id": 3, "amount": 4}])
    saved = snapshot(context)
    catalog = GameplayCatalog(context.tables, context.schema)
    with pytest.raises(GameplayError, match="availability"):
        update(context, lambda state, progress: consume(
            Gameplay(state, progress, catalog, context.identity["user_id"], NOW),
            [{"content_type": 170, "content_id": 3, "amount": 1}]))
    assert snapshot(context) == saved


def test_concurrent_expiry_and_overflow_are_atomic(setup_gameplay):
    context = setup_gameplay
    give(context, [{"content_type": 170, "content_id": 3, "amount": 20}])
    before = records(context)["t_statuses"]["up"][0]["gold"]
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: call(context, "api/gacha"), range(3)))
    assert sum(len(result["converted_gacha_tokens"]) for result in results) == 1
    assert records(context)["t_statuses"]["up"][0]["gold"] == before + 20
    give(context, [{"content_type": 170, "content_id": 3, "amount": 20}])
    seed(context, gold=2 ** 31 - 5)
    saved = snapshot(context)
    call(context, "api/gacha", expected=400)
    assert snapshot(context) == saved


def test_new_expired_balance_is_accounted_without_regranting_old_balance(setup_gameplay):
    context = setup_gameplay
    give(context, [{"content_type": 170, "content_id": 3, "amount": 4}])
    call(context, "api/gacha")
    give(context, [{"content_type": 170, "content_id": 3, "amount": 2}])
    assert call(context, "api/gacha")["converted_gacha_tokens"][0]["converted_item_acquire_amount"] == 2
    receipts = json.loads(snapshot(context)[1])["features"]["gacha"]["data"]["expiry_receipts"]
    assert list(receipts.values())[0]["token_num"] == 6
    assert list(receipts.values())[0]["gold"] == 6
