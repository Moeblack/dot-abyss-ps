import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from dotabyss_ps.gameplay_catalog import GameplayCatalog
from test_gameplay import NOW, call, gameplay_master, setup_gameplay, snapshot
from test_permanent_features import records, seed


def initialize(context):
    call(context, "api/idle-exploration", {"sequence": ""})
    call(context, "api/party/idle-exploration/edit", {"party_no": 1, "characters": [{"position_no": 1, "t_character_id": 1, "t_weapon_id": 0}]})
    call(context, "api/party/idle-exploration/assign", {"party_no": 1, "assign_to": 101})


def advance(monkeypatch, seconds):
    monkeypatch.setattr("test_gameplay.NOW", NOW + timedelta(seconds=seconds))


def test_idle_receive_uses_server_time_and_preserves_partial_intervals(setup_gameplay, monkeypatch):
    c = setup_gameplay
    initialize(c)
    advance(monkeypatch, 899)
    assert call(c, "api/idle-exploration/receive")["grant_gold"] == 0
    advance(monkeypatch, 2100)
    result = call(c, "api/idle-exploration/receive")
    assert result["grant_gold"] == 166 * 2 and result["grant_character_exp"] == 208 * 2
    assert call(c, "api/idle-exploration/receive")["grant_gold"] == 0
    advance(monkeypatch, 2700)
    assert call(c, "api/idle-exploration/receive")["grant_gold"] == 166
    assert records(c)["t_idle_exploration_statuses"]["up"][0]["stacked_treasure"] == 0


def test_unassign_and_reassign_preserve_remainder_without_idle_time_rewards(setup_gameplay, monkeypatch):
    c = setup_gameplay
    initialize(c)
    advance(monkeypatch, 600)
    call(c, "api/party/idle-exploration/assign", {"party_no": 1, "assign_to": 0})
    advance(monkeypatch, 900)
    call(c, "api/party/idle-exploration/assign", {"party_no": 1, "assign_to": 101})
    assert call(c, "api/idle-exploration/receive")["grant_gold"] == 0
    advance(monkeypatch, 1200)
    assert call(c, "api/idle-exploration/receive")["grant_gold"] == 166


def test_offline_cap_and_receiving_resume_exploration(setup_gameplay, monkeypatch):
    c = setup_gameplay
    initialize(c)
    advance(monkeypatch, 86400)
    result = call(c, "api/idle-exploration/receive")
    assert result["grant_gold"] == 166 * 48
    assert call(c, "api/idle-exploration/receive")["grant_gold"] == 0
    advance(monkeypatch, 87300)
    assert call(c, "api/idle-exploration/receive")["grant_gold"] == 166


def test_boost_and_instant_items_charge_correctly_and_fail_atomically(setup_gameplay):
    c = setup_gameplay
    initialize(c)
    seed(c, items={60001: 2, 61001: 2, 62001: 2, 63001: 2})
    headers = {**c.headers, "x-request-id": "idle-boost"}
    body = {"boosts": [{"party_no": 1, "use_items": [{"m_item_id": 60001, "amount": 1}]}]}
    result = call(c, "api/idle-exploration/boost", body, headers=headers)
    assert result["grant_gold"] == 166 * 4 and result["grant_character_exp"] == 208 * 4
    assert call(c, "api/idle-exploration/boost", body, headers=headers) == result
    for identifier, kind, amount in ((61001, 50, 166 * 4), (62001, 110, 208 * 4), (63001, 30, 13 * 4)):
        reward = call(c, "api/idle-exploration/instant-item", {"m_item_id": identifier, "amount": 1})["receive_contents"]
        assert len(reward) == 1 and reward[0]["content_type"] == kind and reward[0]["amount"] == amount
    before = snapshot(c)
    call(c, "api/idle-exploration/boost", {"boosts": [{"party_no": 1, "use_items": [{"m_item_id": 60001, "amount": 1}, {"m_item_id": 61001, "amount": 1}]}]}, expected=409)
    assert snapshot(c) == before


def test_concurrent_receives_grant_pending_rewards_once(setup_gameplay, monkeypatch):
    c = setup_gameplay
    initialize(c)
    advance(monkeypatch, 900)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: call(c, "api/idle-exploration/receive"), range(4)))
    assert sum(r["grant_gold"] for r in results) == 166


def test_moving_character_settles_old_party_and_clears_assignment(setup_gameplay, monkeypatch):
    c = setup_gameplay
    from test_economy_features import update
    update(c, lambda state, progress: progress["features"]["tutorial"]["data"].update(mode="legacy"))
    (c.settings.data_dir / "gameplay-rules.json").write_text(json.dumps({"idle_party_slots": 2}), encoding="utf-8")
    initialize(c)
    advance(monkeypatch, 900)
    call(c, "api/party/idle-exploration/edit", {"party_no": 2, "characters": [{"position_no": 1, "t_character_id": 1, "t_weapon_id": 0}]})
    data = records(c)
    assert not data["t_idle_exploration_party_relations"]["up"]
    assert [(r["party_no"], r["t_character_id"]) for r in data["t_idle_exploration_party_characters"]["up"] if r["t_character_id"]] == [(2, 1)]
    assert call(c, "api/idle-exploration/receive")["grant_gold"] == 166


def test_locked_areas_empty_parties_and_bad_sequences_roll_back(setup_gameplay):
    c = setup_gameplay
    call(c, "api/idle-exploration", {"sequence": ""})
    before = snapshot(c)
    call(c, "api/party/idle-exploration/assign", {"party_no": 1, "assign_to": 101}, expected=409)
    call(c, "api/party/idle-exploration/assign", {"party_no": 1, "assign_to": 102}, expected=409)
    call(c, "api/idle-exploration", {"sequence": "1e10"}, expected=400)
    assert snapshot(c) == before
