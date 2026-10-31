import copy
from datetime import timedelta

import pytest

from dotabyss_ps.gameplay import Gameplay
from dotabyss_ps.gameplay_catalog import GameplayCatalog, GameplayError
from dotabyss_ps.gameplay_comeback import active_record, reconcile_comeback
from dotabyss_ps.gameplay_login import eligible as bonus_eligible
from dotabyss_ps.gameplay_login_state import bonus_entry
from dotabyss_ps.gameplay_records import find, rows
from dotabyss_ps.gameplay_rules import load_rules, validate_rules
from dotabyss_ps.gameplay_shop import listing, validate_purchase
from dotabyss_ps.newgame import build_initial_state
from test_gameplay import NOW, gameplay_master


@pytest.fixture
def game(gameplay_master):
    schema, tables = gameplay_master
    state = build_initial_state(tables, schema, 7, "Comeback check", "2026-09-01 00:00:00", start_tutorial=False)
    game = Gameplay(state, {}, GameplayCatalog(tables, schema), 7, NOW, load_rules())
    game.prepare()
    return game


def history(game, days):
    game.progress["login_days"] = [(game.now.date() - timedelta(days=days)).isoformat(), game.now.date().isoformat()]


def return_lineups(game):
    return {row["m_payment_lineup_id"] for row in listing(game, {})["categories"]["commons"]} & {130001, 130002, 130003}


def test_new_profile_cannot_list_buy_or_claim_returning_player_content(game):
    assert not return_lineups(game)
    assert not rows(game.state, "t_come_backs")
    for identifier in (130001, 130002, 130003):
        with pytest.raises(GameplayError, match="comeback"):
            validate_purchase(game, identifier)
    with pytest.raises(GameplayError, match="comeback"):
        game.execute("api/special-mission/top", "POST", {"m_special_mission_id": 103})
    assert find(game.state, "t_special_missions", m_special_mission_id=103) is None


@pytest.mark.parametrize("days,accepted", [(29, False), (30, True), (31, True)])
def test_return_qualification_includes_the_configured_boundary(game, days, accepted):
    history(game, days)
    game.prepare()
    assert bool(active_record(game, 1)) is accepted
    assert bool(return_lineups(game)) is accepted
    if accepted:
        assert validate_purchase(game, 130001)[0]["id"] == 130001
        response = game.execute("api/special-mission/top", "POST", {"m_special_mission_id": 103})
        assert response["missions"]["special"]
        special = find(game.state, "t_special_missions", m_special_mission_id=103)
        record = active_record(game, 1)
        assert special["end_at"].startswith(record["end_at"][:10])


def test_qualification_does_not_keep_extending_or_reset_existing_claims(game):
    history(game, 30)
    game.prepare()
    before = copy.deepcopy(rows(game.state, "t_come_backs"))
    game.progress["mission_claims"]["once:123"] = []
    reconcile_comeback(game)
    assert rows(game.state, "t_come_backs") == before
    assert "once:123" in game.progress["mission_claims"]
    game.now += timedelta(days=1)
    game.prepare()
    assert rows(game.state, "t_come_backs") == before


def test_expired_return_qualification_closes_access_without_revoking_paid_login(game):
    history(game, 30)
    game.prepare()
    record = active_record(game, 1)
    record["end_at"] = game.now.strftime("%Y-%m-%d %H:%M:%S")
    history(game, 1)
    assert not return_lineups(game)
    with pytest.raises(GameplayError, match="comeback"):
        validate_purchase(game, 130001)
    with pytest.raises(GameplayError, match="comeback"):
        game._missions().special(103)
    entry = bonus_entry(game.progress, 20001)
    entry.update(paid_at=game.now.isoformat(), limit_at=(game.now + timedelta(days=5)).isoformat())
    assert bonus_eligible(game, game.catalog.get("m_login_bonuses", 20001), entry)


def test_disabled_local_qualification_and_invalid_rules(game):
    history(game, 300)
    game.rules["comeback_inactive_days"] = 0
    game.prepare()
    assert not rows(game.state, "t_come_backs")
    for value in (-1, True, 3651):
        with pytest.raises(GameplayError):
            validate_rules({"comeback_inactive_days": value})


def test_clock_rollback_does_not_qualify_as_inactivity(game):
    history(game, 40)
    game.progress["login_days"].append((game.now.date() + timedelta(days=1)).isoformat())
    reconcile_comeback(game)
    assert not rows(game.state, "t_come_backs")
