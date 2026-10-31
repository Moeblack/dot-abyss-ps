import copy
import json

import pytest

from dotabyss_ps.newgame import NewGameError, validate_saved_state
from test_gameplay import call, gameplay_master, setup_gameplay, snapshot
from test_idle_features import advance, initialize
from test_permanent_features import records


def test_quest_overview_returns_idle_nether_record_and_preserves_it(setup_gameplay):
    context = setup_gameplay
    result = call(context, "api/quest/top")
    nether = result["records"]["t_nethers"]["up"][0]
    assert nether["status"] == 1
    assert nether["floor_level"] == nether["m_nether_map_floor_id"] == 0
    assert nether["m_nether_code_ids"] == []
    assert call(context, "api/quest/top")["records"].get("t_nethers") is None
    assert records(context)["t_nethers"]["up"] == [nether]


@pytest.mark.parametrize("field,value", [("m_nether_map_floor_id", -1), ("m_nether_code_ids", [0]),
                                         ("m_nether_code_ids", [1, 1]), ("m_nether_code_ids", [True]),
                                         ("status", 4)])
def test_nether_record_rejects_invalid_states(setup_gameplay, field, value):
    call(setup_gameplay, "api/quest/top")
    state = json.loads(snapshot(setup_gameplay)[0])
    state["records"]["t_nethers"]["up"][0][field] = value
    with pytest.raises(NewGameError):
        validate_saved_state(state)


def enable_initial_assignment(context):
    (context.settings.data_dir / "gameplay-rules.json").write_text(
        json.dumps({"idle_auto_start": 1}), encoding="utf-8")


def test_initial_assignment_is_present_before_native_page_builds_models(setup_gameplay):
    context = setup_gameplay
    enable_initial_assignment(context)
    result = call(context, "api/user")["records"]
    assert result["t_idle_exploration_party_relations"]["up"][0]["party_no"] == 1
    assert any(row["position_no"] == 1 and row["t_character_id"]
               for row in result["t_idle_exploration_party_characters"]["up"])
    changes = call(context, "api/idle-exploration", {"sequence": ""})["records"]
    assert "t_idle_exploration_party_characters" not in changes


def test_idle_initial_assignment_earns_once_and_respects_manual_stop(setup_gameplay, monkeypatch):
    context = setup_gameplay
    enable_initial_assignment(context)
    result = call(context, "api/idle-exploration", {"sequence": ""})
    assert result["records"]["t_idle_exploration_party_relations"]["up"][0]["party_no"] == 1
    assert call(context, "api/idle-exploration/receive")["grant_gold"] == 0
    advance(monkeypatch, 900)
    assert call(context, "api/idle-exploration/receive")["grant_gold"] == 166
    assert call(context, "api/idle-exploration/receive")["grant_gold"] == 0
    call(context, "api/party/idle-exploration/assign", {"party_no": 1, "assign_to": 0})
    call(context, "api/idle-exploration", {"sequence": ""})
    assert not records(context)["t_idle_exploration_party_relations"]["up"]


def test_idle_initial_assignment_preserves_existing_manual_party(setup_gameplay):
    context = setup_gameplay
    initialize(context)
    previous = copy.deepcopy(records(context)["t_idle_exploration_party_characters"])
    enable_initial_assignment(context)
    call(context, "api/idle-exploration", {"sequence": ""})
    assert records(context)["t_idle_exploration_party_characters"] == previous
