import copy
from types import SimpleNamespace

import pytest

from dotabyss_ps.gameplay_catalog import GameplayError
from dotabyss_ps.gameplay_repairs import repair_ability_responses


def fixture():
    row = {"id": 7, "user_id": 2, "t_character_id": 3, "m_character_ability_id": 130003302,
           "level": 1, "possible_max_level": 10}
    stale = {**row, "level": 0, "possible_max_level": 0}
    response = {"records": {"t_character_abilities": {"up": [stale], "down": []}},
                "t_character_ability": copy.deepcopy(stale), "received_items": [{"amount": 50}]}
    progress = {"operation_receipts": {"old": {"digest": "a" * 64, "response": response}},
                "features": {"shop": {"version": 1, "data": {"receipts": {"purchase": {
                    "response": copy.deepcopy(response), "result": copy.deepcopy(response)}}}}}}
    return SimpleNamespace(state={"records": {"t_character_abilities": {"up": [row], "down": []}}}, progress=progress)


def test_normalizes_ability_receipts_without_losing_purchase_or_request_identity():
    game = fixture()
    repair_ability_responses(game)
    operation = game.progress["operation_receipts"]["old"]
    assert operation["digest"] == "a" * 64
    receipt = game.progress["features"]["shop"]["data"]["receipts"]["purchase"]
    for response in (operation["response"], receipt["response"], receipt["result"]):
        assert response["received_items"] == [{"amount": 50}]
        assert response["t_character_ability"]["level"] == 1
        assert response["records"]["t_character_abilities"]["up"][0]["possible_max_level"] == 10
    before = copy.deepcopy(game.progress)
    repair_ability_responses(game)
    assert game.progress == before


def test_different_owned_ability_is_not_rewritten():
    game = fixture()
    game.progress["operation_receipts"]["old"]["response"]["t_character_ability"]["t_character_id"] = 4
    repair_ability_responses(game)
    assert game.progress["operation_receipts"]["old"]["response"]["t_character_ability"]["level"] == 0


@pytest.mark.parametrize("version", [2, True])
def test_invalid_repair_version_is_rejected(version):
    game = fixture()
    game.progress["features"]["character"] = {"version": 1, "data": {"ability_response_version": version}}
    with pytest.raises(GameplayError):
        repair_ability_responses(game)
