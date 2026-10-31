import copy
import json

from dotabyss_ps.admin_display import DisplayTranslations
from dotabyss_ps.admin_display_references import DisplayResolver, MasterDisplayIndex
from dotabyss_ps.admin_master import atomic_json, patch_config
from test_admin_console import get, headers, preview
from test_admin_display import dictionary
from test_gameplay import call, gameplay_master, setup_gameplay, snapshot


def definitions():
    return {
        "m_characters": {1: {"id": 1, "name": "アリシア"}, 2: {"id": 2, "name": "Second"}},
        "m_character_abilities": {8: {"id": 8, "name": "護り", "m_character_id": 1, "ability_no": 7}},
        "m_weapons": {50: {"id": 50, "name": "Sword"}},
        "m_accessories": {60: {"id": 60, "name": "Ring"}},
        "m_gacha_groups": {7: {"id": 7, "name": "始まり"}},
        "m_gachas": {8: {"id": 8, "m_gacha_group_id": 7, "lottery_num": 10, "m_gacha_token_id": 9}},
        "m_gacha_tokens": {9: {"id": 9}},
        "m_degrees": {99: {"id": 99, "display_text": "称号"}},
        "m_payment_dmm_products": {9: {"id": 9, "name": "Pack"}},
        "m_payments": {30: {"id": 30, "m_payment_dmm_product_id": 9}},
        "m_enchantment_details": {1: {"id": 1, "enchantment_no": 70, "name": "Sharp"}},
        "m_enchantments": {7: {"id": 7, "enchantment_no": 70, "enchantment_ability_no": 300}},
        "m_enchantment_abilities": {22: {"id": 22, "enchantment_ability_no": 300}},
    }


def native(master_id=1):
    return {
        "t_characters": {"up": [{"id": 100, "m_character_id": master_id}]},
        "t_weapons": {"up": [{"id": 100, "m_weapon_id": 50}]},
        "t_accessories": {"up": [{"id": 100, "m_accessory_id": 60}]},
        "t_character_abilities": {"up": [{"id": 2, "t_character_id": 100, "m_character_ability_id": 8}]},
        "t_accessory_enchantments": {"up": [{"id": 3, "t_accessory_id": 100, "enchantment_no": 70}]},
        "t_exploration_party_characters": {"up": [{"id": 4, "t_character_id": 100, "t_weapon_id": 100, "t_armor_id": 0}]},
        "t_statuses": {"up": [{"id": 1, "name": "アリシア", "favorite_t_character_id": 100}]},
    }


def test_instance_chains_aliases_translations_and_profile_scoping_are_display_only(tmp_path):
    dictionary(tmp_path, "names", {"アリシア": "艾莉西亞"})
    translator = DisplayTranslations(tmp_path)
    index = MasterDisplayIndex(definitions())
    records = native()
    saved = copy.deepcopy(records)
    display = translator.native_records(records, index)
    assert records == saved
    assert display["t_exploration_party_characters"]["4"]["t_character_id"] == "艾莉西亞"
    assert display["t_exploration_party_characters"]["4"]["t_weapon_id"] == "Sword"
    assert "t_armor_id" not in display["t_exploration_party_characters"]["4"]
    assert display["t_accessory_enchantments"]["3"]["name"] == "Ring · Sharp"
    assert display["t_character_abilities"]["2"]["name"] == "艾莉西亞 · 護り"
    assert display["t_statuses"]["1"]["favorite_t_character_id"] == "艾莉西亞"
    assert "name" not in display["t_statuses"]["1"]
    assert "アリシア" in display["t_exploration_party_characters"]["4"]["_search"]
    assert translator.native_records(native(2), index)["t_exploration_party_characters"]["4"]["t_character_id"] == "Second"


def test_polymorphic_content_nested_arrays_and_derived_titles(tmp_path):
    dictionary(tmp_path, "m_gacha_groups", {"始まり": "启程"})
    index = MasterDisplayIndex(definitions())
    resolver = DisplayResolver(index, DisplayTranslations(tmp_path))
    row = {"id": 1, "rewards": [{"content_type": 70, "content_id": 50}, {"content_type": 50, "content_id": 0},
                               {"content_type": 70, "content_id": 999}], "m_weapon_ids": [50, 0, 999]}
    before = copy.deepcopy(row)
    result = resolver.describe("m_reward_rows", row)
    assert row == before
    assert result["rewards"] == [{"content_id": "Sword"}, {"content_id": "金币"}, None]
    assert result["m_weapon_ids"] == ["Sword", None, None]
    assert resolver.describe("m_reward_rows", {"m_weapon_ids": "50, 0, 999"})["m_weapon_ids"] == "Sword（50），0，999"
    assert resolver.label("m_gachas", 8) == "启程 · 10次"
    assert resolver.label("m_payments", 30) == "Pack"
    assert resolver.label("m_degrees", 99) == "称号"
    assert resolver.label("m_enchantment_abilities", 22) == "Sharp"
    assert resolver.label("m_gacha_tokens", 9) == "抽卡代币 · 启程"
    progress = {"operation_receipts": {"x": {"m_character_id": 1}}, "features": {"reward": {"data": {
        "rewards": [{"content_type": 70, "content_id": 50}], "receipts": [{"m_character_id": 1}],
        "seed": {"m_character_id": 1}, "counters": {"123": {"m_character_id": 1}}}}}}
    before = copy.deepcopy(progress)
    assert resolver.progress(progress) == {"features": {"reward": {"data": {"rewards": [{"content_id": "Sword"}]}}}}
    assert progress == before


def test_lazy_full_schema_indexes_cycles_and_ambiguous_ids_keep_raw_values(tmp_path):
    tables = {"m_union_types": [[1, "Union"]], "m_nodes": [[1, 2], [2, 1]],
              "m_ambiguous": [[1, "One"], [1, "Two"]], "m_unbound": [[1, "Unknown"]]}
    schema = {"tables": {"m_union_types": [{"name": "id"}, {"name": "name"}],
                         "m_nodes": [{"name": "id"}, {"name": "next_m_node_id"}],
                         "m_ambiguous": [{"name": "id"}, {"name": "name"}]}}
    index = MasterDisplayIndex(tables, schema)
    assert index.decoded == {}
    resolver = DisplayResolver(index, DisplayTranslations(tmp_path))
    assert resolver.label("m_union_types", 1) == "Union"
    assert set(index.decoded) == {"m_union_types"}
    assert resolver.label("m_nodes", 1) is None
    assert resolver.label("m_ambiguous", 1) is None and index.identity("m_ambiguous") is None
    assert resolver.label("m_unbound", 1) is None
    assert resolver.label("m_union_types", 0) is None and resolver.label("m_union_types", 999) is None


def test_resolve_and_snapshot_names_do_not_change_save_or_export(setup_gameplay, tmp_path):
    context = setup_gameplay
    call(context, "api/user")
    saved = snapshot(context)
    auth = headers(context)
    service = context.client.app.state.admin
    translator_root = tmp_path / "translations"
    raw_name = service.display_data()[1].get("m_characters", 1200002)["name"]
    dictionary(translator_root, "names", {raw_name: "测试角色译名"})
    service.display = DisplayTranslations(translator_root)
    body = {"table": "m_characters", "ids": [1200002, "1200002", 0, 999999999]}
    assert context.client.post("/admin/resolve", json=body).status_code == 401
    response = context.client.post("/admin/resolve", headers=auth, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["labels"] == {"1200002": "测试角色译名"}
    assert response.json()["identity_field"] == "id"
    for invalid in ({"table": "t_characters", "ids": [1]}, {"table": "../outside", "ids": [1]},
                    {"table": "m_characters", "ids": [1] * 101}, {"table": "m_characters", "ids": [True]},
                    {"table": "m_characters", "ids": ["01"]}, {"table": "m_characters", "ids": [-1]}):
        assert context.client.post("/admin/resolve", headers=auth, json=invalid).status_code == 400
    shown = get(context)
    assert shown["record_identity_fields"]["t_event_disaster_quests"] == "m_event_disaster_quest_id"
    assert shown["record_sources"]["t_presents"] == ["progress", "features", "presents", "data", "entries"]
    assert shown["record_sources"]["t_local_profiles"] == ["profile_choices"]
    exported = get(context, "/export")
    assert not {"record_display", "progress_display", "record_sources", "profile_choices", "record_identity_fields"} & exported.keys()
    assert exported["state"] == shown["state"]
    assert snapshot(context) == saved


def test_master_foreign_names_search_and_name_cache_refresh(setup_gameplay, tmp_path):
    context = setup_gameplay
    service = context.client.app.state.admin
    names = tmp_path / "translations"
    service.display = DisplayTranslations(names)
    record, index = service.display_data()
    gacha = index.rows("m_gachas")[0]
    group = index.get("m_gacha_groups", gacha["m_gacha_group_id"])
    path = dictionary(names, "m_gacha_groups", {group["name"]: "翻译后的卡池"})
    for query in (group["name"], "翻译后的卡池", str(gacha["id"])):
        result = service.master_table("m_gachas", query=query)
        assert any(row["values"]["id"] == gacha["id"] for row in result["rows"])
    fields = {field["name"]: field for field in result["fields"]}
    assert fields["m_gacha_group_id"]["source"] == "m_gacha_groups"
    assert fields["content_id"]["source_by"]["field"] == "content_type"
    assert fields["content_id"]["editable"] is False
    old_index = service.display_data()[1]
    path.write_text(json.dumps({group["name"]: "更新后的名称"}, ensure_ascii=False) + " ", encoding="utf-8")
    assert "更新后的名称" in service.resolve_labels("m_gachas", [gacha["id"]])["labels"][str(gacha["id"])]
    assert service.display_data()[1] is old_index
    config = patch_config({"format_version": 1, "patches": []}, index.tables, context.schema, record,
                          "m_gacha_groups", 0, {"name": "Override group"})
    atomic_json(service.overrides_path, config)
    assert service.display_data()[1] is not old_index
    assert service.resolve_labels("m_gacha_groups", [index.rows("m_gacha_groups")[0]["id"]])["labels"]


def test_preview_annotations_and_sources_preserve_raw_ids(setup_gameplay):
    context = setup_gameplay
    call(context, "api/user")
    saved = snapshot(context)
    proposal = preview(context, "character.edit", {"ids": [1200002], "level": 2})
    example = next(change for change in proposal["changes"] if change["table"] == "t_characters")["examples"][0]
    assert example["after"]["m_character_id"] == 1200002 and example["after"]["_display"]["name"]
    item = context.client.app.state.admin.tickets[proposal["ticket"]]
    assert "_display" not in json.dumps([item["before"], item["after"], item["delta"]])
    assert snapshot(context) == saved
    actions = {row["id"]: {field["name"]: field for field in row["fields"]}
               for row in context.client.get("/admin/schema", headers=headers(context)).json()["actions"]}
    assert actions["gacha.wishlist"]["wishlist"]["source"] == "m_characters"
    assert actions["shop.pass_rewards"]["m_mission_pass_id"]["source"] == "m_mission_passes"
    assert actions["equipment.disassemble"]["t_accessory_ids"]["source"] == "t_accessories"
    assert actions["story.read"]["m_novel_id"]["source_by"]["field"] == "novel_type"
    assert actions["mail.withdraw"]["mail_id"]["source"] == "t_presents"
