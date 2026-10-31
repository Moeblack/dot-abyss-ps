import copy

import pytest

from dotabyss_ps.gameplay import Gameplay, prepare_progress
from dotabyss_ps.gameplay_catalog import GameplayCatalog, GameplayError
from dotabyss_ps.gameplay_content import create_character
from dotabyss_ps.gameplay_records import find, put, rows
from dotabyss_ps.gameplay_skin_bonds import reconcile_skin_bonds, validate_skin_bond_rewards
from test_gameplay import NOW, gameplay_master
from test_newgame import make_state


@pytest.fixture
def game(gameplay_master):
    schema, tables = gameplay_master
    progress = {}
    prepare_progress(progress, NOW)
    return Gameplay(make_state(gameplay_master), progress, GameplayCatalog(tables, schema), 42, NOW)


def receipts(game):
    return game.progress["features"]["character"]["data"]["skin_bond_rewards"]


def character_skin(game, character):
    owned = {row["m_character_skin_id"] for row in rows(game.state, "t_character_skins")}
    return next(row for row in game.catalog.rows("m_character_skins")
                if row["id"] in owned and row["m_character_id"] == character["m_character_id"])


def add_character(game, identifier):
    create_character(game.state, identifier, game.catalog, game.user_id, game.now, game.progress)
    return find(game.state, "t_characters", m_character_id=identifier)


def test_zero_bonus_is_recorded_without_changing_old_balances(game):
    before = copy.deepcopy(rows(game.state, "t_characters"))
    reconcile_skin_bonds(game)
    assert rows(game.state, "t_characters") == before
    assert receipts(game) and set(receipts(game).values()) == {0}
    character = before[0]
    skin = character_skin(game, character)
    skin["bonus_bond_point"] = 8
    reconcile_skin_bonds(game)
    assert rows(game.state, "t_characters") == before
    assert receipts(game)[f"character:{skin['id']}:{character['m_character_id']}"] == 0


def test_character_skin_grants_once_and_master_changes_do_not_reissue(game):
    character = rows(game.state, "t_characters")[0]
    before = character["bond_point"]
    skin = character_skin(game, character)
    skin["bonus_bond_point"] = 7
    reconcile_skin_bonds(game)
    assert character["bond_point"] == before + 7
    skin["bonus_bond_point"] = 19
    reconcile_skin_bonds(game)
    assert character["bond_point"] == before + 7
    assert receipts(game)[f"character:{skin['id']}:{character['m_character_id']}"] == 7


def test_owned_skin_waits_until_its_character_is_acquired(game):
    owned = {row["m_character_id"] for row in rows(game.state, "t_characters")}
    skin = next(row for row in game.catalog.rows("m_character_skins") if row["m_character_id"] not in owned)
    skin["bonus_bond_point"] = 9
    put(game.state, "t_character_skins", {"m_character_skin_id": skin["id"]}, {}, game.user_id)
    key = f"character:{skin['id']}:{skin['m_character_id']}"
    reconcile_skin_bonds(game)
    assert key not in receipts(game)
    character = add_character(game, skin["m_character_id"])
    reconcile_skin_bonds(game)
    assert character["bond_point"] == 9
    assert receipts(game)[key] == 9


def test_universal_weapon_skin_grants_to_matching_characters_once_each(game):
    character = rows(game.state, "t_characters")[0]
    master = game.catalog.get("m_characters", character["m_character_id"])
    weapon_type = master["weapon_type"]
    skin = next(row for row in game.catalog.rows("m_weapon_skins")
                if row["weapon_type"] == weapon_type and row["m_character_id"] == 0)
    skin["bonus_bond_point"] = 4
    put(game.state, "t_weapon_skins", {"m_weapon_skin_id": skin["id"]}, {}, game.user_id)
    before = {row["m_character_id"]: row["bond_point"] for row in rows(game.state, "t_characters")}
    reconcile_skin_bonds(game)
    for row in rows(game.state, "t_characters"):
        expected = 4 if game.catalog.get("m_characters", row["m_character_id"])["weapon_type"] == weapon_type else 0
        assert row["bond_point"] == before[row["m_character_id"]] + expected
    later = next(row for row in game.catalog.rows("m_characters")
                 if row["id"] not in before and row["weapon_type"] == weapon_type)
    acquired = add_character(game, later["id"])
    reconcile_skin_bonds(game)
    assert acquired["bond_point"] == 4
    assert receipts(game)[f"weapon:{skin['id']}:{later['id']}"] == 4
    stable = copy.deepcopy((game.state, game.progress))
    reconcile_skin_bonds(game)
    assert (game.state, game.progress) == stable


def test_dedicated_weapon_skin_only_grants_to_its_character(game):
    skin = next(row for row in game.catalog.rows("m_weapon_skins") if row["m_character_id"] > 0)
    target = find(game.state, "t_characters", m_character_id=skin["m_character_id"])
    if target is None:
        target = add_character(game, skin["m_character_id"])
    reconcile_skin_bonds(game)
    receipts(game).pop(f"weapon:{skin['id']}:{target['m_character_id']}", None)
    skin["bonus_bond_point"] = 6
    before = {row["m_character_id"]: row["bond_point"] for row in rows(game.state, "t_characters")}
    put(game.state, "t_weapon_skins", {"m_weapon_skin_id": skin["id"]}, {}, game.user_id)
    reconcile_skin_bonds(game)
    for row in rows(game.state, "t_characters"):
        assert row["bond_point"] == before[row["m_character_id"]] + (6 if row is target else 0)


def test_history_survives_lost_ownership_and_prevents_reacquisition_rewards(game):
    character = rows(game.state, "t_characters")[0]
    skin = character_skin(game, character)
    skin["bonus_bond_point"] = 5
    reconcile_skin_bonds(game)
    original = copy.deepcopy(character)
    rows(game.state, "t_characters").remove(character)
    validate_skin_bond_rewards(game)
    reconcile_skin_bonds(game)
    original["bond_point"] = 0
    rows(game.state, "t_characters").append(original)
    reconcile_skin_bonds(game)
    assert original["bond_point"] == 0
    assert receipts(game)[f"character:{skin['id']}:{original['m_character_id']}"] == 5


@pytest.mark.parametrize("bad", [-1, True, 1.5, "2"])
def test_receipts_reject_invalid_amounts_without_mutation(game, bad):
    reconcile_skin_bonds(game)
    receipts(game)[next(iter(receipts(game)))] = bad
    before = copy.deepcopy((game.state, game.progress))
    with pytest.raises(GameplayError):
        reconcile_skin_bonds(game)
    assert (game.state, game.progress) == before


@pytest.mark.parametrize("key", ["character:01:1", "other:1:1", "character:999999999:1", "character:1:999999999"])
def test_receipts_reject_malformed_or_unknown_references(game, key):
    reconcile_skin_bonds(game)
    receipts(game)[key] = 0
    with pytest.raises(GameplayError):
        validate_skin_bond_rewards(game)


def test_receipts_reject_skin_character_mismatch(game):
    character = rows(game.state, "t_characters")[0]
    skin = character_skin(game, character)
    other = next(row for row in game.catalog.rows("m_characters") if row["id"] != character["m_character_id"])
    reconcile_skin_bonds(game)
    receipts(game)[f"character:{skin['id']}:{other['id']}"] = 0
    with pytest.raises(GameplayError):
        validate_skin_bond_rewards(game)


def test_overflow_does_not_partly_change_balances_or_receipts(game):
    character = rows(game.state, "t_characters")[0]
    skin = character_skin(game, character)
    skin["bonus_bond_point"] = 2
    character["bond_point"] = 2 ** 31 - 2
    before = copy.deepcopy((game.state, game.progress))
    with pytest.raises(GameplayError):
        reconcile_skin_bonds(game)
    assert (game.state, game.progress) == before


def test_history_limit_does_not_write_an_unloadable_ledger(game, monkeypatch):
    monkeypatch.setattr("dotabyss_ps.gameplay_skin_bonds.MAX_REWARDS", 1)
    before = copy.deepcopy((game.state, game.progress))
    with pytest.raises(GameplayError):
        reconcile_skin_bonds(game)
    assert (game.state, game.progress) == before
