import json

from fastapi.testclient import TestClient

from dotabyss_ps.app import create_app
from test_gameplay import call, gameplay_master, setup_gameplay, snapshot


def saved_favorites(context):
    state = json.loads(snapshot(context)[0])
    return {row["id"] for row in state["records"]["t_characters"]["up"] if row["is_favorite"] == 1}


def test_favorite_list_replaces_the_full_selection_and_accepts_empty(setup_gameplay):
    context = setup_gameplay
    call(context, "api/user")
    call(context, "api/character/favorite/toggle", {"t_character_id": 1, "is_favorite": True})
    response = call(context, "api/character/favorites/reset", {"t_character_ids": [2]})
    assert saved_favorites(context) == {2}
    assert {row["id"]: row["is_favorite"] for row in response["records"]["t_characters"]["up"]} == {1: 0, 2: 1}
    call(context, "api/character/favorites/reset", {"t_character_ids": []})
    assert saved_favorites(context) == set()


def test_favorite_list_retries_and_restart_preserve_selection(setup_gameplay):
    context = setup_gameplay
    call(context, "api/user")
    body = {"t_character_ids": [1, 2]}
    headers = {**context.headers, "x-request-id": "favorite-complete-selection"}
    result = call(context, "api/character/favorites/reset", body, headers=headers)
    assert saved_favorites(context) == {1, 2}
    after = snapshot(context)
    assert call(context, "api/character/favorites/reset", body, headers=headers) == result
    assert snapshot(context) == after
    with TestClient(create_app(context.settings)) as restarted:
        response = restarted.post("/api/character/favorites/reset", json=body, headers=headers)
        assert response.status_code == 200
        assert response.json()["contents"] == result
        user = restarted.get("/api/user", headers=context.headers).json()["contents"]
        assert {row["id"] for row in user["records"]["t_characters"]["up"] if row["is_favorite"] == 1} == {1, 2}
    call(context, "api/character/favorite/toggle", {"t_character_id": 1, "is_favorite": False})
    assert saved_favorites(context) == {2}


def test_invalid_favorite_selection_does_not_change_existing_favorites(setup_gameplay):
    context = setup_gameplay
    call(context, "api/user")
    call(context, "api/character/favorite/toggle", {"t_character_id": 1, "is_favorite": True})
    for selection, status in (([2, 999], 409), ([2, 2], 400), ([2, "1"], 400), ([2, True], 400)):
        before = snapshot(context)
        call(context, "api/character/favorites/reset", {"t_character_ids": selection}, expected=status)
        assert snapshot(context) == before
