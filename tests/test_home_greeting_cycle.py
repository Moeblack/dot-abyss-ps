import copy
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from dotabyss_ps.app import create_app
from dotabyss_ps.gameplay import Gameplay
from dotabyss_ps.gameplay_catalog import GameplayCatalog, GameplayError, JST
from dotabyss_ps.gameplay_content import create_character
from dotabyss_ps.gameplay_home import greetings, home_data, receive as receive_in_memory, validate_cycle
from test_admin_console import endpoint, get, headers
from test_economy_features import update
from test_gameplay import NOW, call, gameplay_master, setup_gameplay, snapshot
from test_newgame import make_state


@pytest.fixture
def context(setup_gameplay):
    context = setup_gameplay
    call(context, "api/user")

    def ordinary(state, progress):
        state["records"]["t_prologue_tutorials"]["up"][0]["status"] = 99
        progress["features"]["tutorial"]["data"]["mode"] = "legacy"
        for row in state["records"]["t_characters"]["up"]:
            row["intimacy_point"] = 0

    update(context, ordinary)
    return context


def catalog(context):
    return GameplayCatalog(context.tables, context.schema)


def home(context):
    return [row for row in call(context, "api/home")["daily_greetings"] if not row["is_visitor"]]


def positions(entries):
    return [(row["m_home_daily_greeting_id"], row["pos_x"], row["pos_z"]) for row in entries]


def selected_character(context, entries, identifier=1200002):
    data = catalog(context)
    return next(row for row in entries if data.get("m_home_daily_greetings", row["m_home_daily_greeting_id"])["m_character_id"] == identifier)


def request(entry):
    return {key: entry[key] for key in ("m_home_daily_greeting_id", "m_home_visitor_daily_greeting_id")}


def receive(context, entry, operation_id=None):
    headers = context.headers if operation_id is None else {**context.headers, "x-request-id": operation_id}
    return call(context, "api/home/daily-greeting/receive-reward", request(entry), headers=headers)


def inventory(context):
    records = json.loads(snapshot(context)[0])["records"]
    return records["t_items"], records["t_statuses"]


def set_intimacy(context, level):
    point = next(row["total_point"] for row in catalog(context).rows("m_character_intimacy_levels") if row["level"] == level)
    update(context, lambda state, _: next(row for row in state["records"]["t_characters"]["up"]
                                        if row["m_character_id"] == 1200002).update(intimacy_point=point))


def test_claim_and_positions_survive_restarting_application(context):
    entries = home(context)
    assert entries and all(row["is_received"] == 0 for row in entries)
    entry = entries[0]
    assert receive(context, entry)["rewards"]
    earned = inventory(context)
    with TestClient(create_app(context.settings)) as restarted:
        response = restarted.post("/api/home", headers=context.headers, json={})
        assert response.status_code == 200, response.text
        restored = [row for row in response.json()["contents"]["daily_greetings"] if not row["is_visitor"]]
        assert positions(restored) == positions(entries)
        assert next(row for row in restored if row["m_home_daily_greeting_id"] == entry["m_home_daily_greeting_id"])["is_received"] == 1
        response = restarted.post("/api/home/daily-greeting/receive-reward", headers=context.headers, json=request(entry))
        assert response.status_code == 200, response.text
        assert response.json()["contents"]["rewards"] == []
    assert inventory(context) == earned


def test_fresh_duplicate_returns_empty_but_same_request_id_replays_result(context):
    entry = home(context)[0]
    first = receive(context, entry, "greeting-first")
    assert first["rewards"]
    earned = inventory(context)
    assert receive(context, entry, "greeting-another")["rewards"] == []
    assert receive(context, entry, "greeting-first")["rewards"] == first["rewards"]
    assert inventory(context) == earned


def test_concurrent_claims_only_award_once(context):
    entry = home(context)[0]
    before = inventory(context)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda index: receive(context, entry, f"greeting-parallel-{index}"), range(2)))
    assert sum(bool(result["rewards"]) for result in results) == 1
    assert inventory(context) != before
    earned = inventory(context)
    assert receive(context, entry)["rewards"] == []
    assert inventory(context) == earned


def test_claimed_batch_does_not_reset_at_midnight_but_resets_at_five_jst(context, monkeypatch):
    monkeypatch.setattr("test_gameplay.NOW", datetime(2026, 10, 31, 23, 59, tzinfo=JST))
    initial = home(context)
    for entry in initial:
        receive(context, entry)
    for moment in (datetime(2026, 11, 1, 0, 0, tzinfo=JST), datetime(2026, 11, 1, 4, 59, 59, tzinfo=JST)):
        monkeypatch.setattr("test_gameplay.NOW", moment)
        current = home(context)
        assert positions(current) == positions(initial)
        assert all(row["is_received"] == 1 for row in current)
    monkeypatch.setattr("test_gameplay.NOW", datetime(2026, 11, 1, 5, 0, tzinfo=JST))
    assert all(row["is_received"] == 0 for row in home(context))


def test_batch_uses_distinct_positions_and_does_not_refill_after_claim(context):
    initial = home(context)
    assert len(initial) == 3
    assert len({(row["pos_x"], row["pos_z"]) for row in initial}) == len(initial)
    data = catalog(context)
    assert len({data.get("m_home_daily_greetings", row["m_home_daily_greeting_id"])["m_character_id"] for row in initial}) == len(initial)
    for entry in initial:
        receive(context, entry)
        assert positions(home(context)) == positions(initial)
    assert all(row["is_received"] == 1 for row in home(context))


def test_random_selection_and_position_are_drawn_once_per_cycle(context, monkeypatch):
    (context.settings.data_dir / "gameplay-rules.json").write_text(json.dumps({"home_greeting_limit": 1}), encoding="utf-8")
    draws, reverse = [], [False]

    def choose(bound):
        draws.append(bound)
        return bound - 1 if reverse[0] else 0

    monkeypatch.setattr("dotabyss_ps.gameplay_home.secrets.randbelow", choose)
    initial = home(context)
    assert len(initial) == 1 and draws
    count = len(draws)
    reverse[0] = True
    assert positions(home(context)) == positions(initial)
    assert len(draws) == count
    monkeypatch.setattr("test_gameplay.NOW", NOW + timedelta(days=1))
    refreshed = home(context)
    assert len(draws) > count
    assert refreshed[0]["m_home_daily_greeting_id"] != initial[0]["m_home_daily_greeting_id"]


def test_new_character_and_intimacy_do_not_replace_current_batch(context, monkeypatch):
    initial = home(context)
    entry = selected_character(context, initial)
    receive(context, entry)
    set_intimacy(context, 5)
    data = catalog(context)
    existing = {row["m_character_id"] for row in json.loads(snapshot(context)[0])["records"]["t_characters"]["up"]}
    identifier = next(row["id"] for row in data.rows("m_characters") if row["id"] not in existing)
    update(context, lambda state, progress: create_character(state, identifier, data, context.identity["user_id"], NOW, progress))
    current = home(context)
    assert positions(current) == positions(initial)
    assert selected_character(context, current)["is_received"] == 1
    assert receive(context, entry)["rewards"] == []
    monkeypatch.setattr("test_gameplay.NOW", NOW + timedelta(days=1))
    next_story = selected_character(context, home(context))
    assert next_story["m_home_daily_greeting_id"] == 3
    assert next_story["is_received"] == 0


def test_unread_story_priority_and_completion_are_permanent(context, monkeypatch):
    set_intimacy(context, 15)
    completed = []
    for day, expected_id in enumerate((3, 4, 5)):
        monkeypatch.setattr("test_gameplay.NOW", NOW + timedelta(days=day))
        entries = home(context)
        entry = selected_character(context, entries)
        assert entry["m_home_daily_greeting_id"] == expected_id
        assert entry["is_received"] == 0
        assert receive(context, entry)["rewards"]
        completed.append(catalog(context).get("m_home_daily_greetings", expected_id)["m_novel_home_id"])
        assert positions(home(context)) == positions(entries)
        assert selected_character(context, home(context))["is_received"] == 1
    monkeypatch.setattr("test_gameplay.NOW", NOW + timedelta(days=3))
    entry = selected_character(context, home(context))
    assert catalog(context).get("m_home_daily_greetings", entry["m_home_daily_greeting_id"])["m_novel_home_id"] == 0
    owned = {row["m_novel_home_id"] for row in json.loads(snapshot(context)[0])["records"]["t_novel_homes"]["up"]}
    assert set(completed) <= owned


@pytest.mark.parametrize("legacy_day", ["2026-10-31", "2026-11-01"])
def test_legacy_midnight_receipts_are_respected_in_current_business_cycle(context, monkeypatch, legacy_day):
    monkeypatch.setattr("test_gameplay.NOW", datetime(2026, 11, 1, 0, 30, tzinfo=JST))
    data = catalog(context)
    definition = data.get("m_home_daily_greetings", 149)
    rewards = [{key: row[key] for key in ("content_type", "content_id", "amount")}
               for row in data.rows("m_home_greeting_rewards") if row["group_id"] == definition["m_home_greeting_reward_group_id"]]

    def legacy(state, progress):
        progress["greeting_claims"][f"{legacy_day}:149"] = rewards
        progress["features"]["home"] = {"version": 1, "data": {
            "visitor_day": "", "visitor_ids": [], "visitor_claims": {}}}

    update(context, legacy)
    before = inventory(context)
    entry = selected_character(context, home(context), 1200003)
    assert entry["is_received"] == 1
    assert receive(context, entry)["rewards"] == []
    assert inventory(context) == before


def test_legacy_unclaimed_story_remains_claimable_from_an_already_open_home(context):
    set_intimacy(context, 5)
    data = catalog(context)
    group = data.get("m_home_daily_greetings", 149)["m_home_greeting_reward_group_id"]
    rewards = [{key: row[key] for key in ("content_type", "content_id", "amount")}
               for row in data.rows("m_home_greeting_rewards") if row["group_id"] == group]

    def legacy(state, progress):
        progress["greeting_claims"][f"{NOW.date().isoformat()}:149"] = rewards
        progress["features"]["home"] = {"version": 1, "data": {
            "visitor_day": "", "visitor_ids": [], "visitor_claims": {}}}

    update(context, legacy)
    pending = {"m_home_daily_greeting_id": 4, "m_home_visitor_daily_greeting_id": 0}
    assert receive(context, pending)["rewards"]
    entry = selected_character(context, home(context))
    assert entry["m_home_daily_greeting_id"] == 4 and entry["is_received"] == 1
    assert receive(context, pending)["rewards"] == []


@pytest.mark.parametrize("axis,value", [("pos_x", 0.5), ("pos_z", -0.5), ("pos_x", True), ("pos_z", False)])
def test_cycle_coordinates_require_int32_in_validation_and_import(context, axis, value):
    home(context)
    source = get(context)
    proposed = copy.deepcopy(source)
    cycle = proposed["progress"]["features"]["home"]["data"]["greeting_cycle"]
    cycle["entries"][0][axis] = value
    game = Gameplay(proposed["state"], proposed["progress"], catalog(context), context.identity["user_id"], NOW)
    with pytest.raises(GameplayError):
        validate_cycle(game, cycle)
    before = snapshot(context)
    response = context.client.post(endpoint(context) + "/preview", headers=headers(context), json={
        "operation": "save.import", "args": {"snapshot": proposed}, "expected_version": source["version"]})
    assert response.status_code == 400, response.text
    assert snapshot(context) == before


@pytest.fixture
def cycle_game(gameplay_master):
    schema, tables = gameplay_master
    game = Gameplay(make_state(gameplay_master), {}, GameplayCatalog(tables, schema), 42, NOW)
    game.prepare()
    for row in game.state["records"]["t_characters"]["up"]:
        row["intimacy_point"] = 0
    game.rules["home_enable_visitors"] = 1
    return game


def claim_batch(game):
    entries = greetings(game)
    assert any(row["is_visitor"] for row in entries)
    assert any(not row["is_visitor"] for row in entries)
    for row in entries:
        assert receive_in_memory(game, request(row))["rewards"]
    return greetings(game)


def test_changing_reset_hour_does_not_reroll_visitors_or_reopen_claims(cycle_game):
    game = cycle_game
    game.now = datetime(2026, 11, 1, 5, 30, tzinfo=JST)
    claimed = claim_batch(game)
    assert all(row["is_received"] == 1 for row in claimed)
    game.rules["home_greeting_reset_hour"] = 6
    assert greetings(game) == claimed
    earned = copy.deepcopy(game.state)
    for row in claimed:
        assert receive_in_memory(game, request(row))["rewards"] == []
    assert game.state == earned


def test_later_reset_hour_preserves_same_day_until_new_deadline(cycle_game):
    game = cycle_game
    claimed = claim_batch(game)
    game.now = datetime(2026, 11, 1, 4, 30, tzinfo=JST)
    game.rules["home_greeting_reset_hour"] = 6
    assert greetings(game) == claimed
    for moment in (datetime(2026, 11, 1, 5, 0, tzinfo=JST), datetime(2026, 11, 1, 5, 59, 59, tzinfo=JST)):
        game.now = moment
        assert greetings(game) == claimed
    game.now = datetime(2026, 11, 1, 6, 0, tzinfo=JST)
    refreshed = greetings(game)
    assert len(refreshed) == len(claimed)
    assert all(row["is_received"] == 0 for row in refreshed)


def test_empty_cycle_preserves_deadline_and_can_fill_first_batch(cycle_game):
    game = cycle_game
    game.rules["home_enable_visitors"] = 0
    characters = game.state["records"]["t_characters"]["up"]
    first_character = copy.deepcopy(characters[0])
    characters.clear()
    assert greetings(game) == []
    deadline = home_data(game)["greeting_cycle"]["ends_at"]
    game.now += timedelta(hours=1)
    game.rules["home_greeting_reset_hour"] = 6
    assert greetings(game) == []
    assert home_data(game)["greeting_cycle"]["ends_at"] == deadline
    characters.append(first_character)
    first = greetings(game)
    assert len(first) == 1 and first[0]["is_received"] == 0
    assert home_data(game)["greeting_cycle"]["ends_at"] == deadline
    assert greetings(game) == first
