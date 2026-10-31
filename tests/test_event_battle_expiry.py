import copy
from datetime import timedelta

import pytest

from dotabyss_ps.gameplay_battle_session import battle_state
from dotabyss_ps.gameplay_catalog import GameplayError
from dotabyss_ps.gameplay_events import event_data
from dotabyss_ps.gameplay_mining_stage import skipped_mining_result
from dotabyss_ps.gameplay_records import find, rows
from test_battle_rewards import reward_game, reward_master
from test_event_miner_mutant import call, event_game, join, miner_start, mutant_start, mutant_victory


MODES = [("miner", 100001, 100001101), ("disaster", 1050001, 101)]


def begin(game, mode, event_id, quest_id, transition):
    if transition == "expiry":
        game.rules["event_open_all"] = 0
        game.catalog.tables["m_events"] = dict(game.catalog.tables["m_events"])
        event = {**game.catalog.get("m_events", event_id),
                 "open_at": (game.now - timedelta(days=1)).isoformat(),
                 "end_at": (game.now + timedelta(seconds=1)).isoformat(),
                 "close_at": (game.now + timedelta(seconds=2)).isoformat()}
        game.catalog.tables["m_events"][event_id] = event
    join(game, event_id)
    (miner_start if mode == "miner" else mutant_start)(game, quest_id)
    if transition == "expiry":
        game.now += timedelta(seconds=5)
    else:
        game.rules["event_open_all"] = 0
        game.rules["event_open_ids"] = []


def ending_body(game, mode, ending):
    session = battle_state(game)["active"]
    if ending == "clear":
        return {"stage_results": skipped_mining_result(session["stage_detail"], 4)} if mode == "miner" else mutant_victory(game)
    if mode == "disaster" and ending == "close":
        return {"play_time": 1, "phase_no": session["context"]["phase_no"],
                "remain_hp": session["context"]["remain_hp"] - 1, "boss_damage": 1, "battle_detail": []}
    return {"play_time": 1}


@pytest.mark.parametrize("mode,event_id,quest_id", MODES)
@pytest.mark.parametrize("transition", ["expiry", "disable_reopening"])
@pytest.mark.parametrize("ending", ["clear", "close", "surrender"])
def test_admitted_event_battle_can_finish_after_window_closes(event_game, mode, event_id, quest_id, transition, ending):
    game = event_game
    begin(game, mode, event_id, quest_id, transition)
    supply = find(game.state, "t_event_timed_parameters", m_event_id=event_id, type=1)
    assert supply["amount"] == 2
    body = ending_body(game, mode, ending)
    call(game, f"event/{mode}/{ending}-quest", body)
    assert battle_state(game)["active"] is None and supply["amount"] == 2
    stable = copy.deepcopy([game.state, game.progress])
    call(game, f"event/{mode}/{ending}-quest", body)
    assert [game.state, game.progress] == stable
    field = "m_event_miner_quest_id" if mode == "miner" else "m_event_disaster_quest_id"
    for operation, request in (
        ("start-quest", {field: quest_id, "party_no": 1}),
        ("skip-quest", {field: quest_id, "skip_count": 1}),
        ("recovery", {"m_event_id": event_id, **({"recovery_count": 1} if mode == "miner" else {})}),
    ):
        with pytest.raises(GameplayError, match="outside its available period"):
            call(game, f"event/{mode}/{operation}", request)
    assert supply["amount"] == 2


@pytest.mark.parametrize("mode,event_id,quest_id", MODES)
@pytest.mark.parametrize("ending", ["clear", "close", "surrender"])
def test_expired_battle_still_rejects_a_changed_epoch(event_game, mode, event_id, quest_id, ending):
    game = event_game
    begin(game, mode, event_id, quest_id, "disable_reopening")
    body = ending_body(game, mode, ending)
    event_data(game, event_id)["epoch"] += 1
    with pytest.raises(GameplayError, match="another event period"):
        call(game, f"event/{mode}/{ending}-quest", body)
    assert battle_state(game)["active"] is not None


@pytest.mark.parametrize("invalid", ["participation", "event", "quest_version"])
def test_mutant_surrender_preserves_frozen_identity_checks(event_game, invalid):
    game = event_game
    begin(game, "disaster", 1050001, 101, "disable_reopening")
    if invalid == "participation":
        rows(game.state, "t_events").clear()
    elif invalid == "event":
        battle_state(game)["active"]["context"]["m_event_id"] = 1050003
    else:
        event_data(game, 1050001)["mutant"]["quests"]["101"]["version"] += 1
    with pytest.raises(GameplayError, match="participation|saved event|reset after"):
        call(game, "event/disaster/surrender-quest", {"play_time": 1})
    assert battle_state(game)["active"] is not None
