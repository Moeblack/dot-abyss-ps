from __future__ import annotations

import copy
from datetime import datetime
from typing import Any

from .protocol import decode_json
from .gameplay_records import OPTIONAL_ROW_FIELDS, validate_optional_records, identity_field

POLICY_VERSION = 5
SUPPORTED_CLIENT = {
    "package": "jp.co.fanzagames.dotabyss_x_a.mod",
    "version": "1.14.4",
    "metadata_sha256": "e144b637ceaabbcb385555d11213ba46b30216ff8df76d3d8fa8854d1582f130",
}
SUPPORTED_CLIENTS = (
    SUPPORTED_CLIENT,
    {
        "package": "com.exnoa.abyss",
        "version": "1.15.0",
        "metadata_sha256": "2bbdadf0952016d5e21a34e2699de54bc97eacb9c8f85b4650e93e80c3cb0e7c",
    },
    {
        "package": "dotabyss_x_cl",
        "version": "1.15.0",
        "metadata_sha256": "d38f026a769e522b136830785ad2789f834c47127be764f89ab294b4d91f056d",
    },
)
LOCAL_BUILDING_IDS = (100, 200, 300, 500, 1000)
EQUIPMENT_LIMIT = 100
MAX_PARTY_COUNT = 1
PROLOGUE_COMPLETED = 99
PROLOGUE_STATES = frozenset(range(16)) | {PROLOGUE_COMPLETED}
NULLABLE_STRINGS = {("t_idle_exploration_statuses", "exploring_start_at")}
TAVERN_CARD_GROUPS = {
    "selected_cards": "m_tavern_card_id", "use_cards": "m_tavern_card_id",
    "selected_character_cards": "m_character_id", "use_character_cards": "m_character_id",
}

TABLE_FIELDS = {
    "m_initial_contents": {"id": "long", "content_type": "int", "content_id": "long", "amount": "int", "options": "string"},
    "m_characters": {"id": "long", "weapon_type": "long"},
    "m_character_skins": {"id": "long", "m_character_id": "long", "type": "int", "is_default": "int"},
    "m_weapon_skins": {"id": "long", "weapon_type": "long", "m_character_id": "int", "is_default": "int", "is_paid": "int"},
    "m_character_levels": {"id": "long", "level": "int", "require_amount": "int"},
    "m_user_ranks": {"id": "long", "rank": "int", "total_exp": "int"},
    "m_mana_gems": {"id": "long"},
    "m_degrees": {"id": "long"},
    "m_plans": {"id": "long"},
    "m_plan_steps": {"id": "long", "m_plan_id": "long", "step": "int"},
    "m_buildings": {"id": "long"},
    "m_building_levels": {"id": "long", "m_building_id": "long", "level": "int", "exp": "int"},
}
REQUIRED_TABLES = tuple(TABLE_FIELDS)

RECORD_NAMES = """
t_statuses t_profiles t_characters t_character_abilities t_items t_limited_items
t_chapter_quests t_chapter_repeat_quests t_union_requests t_exploration_parties
t_preset_exploration_parties t_exploration_party_characters t_exploration_party_mana_gems
t_character_attachment_skins t_mana_gems t_timed_parameters t_stamina_recoveries
t_weapons t_weapon_enchantments t_armors t_armor_enchantments t_accessories
t_accessory_enchantments t_character_bond_parameters t_character_condition_resistances
t_novel_characters t_novel_character_skins t_buildings t_building_skills t_plans
t_disaster_statuses t_disaster_bosses t_disaster_slots t_equipment_mileages
t_appraisal_result_pools t_character_skins t_weapon_skins t_disaster_parties
t_disaster_party_defensive_units t_defensive_units t_idle_exploration_maps
t_idle_exploration_map_areas t_idle_exploration_parties t_idle_exploration_party_characters
t_idle_exploration_party_relations t_idle_exploration_statuses t_idle_exploration_requests
t_idle_exploration_encounters t_idle_exploration_encounter_skip_configs t_gachas
t_gacha_tokens t_gacha_mileages t_tavern_daily_cards t_tavern_shops t_novel_events
t_novel_others t_dictionary_non_player_characters t_dictionary_enemies t_dictionary_worlds
t_special_missions t_come_backs t_degrees t_events t_event_stage_effect_levels
t_event_timed_parameters t_event_currencies t_event_boss_tickets t_event_battle_stats
t_event_story_stages t_event_training_stages t_event_miner_quests t_event_disaster_quests
t_event_commission_quests t_event_hunt_quests t_event_speedrun_quests t_event_box_gachas
t_event_bonus_quests t_event_bonus_quest_stages t_coaching_quests t_recollection_quests
t_tryout_quests t_nethers t_nether_items t_nether_lock_items t_nether_codes t_level_synchros
t_feature_locks t_feature_tips t_prologue_tutorials t_feature_tutorials t_novel_homes
t_visitors t_payment_subscription_benefits t_vip_ranks t_configs t_payment_dmm_currencies
t_payment_googleplay_currencies t_payment_appstore_currencies t_payment_currency_debts
""".split()

ROW_FIELDS = {
    **OPTIONAL_ROW_FIELDS,
    "t_statuses": {
        "long": "id user_id favorite_t_character_id rental_t_character_id",
        "int": "gold soul disaster_coin player_exp user_rank character_exp is_staff free_virtual_money max_party_count friend_point",
        "string": "name", "container": "t_novel_homes",
    },
    "t_profiles": {"long": "id user_id favorite_m_character_skin_id m_degree_id", "string": "comment"},
    "t_characters": {
        "long": "id user_id m_character_id",
        "int": "level exp limit_break_count is_favorite is_new is_unread love_point bond_point limit_break_core_point battle_m_character_skin_id tavern_m_character_skin_id battle_m_weapon_skin_id intimacy_point",
        "string": "created_at",
    },
    "t_character_skins": {"long": "id user_id m_character_skin_id"},
    "t_weapon_skins": {"long": "id user_id m_weapon_skin_id"},
    "t_mana_gems": {"long": "id user_id m_mana_gem_id"},
    "t_degrees": {"long": "id user_id m_degree_id"},
    "t_plans": {"long": "id m_plan_id", "int": "step"},
    "t_buildings": {"int": "id level exp checked_level", "long": "user_id m_building_id", "string": "opened_at"},
    "t_prologue_tutorials": {"long": "id user_id", "int": "status"},
    "t_tavern_daily_cards": {
        "long": "id user_id", "int": "day_of_week day use_ticket work_seed worked_count selected_count score",
        "string": "selected_result", "tavern_cards": "selected_cards",
    },
    "t_idle_exploration_statuses": {
        "long": "id user_id",
        "int": "level point explorable_duration exploring_state has_rare_drop stacked_treasure",
        "string": "exploring_start_at",
    },
}


class NewGameError(ValueError):
    pass


def integer(value: Any, name: str, *, bits: int = 32, minimum: int = 0, unsigned: bool = False) -> int:
    if type(value) is not int or not minimum <= value < 2 ** (bits if unsigned else bits - 1):
        raise NewGameError(f"{name}: expected a {bits}-bit integer at least {minimum}")
    return value


def wire_timestamp(value: Any) -> None:
    try:
        if not isinstance(value, str) or datetime.strptime(value, "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d %H:%M:%S") != value:
            raise ValueError("Noncanonical timestamp")
    except ValueError as exc:
        raise NewGameError("Invalid wire timestamp") from exc


def dormant_tavern_values() -> dict:
    return {"day_of_week": 0, "day": 0, "use_ticket": 0, "work_seed": 0, "worked_count": 0,
            "selected_count": 0, "score": 0, "selected_result": "",
            "selected_cards": {name: [] for name in TAVERN_CARD_GROUPS}}


def dormant_exploration_values() -> dict:
    return {"level": 1, "point": 0, "explorable_duration": 43200, "exploring_state": 0,
            "exploring_start_at": None, "has_rare_drop": 0, "stacked_treasure": 0}


def validate_tavern_cards(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != set(TAVERN_CARD_GROUPS):
        raise NewGameError("Stored tavern card groups do not match the client contract")
    for name, reference in TAVERN_CARD_GROUPS.items():
        rows = value[name]
        if not isinstance(rows, list) or len(rows) > 10000:
            raise NewGameError("Stored tavern card group exceeds its row limit")
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"turn", reference}:
                raise NewGameError("Stored tavern card fields do not match the client contract")
            integer(row["turn"], "Tavern card turn", minimum=-(2 ** 31))
            integer(row[reference], "Tavern card reference", bits=64, minimum=1)


class MasterRows:
    def __init__(self, tables: dict, schema: dict):
        if not isinstance(schema, dict) or schema.get("client") not in SUPPORTED_CLIENTS:
            raise NewGameError("New-game policy requires a verified client package, version and metadata hash")
        if not isinstance(tables, dict) or not isinstance(schema.get("tables"), dict):
            raise NewGameError("Master tables and schema are required")
        self.tables: dict[str, dict[int, dict]] = {}
        for table, required in TABLE_FIELDS.items():
            columns, rows = schema["tables"].get(table), tables.get(table)
            if not isinstance(columns, list) or not isinstance(rows, list) or len(rows) > 10000:
                raise NewGameError(f"{table}: missing or oversized table")
            if any(not isinstance(column, dict) or not isinstance(column.get("name"), str) for column in columns):
                raise NewGameError(f"{table}: invalid column definition")
            definition = {column["name"]: column.get("type") for column in columns}
            if len(definition) != len(columns) or any(definition.get(name) != kind for name, kind in required.items()):
                raise NewGameError(f"{table}: required column contract changed")
            indexed = {}
            for raw in rows:
                if not isinstance(raw, list) or len(raw) != len(columns):
                    raise NewGameError(f"{table}: unexpected row width")
                row = dict(zip(definition, raw))
                for name, kind in required.items():
                    value = row[name]
                    if kind in ("int", "long"):
                        integer(value, f"{table}.{name}", bits=32 if kind == "int" else 64)
                    elif not isinstance(value, str):
                        raise NewGameError(f"{table}.{name}: expected a string")
                identifier = integer(row["id"], f"{table}.id", bits=64, minimum=1)
                if identifier in indexed:
                    raise NewGameError(f"{table}: duplicate master ID {identifier}")
                indexed[identifier] = row
            self.tables[table] = indexed

    def rows(self, table: str) -> list[dict]:
        return list(self.tables[table].values())

    def get(self, table: str, identifier: int) -> dict:
        try:
            return self.tables[table][identifier]
        except KeyError as exc:
            raise NewGameError(f"{table}: missing referenced ID {identifier}") from exc


def single(rows: list[dict], description: str) -> dict:
    if len(rows) != 1:
        raise NewGameError(f"{description}: expected one matching master row, found {len(rows)}")
    return rows[0]


def minimum_row(rows: list[dict], field: str, description: str) -> dict:
    if not rows or any(row[field] <= 0 for row in rows):
        raise NewGameError(f"{description}: missing positive {field}")
    lowest = min(row[field] for row in rows)
    return single([row for row in rows if row[field] == lowest], description)


def initial_options(seed: dict) -> dict:
    try:
        options = decode_json(seed["options"]) if seed["options"] else {}
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise NewGameError("Invalid initial-content options") from exc
    allowed = {"intimacy_point"} if seed["content_type"] == 40 else set()
    if not isinstance(options, dict) or set(options) - allowed:
        raise NewGameError("Unsupported initial-content options")
    if "intimacy_point" in options:
        integer(options["intimacy_point"], "intimacy_point")
    return options


def build_initial_state(master_rows: dict[str, list], schema: dict, user_id: int, profile_name: str, created_at: str,
                        *, start_tutorial: bool | None = None) -> dict:
    integer(user_id, "user_id", bits=64, minimum=1)
    if not isinstance(profile_name, str) or not 1 <= len(profile_name) <= 64:
        raise NewGameError("Profile name must contain 1 to 64 characters")
    try:
        profile_name.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise NewGameError("Profile name contains invalid Unicode") from exc
    wire_timestamp(created_at)
    masters = MasterRows(master_rows, schema)
    if start_tutorial is None:
        start_tutorial = schema["client"]["package"] == "dotabyss_x_cl"
    if type(start_tutorial) is not bool:
        raise NewGameError("Tutorial start policy must be a boolean")
    records = {name: {"up": [], "down": []} for name in RECORD_NAMES}
    unresolved = []
    seeded = set()

    def add(table: str, values: dict, *, owner: bool = True) -> dict:
        rows = records[table]["up"]
        row = {"id": len(rows) + 1, **({"user_id": user_id} if owner else {}), **values}
        rows.append(row)
        return row

    level = minimum_row(masters.rows("m_character_levels"), "level", "Initial character level")
    if level["require_amount"] != 0:
        raise NewGameError("The initial character level requires a cost")
    rank = minimum_row(masters.rows("m_user_ranks"), "rank", "Initial user rank")
    for seed in sorted(masters.rows("m_initial_contents"), key=lambda row: row["id"]):
        kind, content_id = seed["content_type"], seed["content_id"]
        if kind not in (40, 60, 130, 131, 190):
            raise NewGameError(f"Unsupported initial content type {kind}")
        if seed["amount"] != 1 or (kind, content_id) in seeded:
            raise NewGameError("Initial owned content must have amount 1 and a unique type/ID pair")
        seeded.add((kind, content_id))
        options = initial_options(seed)
        if kind == 130:
            unresolved.append({key: seed[key] for key in TABLE_FIELDS["m_initial_contents"]})
        elif kind == 40:
            character = masters.get("m_characters", content_id)
            skins = [row for row in masters.rows("m_character_skins") if row["m_character_id"] == content_id and row["is_default"] == 1]
            normal = single([row for row in skins if row["type"] == 1], f"Character {content_id} normal skin")
            tavern = single([row for row in skins if row["type"] == 2], f"Character {content_id} tavern skin")
            weapon = single([row for row in masters.rows("m_weapon_skins") if row["weapon_type"] == character["weapon_type"]
                             and row["m_character_id"] == 0 and row["is_default"] == 1 and row["is_paid"] == 0],
                            f"Character {content_id} default weapon skin")
            for skin in (normal, tavern, weapon):
                integer(skin["id"], "Character skin reference", minimum=1)
            add("t_characters", {
                "m_character_id": content_id, "level": level["level"], "exp": 0, "limit_break_count": 0,
                "is_favorite": 0, "is_new": 1, "is_unread": 1, "love_point": 0, "bond_point": 0,
                "limit_break_core_point": 0, "battle_m_character_skin_id": normal["id"],
                "tavern_m_character_skin_id": tavern["id"], "battle_m_weapon_skin_id": weapon["id"],
                "created_at": created_at, "intimacy_point": options.get("intimacy_point", 0),
            })
            for skin in (normal, tavern):
                add("t_character_skins", {"m_character_skin_id": skin["id"]})
            if not any(row["m_weapon_skin_id"] == weapon["id"] for row in records["t_weapon_skins"]["up"]):
                add("t_weapon_skins", {"m_weapon_skin_id": weapon["id"]})
        elif kind == 60:
            masters.get("m_mana_gems", content_id)
            add("t_mana_gems", {"m_mana_gem_id": content_id})
        elif kind == 190:
            masters.get("m_degrees", content_id)
            add("t_degrees", {"m_degree_id": content_id})
        elif kind == 131:
            masters.get("m_plans", content_id)
            steps = sorted(row["step"] for row in masters.rows("m_plan_steps") if row["m_plan_id"] == content_id)
            if not steps or steps != list(range(1, len(steps) + 1)):
                raise NewGameError(f"Plan {content_id}: steps must be consecutive from one")
            add("t_plans", {"m_plan_id": content_id, "step": 0}, owner=False)

    for identifier in LOCAL_BUILDING_IDS:
        masters.get("m_buildings", identifier)
        start = minimum_row([row for row in masters.rows("m_building_levels") if row["m_building_id"] == identifier], "level", f"Building {identifier}")
        add("t_buildings", {"m_building_id": identifier, "level": start["level"], "exp": start["exp"],
                            "opened_at": created_at, "checked_level": start["level"]})

    characters, degrees = records["t_characters"]["up"], records["t_degrees"]["up"]
    if not characters or not degrees:
        raise NewGameError("Initial contents must provide a character and degree for the profile")
    favorite = characters[0]
    add("t_profiles", {"favorite_m_character_skin_id": favorite["battle_m_character_skin_id"],
                       "m_degree_id": degrees[0]["m_degree_id"], "comment": ""})
    add("t_statuses", {
        "name": profile_name, "gold": 0, "soul": 0, "disaster_coin": 0, "player_exp": rank["total_exp"],
        "user_rank": rank["rank"], "character_exp": 0, "is_staff": 0,
        "favorite_t_character_id": favorite["id"], "rental_t_character_id": 0, "free_virtual_money": 0,
        "max_party_count": MAX_PARTY_COUNT, "t_novel_homes": {"up": [], "down": []}, "friend_point": 0,
    })
    add("t_prologue_tutorials", {"status": 0 if start_tutorial else PROLOGUE_COMPLETED})
    add("t_tavern_daily_cards", dormant_tavern_values())
    add("t_idle_exploration_statuses", dormant_exploration_values())
    state = {"policy_version": POLICY_VERSION, "records": records, "unresolved_initial_contents": unresolved}
    validate_saved_state(state)
    return state


def validate_saved_state(state: dict, *, policy_version: int = POLICY_VERSION) -> None:
    if not isinstance(state, dict) or set(state) != {"policy_version", "records", "unresolved_initial_contents"}:
        raise NewGameError("Invalid stored new-game state")
    if policy_version not in (1, 2, 3, 4, 5) or type(state["policy_version"]) is not int or state["policy_version"] != policy_version:
        raise NewGameError("Unsupported stored new-game policy")
    records = state["records"]
    if not isinstance(records, dict) or set(records) != set(RECORD_NAMES):
        raise NewGameError("Stored record collections do not match the client contract")
    owners = set()
    for name, container in records.items():
        if not isinstance(container, dict) or set(container) != {"up", "down"} or container["down"] != []:
            raise NewGameError(f"{name}: expected a complete record snapshot")
        rows = container["up"]
        if not isinstance(rows, list) or len(rows) > 10000 or (rows and name not in ROW_FIELDS):
            raise NewGameError(f"{name}: unsupported stored records")
        fields = {field: kind for kind, names in ROW_FIELDS.get(name, {}).items() for field in names.split()}
        identities = set()
        for row in rows:
            if not isinstance(row, dict) or set(row) != set(fields):
                raise NewGameError(f"{name}: stored row fields do not match the client contract")
            for field, kind in fields.items():
                value = row[field]
                if kind in ("int", "long", "uint", "ulong", "ushort"):
                    integer(value, f"{name}.{field}", bits={"int": 32, "long": 64, "uint": 32, "ulong": 64, "ushort": 16}[kind], unsigned=kind in ("uint", "ulong", "ushort"),
                            minimum=0 if (name, field) == ("t_nethers", "m_nether_map_floor_id") or name in ("t_disaster_slots", "t_disaster_parties") and field in ("m_disaster_quest_id", "m_disaster_boss_id") else 1 if field in ("id", "user_id") or field.startswith("m_")
                            else -(2 ** 31) if name == "t_tavern_daily_cards" and kind == "int" else 0)
                elif kind == "string":
                    if value is None and (name, field) in NULLABLE_STRINGS:
                        continue
                    if not isinstance(value, str):
                        raise NewGameError(f"{name}.{field}: expected a string")
                    try:
                        value.encode("utf-8", errors="strict")
                    except UnicodeError as exc:
                        raise NewGameError(f"{name}.{field}: invalid Unicode") from exc
                    if field in ("created_at", "opened_at"):
                        wire_timestamp(value)
                elif kind == "bool":
                    if type(value) is not bool:
                        raise NewGameError(f"{name}.{field}: expected a boolean")
                elif kind == "tavern_cards":
                    validate_tavern_cards(value)
                elif kind == "optional_long":
                    if value is not None:
                        integer(value, f"{name}.{field}", bits=64, minimum=1)
                elif kind in ("long_ids", "long_list"):
                    if not isinstance(value, list) or len(value) > 1000:
                        raise NewGameError(f"{name}.{field}: expected an identifier list")
                    for identifier in value:
                        integer(identifier, f"{name}.{field}", bits=64, minimum=1)
                    if len(set(value)) != len(value):
                        raise NewGameError(f"{name}.{field}: duplicate identifier")
                elif kind == "disaster_reports":
                    pending, count = [(value, 0)], 0
                    while pending:
                        entries, depth = pending.pop()
                        if not isinstance(entries, list) or depth > 8:
                            raise NewGameError("Stored disaster reports are invalid")
                        count += len(entries)
                        if count > 1000:
                            raise NewGameError("Stored disaster reports exceed their limit")
                        for report in entries:
                            if not isinstance(report, dict) or set(report) != {"message_id", "replies"}:
                                raise NewGameError("Stored disaster report fields are invalid")
                            integer(report["message_id"], "Disaster report message", bits=64, minimum=1)
                            pending.append((report["replies"], depth + 1))
                elif value != {"up": [], "down": []}:
                    raise NewGameError(f"{name}.{field}: unsupported nested records")
            identity = row[identity_field(name)]
            if identity in identities:
                raise NewGameError(f"{name}: duplicate owned row ID")
            identities.add(identity)
            if "user_id" in row:
                owners.add(row["user_id"])
        master_field = {"t_chapter_quests": "m_chapter_quest_id", "t_gachas": "m_gacha_id"}.get(name, next((field for field in fields if field.startswith("m_")), None))
        repeatable = {"t_limited_items", "t_building_skills", "t_exploration_party_mana_gems", "t_weapons", "t_armors", "t_accessories", "t_weapon_enchantments", "t_armor_enchantments", "t_accessory_enchantments", "t_appraisal_result_pools", "t_disaster_slots", "t_disaster_parties", "t_idle_exploration_encounters", "t_nether_items", "t_nether_lock_items", "t_nether_codes"}
        repeatable.update({"t_event_stage_effect_levels", "t_event_timed_parameters", "t_event_battle_stats", "t_event_bonus_quest_stages", "t_idle_exploration_encounter_campaign_quests"})
        if master_field and name not in repeatable and len({row[master_field] for row in rows}) != len(rows):
            raise NewGameError(f"{name}: duplicate owned master reference")
    if len(owners) != 1:
        raise NewGameError("Stored records must belong to one local profile")
    try:
        validate_optional_records(state)
    except ValueError as exc:
        raise NewGameError(str(exc)) from exc
    prologue = records["t_prologue_tutorials"]["up"]
    if policy_version >= 2 or prologue:
        tutorial = single(prologue, "Stored prologue tutorial")
        if tutorial["status"] not in PROLOGUE_STATES:
            raise NewGameError("Stored prologue tutorial has an unsupported status")
    tavern = records["t_tavern_daily_cards"]["up"]
    if policy_version >= 3 or tavern:
        single(tavern, "Stored tavern daily card")
    exploration = records["t_idle_exploration_statuses"]["up"]
    if policy_version >= 4 or exploration:
        idle = single(exploration, "Stored idle exploration status")
        if idle["level"] < 1 or idle["explorable_duration"] < 1 or idle["has_rare_drop"] not in (0, 1):
            raise NewGameError("Stored idle exploration status has invalid values")
        if idle["exploring_start_at"] is not None:
            try:
                datetime.fromisoformat(idle["exploring_start_at"].replace("Z", "+00:00"))
            except ValueError as exc:
                raise NewGameError("Stored idle exploration start time is invalid") from exc
    status = single(records["t_statuses"]["up"], "Stored user status")
    profile = single(records["t_profiles"]["up"], "Stored profile")
    characters = records["t_characters"]["up"]
    character_ids = {row["id"] for row in characters}
    skins = {row["m_character_skin_id"] for row in records["t_character_skins"]["up"]}
    weapons = {row["m_weapon_skin_id"] for row in records["t_weapon_skins"]["up"]}
    degrees = {row["m_degree_id"] for row in records["t_degrees"]["up"]}
    if status["favorite_t_character_id"] not in character_ids or status["rental_t_character_id"] not in character_ids | {0}:
        raise NewGameError("Stored favorite or rental character is not owned")
    if profile["favorite_m_character_skin_id"] not in skins or profile["m_degree_id"] not in degrees:
        raise NewGameError("Stored profile refers to unowned content")
    if not 1 <= len(status["name"]) <= 64 or status["user_rank"] < 1 or status["max_party_count"] < 1:
        raise NewGameError("Stored user status has invalid starting values")
    for row in characters:
        if row["level"] < 1 or row["m_character_id"] < 1:
            raise NewGameError("Stored character has invalid starting values")
        if {row["battle_m_character_skin_id"], row["tavern_m_character_skin_id"]} - skins or row["battle_m_weapon_skin_id"] not in weapons:
            raise NewGameError("Stored character refers to unowned skins")
    for row in records["t_buildings"]["up"]:
        if row["m_building_id"] not in LOCAL_BUILDING_IDS or row["level"] < 1:
            raise NewGameError("Stored building is outside the local starting policy")
    if {row["m_building_id"] for row in records["t_buildings"]["up"]} != set(LOCAL_BUILDING_IDS):
        raise NewGameError("Stored starting buildings are incomplete")
    if any(row["m_plan_id"] < 1 or row["step"] < (0 if policy_version >= 5 else 1) for row in records["t_plans"]["up"]):
        raise NewGameError("Stored plan has invalid starting values")
    unresolved = state["unresolved_initial_contents"]
    if not isinstance(unresolved, list) or len(unresolved) > 10000:
        raise NewGameError("Invalid unresolved initial-content ledger")
    for seed in unresolved:
        if not isinstance(seed, dict) or set(seed) != set(TABLE_FIELDS["m_initial_contents"]):
            raise NewGameError("Invalid unresolved initial-content row")
        integer(seed["id"], "Unresolved seed ID", bits=64, minimum=1)
        integer(seed["content_id"], "Unresolved content ID", bits=64, minimum=1)
        if type(seed["content_type"]) is not int or seed["content_type"] != 130 or type(seed["amount"]) is not int or seed["amount"] != 1 or not isinstance(seed["options"], str):
            raise NewGameError("Invalid unresolved initial-content values")
        initial_options(seed)


def common_contents(state: dict) -> dict:
    validate_saved_state(state)
    return {"records": copy.deepcopy(state["records"]), "notifies": None}


def user_contents(state: dict) -> dict:
    return {**common_contents(state), "possession_limit": {"equipment": EQUIPMENT_LIMIT}}


def home_contents(state: dict) -> dict:
    return {
        **common_contents(state), "daily_greetings": [], "daily_visitors": [], "available_recommends": [],
        "badges": {"info": 0, "present_count": 0, "mission_reward": 0, "mission_pass_reward": 0,
                   "sp_mission_reward": [], "building": 0, "unarrived_plan_mission": 0},
    }


def login_announcement_contents(state: dict) -> dict:
    return {
        **common_contents(state),
        "login_bonus_list": {"daily": [], "special": [], "special_condition": []},
        "announcement_ids": [], "friend_point_data": {"follow_count": 0, "get_friend_point": 0},
        "expired_limited_items": [], "expires_soon_t_limited_item_ids": [], "has_new_shop": 0,
        "t_login_thirty_days_bonuses": [], "t_login_limited_bonuses": [],
    }
