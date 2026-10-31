from __future__ import annotations

from .gameplay_catalog import GameplayError, number
from .gameplay_routes import route


def community(game):
    if game.community is None:
        raise GameplayError("Local community storage is not configured", 503, 5036)
    return game.community


def top(game, body):
    service = community(game)
    return {"user_list": service.recommendations(game.rules["friend_recommendation_limit"]), **service.lists()}


def search(game, body):
    service = community(game)
    return {**service.public_profile(number(body["search_user_id"])), **service.lists()}


def follow(game, body):
    community(game).follow(number(body["target_user_id"]), game.rules["friend_limit"])
    return {}


def unfollow(game, body):
    community(game).unfollow(number(body["target_user_id"]))
    return {}


def remove_follower(game, body):
    community(game).remove_follower(number(body["target_user_id"]))
    return {}


FRIEND_ROUTES = {
    "api/friend/top": route("GET", "", top),
    "api/friend/search/{0}": route("GET", "search_user_id", search, "search_user_id"),
    "api/friend/follow": route("POST", "target_user_id", follow),
    "api/friend/unfollow": route("POST", "target_user_id", unfollow),
    "api/friend/follower/remove": route("POST", "target_user_id", remove_follower),
}
