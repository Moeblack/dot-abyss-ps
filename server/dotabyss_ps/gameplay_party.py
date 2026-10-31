from __future__ import annotations

from .gameplay_catalog import GameplayError, number
from .gameplay_economy import text_argument
from .gameplay_records import find, rows
from .gameplay_routes import route


def party_number(game, value):
    return number(value, 1, rows(game.state, "t_statuses")[0]["max_party_count"])


def party(game, identifier, *, create=False):
    row = find(game.state, "t_exploration_parties", party_no=identifier)
    if row is None and create:
        game.party_edit({"party_no": identifier, "characters": [], "mana_gems": []})
        row = find(game.state, "t_exploration_parties", party_no=identifier)
    if row is None:
        raise GameplayError("Party does not exist")
    return row


def profile(game, body):
    identifier = party_number(game, body["party_no"])
    values = {"name": text_argument(body["name"], game.rules["max_party_name_length"])}
    values.update({key: number(body[key], 0, 2 ** 31 - 1) for key in ("icon_1", "icon_2", "icon_3")})
    record = party(game, identifier, create=True)
    if any(record[key] != value for key, value in values.items()):
        record.update(values, updated_at=game.now.isoformat())
    return {}


def copy_party(game, body):
    source_no, target_no = party_number(game, body["original_party_no"]), party_number(game, body["replica_party_no"])
    source = party(game, source_no)
    if source_no == target_no:
        return {}
    characters = [{key: row[key] for key in ("position_no", "t_character_id", "t_weapon_id", "t_armor_id", "t_accessory_id")}
                  for row in rows(game.state, "t_exploration_party_characters") if row["t_exploration_party_id"] == source["id"]]
    gems = [{key: row[key] for key in ("slot_no", "m_mana_gem_id")}
            for row in rows(game.state, "t_exploration_party_mana_gems") if row["t_exploration_party_id"] == source["id"]]
    return game.party_edit({"party_no": target_no, "characters": characters, "mana_gems": gems})


def slots(game, body):
    return {"capacity": {"character": 10, "mana_gem": 3}}


PARTY_ROUTES = {
    "api/party/exploration/edit/profile": route("POST", "party_no name icon_1 icon_2 icon_3", profile),
    "api/party/exploration/copy": route("POST", "original_party_no replica_party_no", copy_party),
    "api/party/slot": route("GET", "", slots),
}
