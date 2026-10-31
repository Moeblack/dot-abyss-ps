import copy
import json
from pathlib import Path

import pytest

from dotabyss_ps.newgame import (
    NewGameError,
    NULLABLE_STRINGS,
    ROW_FIELDS,
    SUPPORTED_CLIENTS,
    TABLE_FIELDS,
    build_initial_state,
    home_contents,
    login_announcement_contents,
    user_contents,
)

PLAY_CLIENT = {
    "package": "com.exnoa.abyss",
    "version": "1.15.0",
    "metadata_sha256": "2bbdadf0952016d5e21a34e2699de54bc97eacb9c8f85b4650e93e80c3cb0e7c",
}
PC_CLIENT = {
    "package": "dotabyss_x_cl",
    "version": "1.15.0",
    "metadata_sha256": "d38f026a769e522b136830785ad2789f834c47127be764f89ab294b4d91f056d",
}


def minimal_master(client=None):
    definitions = {
        "m_initial_contents": [("id", "long"), ("content_type", "int"), ("content_id", "long"), ("amount", "int"), ("options", "string")],
        "m_characters": [("id", "long"), ("weapon_type", "long")],
        "m_character_skins": [("id", "long"), ("m_character_id", "long"), ("type", "int"), ("is_default", "int")],
        "m_weapon_skins": [("id", "long"), ("weapon_type", "long"), ("m_character_id", "int"), ("is_default", "int"), ("is_paid", "int")],
        "m_character_levels": [("id", "long"), ("level", "int"), ("require_amount", "int")],
        "m_user_ranks": [("id", "long"), ("rank", "int"), ("total_exp", "int")],
        "m_mana_gems": [("id", "long")],
        "m_degrees": [("id", "long")],
        "m_plans": [("id", "long")],
        "m_plan_steps": [("id", "long"), ("m_plan_id", "long"), ("step", "int")],
        "m_buildings": [("id", "long")],
        "m_building_levels": [("id", "long"), ("m_building_id", "long"), ("level", "int"), ("exp", "int")],
    }
    schema = {
        "schema_version": 1,
        "client": {
            "package": "jp.co.fanzagames.dotabyss_x_a.mod",
            "version": "1.14.4",
            "metadata_sha256": "e144b637ceaabbcb385555d11213ba46b30216ff8df76d3d8fa8854d1582f130",
        },
        "tables": {name: [{"name": field, "type": kind} for field, kind in columns] for name, columns in definitions.items()},
    }
    if client is not None:
        schema["client"] = copy.deepcopy(client)
    buildings = [100, 200, 300, 500, 1000]
    tables = {
        "m_initial_contents": [
            [1, 40, 1200016, 1, ""], [2, 40, 1200002, 1, '{"intimacy_point":50}'],
            [3, 40, 1200003, 1, ""], [4, 60, 101, 1, ""], [5, 60, 102, 1, ""], [6, 60, 301, 1, ""],
            *[[index + 7, 130, identifier, 1, ""] for index, identifier in enumerate(buildings)],
            [12, 190, 1, 1, ""], [17, 131, 1001, 1, ""], [18, 131, 5001, 1, ""],
        ],
        "m_characters": [[1200016, 1010], [1200002, 1060], [1200003, 1080]],
        "m_character_skins": [
            [120001601, 1200016, 1, 1], [120001602, 1200016, 2, 1],
            [120000201, 1200002, 1, 1], [120000203, 1200002, 2, 1],
            [120000301, 1200003, 1, 1], [120000302, 1200003, 2, 1],
        ],
        "m_weapon_skins": [
            [100011001, 1010, 0, 1, 0], [100016001, 1060, 0, 1, 0], [100018001, 1080, 0, 1, 0],
            [120001601, 1010, 1200016, 0, 1],
        ],
        "m_character_levels": [[201, 2, 55], [101, 1, 0]],
        "m_user_ranks": [[1002, 2, 80], [1001, 1, 0]],
        "m_mana_gems": [[101], [102], [301]],
        "m_degrees": [[1]],
        "m_plans": [[1001], [5001]],
        "m_plan_steps": [[10011, 1001, 2], [10010, 1001, 1], [50010, 5001, 1]],
        "m_buildings": [[identifier] for identifier in buildings],
        "m_building_levels": [[identifier * 100 + level, identifier, level, 0 if level == 1 else 100]
                              for identifier in buildings for level in (2, 1)],
    }
    return schema, tables


@pytest.fixture
def master():
    return minimal_master()


def make_state(master, **values):
    schema, tables = master
    return build_initial_state(tables, schema, **{
        "user_id": 42, "profile_name": "local-player", "created_at": "2026-10-30 12:00:00", **values,
    })


def test_initial_state_references_owned_rows_and_default_master_assets(master):
    state = make_state(master)
    assert state["policy_version"] == 5
    records = state["records"]
    assert records["t_prologue_tutorials"]["up"] == [{"id": 1, "user_id": 42, "status": 99}]
    status = records["t_statuses"]["up"][0]
    characters = records["t_characters"]["up"]
    assert [row["id"] for row in characters] == [1, 2, 3]
    assert [row["m_character_id"] for row in characters] == [1200016, 1200002, 1200003]
    assert status["favorite_t_character_id"] == characters[0]["id"]
    assert status["user_rank"] == 1 and status["player_exp"] == 0
    assert all(row["level"] == 1 and row["user_id"] == 42 for row in characters)
    assert [row["intimacy_point"] for row in characters] == [0, 50, 0]
    assert [row["battle_m_weapon_skin_id"] for row in characters] == [100011001, 100016001, 100018001]
    skins = {row["m_character_skin_id"] for row in records["t_character_skins"]["up"]}
    weapons = {row["m_weapon_skin_id"] for row in records["t_weapon_skins"]["up"]}
    for character in characters:
        assert {character["battle_m_character_skin_id"], character["tavern_m_character_skin_id"]} <= skins
        assert character["battle_m_weapon_skin_id"] in weapons
    profile = records["t_profiles"]["up"][0]
    assert profile["favorite_m_character_skin_id"] in skins
    assert profile["m_degree_id"] == records["t_degrees"]["up"][0]["m_degree_id"]
    assert all(row["step"] == 0 and "user_id" not in row for row in records["t_plans"]["up"])
    assert [row["id"] for row in records["t_buildings"]["up"]] == [1, 2, 3, 4, 5]
    assert [row["m_building_id"] for row in records["t_buildings"]["up"]] == [100, 200, 300, 500, 1000]
    assert all(row["level"] == 1 and row["exp"] == 0 for row in records["t_buildings"]["up"])
    assert all(container["down"] == [] for container in records.values())


def test_unresolved_initial_contents_remain_auditable_and_are_not_wire_records(master):
    state = make_state(master)
    assert state["unresolved_initial_contents"] == [dict(zip(
        ["id", "content_type", "content_id", "amount", "options"], row,
    )) for row in master[1]["m_initial_contents"] if row[1] == 130]
    assert all("unresolved_initial_contents" not in view for view in (user_contents(state), home_contents(state), login_announcement_contents(state)))
    master[1]["m_initial_contents"] = [row for row in master[1]["m_initial_contents"] if row[1] != 130]
    separate = make_state(master)
    assert separate["unresolved_initial_contents"] == []
    assert separate["records"]["t_buildings"] == state["records"]["t_buildings"]


def test_views_are_isolated_and_never_create_login_rewards(master):
    state = make_state(master)
    snapshot = copy.deepcopy(state)
    user, home = user_contents(state), home_contents(state)
    assert user["records"] == home["records"]
    assert user["possession_limit"] == {"equipment": 100}
    assert home["daily_greetings"] == home["daily_visitors"] == home["available_recommends"] == []
    assert home["badges"]["sp_mission_reward"] == []
    assert user["notifies"] is None and home["notifies"] is None
    user["records"]["t_characters"]["up"][0]["level"] = 999
    home["records"]["t_mana_gems"]["up"].clear()
    assert state == snapshot


def test_tavern_starts_dormant_with_independent_nested_card_lists(master):
    state = make_state(master)
    expected = {"id": 1, "user_id": 42, "day_of_week": 0, "day": 0, "use_ticket": 0, "work_seed": 0,
                "worked_count": 0, "selected_count": 0, "score": 0, "selected_result": "",
                "selected_cards": {"selected_cards": [], "use_cards": [], "selected_character_cards": [], "use_character_cards": []}}
    assert state["records"]["t_tavern_daily_cards"]["up"] == [expected]
    view = user_contents(state)
    view["records"]["t_tavern_daily_cards"]["up"][0]["selected_cards"]["selected_cards"].append({"turn": 1, "m_tavern_card_id": 1})
    assert home_contents(state)["records"]["t_tavern_daily_cards"]["up"] == [expected]


def test_master_row_order_does_not_change_owned_ids_or_starting_state(master):
    original = make_state(master)
    for rows in master[1].values():
        rows.reverse()
    assert make_state(master) == original


@pytest.mark.parametrize("table", ["m_characters", "m_character_skins", "m_weapon_skins", "m_mana_gems", "m_degrees", "m_plans", "m_plan_steps", "m_buildings", "m_building_levels"])
def test_missing_initial_references_fail_instead_of_building_partial_state(master, table):
    master[1][table].clear()
    with pytest.raises(NewGameError):
        make_state(master)


@pytest.mark.parametrize("options", ['{"intimacy_point":-1}', '{"intimacy_point":true}', '{"intimacy_point":2147483648}', '{"level":99}', '[]', 'null', '{'])
def test_unsupported_character_options_rejected(master, options):
    master[1]["m_initial_contents"][0][4] = options
    with pytest.raises(NewGameError):
        make_state(master)


@pytest.mark.parametrize("field,value", [(1, 999), (3, 2), (3, True), (4, '{"level":1}')])
def test_unsupported_seeds_do_not_silently_grant_or_drop_content(master, field, value):
    master[1]["m_initial_contents"][3][field] = value
    with pytest.raises(NewGameError):
        make_state(master)


def test_duplicate_content_and_ambiguous_skin_defaults_rejected(master):
    schema, tables = master
    seed = tables["m_initial_contents"][0].copy()
    seed[0] = 99
    tables["m_initial_contents"].append(seed)
    with pytest.raises(NewGameError, match="unique type/ID"):
        make_state(master)
    tables["m_initial_contents"].pop()
    tables["m_character_skins"].append([120001603, 1200016, 1, 1])
    with pytest.raises(NewGameError, match="normal skin"):
        make_state(master)


def test_paid_weapon_skin_does_not_replace_missing_free_default(master):
    master[1]["m_weapon_skins"].pop(0)
    with pytest.raises(NewGameError, match="default weapon skin"):
        make_state(master)


@pytest.mark.parametrize("user_id", [0, -1, True, 2**63, "1"])
def test_invalid_user_identity_rejected(master, user_id):
    with pytest.raises(NewGameError):
        make_state(master, user_id=user_id)


def test_maximum_signed_user_id_is_not_multiplied_into_owned_ids(master):
    state = make_state(master, user_id=2**63 - 1)
    assert state["records"]["t_characters"]["up"][0]["user_id"] == 2**63 - 1
    assert state["records"]["t_characters"]["up"][0]["id"] == 1


def test_int32_skin_reference_overflow_rejected_even_when_master_id_is_long(master):
    master[1]["m_character_skins"][0][0] = 2**31
    with pytest.raises(NewGameError, match="32-bit"):
        make_state(master)


@pytest.mark.parametrize("values", [
    {"profile_name": ""}, {"profile_name": "x" * 65}, {"profile_name": "\ud800"},
    {"created_at": "2026-02-31 12:00:00"}, {"created_at": "2026-1-1 12:00:00"}, {"created_at": None},
])
def test_invalid_identity_text_or_time_rejected(master, values):
    with pytest.raises(NewGameError):
        make_state(master, **values)


def test_changed_client_or_column_contract_requires_a_new_policy(master):
    schema, tables = master
    schema["client"]["version"] = "1.15.0"
    with pytest.raises(NewGameError, match="verified client"):
        make_state(master)
    schema["client"]["version"] = "1.14.4"
    schema["tables"]["m_initial_contents"][1]["type"] = "long"
    with pytest.raises(NewGameError, match="contract changed"):
        make_state(master)


def test_seed_tables_never_silently_deduplicate_master_ids(master):
    master[1]["m_characters"].append(master[1]["m_characters"][0].copy())
    with pytest.raises(NewGameError, match="duplicate master ID"):
        make_state(master)


def test_generated_wire_fields_match_exported_client_dtos(master):
    source = Path(__file__).resolve().parents[1] / "artifacts" / "protocol.json"
    if not source.is_file():
        pytest.skip("Protocol export is not part of this test checkout")
    types = {entry["full_name"]: entry for entry in json.loads(source.read_text(encoding="utf-8"))["types"]}
    state = make_state(master)
    records = state["records"]
    record_model = types["Il2CppProject.Api.RecordEntity"]
    assert set(records) == {field["name"] for field in record_model["fields"]}
    for field in record_model["fields"]:
        container = types[field["type"]]
        element_name = container["base_type"].split("<", 1)[1][:-1]
        expected = {field["name"]: field["type"] for field in types[element_name]["fields"]}
        if field["name"] in ROW_FIELDS:
            declared = {name: kind for kind, names in ROW_FIELDS[field["name"]].items() for name in names.split()}
            assert set(declared) == set(expected)
            aliases = {"long_ids": "Il2CppInterop.Runtime.InteropTypes.Arrays.Il2CppStructArray`1<long>",
                       "long_list": "Il2CppSystem.Collections.Generic.List`1<long>", "optional_long": "Il2CppSystem.Nullable`1<long>",
                       "disaster_reports": "Il2CppInterop.Runtime.InteropTypes.Arrays.Il2CppReferenceArray`1<Il2CppProject.Api.DisasterReportEntity>"}
            assert all(aliases.get(kind, kind) == expected[name] for name, kind in declared.items() if kind not in ("container", "tavern_cards"))
        for row in records[field["name"]]["up"]:
            assert set(row) == set(expected)
            for name, kind in expected.items():
                if kind in ("int", "long"):
                    assert type(row[name]) is int
                    assert -(2**(31 if kind == "int" else 63)) <= row[name] < 2**(31 if kind == "int" else 63)
                elif kind == "string":
                    assert isinstance(row[name], str) or (row[name] is None and (field["name"], name) in NULLABLE_STRINGS)
    base_fields = {field["name"]: field["type"] for field in types["Il2CppProject.Api.AppContentsEntityBase"]["fields"]}
    assert base_fields["notifies"] == "Il2CppProject.Api.NotifyEntity"
    assert user_contents(state)["notifies"] is None


@pytest.mark.parametrize("filename", ["master-schema-fanza-1.14.4.json", "master-schema-play-1.15.0.json", "master-schema-pc-1.15.0.json"])
def test_required_master_column_types_match_exported_native_schema(filename):
    source = Path(__file__).resolve().parents[1] / "artifacts" / filename
    if not source.is_file():
        pytest.skip("Master schema export is not part of this test checkout")
    tables = json.loads(source.read_text(encoding="utf-8"))["tables"]
    for name, fields in TABLE_FIELDS.items():
        actual = {column["name"]: column["type"] for column in tables[name]}
        assert all(actual[field] == kind for field, kind in fields.items())


@pytest.mark.parametrize("client", [PLAY_CLIENT, PC_CLIENT], ids=["play", "pc"])
def test_verified_identity_uses_platform_tutorial_policy_without_changing_old_identity(master, client):
    old = make_state(master)
    play_schema, play_tables = minimal_master(client)
    expected = copy.deepcopy(old)
    if client == PC_CLIENT:
        expected["records"]["t_prologue_tutorials"]["up"][0]["status"] = 0
    assert make_state((play_schema, play_tables)) == expected
    assert master[0]["client"]["version"] == "1.14.4"


@pytest.mark.parametrize("client", [None, PLAY_CLIENT, PC_CLIENT], ids=["fanza", "play", "pc"])
@pytest.mark.parametrize("field,value", [
    ("package", "com.unverified.abyss"), ("version", "1.15.1"), ("metadata_sha256", "0" * 64),
])
def test_client_allowlist_requires_all_identity_fields(client, field, value):
    schema, tables = minimal_master(client)
    schema["client"][field] = value
    with pytest.raises(NewGameError, match="verified client"):
        make_state((schema, tables))


@pytest.mark.parametrize("field", ["package", "version", "metadata_sha256"])
def test_supported_identity_components_cannot_be_mixed(master, field):
    master[0]["client"][field] = PLAY_CLIENT[field]
    with pytest.raises(NewGameError, match="verified client"):
        make_state(master)


@pytest.mark.parametrize("client,other", [(PLAY_CLIENT, PC_CLIENT), (PC_CLIENT, PLAY_CLIENT)], ids=["play-to-pc", "pc-to-play"])
@pytest.mark.parametrize("field", ["package", "metadata_sha256"])
def test_same_version_pc_and_play_identity_components_cannot_be_mixed(client, other, field):
    schema, tables = minimal_master(client)
    schema["client"][field] = other[field]
    with pytest.raises(NewGameError, match="verified client"):
        make_state((schema, tables))


def test_play_native_contract_proof_matches_supported_schemas_and_response_views():
    artifacts = Path(__file__).resolve().parents[1] / "artifacts"
    proof = json.loads((artifacts / "server-play-contract-comparison.json").read_text(encoding="utf-8"))
    old = proof["fanza-mod-1.14.4"]
    play = proof["play-1.15.0"]
    for name, filename in (("fanza-mod-1.14.4", "master-schema-fanza-1.14.4.json"),
                           ("play-1.15.0", "master-schema-play-1.15.0.json")):
        schema = json.loads((artifacts / filename).read_text(encoding="utf-8"))
        assert schema["client"] in SUPPORTED_CLIENTS
        assert proof[name]["metadata_sha256"] == schema["client"]["metadata_sha256"]
    for category, count in (("response_models", 105), ("record_containers", 99), ("record_rows", 99)):
        assert len(play[category]) == count
        assert play[category] == old[category]
        assert proof["differences"][category] == proof["field_order_differences"][category] == []
        for name, model in play[category].items():
            assert list(model["fields"].items()) == list(old[category][name]["fields"].items())
    for table in TABLE_FIELDS:
        assert list(play["master_tables"][table].items()) == list(old["master_tables"][table].items())
    assert proof["differences"]["master_tables"]

    state = make_state(minimal_master(PLAY_CLIENT))
    aliases = {"int": "int32", "long": "int64", "uint": "uint32", "ulong": "uint64", "ushort": "uint16", "string": "string", "bool": "bool", "container": "Project.Api.NovelHomesListEntity",
               "tavern_cards": "Project.Api.TavernSelectCardsEntity", "long_ids": "int64[]",
               "disaster_reports": "Project.Api.DisasterReportEntity[]", "long_list": "System.Collections.Generic.List`1<int64>", "optional_long": "System.Nullable`1<int64>"}
    assert set(state["records"]) == set(play["record_containers"]) == set(play["record_rows"])
    for collection, groups in ROW_FIELDS.items():
        declared = {field: aliases[kind] for kind, fields in groups.items() for field in fields.split()}
        assert declared == play["record_rows"][collection]["fields"]
    models = play["response_models"]
    base = models["Project.Api.AppContentsEntityBase"]["fields"]
    for view, model_name in ((user_contents, "Project.Api.UserResponseEntity"), (home_contents, "Project.Api.HomeResponseEntity")):
        assert set(view(state)) == set(base) | set(models[model_name]["fields"])
        assert base["notifies"] == "Project.Api.NotifyEntity"
        assert view(state)["notifies"] is None


@pytest.mark.parametrize("damage", [
    lambda state: state["records"].pop("t_statuses"),
    lambda state: state["records"]["t_characters"].update(up={}),
    lambda state: state["records"]["t_characters"].update(down=[1]),
    lambda state: state["records"]["t_characters"]["up"][0].update(user_id=43),
    lambda state: state["records"]["t_characters"]["up"][0].update(level=True),
    lambda state: state["records"]["t_characters"]["up"][0].update(created_at="tomorrow"),
    lambda state: state["records"]["t_characters"]["up"][0].update(battle_m_character_skin_id=999),
    lambda state: state["records"]["t_statuses"]["up"][0].update(favorite_t_character_id=999),
    lambda state: state["records"]["t_profiles"]["up"][0].update(m_degree_id=999),
    lambda state: state["records"]["t_buildings"]["up"].pop(),
    lambda state: state["records"]["t_character_skins"]["up"][0].update(m_character_skin_id=0),
    lambda state: state["records"]["t_plans"]["up"][0].update(step=-1),
    lambda state: state["records"]["t_prologue_tutorials"]["up"].clear(),
    lambda state: state["records"]["t_prologue_tutorials"]["up"][0].update(status=98),
    lambda state: state["records"]["t_prologue_tutorials"]["up"][0].update(status=True),
    lambda state: state["records"]["t_tavern_daily_cards"]["up"].clear(),
    lambda state: state["records"]["t_tavern_daily_cards"]["up"][0].update(worked_count=True),
    lambda state: state["records"]["t_tavern_daily_cards"]["up"][0].update(work_seed=2 ** 31),
    lambda state: state["records"]["t_tavern_daily_cards"]["up"][0].update(selected_cards=None),
    lambda state: state["unresolved_initial_contents"][0].update(content_type=40),
    lambda state: state.update(policy_version=True),
])
@pytest.mark.parametrize("view", [user_contents, home_contents, login_announcement_contents])
def test_state_views_reject_corrupted_stored_data(master, damage, view):
    state = make_state(master)
    damage(state)
    with pytest.raises(NewGameError):
        view(state)


def test_login_announcement_contract_matches_both_client_models():
    artifacts = Path(__file__).resolve().parents[1] / "artifacts"
    proof = json.loads((artifacts / "mypage-login-contract.json").read_text(encoding="utf-8"))
    assert {key: proof["client"][key] for key in PLAY_CLIENT} == PLAY_CLIENT
    assert proof["route"]["method"] == "GET"
    assert proof["route"]["path"] == "/api/mypage/login"
    assert proof["response"]["base"] == "Project.Api.AppContentsEntityBase"
    assert proof["local_policy"]["mutates_currency_inventory_or_login_rewards"] is False
    old = {model["full_name"]: model for model in json.loads((artifacts / "protocol.json").read_text(encoding="utf-8"))["types"]}

    def native_type(value):
        if value.startswith("Il2CppInterop.Runtime.InteropTypes.Arrays."):
            return native_type(value.split("<", 1)[1][:-1]) + "[]"
        return {"int": "int32", "long": "int64"}.get(value, value.removeprefix("Il2Cpp"))

    for name, model in proof["models"].items():
        if name.endswith("LoginAnnouncementRequestEntity"):
            continue
        actual = [(field["name"], native_type(field["type"])) for field in old["Il2Cpp" + name]["fields"]]
        assert actual == [(field["name"], field["type"]) for field in model["fields"]]
    schema, tables = minimal_master(PLAY_CLIENT)
    state = make_state((schema, tables))
    contents = login_announcement_contents(state)
    fields = proof["models"]["Project.Api.LoginAnnouncementResponseEntity"]["fields"]
    assert set(contents) == set(proof["response"]["base_fields"]) | {field["name"] for field in fields}
    assert {key: contents[key] for key in contents if key not in ("records", "notifies")} == proof["local_policy"]["additional_contents_fields"]
    assert set(contents["login_bonus_list"]) == {field["name"] for field in proof["models"]["Project.Api.LoginBonusListEntity"]["fields"]}
    assert set(contents["friend_point_data"]) == {field["name"] for field in proof["models"]["Project.Api.FriendPointDataEntity"]["fields"]}


def test_login_announcement_view_copies_records_and_allocates_fresh_nested_values(master):
    state = make_state(master)
    before = copy.deepcopy(state)
    first = login_announcement_contents(state)
    first["records"]["t_statuses"]["up"][0]["friend_point"] = 99
    first["login_bonus_list"]["daily"].append({"id": 1})
    first["friend_point_data"]["get_friend_point"] = 99
    second = login_announcement_contents(state)
    assert state == before
    assert second["records"] == before["records"]
    assert second["login_bonus_list"]["daily"] == []
    assert second["friend_point_data"] == {"follow_count": 0, "get_friend_point": 0}
