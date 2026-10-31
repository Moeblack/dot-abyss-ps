import json
from types import SimpleNamespace

from dotabyss_ps.admin_display import DisplayTranslations


def dictionary(root, table, entries):
    path = root / table / "zh_Hant.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
    return path


def test_translations_are_display_only_and_refresh_after_file_update(tmp_path):
    path = dictionary(tmp_path, "m_accessories", {"炎上の指輪": "燃燒戒指"})
    translator = DisplayTranslations(tmp_path)
    raw = {"id": 42, "name": "炎上の指輪", "asset_id": 123}
    assert translator.row("m_accessories", raw) == {"name": "燃燒戒指"}
    assert raw == {"id": 42, "name": "炎上の指輪", "asset_id": 123}
    path.write_text(json.dumps({"炎上の指輪": "火焰之戒"}, ensure_ascii=False) + " ", encoding="utf-8")
    assert translator.row("m_accessories", raw)["name"] == "火焰之戒"
    path.write_text("invalid", encoding="utf-8")
    assert translator.row("m_accessories", raw) == {}


def test_names_dictionary_matches_game_priority_and_native_input_stays_original(tmp_path):
    dictionary(tmp_path, "m_characters", {"アリシア": "另一譯名"})
    dictionary(tmp_path, "names", {"アリシア": "艾莉西亞"})
    translator = DisplayTranslations(tmp_path)
    catalog = SimpleNamespace(tables={"m_characters": {10: {"id": 10, "name": "アリシア"}}})
    records = {"t_characters": {"up": [{"id": 1, "m_character_id": 10}], "down": []},
               "t_statuses": {"up": [{"id": 1, "name": "アリシア"}], "down": []}}
    assert translator.native_records(records, catalog) == {"t_characters": {"1": {"m_character_id": "艾莉西亞", "name": "艾莉西亞", "_search": ["アリシア"]}}}
    assert records["t_statuses"]["up"][0]["name"] == "アリシア"
    assert translator.dictionary("../outside") == {}
