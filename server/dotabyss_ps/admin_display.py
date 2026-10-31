from __future__ import annotations

import json
import re
import threading
from pathlib import Path

from .admin_display_references import DisplayResolver, MasterDisplayIndex, reference_table


class DisplayTranslations:
    def __init__(self, root=None):
        workspace = Path(__file__).resolve().parents[3]
        self.root = Path(root) if root is not None else workspace / "dotabyss_pc" / "local-client" / "BepInEx" / "plugins" / "AbyssMod" / "cache" / "translations"
        self.lock = threading.RLock()
        self.cache = {}

    def dictionary(self, table):
        if not isinstance(table, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,127}", table):
            return {}
        path = self.root / table / "zh_Hant.json"
        with self.lock:
            try:
                stat = path.stat()
                stamp = (stat.st_mtime_ns, stat.st_size)
                if stamp == self.cache.get(table, (None,))[0]:
                    return self.cache[table][1]
                if stat.st_size > 16 * 1024 * 1024 or not path.resolve().is_relative_to(self.root.resolve()):
                    return {}
                data = json.loads(path.read_text(encoding="utf-8-sig"))
                if not isinstance(data, dict):
                    return {}
                data = {key: value for key, value in data.items() if isinstance(key, str) and isinstance(value, str) and value}
            except (OSError, ValueError, UnicodeError):
                data, stamp = {}, None
            self.cache[table] = stamp, data
            return data

    def lookup(self, table):
        result = dict(self.dictionary("ui_texts"))
        result.update(self.dictionary(table))
        if table in ("m_characters", "m_character_skins", "m_dictionary_non_player_characters"):
            result.update(self.dictionary("names"))
        return result

    def row(self, table, values, lookup=None):
        lookup = self.lookup(table) if lookup is None else lookup
        result = {}
        for field, value in values.items():
            if not isinstance(value, str) or field.endswith("_at"):
                continue
            translated = lookup.get(value, value)
            if translated != value:
                result[field] = translated
        return result

    @staticmethod
    def referenced_table(field, catalog):
        return reference_table(field, catalog.tables)

    def native_records(self, records, catalog):
        index = catalog if isinstance(catalog, MasterDisplayIndex) else MasterDisplayIndex(catalog.tables)
        return DisplayResolver(index, self, records).native_records()
