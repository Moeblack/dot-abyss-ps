import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("translation_overlays", ROOT / "pc-translation/translation_overlays.py")
overlays = importlib.util.module_from_spec(spec)
spec.loader.exec_module(overlays)


def write(path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values, ensure_ascii=False), encoding="utf-8")


def test_supplements_survive_updates_but_prefer_new_upstream_translation(tmp_path):
    cache, source = tmp_path / "cache", tmp_path / "overrides"
    local = source / "ui_texts/zh_Hant.json"
    target = cache / "ui_texts/zh_Hant.json"
    write(local, {"empty": "空值補譯", "same": "同值補譯", "missing": "缺失補譯", "valid": "本地譯文"})
    write(target, {"empty": "", "same": "same", "valid": "上游譯文"})
    result = overlays.merge_overrides(cache, source)
    assert result["added"] == 3 and result["upstream_preferred"] == 1
    assert overlays.read_json(target)["valid"] == "上游譯文"
    write(target, {"missing": "上游新版譯文"})
    overlays.merge_overrides(cache, source)
    assert overlays.read_json(target)["missing"] == "上游新版譯文"


def test_reviewed_corrections_only_apply_to_known_upstream_value(tmp_path):
    cache, source = tmp_path / "cache", tmp_path / "overrides"
    source.mkdir()
    target, corrections = cache / "ui_texts/zh_Hant.json", tmp_path / "corrections.json"
    write(target, {"old": "已知錯譯", "changed": "上游已修正"})
    write(corrections, {"ui_texts": {key: {"expected": "已知錯譯", "translation": "確認修正"} for key in ("old", "changed")}})
    result = overlays.merge_overrides(cache, source, corrections)
    assert result["corrected"] == 1
    assert overlays.read_json(target) == {"old": "確認修正", "changed": "上游已修正"}


@pytest.mark.parametrize("source,translated", [
    ("ダメージ{15%}", "傷害{16%}"),
    ("上昇{0:P0}", "提升{0:N0}"),
    ("15秒 / 2HIT", "15秒 / 3HIT"),
    ("<b><i>説明</i></b>", "<b><i>說明</b></i>"),
    ("説明\\n詳細", "說明詳細"),
])
def test_structural_changes_are_rejected(source, translated):
    with pytest.raises(ValueError, match="Supplement changes"):
        overlays.validate_translation(source, translated)


def test_translated_slot_order_and_newline_representation_are_supported():
    overlays.validate_translation("{0}から{1}へ\\n{0:P0}", "從{1}到{0}\n{0:P0}")


def test_invalid_later_dictionary_does_not_partially_publish(tmp_path):
    cache, source = tmp_path / "cache", tmp_path / "overrides"
    write(cache / "a/zh_Hant.json", {"old": "原譯文"})
    write(source / "a/zh_Hant.json", {"new": "新譯文"})
    write(source / "z/zh_Hant.json", {"15秒": "16秒"})
    with pytest.raises(ValueError):
        overlays.merge_overrides(cache, source)
    assert overlays.read_json(cache / "a/zh_Hant.json") == {"old": "原譯文"}


@pytest.mark.parametrize("name", ["../escape", "m_ng_words", "novels/a", "/absolute", "other/../x", "manifest"])
def test_unsafe_or_narrative_paths_are_rejected(tmp_path, name):
    with pytest.raises(ValueError):
        overlays.dictionary_path(tmp_path, name)


def test_mapping_preserves_existing_seal_rule_and_adds_new_fields():
    original = {"tables": {"MSkills": {"description": {"dict": "m_skills", "seal": True}}}, "dict_types": ["m_skills"]}
    result = overlays.extend_mapping(original, {"MSkills": {"description": "m_skills", "name": "m_names"}})
    assert result["tables"]["MSkills"]["description"]["seal"]
    assert result["dict_types"] == ["m_skills", "m_names"]
    assert "name" not in original["tables"]["MSkills"]
    with pytest.raises(ValueError):
        overlays.extend_mapping(original, {"MSkills": {"description": "m_unrelated"}})


def test_authored_supplements_preserve_gameplay_values_and_markup():
    for path in (ROOT / "pc-translation/overrides").rglob("zh_Hant.json"):
        for source, translated in overlays.read_json(path).items():
            overlays.validate_translation(source, translated)
    for entries in overlays.read_json(ROOT / "pc-translation/corrections.json").values():
        for source, correction in entries.items():
            overlays.validate_translation(source, correction["translation"])


def test_supplemental_master_bindings_match_native_schema():
    schema = overlays.read_json(ROOT / "artifacts/master-schema-pc-1.15.0.json")["tables"]
    for table, fields in overlays.read_json(ROOT / "pc-translation/mapping-overrides.json").items():
        for field, dictionary in fields.items():
            native = next(row for row in schema[dictionary] if row["name"] == field)
            assert native["type"] == "string"
