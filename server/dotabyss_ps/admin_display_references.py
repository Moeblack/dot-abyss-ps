from __future__ import annotations

import re

from .gameplay_records import identity_field


CONTENT_TABLES = {
    30: "m_items", 31: "m_items", 33: "m_limited_items", 40: "m_characters", 41: "m_characters",
    45: "m_visitors", 54: "m_event_currencies", 56: "m_event_boss_tickets", 60: "m_mana_gems",
    70: "m_weapons", 80: "m_armors", 90: "m_accessories", 131: "m_plans", 140: "m_character_skins",
    141: "m_weapon_skins", 170: "m_gacha_tokens", 180: "m_mission_passes", 181: "m_mission_passes",
    185: "m_login_bonuses", 186: "m_login_bonuses", 190: "m_degrees",
}
CONTENT_NAMES = {10: "免费宝石", 20: "付费宝石", 50: "金币", 51: "Soul", 52: "厄灾币", 53: "友情点", 110: "角色经验", 111: "玩家经验"}
NOVEL_TABLES = {str(i): "m_novel_" + name for i, name in ((1, "mains"), (2, "events"), (3, "characters"), (4, "homes"), (5, "character_skins"), (6, "prologues"), (7, "others"))}
REFERENCE_ALIASES = {
    "release_condition_area_ids": "m_chapter_areas", "wishlist": "m_characters",
    "t_recent_exploration_party_id": "t_exploration_parties", "m_ability_effects_id": "m_ability_effects",
    "payment_id": "m_payments",
}
PRIMARY_REFERENCES = {
    "m_gachas": ("m_gacha_group_id",), "m_nether_maps": ("m_nether_id",),
    "m_nether_map_floors": ("m_nether_map_id",), "m_idle_exploration_map_area_difficulties": ("m_idle_exploration_map_area_id",),
    "t_chapter_quests": ("m_chapter_quest_id",), "t_idle_exploration_encounters": ("m_encounter_quest_id",),
    "t_exploration_party_characters": ("t_character_id",), "t_idle_exploration_party_characters": ("t_character_id",),
    "t_disaster_slots": ("m_disaster_quest_id", "m_disaster_boss_id"),
    "t_character_bond_parameters": ("t_character_id",), "t_character_condition_resistances": ("t_character_id",),
}
DESCRIPTION_TITLES = {"m_ability_details", "m_nether_spheres", "m_missions"}


def reference_table(field, tables):
    if field in REFERENCE_ALIASES:
        return REFERENCE_ALIASES[field]
    match = re.search(r"(?:^|_)((?:m|t)_.+?)_ids?(?:_(?:prev|next|[1-9][0-9]*))?$", field)
    if match is None:
        return None
    stem = match.group(1)
    candidates = (stem, stem + "s", stem + "es", stem[:-1] + "ies" if stem.endswith("y") else "")
    return next((name for name in candidates if name in tables), None)


def field_source(table, field, tables, fields):
    if field.endswith("content_id") and field[:-len("content_id")] + "content_type" in fields:
        return {"source_by": {"field": field[:-len("content_id")] + "content_type",
                              "values": {str(kind): name for kind, name in CONTENT_TABLES.items() if name in tables}}}
    if field == "m_novel_id" and "novel_type" in fields:
        return {"source_by": {"field": "novel_type", "values": NOVEL_TABLES}}
    if table == "m_payments" and field == "master_id":
        return {"source_by": {"field": "payment_type", "values": {"1": "m_payment_lineups", "2": "m_payment_recommends",
                                                                     "3": "m_payment_gachas", "4": "m_payment_subscriptions"}}}
    target = reference_table(field, tables)
    return {"source": target} if target and target.startswith("m_") else {}


class MasterDisplayIndex:
    def __init__(self, tables, schema=None):
        self.tables, self.schema = tables, schema
        self.decoded, self.indexes, self.groups = {}, {}, {}

    def rows(self, table):
        if table not in self.decoded:
            source = self.tables.get(table, ())
            if isinstance(source, dict):
                values = list(source.values())
            else:
                fields = (self.schema or {}).get("tables", {}).get(table)
                values = [dict(zip((field["name"] for field in fields), row)) for row in source] if fields else []
            self.decoded[table] = values
        return self.decoded[table]

    def index(self, table):
        if table not in self.indexes:
            result = {}
            for row in self.rows(table):
                identifier = row.get("id")
                if type(identifier) is int:
                    result[identifier] = None if identifier in result else row
            self.indexes[table] = result
        return self.indexes[table]

    def get(self, table, identifier):
        return self.index(table).get(identifier)

    def grouped(self, table, field, value):
        if not isinstance(value, (str, int)):
            return ()
        key = (table, field)
        if key not in self.groups:
            result = {}
            for row in self.rows(table):
                item = row.get(field)
                if isinstance(item, (str, int)):
                    result.setdefault(item, []).append(row)
            self.groups[key] = result
        return self.groups[key].get(value, ())

    def identity(self, table):
        rows, index = self.rows(table), self.index(table)
        return "id" if (not rows and any(field["name"] == "id" for field in (self.schema or {}).get("tables", {}).get(table, ()))
                        or rows and len(index) == len(rows) and all(row is not None for row in index.values())) else None


class DisplayResolver:
    def __init__(self, index, translations, records=None, *, translate=True):
        self.index, self.translations = index, translations
        self.records = records or {}
        self.native, self.labels, self.lookups = {}, {}, {}
        self.remaining = 30000
        self.tables = set(index.tables) | set(self.records)
        self.translate_values, self.original = translate, None

    def translated(self, table, row, field):
        value = row.get(field)
        if not isinstance(value, str) or not value.strip():
            return None
        if table.startswith("t_") or not self.translate_values:
            return value
        if table not in self.lookups:
            self.lookups[table] = self.translations.lookup(table)
        return self.lookups[table].get(value, value)

    def get(self, table, identifier):
        if table.startswith("m_"):
            return self.index.get(table, identifier)
        if table not in self.native:
            field = identity_field(table)
            self.native[table] = {row[field]: row for row in self.records.get(table, {}).get("up", ()) if field in row}
        return self.native[table].get(identifier)

    def label(self, table, identifier, seen=()):
        if type(identifier) is not int or identifier <= 0 or len(seen) >= 8:
            return None
        key = table, identifier
        if key in seen:
            return None
        if key not in self.labels:
            row = self.get(table, identifier)
            self.labels[key] = self.title(table, row, (*seen, key)) if row is not None else None
        return self.labels[key]

    def join(self, values):
        return " · ".join(dict.fromkeys(value for value in values if value)) or None

    def content(self, kind, identifier, seen):
        if type(kind) is not int:
            return None
        if kind in CONTENT_NAMES:
            return CONTENT_NAMES[kind]
        table = CONTENT_TABLES.get(kind)
        value = self.label(table, identifier, seen) if table else None
        return self.join((value, "角色核心点")) if value and kind == 41 else value

    def ability_title(self, number):
        named = [self.translated("m_character_abilities", row, "name") for row in self.index.grouped("m_character_abilities", "ability_no", number)]
        if any(named):
            return self.join(named)
        details = self.index.grouped("m_ability_details", "ability_no", number)
        row = min(details, key=lambda item: (item.get("ability_awake_level", 0), item.get("level", 0)), default=None)
        return self.translated("m_ability_details", row, "description") if row else None

    def field_label(self, table, row, field, value, seen):
        if field.endswith("content_id"):
            kind = row.get(field[:-len("content_id")] + "content_type")
            if kind is not None:
                return self.content(kind, value, seen)
        if field == "m_novel_id" and str(row.get("novel_type")) in NOVEL_TABLES:
            return self.label(NOVEL_TABLES[str(row["novel_type"])], value, seen)
        if table == "m_payments" and field == "master_id":
            target = {1: "m_payment_lineups", 2: "m_payment_recommends", 3: "m_payment_gachas", 4: "m_payment_subscriptions"}.get(row.get("payment_type"))
            return self.label(target, value, seen) if target else None
        if field == "enchantment_no":
            names = {text for item in self.index.grouped("m_enchantment_details", field, value)
                     if (text := self.translated("m_enchantment_details", item, "name"))}
            return next(iter(names)) if len(names) == 1 else None
        if field == "ability_no":
            return self.ability_title(value)
        if field in ("m_mission_reward_group_id", "m_payment_pack_pack_id"):
            target, group = ("m_mission_rewards", "group_id") if field == "m_mission_reward_group_id" else ("m_payment_packs", "pack_id")
            return self.join(self.title(target, item, seen) for item in self.index.grouped(target, group, value))
        target = reference_table(field, self.tables)
        if target:
            if isinstance(value, list):
                values = [self.label(target, item, seen) for item in value]
                return values if any(values) else None
            if field.endswith("_ids") and isinstance(value, str) and len(value) <= 16384 and re.fullmatch(r"\s*[0-9]+(?:\s*,\s*[0-9]+)*\s*", value):
                parts = [part.strip() for part in value.split(",")]
                labels = [self.label(target, int(part), seen) for part in parts]
                return "，".join(f"{label}（{part}）" if label else part for part, label in zip(parts, labels)) if any(labels) else None
            return self.label(target, value, seen)
        return None

    def title(self, table, row, seen=()):
        if table == "t_character_abilities":
            return self.join((self.label("t_characters", row.get("t_character_id"), seen),
                              self.label("m_character_abilities", row.get("m_character_ability_id"), seen)))
        if table in ("t_weapon_enchantments", "t_armor_enchantments", "t_accessory_enchantments"):
            kind = table[2:-len("_enchantments")]
            plural = "accessories" if kind == "accessory" else kind + "s"
            return self.join((self.label("t_" + plural, row.get("t_" + kind + "_id"), seen),
                              self.field_label(table, row, "enchantment_no", row.get("enchantment_no"), seen)))
        for field in ("name", "title", "display_text", "label"):
            text = self.translated(table, row, field)
            if text:
                return text
        if table == "m_gachas":
            text = self.label("m_gacha_groups", row.get("m_gacha_group_id"), seen)
            if text:
                return text + (f" · {row['lottery_num']}次" if type(row.get("lottery_num")) is int else "")
        if table == "m_payments":
            for field in ("m_payment_dmm_product_id", "m_payment_googleplay_product_id", "m_payment_appstore_product_id", "master_id"):
                text = self.field_label(table, row, field, row.get(field), seen)
                if text:
                    return text
        if "content_type" in row and "content_id" in row:
            text = self.content(row["content_type"], row["content_id"], seen)
            if text:
                amount = next((row[key] for key in ("amount", "acquire_num", "obtain_num") if type(row.get(key)) is int), None)
                return text + (f" × {amount}" if amount is not None else "")
        if table == "m_enchantment_abilities":
            values = [self.field_label("m_enchantments", item, "enchantment_no", item.get("enchantment_no"), seen)
                      for item in self.index.grouped("m_enchantments", "enchantment_ability_no", row.get("enchantment_ability_no"))]
            if any(values):
                return self.join(values)
        if table == "m_abilities":
            return self.ability_title(row.get("ability_no"))
        if table == "m_gacha_tokens":
            values = [self.label("m_gacha_groups", item.get("m_gacha_group_id"), seen)
                      for item in self.index.grouped("m_gachas", "m_gacha_token_id", row.get("id"))]
            if any(values):
                return self.join(("抽卡代币", self.join(values)))
        if table in DESCRIPTION_TITLES:
            text = self.translated(table, row, "description")
            if text:
                return text
        preferred = PRIMARY_REFERENCES.get(table)
        if preferred is None:
            own = "m_" + table[2:-1] + "_id" if table.startswith("t_") else ""
            preferred = tuple(dict.fromkeys((own, *row)))
        for field in preferred:
            value = self.field_label(table, row, field, row.get(field), seen)
            if isinstance(value, str) and value:
                if table == "m_gachas" and type(row.get("lottery_num")) is int:
                    value += f" · {row['lottery_num']}次"
                return value
        return None

    def walk(self, table, row, seen=(), depth=0, *, progress=False):
        if not isinstance(row, dict) or depth >= 8 or self.remaining <= 0:
            return {}
        self.remaining -= 1
        result = {}
        for field, value in row.items():
            if progress and (field in {"receipts", "history", "response", "responses", "snapshot", "snapshots", "manifest", "random", "rng", "seed", "cache"}
                             or field.endswith(("_receipts", "_receipt", "_history", "_cache", "_seed"))
                             or isinstance(value, dict) and value and all(str(key).isdigit() for key in value)):
                continue
            label = self.field_label(table, row, field, value, seen) if not progress or reference_table(field, self.tables) or field.endswith("content_id") else None
            if label is not None:
                result[field] = label
            elif isinstance(value, dict):
                nested = self.walk(table, value, seen, depth + 1, progress=progress)
                if nested:
                    result[field] = nested
            elif isinstance(value, list):
                nested = [self.walk(table, item, seen, depth + 1, progress=progress) or None for item in value]
                if any(nested):
                    result[field] = nested
        return result

    def progress(self, progress):
        self.remaining = min(self.remaining, 3000)
        return self.walk("progress", {"features": progress.get("features", {})}, progress=True)

    def describe(self, table, row, *, native=False):
        if self.translate_values and not native and table not in self.lookups:
            self.lookups[table] = self.translations.lookup(table)
        result = {} if native or not self.translate_values else self.translations.row(table, row, self.lookups[table])
        result.update(self.walk(table, row))
        if "name" not in row:
            title = self.title(table, row)
            if title:
                result["name"] = title
        if self.translate_values:
            if self.original is None:
                self.original = DisplayResolver(self.index, self.translations, self.records, translate=False)
            def terms(value):
                if isinstance(value, str):
                    return {value}
                values = value.values() if isinstance(value, dict) else value if isinstance(value, list) else ()
                return set().union(*(terms(item) for item in values)) if values else set()
            aliases = terms(self.original.describe(table, row, native=native)) - terms(result)
            if aliases:
                result["_search"] = sorted(aliases)
        return result

    def native_records(self):
        result = {}
        for table, container in self.records.items():
            labels = {}
            for row in container.get("up", ()):
                display = self.describe(table, row, native=True)
                if display:
                    labels[str(row[identity_field(table)])] = display
            if labels:
                result[table] = labels
        return result
