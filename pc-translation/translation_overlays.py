from __future__ import annotations

import copy
import json
import re
from collections import Counter
from pathlib import Path


LANGUAGE = "zh_Hant"
SLOTS = re.compile(r"\{[^{}]*\}")
NUMBERS = re.compile(r"\d+(?:[.,]\d+)*")
TAGS = re.compile(r"</?[A-Za-z][^<>]*>|<#[0-9A-Fa-f]+>")


def read_json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"Duplicate JSON key in {path}")
            result[key] = value
        return result
    return json.loads(Path(path).read_text(encoding="utf-8-sig"), object_pairs_hook=pairs)


def validate_translation(source, translated):
    if not isinstance(source, str) or not source or not isinstance(translated, str) or not translated:
        raise ValueError("Supplement entries must contain nonempty source and translation strings")
    checks = {
        "placeholders": lambda value: Counter(SLOTS.findall(value)),
        "numbers": lambda value: Counter(NUMBERS.findall(SLOTS.sub("", value))),
        "rich text": lambda value: TAGS.findall(value),
        "line breaks": lambda value: value.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\r\n", "\n").replace("\r", "\n").count("\n"),
    }
    for label, tokens in checks.items():
        if tokens(source) != tokens(translated):
            raise ValueError(f"Supplement changes {label}: {source[:100]!r}")


def dictionary_path(root, name):
    parts = name.split("/")
    if (not parts or any(not re.fullmatch(r"[a-z][a-z0-9_-]*", part) for part in parts)
            or len(parts) > 2 or len(parts) == 2 and parts[0] not in {"other", "add-on"}
            or parts[0] in {"manifest", "legacy", "novels", "m_ng_words"}):
        raise ValueError("Invalid supplement dictionary: " + name)
    result = root.joinpath(*parts, LANGUAGE + ".json")
    if not result.resolve().is_relative_to(root.resolve()):
        raise ValueError("Supplement path leaves the translation cache")
    return result


def merge_overrides(cache, overrides, corrections=None):
    planned = {}
    report = {"added": 0, "corrected": 0, "upstream_preferred": 0, "dictionaries": {}}
    for path in sorted(overrides.rglob(LANGUAGE + ".json")):
        if not path.resolve().is_relative_to(overrides.resolve()):
            raise ValueError("Supplement source leaves its directory")
        name = path.parent.relative_to(overrides).as_posix()
        destination = dictionary_path(cache, name)
        upstream = read_json(destination) if destination.exists() else {}
        values = read_json(path)
        if not isinstance(upstream, dict) or not isinstance(values, dict):
            raise ValueError("Supplement and target must be dictionaries: " + name)
        stats = {"added": 0, "corrected": 0, "upstream_preferred": 0}
        for source, translated in values.items():
            validate_translation(source, translated)
            if not upstream.get(source) or upstream[source] == source:
                upstream[source] = translated
                stats["added"] += 1
            elif upstream[source] != translated:
                stats["upstream_preferred"] += 1
        planned[name] = upstream
        report["dictionaries"][name] = stats
    for name, entries in (read_json(corrections) if corrections and corrections.exists() else {}).items():
        destination = dictionary_path(cache, name)
        upstream = planned.setdefault(name, read_json(destination) if destination.exists() else {})
        stats = report["dictionaries"].setdefault(name, {"added": 0, "corrected": 0, "upstream_preferred": 0})
        for source, correction in entries.items():
            if not isinstance(correction, dict) or set(correction) != {"expected", "translation"}:
                raise ValueError("Correction must specify the expected upstream text and translation")
            validate_translation(source, correction["translation"])
            if upstream.get(source) == correction["expected"]:
                upstream[source] = correction["translation"]
                stats["corrected"] += 1
    for name, data in planned.items():
        destination = dictionary_path(cache, name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for field in ("added", "corrected", "upstream_preferred"):
            report[field] += report["dictionaries"][name][field]
    return report


def extend_mapping(mapping, additions):
    result = copy.deepcopy(mapping)
    for table, fields in additions.items():
        if not re.fullmatch(r"M[A-Za-z0-9]+", table) or table == "MNgWords" or not isinstance(fields, dict):
            raise ValueError("Invalid supplemental master table")
        target = result["tables"].setdefault(table, {})
        for field, dictionary in fields.items():
            if not re.fullmatch(r"[a-z][a-z0-9_]*", field) or not re.fullmatch(r"m_[a-z0-9_]+", dictionary) or dictionary == "m_ng_words":
                raise ValueError("Invalid supplemental master field or dictionary")
            rule = {"dict": dictionary}
            if field in target and target[field].get("dict") != dictionary:
                raise ValueError("Supplement conflicts with an existing master mapping")
            target.setdefault(field, rule)
            if dictionary not in result["dict_types"]:
                result["dict_types"].append(dictionary)
    return result
