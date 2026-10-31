import json
from datetime import datetime, timedelta

from dotabyss_ps.gameplay_catalog import JST
from test_gameplay import NOW, call, gameplay_master, setup_gameplay, snapshot
from test_training_encounter_modes import clock, prepare, records, supply, victory


def start_campaign(context):
    prepare(context, chapters=True)
    call(context, "api/idle-exploration/campaign/join", {"m_encounter_campaign_id": 10000})
    call(context, "api/party/idle-exploration/edit", {"party_no": 1, "characters": [{"position_no": 1, "t_character_id": 1, "t_weapon_id": 0}]})
    call(context, "api/party/idle-exploration/assign", {"party_no": 1, "assign_to": 701})


def campaign_encounter(context, kind=10):
    return next(row for row in records(context)["t_idle_exploration_encounters"]["up"] if row["encount_type"] == kind)


def campaign_progress(context):
    return json.loads(snapshot(context)[1])["features"]["encounter"]["data"]["campaigns"]


def test_campaign_join_supply_and_listing_are_idempotent(setup_gameplay):
    c = setup_gameplay
    prepare(c, chapters=True)
    assert supply(c, 4)["amount"] == 3
    view = call(c, "api/idle-exploration/campaign/10000", method="GET")
    assert view["extend_map_areas"] == [701] and len(view["encounters"]) == 10
    assert view["additional_encounter_supply"] == 3 and view["require_clear_count"] == 2
    call(c, "api/idle-exploration/campaign/join", {"m_encounter_campaign_id": 10000})
    assert supply(c, 4)["amount"] == supply(c, 4)["capacity"] == 6
    before = snapshot(c)
    call(c, "api/idle-exploration/campaign/join", {"m_encounter_campaign_id": 10000})
    assert snapshot(c) == before
    assert campaign_progress(c)["current_id"] == 10000
    assert any(row["m_idle_exploration_map_area_id"] == 701 for row in records(c)["t_idle_exploration_map_areas"]["up"])


def test_campaign_normal_clears_generate_bonus_and_bonus_does_not_recurse(setup_gameplay, monkeypatch):
    c = setup_gameplay
    start_campaign(c)
    for index in (1, 2):
        clock(monkeypatch, NOW + timedelta(seconds=index * 110))
        call(c, "api/idle-exploration", {"sequence": ""})
        row = campaign_encounter(c)
        call(c, "api/idle-exploration/encounter/start-quest", {"t_idle_exploration_encounter_id": row["id"], "party_no": 1})
        body = victory(c)
        call(c, "api/idle-exploration/encounter/clear-quest", body)
        before = snapshot(c)
        call(c, "api/idle-exploration/encounter/clear-quest", body)
        assert snapshot(c) == before
    bonus = campaign_encounter(c, 11)
    assert bonus["m_encounter_quest_id"] >= 20000000
    entry = campaign_progress(c)["entries"]["10000"]
    assert sum(entry["clear_counts"].values()) == 2 and entry["bonus_progress"] == 0
    call(c, "api/idle-exploration/encounter/start-quest", {"t_idle_exploration_encounter_id": bonus["id"], "party_no": 1})
    call(c, "api/idle-exploration/encounter/clear-quest", victory(c))
    entry = campaign_progress(c)["entries"]["10000"]
    assert sum(entry["clear_counts"].values()) == 3 and entry["bonus_progress"] == 0
    assert not any(row["encount_type"] == 11 for row in records(c)["t_idle_exploration_encounters"]["up"])
    assert supply(c, 4)["amount"] == 3


def test_expired_campaign_preserves_started_battle_and_disallows_new_join(setup_gameplay, monkeypatch):
    c = setup_gameplay
    (c.settings.data_dir / "gameplay-rules.json").write_text('{"event_open_all":0}', encoding="utf-8")
    opened = datetime(2026, 6, 22, 23, 59, tzinfo=JST)
    clock(monkeypatch, opened)
    start_campaign(c)
    clock(monkeypatch, opened + timedelta(seconds=110))
    call(c, "api/idle-exploration", {"sequence": ""})
    row = campaign_encounter(c)
    call(c, "api/idle-exploration/encounter/start-quest", {"t_idle_exploration_encounter_id": row["id"], "party_no": 1})
    body = victory(c)
    clock(monkeypatch, datetime(2026, 6, 23, 6, tzinfo=JST))
    call(c, "api/idle-exploration/encounter/clear-quest", body)
    assert sum(campaign_progress(c)["entries"]["10000"]["clear_counts"].values()) == 1
    call(c, "api/idle-exploration/campaign/join", {"m_encounter_campaign_id": 10000}, expected=409)
    assert supply(c, 4)["capacity"] == 3
    call(c, "api/idle-exploration", {"sequence": ""})
    assert not records(c)["t_idle_exploration_encounters"]["up"]


def test_switching_campaign_stops_old_area_spawns_and_join_does_not_refill(setup_gameplay, monkeypatch):
    c = setup_gameplay
    start_campaign(c)
    call(c, "api/idle-exploration/campaign/join", {"m_encounter_campaign_id": 10001})
    clock(monkeypatch, NOW + timedelta(hours=1))
    call(c, "api/idle-exploration", {"sequence": ""})
    assert not records(c)["t_idle_exploration_encounters"]["up"]
    call(c, "api/party/idle-exploration/assign", {"party_no": 1, "assign_to": 702})
    clock(monkeypatch, NOW + timedelta(hours=1, seconds=110))
    call(c, "api/idle-exploration", {"sequence": ""})
    row = campaign_encounter(c)
    call(c, "api/idle-exploration/encounter/start-quest", {"t_idle_exploration_encounter_id": row["id"], "party_no": 1})
    call(c, "api/idle-exploration/encounter/surrender-quest", {"play_time": 0})
    assert supply(c, 4)["amount"] == 5
    call(c, "api/idle-exploration/campaign/join", {"m_encounter_campaign_id": 10001})
    assert supply(c, 4)["amount"] == 5
    assert campaign_progress(c)["origins"][str(row["id"])]["campaign_id"] == 10001
