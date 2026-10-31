from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading

import msgpack

from .gameplay_rules import load_rules
from .masterdata import MAX_PAYLOAD_BYTES, MasterDataError, canonical_json, inspect_payload, unique_map
from .admin_master import apply_overrides, fingerprint, read_overrides


OPEN_DATE = "2020-01-01 00:00:00"
CLOSE_DATE = "2099-12-31 23:59:59"
START_FIELDS = {"open_at", "start_at", "release_at", "released_at", "begin_at"}
END_FIELDS = {"end_at", "close_at"}


def reopened_tables(tables, schema, rules, campaign_id=0):
    result = dict(tables)
    columns = {name: [field["name"] for field in fields] for name, fields in schema["tables"].items()}

    def rows(name):
        return [dict(zip(columns[name], row)) for row in result.get(name, [])] if name in columns else []

    events = [row for row in rows("m_events") if rules["event_open_all"] or row["id"] in rules["event_open_ids"]]
    event_ids = {row["id"] for row in events}
    exchange_ids = {row["m_exchange_category_id"] for row in events} - {0}
    gacha_ids = {row["m_gacha_group_id"] for row in events} - {0}
    term_groups = {row["m_mission_term_category_group"] for row in events} - {0}
    token_ids = {row["m_gacha_token_id"] for row in rows("m_gachas") if row.get("m_gacha_group_id") in gacha_ids and row.get("m_gacha_token_id")}
    campaign_terms = {row["m_mission_term_id"] for row in rows("m_encounter_campaigns")} if rules["event_open_all"] else set()
    campaigns = rows("m_encounter_campaigns")
    if rules["event_open_all"] and campaigns:
        campaign_id = campaign_id or campaigns[0]["id"]
        if campaign_id not in {row["id"] for row in campaigns}:
            raise MasterDataError("Selected encounter campaign is not present in the active master")
    changed = {}
    for name, fields in columns.items():
        date_fields = START_FIELDS.union(END_FIELDS).intersection(fields)
        if not date_fields:
            continue
        for index, row in enumerate(rows(name)):
            selected = (name == "m_events" and row["id"] in event_ids
                        or name.startswith("m_event_") and row.get("m_event_id") in event_ids
                        or name == "m_novel_events" and row.get("m_event_id") in event_ids
                        or name == "m_exchange_categories" and row["id"] in exchange_ids
                        or name == "m_exchange_lineups" and row.get("m_exchange_category_id") in exchange_ids
                        or name == "m_mission_terms" and row.get("category_group") in term_groups
                        or name == "m_mission_terms" and row["id"] in campaign_terms
                        or name == "m_gacha_groups" and row["id"] in gacha_ids
                        or name == "m_gachas" and row.get("m_gacha_group_id") in gacha_ids
                        or name == "m_gacha_tokens" and row["id"] in token_ids
                        or name == "m_encounter_campaigns" and rules["event_open_all"])
            if not selected:
                continue
            if result[name] is tables[name]:
                result[name] = list(tables[name])
            result[name][index] = list(result[name][index])
            for field in date_fields:
                opened = field in START_FIELDS and (name != "m_encounter_campaigns" or row["id"] == campaign_id)
                result[name][index][fields.index(field)] = OPEN_DATE if opened else CLOSE_DATE
            changed[name] = changed.get(name, 0) + 1
    fill_missing_disasters(result, schema, events, rows, changed)
    return result, changed


def fill_missing_disasters(tables, schema, events, rows, changed):
    if "m_event_disasters" not in schema["tables"] or "m_disaster_bosses" not in schema["tables"]:
        return
    configs, bosses = rows("m_event_disasters"), rows("m_disaster_bosses")
    existing = {row["m_event_id"] for row in configs}
    enemies = {row["id"]: row for row in rows("m_enemies")}
    stages = {row["id"]: row for row in rows("m_defend_stages")}
    for event in events:
        if event["type"] != 2 or event["id"] in existing:
            continue
        quests = sorted((row for row in rows("m_event_disaster_quests") if row["m_event_id"] == event["id"]), key=lambda row: row["id"])
        if not quests:
            continue
        enemy = enemies[stages[quests[0]["stage_id"]]["m_enemy_id"]]
        boss_id = 99000000000 + event["id"]
        if any(row["id"] == boss_id for row in bosses):
            raise MasterDataError("Local event boss identifier collides with the source master")
        boss = {field["name"]: "" if field["type"] == "string" else 0 for field in schema["tables"]["m_disaster_bosses"]}
        boss.update(id=boss_id, name=enemy["name"], display_order=max((row["display_order"] for row in bosses), default=0) + 1)
        tables["m_disaster_bosses"] = list(tables["m_disaster_bosses"])
        tables["m_event_disasters"] = list(tables["m_event_disasters"])
        tables["m_disaster_bosses"].append([boss[field["name"]] for field in schema["tables"]["m_disaster_bosses"]])
        config = {"id": max((row["id"] for row in configs), default=0) + 1, "m_event_id": event["id"], "element_type": 0, "m_disaster_boss_id": boss_id}
        tables["m_event_disasters"].append([config[field["name"]] for field in schema["tables"]["m_event_disasters"]])
        configs.append(config)
        bosses.append(boss)
        changed["m_disaster_bosses"] = changed.get("m_disaster_bosses", 0) + 1
        changed["m_event_disasters"] = changed.get("m_event_disasters", 0) + 1


class EventMasterView:
    def __init__(self, masters, schema, rules_path):
        self.masters, self.schema, self.rules_path = masters, schema, rules_path
        self.lock = threading.RLock()
        self.views = {}
        self.cache = {}
        self.base_revision = None
        self.base_record = None
        self.base_payload = None
        self.base_tables = None

    def current(self, campaign_id=0):
        with self.lock:
            original = self.masters.store.master()
            if original is None:
                return None
            rules = load_rules(self.rules_path)
            settings = {key: rules[key] for key in ("event_open_all", "event_open_ids")}
            overrides = read_overrides(self.rules_path.with_name("master-overrides.json"))
            key = (original["revision"], hashlib.sha256(canonical_json(settings)).hexdigest(), campaign_id, fingerprint(overrides))
            if key in self.views:
                cached = self.views.pop(key)
                self.views[key] = cached
                return cached
            if self.base_revision != original["revision"]:
                self.base_record, self.base_payload = self.masters.read(original["revision"])
                self.base_tables = msgpack.unpackb(self.base_payload, raw=False, strict_map_key=True, object_pairs_hook=unique_map)
                self.base_revision = original["revision"]
            record, payload, tables = self.base_record, self.base_payload, self.base_tables
            overridden, changed = apply_overrides(tables, self.schema, original, overrides)
            derived, reopened = reopened_tables(overridden, self.schema, settings, campaign_id)
            changed = {name: changed.get(name, 0) + reopened.get(name, 0) for name in changed.keys() | reopened.keys()}
            if changed:
                payload = msgpack.packb(derived, use_bin_type=True)
                report = inspect_payload(payload, self.schema)
                revision = "events-" + report["sha256"][:24] + "-" + report["schema_sha256"][:12]
                record = {**record, **report, "revision": revision,
                          "base_revision": original["revision"], "base_sha256": original["sha256"],
                          "changed_tables": changed, "payload_modified": True,
                          "payload_path": "master/event-views/" + revision + ".msgpack"}
                tables = derived
                self._persist(record, payload)
            current = self.cache.get(record["revision"], (record, payload, tables))
            self.views[key] = current
            while len(self.views) > 4:
                self.views.pop(next(iter(self.views)))
            self.cache = {view[0]["revision"]: view for view in self.views.values()}
            return current

    def clear(self):
        with self.lock:
            self.views.clear()
            self.cache.clear()
            self.base_revision = self.base_record = self.base_payload = self.base_tables = None

    def read(self, revision):
        with self.lock:
            if revision in self.cache:
                return self.cache[revision]
        saved = self._read_saved(revision)
        if saved is not None:
            return saved
        self.current()
        with self.lock:
            return self.cache.get(revision)

    def _persist(self, record, payload):
        path = self.masters._path(record["payload_path"])
        if path.with_suffix(".json").exists():
            if self._read_saved(record["revision"])[1] != payload:
                raise MasterDataError("Published event master content changed")
            return
        self._write_immutable(path, payload)
        self._write_immutable(path.with_suffix(".json"), canonical_json(record))

    @staticmethod
    def _write_immutable(path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_bytes() != content:
                raise MasterDataError("An immutable event master cache file changed")
            return
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".partial", delete=False) as file:
                temporary = file.name
                file.write(content)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    def _read_saved(self, revision):
        if not re.fullmatch(r"events-[0-9a-f]{24}-[0-9a-f]{12}", revision):
            return None
        path = self.masters._path("master/event-views/" + revision + ".msgpack")
        metadata = path.with_suffix(".json")
        if not metadata.is_file():
            return None
        if metadata.stat().st_size > 64 * 1024:
            raise MasterDataError("Event master cache metadata exceeds its limit")
        try:
            record = json.loads(metadata.read_bytes())
            if (record["revision"] != revision or not path.is_file()
                    or not 0 < record["bytes"] <= MAX_PAYLOAD_BYTES or path.stat().st_size != record["bytes"]):
                raise ValueError("Invalid cache metadata")
            if (not re.fullmatch(r"[0-9a-f]{64}", record["sha256"])
                    or not re.fullmatch(r"[0-9a-f]{64}", record["schema_sha256"])
                    or revision != "events-" + record["sha256"][:24] + "-" + record["schema_sha256"][:12]):
                raise ValueError("Cache revision does not match its hashes")
            payload = path.read_bytes()
            if hashlib.sha256(payload).hexdigest() != record["sha256"]:
                raise ValueError("Event master cache hash mismatch")
            return record, payload, None
        except (ValueError, TypeError, KeyError) as error:
            raise MasterDataError("Published event master cache failed integrity verification") from error
