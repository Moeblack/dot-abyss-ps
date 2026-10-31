import json
from datetime import timedelta

from dotabyss_ps.gameplay import Gameplay
from dotabyss_ps.gameplay_catalog import GameplayCatalog
from dotabyss_ps.gameplay_home import visitors
from dotabyss_ps.gameplay_records import put
from dotabyss_ps.gameplay_rules import load_rules
from test_gameplay import NOW, call, gameplay_master, setup_gameplay, snapshot


def local_game(context):
    call(context, "api/user")
    state, progress = map(json.loads, snapshot(context))
    rules = load_rules()
    rules["home_enable_visitors"] = 1
    return Gameplay(state, progress, GameplayCatalog(context.tables, context.schema),
                    context.identity["user_id"], NOW, rules)


def test_disabling_visitors_removes_temporary_rows_and_preserves_permanent_ownership(setup_gameplay, monkeypatch):
    game = local_game(setup_gameplay)
    monkeypatch.setattr("dotabyss_ps.gameplay_content.secrets.randbelow", lambda n: 0)
    first = visitors(game)
    assert first[0]["m_visitor_id"] == 1
    put(game.state, "t_visitors", {"m_visitor_id": 6}, {"is_temporary": 0}, game.user_id)
    game.rules["home_enable_visitors"] = 0
    assert visitors(game) == []
    assert [(row["m_visitor_id"], row["is_temporary"]) for row in game.state["records"]["t_visitors"]["up"]] == [(6, 0)]
    game.rules["home_enable_visitors"] = 1
    assert visitors(game) == first


def test_midday_expiry_removes_visitor_without_rerolling(setup_gameplay, monkeypatch):
    game = local_game(setup_gameplay)
    monkeypatch.setattr("dotabyss_ps.gameplay_content.secrets.randbelow", lambda n: 0)
    visitors(game)
    game.catalog.tables["m_visitors"][1]["end_at"] = (NOW + timedelta(minutes=1)).isoformat()
    game.now = NOW + timedelta(minutes=2)
    assert visitors(game) == []
    assert game.state["records"]["t_visitors"]["up"] == []
    assert game.progress["features"]["home"]["data"]["visitor_ids"] == [1]


def test_home_deletion_delta_does_not_enter_persistent_snapshot(setup_gameplay, monkeypatch):
    game = local_game(setup_gameplay)
    monkeypatch.setattr("dotabyss_ps.gameplay_content.secrets.randbelow", lambda n: 0)
    visitors(game)
    identifier = game.state["records"]["t_visitors"]["up"][0]["id"]
    game.rules["home_enable_visitors"] = 0
    result = game.visit(lambda state: {"records": state["records"], "badges": {}}, home=True)
    assert result["records"]["t_visitors"]["down"] == [identifier]
    assert game.state["records"]["t_visitors"] == {"up": [], "down": []}


def test_zero_weight_pool_is_empty_and_daily_selection_is_stable(setup_gameplay, monkeypatch):
    game = local_game(setup_gameplay)
    monkeypatch.setattr("dotabyss_ps.gameplay_content.secrets.randbelow", lambda n: 0)
    first = visitors(game)
    assert visitors(game) == first
    for row in game.catalog.rows("m_home_visitor_lotteries"):
        row["weight"] = 0
    game.now = NOW + timedelta(days=1)
    assert visitors(game) == []
    assert game.state["records"]["t_visitors"]["up"] == []


def test_visitor_claims_survive_toggle_restart_and_reset_only_next_day(setup_gameplay, monkeypatch):
    context = setup_gameplay
    path = context.settings.data_dir / "gameplay-rules.json"
    path.write_text(json.dumps({"home_enable_visitors": 1}), encoding="utf-8")
    monkeypatch.setattr("dotabyss_ps.gameplay_content.secrets.randbelow", lambda n: 0)
    result = call(context, "api/home")
    greeting = next(row for row in result["daily_greetings"] if row["is_visitor"])
    body = {key: greeting[key] for key in ("m_home_daily_greeting_id", "m_home_visitor_daily_greeting_id")}
    call(context, "api/home/daily-greeting/receive-reward", body)
    after_claim = json.loads(snapshot(context)[0])["records"]["t_statuses"]["up"][0]
    visitor_ids = [row["id"] for row in result["records"]["t_visitors"]["up"]]
    path.write_text(json.dumps({"home_enable_visitors": 0}), encoding="utf-8")
    disabled = call(context, "api/home")
    assert disabled["daily_visitors"] == []
    assert disabled["records"]["t_visitors"]["down"] == visitor_ids
    assert json.loads(snapshot(context)[0])["records"]["t_visitors"]["down"] == []
    path.write_text(json.dumps({"home_enable_visitors": 1}), encoding="utf-8")
    assert any(row["is_visitor"] and row["is_received"] for row in call(context, "api/home")["daily_greetings"])
    call(context, "api/home/daily-greeting/receive-reward", body)
    assert json.loads(snapshot(context)[0])["records"]["t_statuses"]["up"][0] == after_claim
    monkeypatch.setattr("test_gameplay.NOW", NOW + timedelta(days=1))
    assert any(row["is_visitor"] and not row["is_received"] for row in call(context, "api/home")["daily_greetings"])
