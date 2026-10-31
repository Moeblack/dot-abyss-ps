from __future__ import annotations

import copy

from .gameplay_catalog import GameplayError, number
from .gameplay_economy import text_argument
from .gameplay_records import find, put, rows
from .gameplay_routes import route


def profile_top(game, body):
    return game.community.counts() if game.community is not None else {"follow_count": 0, "follower_count": 0}


def profile_name(game, body):
    sync = number(body["is_sync_dmm"], 0, 1)
    supplied = body["name"]
    if sync == 1 and (supplied is None or isinstance(supplied, str) and not supplied.strip()):
        return {}
    name = text_argument(body["name"], game.rules["max_profile_name_length"])
    rows(game.state, "t_statuses")[0]["name"] = name
    return {}


def profile_comment(game, body):
    rows(game.state, "t_profiles")[0]["comment"] = text_argument(body["comment"], game.rules["max_profile_comment_length"], empty=True, multiline=True)
    return {}


def profile_degree(game, body):
    identifier = number(body["m_degree_id"])
    if find(game.state, "t_degrees", m_degree_id=identifier) is None:
        raise GameplayError("Profile degree is not owned")
    rows(game.state, "t_profiles")[0]["m_degree_id"] = identifier
    return {}


def profile_character(game, body):
    character_id, skin_id = number(body["m_character_id"]), number(body["m_character_skin_id"])
    character = find(game.state, "t_characters", m_character_id=character_id)
    skin = game.catalog.get("m_character_skins", skin_id)
    if character is None or skin["m_character_id"] != character_id or find(game.state, "t_character_skins", m_character_skin_id=skin_id) is None:
        raise GameplayError("Profile character or skin is not owned")
    rows(game.state, "t_profiles")[0]["favorite_m_character_skin_id"] = skin_id
    rows(game.state, "t_statuses")[0]["favorite_t_character_id"] = character["id"]
    return {}


def user_config(game, body):
    kind, value = number(body["type"], 1, 1), number(body["value"], 0, 1)
    record = put(game.state, "t_configs", {"type": kind}, {"value": value}, game.user_id)
    return {"t_config": copy.deepcopy(record)}


def feature_tip(game, body):
    identifier = number(body["m_feature_tip_id"])
    definition = game.catalog.get("m_feature_tips", identifier)
    record = put(game.state, "t_feature_tips", {"m_feature_tip_id": identifier}, {"read_version": definition["version"]}, game.user_id)
    return {"m_feature_tip_id": identifier, "version": record["read_version"]}


PROFILE_ROUTES = {
    "api/profile/top": route("GET", "", profile_top),
    "api/profile/edit/name": route("POST", "name is_sync_dmm", profile_name),
    "api/profile/edit/comment": route("POST", "comment", profile_comment),
    "api/profile/edit/degree": route("POST", "m_degree_id", profile_degree),
    "api/profile/edit/favorite-character": route("POST", "m_character_id m_character_skin_id", profile_character),
    "api/user/config/update": route("POST", "type value", user_config),
    "api/feature-tip/read": route("POST", "m_feature_tip_id", feature_tip),
}
