import copy
import json
from datetime import datetime

from dotabyss_ps.gameplay import Gameplay
from dotabyss_ps.gameplay_catalog import GameplayCatalog, JST
from dotabyss_ps.gameplay_event_bonus import bonus_record, trigger_bonus
from dotabyss_ps.gameplay_records import put
from test_economy_features import update
from test_gameplay import NOW, call, gameplay_master, setup_gameplay, snapshot
from test_training_encounter_modes import active, clock, prepare, victory


def join(context, event_id, amount=100000):
    prepare(context, chapters=True)
    call(context, "api/event/join", {"m_event_id": event_id})
    catalog = GameplayCatalog(context.tables, context.schema)
    currency = next(row["m_event_currency_id"] for row in catalog.rows("m_event_currency_relations") if row["m_event_id"] == event_id)
    update(context, lambda state, _: put(state, "t_event_currencies", {"m_event_currency_id": currency}, {"amount": amount}))
    return currency


def balance(context, currency):
    state = json.loads(snapshot(context)[0])
    return next(row["amount"] for row in state["records"]["t_event_currencies"]["up"] if row["m_event_currency_id"] == currency)


def event_progress(context, identifier):
    return json.loads(snapshot(context)[1])["features"]["events"]["data"]["instances"][str(identifier)]


def force_bonus(context, event_id=700001):
    catalog = GameplayCatalog(context.tables, context.schema)
    def apply(state, progress):
        game = Gameplay(state, progress, catalog, context.identity["user_id"], NOW)
        bonus_record(game, event_id)["probability"] = 1000
        assert trigger_bonus(game, chapter_id=1, difficulty=1)
    update(context, apply)


def test_treasure_board_spends_only_event_currency_and_preserves_replay(setup_gameplay):
    c = setup_gameplay
    currency = join(c, 700001)
    before_tokens = copy.deepcopy(json.loads(snapshot(c)[0])["records"]["t_gacha_tokens"])
    first = call(c, "api/event/treasure-board/status", {"m_event_id": 700001})["t_event_treasure_board_sheets"][0]
    assert len(first["space_data"]) == 25
    assert sorted(row["space_number"] for row in first["space_data"]) == list(range(25))
    win = first["win_space_number"]
    result = call(c, "api/event/treasure-board/open", {"m_event_id": 700001, "space_numbers": [win]})
    assert result["space_data"][win]["is_received"] == 1
    assert balance(c, currency) == 99950
    stable = snapshot(c)
    call(c, "api/event/treasure-board/open", {"m_event_id": 700001, "space_numbers": [win]})
    assert snapshot(c) == stable
    next_sheet = call(c, "api/event/treasure-board/clear", {"m_event_id": 700001})
    assert next_sheet["sheet_no"] == 2
    assert json.loads(snapshot(c)[0])["records"]["t_gacha_tokens"] == before_tokens


def test_treasure_board_validates_batch_and_loops_final_sheet(setup_gameplay):
    c = setup_gameplay
    currency = join(c, 700001)
    call(c, "api/event/treasure-board/status", {"m_event_id": 700001})
    before = snapshot(c)
    call(c, "api/event/treasure-board/open", {"m_event_id": 700001, "space_numbers": [0, 0]}, expected=400)
    call(c, "api/event/treasure-board/clear", {"m_event_id": 700001}, expected=409)
    assert snapshot(c) == before
    update(c, lambda _, progress: progress["features"]["events"]["data"]["instances"]["700001"]["board"].update(current_sheet=12))
    last = call(c, "api/event/treasure-board/status", {"m_event_id": 700001})["t_event_treasure_board_sheets"][-1]
    assert last["win_space_number"] == -1
    call(c, "api/event/treasure-board/open", {"m_event_id": 700001, "space_numbers": list(range(25))})
    next_sheet = call(c, "api/event/treasure-board/clear", {"m_event_id": 700001})
    assert next_sheet["sheet_no"] == 13 and next_sheet["m_event_treasure_board_sheet_sheet_no"] == 12
    assert balance(c, currency) == 98750


def test_box_inventory_delta_and_request_replay(setup_gameplay):
    c = setup_gameplay
    currency = join(c, 600003)
    initial = call(c, "api/event/box-gacha/status", {"m_event_id": 600003})
    assert initial["emission_data"] == []
    headers = {**c.headers, "x-request-id": "box-first-ten"}
    first = call(c, "api/event/box-gacha/exec", {"m_event_id": 600003, "exec_count": 10}, headers=headers)
    assert sum(row["emission_count"] for row in first["emission_data"]) == 10
    stable = snapshot(c)
    call(c, "api/event/box-gacha/exec", {"m_event_id": 600003, "exec_count": 10}, headers=headers)
    assert snapshot(c) == stable
    second = call(c, "api/event/box-gacha/exec", {"m_event_id": 600003, "exec_count": 7})
    assert sum(row["emission_count"] for row in second["emission_data"]) == 7
    total = call(c, "api/event/box-gacha/status", {"m_event_id": 600003})
    assert sum(row["emission_count"] for row in total["emission_data"]) == 17
    assert balance(c, currency) == 99830


def test_box_renew_pickup_and_loop_requires_empty_pool(setup_gameplay):
    c = setup_gameplay
    join(c, 600003)
    catalog = GameplayCatalog(c.tables, c.schema)
    initial = call(c, "api/event/box-gacha/status", {"m_event_id": 600003})
    call(c, "api/event/box-gacha/renew-box", {"m_event_id": 600003}, expected=409)
    pickup = next(row for row in catalog.rows("m_event_box_gacha_contents") if row["m_event_box_gacha_box_id"] == initial["m_event_box_gacha_box_id"] and row["sp_content"])
    update(c, lambda _, progress: progress["features"]["events"]["data"]["instances"]["600003"]["box"]["draws"].update({str(pickup["id"]): pickup["count_in_box"]}))
    call(c, "api/event/box-gacha/renew-box", {"m_event_id": 600003})
    assert event_progress(c, 600003)["box"]["current_box_no"] == 2
    update(c, lambda _, progress: progress["features"]["events"]["data"]["instances"]["600003"]["box"].update(current_box_no=12, draws={}))
    loop = call(c, "api/event/box-gacha/status", {"m_event_id": 600003})
    call(c, "api/event/box-gacha/renew-box", {"m_event_id": 600003}, expected=409)
    content = [row for row in catalog.rows("m_event_box_gacha_contents") if row["m_event_box_gacha_box_id"] == loop["m_event_box_gacha_box_id"]]
    update(c, lambda _, progress: progress["features"]["events"]["data"]["instances"]["600003"]["box"].update(draws={str(row["id"]): row["count_in_box"] for row in content}))
    stable = snapshot(c)
    call(c, "api/event/box-gacha/exec", {"m_event_id": 600003, "exec_count": 1}, expected=409)
    assert snapshot(c) == stable
    call(c, "api/event/box-gacha/renew-box", {"m_event_id": 600003})
    assert event_progress(c, 600003)["box"]["current_box_no"] == 13
    assert call(c, "api/event/box-gacha/status", {"m_event_id": 600003})["m_event_box_gacha_box_id"] == loop["m_event_box_gacha_box_id"]


def test_reward_exchange_window_ends_at_close_not_battle_end(setup_gameplay, monkeypatch):
    c = setup_gameplay
    (c.settings.data_dir / "gameplay-rules.json").write_text('{"event_open_all":0}', encoding="utf-8")
    clock(monkeypatch, datetime(2026, 8, 25, 14, tzinfo=JST))
    join(c, 700001)
    clock(monkeypatch, datetime(2026, 9, 1, 14, tzinfo=JST))
    call(c, "api/event/treasure-board/open", {"m_event_id": 700001, "space_numbers": [0]})
    clock(monkeypatch, datetime(2026, 9, 8, 14, tzinfo=JST))
    before = snapshot(c)
    call(c, "api/event/treasure-board/open", {"m_event_id": 700001, "space_numbers": [1]}, expected=409)
    assert snapshot(c) == before


def test_bonus_trigger_clear_skip_and_next_spawn_are_distinct(setup_gameplay):
    c = setup_gameplay
    join(c, 700001)
    force_bonus(c)
    call(c, "api/event/bonus-quest/skip-quest", {"m_event_id": 700001}, expected=409)
    call(c, "api/event/bonus-quest/start-quest", {"m_event_id": 700001, "party_no": 1})
    session = active(c)
    master = GameplayCatalog(c.tables, c.schema).get("m_event_bonus_quest_stages", session["quest_id"])
    body = victory(c)
    result = call(c, "api/event/bonus-quest/clear-quest", body)
    assert result["clear_rewards"]
    skip_record = json.loads(snapshot(c)[0])["records"]["t_event_bonus_quest_stages"]["up"][0]
    assert skip_record["stage_id"] == master["stage_id"] and skip_record["stage_id"] != master["id"]
    stable = snapshot(c)
    call(c, "api/event/bonus-quest/clear-quest", body)
    assert snapshot(c) == stable
    for count in (2, 3):
        force_bonus(c)
        call(c, "api/event/bonus-quest/skip-quest", {"m_event_id": 700001})
        assert event_progress(c, 700001)["mission_stats"]["totals"]["110005:0:0"] == count
        stable = snapshot(c)
        call(c, "api/event/bonus-quest/skip-quest", {"m_event_id": 700001})
        assert snapshot(c) == stable


def test_bonus_close_retains_pending_and_giveup_removes_without_rewards(setup_gameplay):
    c = setup_gameplay
    join(c, 700001)
    force_bonus(c)
    before = copy.deepcopy(json.loads(snapshot(c)[0])["records"]["t_items"])
    call(c, "api/event/bonus-quest/start-quest", {"m_event_id": 700001, "party_no": 1})
    call(c, "api/event/bonus-quest/giveup", {"m_event_id": 700001}, expected=409)
    call(c, "api/event/bonus-quest/close-quest", {"play_time": 10})
    call(c, "api/event/bonus-quest/start-quest", {"m_event_id": 700001, "party_no": 1})
    call(c, "api/event/bonus-quest/surrender-quest", {"play_time": 1})
    call(c, "api/event/bonus-quest/giveup", {"m_event_id": 700001})
    call(c, "api/event/bonus-quest/start-quest", {"m_event_id": 700001, "party_no": 1}, expected=409)
    assert json.loads(snapshot(c)[0])["records"]["t_items"] == before
