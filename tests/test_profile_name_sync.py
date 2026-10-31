from types import SimpleNamespace

import pytest

from dotabyss_ps.gameplay_catalog import GameplayError
from dotabyss_ps.gameplay_profile import profile_name
from test_gameplay import call, gameplay_master, setup_gameplay


@pytest.mark.parametrize("name", [None, "", "   "])
def test_empty_platform_name_preserves_local_name_but_manual_edit_rejects(name):
    status = {"name": "Local Player"}
    game = SimpleNamespace(state={"records": {"t_statuses": {"up": [status]}}}, rules={"max_profile_name_length": 32})
    assert profile_name(game, {"name": name, "is_sync_dmm": 1}) == {}
    assert status["name"] == "Local Player"
    with pytest.raises(GameplayError):
        profile_name(game, {"name": name, "is_sync_dmm": 0})
    assert status["name"] == "Local Player"


@pytest.mark.parametrize("body", [
    {"name": None, "is_sync_dmm": True},
    {"name": None, "is_sync_dmm": 2},
    {"name": 123, "is_sync_dmm": 1},
    {"name": "a" * 33, "is_sync_dmm": 1},
    {"name": "bad\x00name", "is_sync_dmm": 1},
])
def test_platform_sync_does_not_bypass_other_name_validation(body):
    status = {"name": "Local Player"}
    game = SimpleNamespace(state={"records": {"t_statuses": {"up": [status]}}}, rules={"max_profile_name_length": 32})
    with pytest.raises(GameplayError):
        profile_name(game, body)
    assert status["name"] == "Local Player"


def test_new_profile_accepts_platform_sync_without_advancing_tutorial(setup_gameplay):
    c = setup_gameplay
    original = call(c, "api/user")["records"]
    call(c, "api/profile/edit/name", {"name": None, "is_sync_dmm": 1})
    after = call(c, "api/user")["records"]
    assert after["t_statuses"]["up"][0]["name"] == original["t_statuses"]["up"][0]["name"]
    assert after["t_prologue_tutorials"] == original["t_prologue_tutorials"]
    call(c, "api/profile/edit/name", {"name": "Chosen Name", "is_sync_dmm": 0})
    call(c, "api/profile/edit/name", {"name": "", "is_sync_dmm": 1})
    assert call(c, "api/user")["records"]["t_statuses"]["up"][0]["name"] == "Chosen Name"
