from __future__ import annotations

from .gameplay_catalog import GameplayError, number
from .gameplay_delivery import deliver
from .gameplay_event_rewards import event_state, randomizer, spend_currency
from .gameplay_routes import route


def board(game, event_id):
    event, data = event_state(game, event_id, "board", {4})
    definitions = [row for row in game.catalog.rows("m_event_treasure_boards") if row["m_event_id"] == event["id"]]
    if len(definitions) != 1:
        raise GameplayError("Treasure board settings are missing", 503, 5036)
    if not data:
        data.update(current_sheet=1, sheets={})
    if set(data) != {"current_sheet", "sheets"} or not isinstance(data["sheets"], dict):
        raise GameplayError("Stored treasure board is invalid")
    number(data["current_sheet"], 1)
    return event, definitions[0], data


def current_sheet(game, event, data):
    logical = data["current_sheet"]
    definitions = [row for row in game.catalog.rows("m_event_treasure_board_sheets") if row["m_event_id"] == event["id"]]
    if not definitions:
        raise GameplayError("Treasure board rewards are missing", 503, 5036)
    template = min(logical, max(row["sheet_no"] for row in definitions))
    rewards = [row for row in definitions if row["sheet_no"] == template]
    sheet = data["sheets"].get(str(logical))
    if sheet is None:
        contents = sorted(row["id"] for row in rewards)
        randomizer(game, event["id"], "board", logical).shuffle(contents)
        sheet = {"template": template, "contents": contents, "received": []}
        data["sheets"][str(logical)] = sheet
    if (set(sheet) != {"template", "contents", "received"} or sheet["template"] != template
            or sorted(sheet["contents"]) != sorted(row["id"] for row in rewards)
            or not isinstance(sheet["received"], list) or len(set(sheet["received"])) != len(sheet["received"])
            or any(type(index) is not int or not 0 <= index < len(rewards) for index in sheet["received"])):
        raise GameplayError("Stored treasure board sheet is invalid")
    return sheet


def sheet_view(game, event_id, logical, sheet):
    contents = [game.catalog.get("m_event_treasure_board_sheets", identifier) for identifier in sheet["contents"]]
    wins = [index for index, row in enumerate(contents) if row["is_win"]]
    return {"m_event_id": event_id, "sheet_no": logical, "m_event_treasure_board_sheet_sheet_no": sheet["template"],
            "win_space_number": wins[0] if wins else -1,
            "space_data": [{"space_number": index, "is_received": int(index in sheet["received"]),
                            "space_icon_asset_id": row["space_icon_asset_id"], "m_event_treasure_board_sheet_id": row["id"]}
                           for index, row in enumerate(contents)]}


def status(game, body):
    event, _, data = board(game, body["m_event_id"])
    current_sheet(game, event, data)
    return {"t_event_treasure_board_sheets": [sheet_view(game, event["id"], int(logical), sheet)
                                            for logical, sheet in sorted(data["sheets"].items(), key=lambda pair: int(pair[0]))]}


def open_spaces(game, body):
    event, definition, data = board(game, body["m_event_id"])
    sheet = current_sheet(game, event, data)
    requested = body["space_numbers"]
    if not isinstance(requested, list) or not 1 <= len(requested) <= len(sheet["contents"]):
        raise GameplayError("Invalid treasure board selection", 400, 4007)
    selected = [number(value, 0, len(sheet["contents"]) - 1) for value in requested]
    if len(set(selected)) != len(selected):
        raise GameplayError("Duplicate treasure board space", 400, 4007)
    pending = [index for index in selected if index not in sheet["received"]]
    if pending:
        spend_currency(game, event["id"], number(definition["space_open_cost_amount"]) * len(pending))
        rewards = [game.catalog.get("m_event_treasure_board_sheets", sheet["contents"][index]) for index in pending]
        deliver(game, [{key: row[key] for key in ("content_type", "content_id", "amount")} for row in rewards])
        sheet["received"].extend(pending)
        sheet["received"].sort()
    return sheet_view(game, event["id"], data["current_sheet"], sheet)


def clear_sheet(game, body):
    event, _, data = board(game, body["m_event_id"])
    sheet = current_sheet(game, event, data)
    won = any(game.catalog.get("m_event_treasure_board_sheets", sheet["contents"][index])["is_win"] for index in sheet["received"])
    if not won and len(sheet["received"]) != len(sheet["contents"]):
        raise GameplayError("Find the treasure or finish the board before advancing")
    data["current_sheet"] += 1
    sheet = current_sheet(game, event, data)
    return sheet_view(game, event["id"], data["current_sheet"], sheet)


EVENT_BOARD_ROUTES = {
    "api/event/treasure-board/status": route("POST", "m_event_id", status),
    "api/event/treasure-board/open": route("POST", "m_event_id space_numbers", open_spaces),
    "api/event/treasure-board/clear": route("POST", "m_event_id", clear_sheet),
}
