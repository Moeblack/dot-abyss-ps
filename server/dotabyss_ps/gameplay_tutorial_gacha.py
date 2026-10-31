from __future__ import annotations

import copy

from .gameplay_catalog import GameplayError, available
from .gameplay_delivery import deliver
from .gameplay_gacha import execute, gacha_record, gacha_state, tokens
from .gameplay_records import find
from .gameplay_routes import route
from .gameplay_statistics import record


def tutorial(game):
    data = gacha_state(game).setdefault("tutorial", {"confirmed": False, "pending": [], "result": None})
    if not isinstance(data, dict) or set(data) != {"confirmed", "pending", "result"} or type(data["confirmed"]) is not bool or not isinstance(data["pending"], list):
        raise GameplayError("Stored tutorial gacha state is invalid")
    if len(data["pending"]) > game.rules["gacha_max_draws"] or data["result"] is not None and not isinstance(data["result"], dict):
        raise GameplayError("Stored tutorial gacha result is invalid")
    definitions = [r for r in game.catalog.rows("m_gachas") if r["type"] == 2 and not r["payment_id"] and r["use_amount"] == 0
                   and available(game.catalog.get("m_gacha_groups", r["m_gacha_group_id"]), game.now, "begin_at", "end_at")]
    if not definitions:
        raise GameplayError("Tutorial gacha is not configured", 503, 5036)
    definition = min(definitions, key=lambda r: r["id"])
    owned = find(game.state, "t_gachas", m_gacha_id=definition["id"])
    if owned and owned["exec_count"] and not data["confirmed"]:
        data.update(confirmed=True, pending=[])
    return data, definition


def keep_list(game, body):
    data, _ = tutorial(game)
    return {"is_confirmed": int(data["confirmed"]), **(copy.deepcopy(data["result"]) if data["result"] else {
        "received_content_list": [], "gacha_token_list": tokens(game), "mileage_list": None, "dropped_data": None})}


def roll(game, body):
    data, definition = tutorial(game)
    if data["confirmed"]:
        raise GameplayError("Tutorial gacha has already been confirmed")
    preview = copy.copy(game)
    preview.state, preview.progress = copy.deepcopy(game.state), copy.deepcopy(game.progress)
    gacha_state(preview).pop("tutorial", None)
    result = execute(preview, {"m_gacha_id": definition["id"], "exec_count": 1, "use_gacha_ticket": 0})
    data["pending"] = [{key: r.get("origin_content", r)[key] for key in ("content_type", "content_id", "amount")} for r in result["received_content_list"]]
    data["result"] = result
    return copy.deepcopy(result)


def confirm(game, body):
    data, definition = tutorial(game)
    if data["confirmed"]:
        return {}
    if not data["pending"]:
        raise GameplayError("Roll the tutorial gacha before confirming it")
    received, mailed = deliver(game, data["pending"])
    gacha_record(game, definition).update(exec_count=1, step_count=1)
    record(game.progress, game.now, 250000, definition["lottery_num"])
    data.update(confirmed=True, pending=[], result={"received_content_list": received + mailed, "gacha_token_list": tokens(game), "mileage_list": None, "dropped_data": None})
    return {}


TUTORIAL_GACHA_ROUTES = {
    "api/gacha/tutorial/keep-list": route("GET", "", keep_list),
    "api/gacha/tutorial/exec": route("POST", "", roll),
    "api/gacha/tutorial/confirm": route("POST", "", confirm),
}
