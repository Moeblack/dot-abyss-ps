import copy
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from dotabyss_ps.app import create_app
from dotabyss_ps.gameplay_catalog import GameplayCatalog, GameplayError
from dotabyss_ps.gameplay_missions import Missions
from dotabyss_ps.gameplay_records import put
from test_gameplay import NOW, call, gameplay_master, setup_gameplay, snapshot
from test_nether_modes import mutate, progress, start, state, unlock_nether


RESULT = {"m_nether_id": 1, "m_nether_map_id": 1, "use_item_flag": 0}


def counter(context, target=1):
    data = progress(context)["features"]["statistics"]["data"]
    return data["totals"].get(f"210001:{target}:0", 0)


def result_payload(response):
    return {key: value for key, value in response.items() if key != "records"}


@pytest.mark.parametrize("status", [2, 7, 8])
def test_return_retreat_and_clear_count_once_across_retries_and_restart(setup_gameplay, status):
    context = setup_gameplay
    start(context)
    mutate(context, lambda state, _: state["records"]["t_nethers"]["up"][0].update(status=status))
    response = call(context, "api/nether/result", RESULT)
    assert counter(context) == counter(context, 0) == 1
    stable = snapshot(context)
    assert result_payload(call(context, "api/nether/result", RESULT)) == result_payload(response)
    assert snapshot(context) == stable
    with TestClient(create_app(context.settings)) as restarted:
        retried = restarted.post("/api/nether/result", headers=context.headers, json=RESULT)
        assert retried.status_code == 200
        assert result_payload(retried.json()["contents"]) == result_payload(response)
    assert snapshot(context) == stable
    call(context, "api/nether/cancel", {})
    call(context, "api/nether/cancel", {})
    assert counter(context) == 1


def test_three_expeditions_complete_pass_task_and_grant_points_once(setup_gameplay):
    context = setup_gameplay
    start(context)
    mutate(context, lambda state, _: put(state, "t_items", {"m_item_id": 200002}, {"amount": 2}))
    for attempt in range(3):
        if attempt:
            call(context, "api/nether/start", {"m_nether_id": 1, "m_nether_map_id": 1, "party_no": 1,
                                               "use_ticket": 1, "start_floor_level": 1})
        call(context, "api/nether/result", RESULT)
        assert counter(context) == attempt + 1
    body = {"m_mission_pass_id": 202611}
    tasks = call(context, "api/mission-pass/top", body)["missions"]["pass_total"]
    assert next(task for task in tasks if task["m_mission_id"] == 50051)["value"] == 3
    claim = {**body, "m_mission_ids": [50051]}
    call(context, "api/mission-pass/mission/reward/receive", claim)
    assert call(context, "api/mission-pass/top", body)["t_mission_pass"]["total_pass_point"] == 300
    stable = snapshot(context)
    call(context, "api/mission-pass/mission/reward/receive", claim)
    assert snapshot(context) == stable
    catalog = GameplayCatalog(context.tables, context.schema)
    evaluator = Missions(state(context), progress(context), catalog, NOW, context.identity["user_id"])
    assert evaluator.value(catalog.get("m_missions", 3006)) == 3
    assert evaluator.value(catalog.get("m_missions", 102401)) == 3


def test_abort_without_result_does_not_create_a_completed_expedition(setup_gameplay):
    context = setup_gameplay
    unlock_nether(context)
    call(context, "api/nether/cancel", {})
    assert counter(context) == 0
    start(context)
    call(context, "api/nether/cancel", {})
    call(context, "api/nether/cancel", {})
    assert counter(context) == 0
    stable = snapshot(context)
    call(context, "api/nether/result", RESULT, expected=409)
    assert snapshot(context) == stable


def test_failed_result_does_not_increment_completion_count(setup_gameplay, monkeypatch):
    context = setup_gameplay
    start(context)

    def failed_delivery(*args, **kwargs):
        raise GameplayError("Synthetic delivery failure")

    stable = snapshot(context)
    with monkeypatch.context() as patch:
        patch.setattr("dotabyss_ps.gameplay_nether.deliver", failed_delivery)
        call(context, "api/nether/result", RESULT, expected=409)
    assert snapshot(context) == stable
    assert counter(context) == 0
    call(context, "api/nether/result", RESULT)
    assert counter(context) == 1


def test_settled_legacy_run_does_not_backfill_unknown_historical_count(setup_gameplay):
    context = setup_gameplay
    start(context)
    response = call(context, "api/nether/result", RESULT)

    def remove_recorded_counts(_, gameplay):
        data = gameplay["features"]["statistics"]["data"]
        for values in [data["totals"], *data["days"].values()]:
            values.pop("210001:0:0", None)
            values.pop("210001:1:0", None)
        periods = gameplay["features"].get("missions", {}).get("data", {}).get("pass_statistics", {}).get("periods", {})
        for period in periods.values():
            period["values"].pop("210001:1:0", None)

    mutate(context, remove_recorded_counts)
    stable = snapshot(context)
    assert result_payload(call(context, "api/nether/result", RESULT)) == result_payload(response)
    assert counter(context) == 0
    assert snapshot(context) == stable


def test_concurrent_result_requests_count_one_expedition(setup_gameplay):
    context = setup_gameplay
    start(context)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: call(context, "api/nether/result", copy.deepcopy(RESULT)), range(4)))
    assert all(result_payload(response) == result_payload(results[0]) for response in results)
    assert counter(context) == counter(context, 0) == 1
