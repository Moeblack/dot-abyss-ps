from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
import uuid

from .admin_master import read_overrides
from .community import Community
from .gameplay import Gameplay, prepare_progress
from .gameplay_rules import load_rules
from .gameplay_tutorial import initialize_tutorial_profile
from .masterdata import canonical_json
from .newgame import build_initial_state, user_contents, validate_saved_state
from .protocol import validate_data


class ProfileError(ValueError):
    def __init__(self, message, status=400, code="invalid_profile"):
        super().__init__(message)
        self.status, self.code = status, code


def profile_text(value, maximum, title):
    if not isinstance(value, str):
        raise ProfileError(title + "必须是文字")
    value = unicodedata.normalize("NFC", value.strip())
    if not 1 <= len(value) <= maximum or any(unicodedata.category(char) in ("Cc", "Cf", "Cs") for char in value):
        raise ProfileError(f"{title}需为 1–{maximum} 个可见字符")
    return value


def label_key(value):
    return unicodedata.normalize("NFKC", value).casefold()


class ProfileManager:
    def __init__(self, store, local_state):
        self.store, self.local = store, local_state
        with self.store.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS profile_labels (
                    profile_id INTEGER PRIMARY KEY REFERENCES profiles(id) ON DELETE CASCADE,
                    label TEXT NOT NULL,
                    label_key TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS profile_create_receipts (
                    operation_id TEXT PRIMARY KEY,
                    request_hash TEXT NOT NULL,
                    profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL
                );
            """)

    def _rows(self, db):
        rows = db.execute("""SELECT p.id,p.name,p.uuid,g.profile_id AS saved_id,g.master_revision,
                             g.contents,g.created_at,l.label,l.updated_at AS label_updated_at
                             FROM profiles p LEFT JOIN game_states g ON g.profile_id=p.id
                             LEFT JOIN profile_labels l ON l.profile_id=p.id ORDER BY p.id""").fetchall()
        clients = {}
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='admin_clients'").fetchone():
            clients = {row[0]: row[1] for row in db.execute("SELECT profile_id,MAX(at) FROM admin_clients GROUP BY profile_id")}
        now = time.time()
        result = []
        for row in rows:
            player_name, state_error = "", ""
            if row["contents"] is not None:
                try:
                    state = json.loads(row["contents"])
                    player_name = state["records"]["t_statuses"]["up"][0]["name"]
                    if not isinstance(player_name, str):
                        raise ValueError("Invalid player name")
                    player_name.encode("utf-8", errors="strict")
                except (ValueError, TypeError, KeyError, IndexError):
                    player_name, state_error = "", "存档资料无法读取"
            last_seen = clients.get(row["id"])
            fallback = player_name.strip() or row["name"]
            result.append({"id": row["id"], "name": row["name"], "label": row["label"] or fallback,
                           "player_name": player_name, "has_state": row["saved_id"] is not None,
                           "master_revision": row["master_revision"], "online": last_seen is not None and now - last_seen < 20,
                           "last_seen": last_seen, "created_at": row["created_at"],
                           "label_updated_at": row["label_updated_at"], "state_error": state_error})
        return result

    def _get(self, db, profile_id):
        if type(profile_id) is not int or profile_id < 1:
            raise ProfileError("存档编号无效")
        result = next((row for row in self._rows(db) if row["id"] == profile_id), None)
        if result is None:
            raise ProfileError("存档不存在", 404, "profile_not_found")
        return result

    def get(self, profile_id):
        with self.store.connection() as db:
            db.execute("BEGIN")
            return self._get(db, profile_id)

    def list_profiles(self):
        with self.store.connection() as db:
            db.execute("BEGIN")
            return {"profiles": self._rows(db)}

    def _check_label(self, db, label, profile_id=None):
        if any(row["id"] != profile_id and label_key(row["label"]) == label_key(label) for row in self._rows(db)):
            raise ProfileError("已有同名存档，请换一个名称", 409, "label_conflict")

    def create(self, label, player_name=None, operation_id=None):
        label = profile_text(label, 64, "存档名称")
        if self.local is None or self.local.schema["client"]["package"] != "dotabyss_x_cl":
            raise ProfileError("当前服务未配置可创建的 PC 游戏存档", 503, "profile_not_configured")
        rules = load_rules(self.store.path.parent / "gameplay-rules.json")
        maximum = rules["max_profile_name_length"]
        if player_name is None or player_name == "":
            player_name = label if len(label) <= maximum else "司令官"
        player_name = profile_text(player_name, maximum, "游戏昵称")
        if operation_id is None:
            operation_id = str(uuid.uuid4())
        if not isinstance(operation_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", operation_id):
            raise ProfileError("创建请求标识无效")
        request_hash = hashlib.sha256(canonical_json({"label": label, "player_name": player_name})).hexdigest()
        record = self.local._active()
        overrides_path = self.store.path.parent / "master-overrides.json"
        overrides = read_overrides(overrides_path)
        tables, catalog = self.local._master_data(record["revision"])
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT * FROM profile_create_receipts WHERE operation_id=?", (operation_id,)).fetchone()
            if previous:
                if previous["request_hash"] != request_hash:
                    raise ProfileError("同一个创建请求不能更换参数", 409, "operation_conflict")
                return {"profile": self._get(db, previous["profile_id"]), "created": False, "operation_id": operation_id}
            active = db.execute("SELECT value FROM settings WHERE name='active_master'").fetchone()
            runtime = db.execute("SELECT value FROM settings WHERE name='runtime'").fetchone()
            if active is None or active[0] != record["revision"] or not runtime or json.loads(runtime[0]).get("resource_version") != record["revision"]:
                raise ProfileError("主数据版本已变化，请重新创建", 409, "version_conflict")
            if rules != load_rules(self.store.path.parent / "gameplay-rules.json") or overrides != read_overrides(overrides_path):
                raise ProfileError("创建规则已变化，请重新创建", 409, "version_conflict")
            self._check_label(db, label)
            internal_name = "local-" + uuid.uuid4().hex
            identity = str(uuid.uuid4())
            created_at = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
            profile_id = db.execute("INSERT INTO profiles(name,uuid) VALUES(?,?)", (internal_name, identity)).lastrowid
            state = build_initial_state(tables, self.local.schema, profile_id, player_name, created_at, start_tutorial=True)
            progress = {}
            game = Gameplay(state, progress, catalog, profile_id, rules=rules, community=Community(db, record["revision"], profile_id))
            prepare_progress(progress, game.now)
            initialize_tutorial_profile(game)
            game.visit(user_contents)
            validate_saved_state(state)
            validate_data(progress)
            db.execute("INSERT INTO game_states VALUES(?,?,?,?,?)", (profile_id, 1, record["revision"], self.store._state_json(state), created_at))
            db.execute("INSERT INTO gameplay_progress VALUES(?,?,?)", (profile_id, record["revision"], self.store._state_json(progress)))
            db.execute("INSERT INTO profile_labels VALUES(?,?,?,?,?)", (profile_id, label, label_key(label), created_at, created_at))
            db.execute("INSERT INTO profile_create_receipts VALUES(?,?,?,?)", (operation_id, request_hash, profile_id, created_at))
            return {"profile": self._get(db, profile_id), "created": True, "operation_id": operation_id}

    def rename(self, profile_id, label, expected_label):
        label = profile_text(label, 64, "存档名称")
        if not isinstance(expected_label, str):
            raise ProfileError("缺少原存档名称，请刷新后重试", 409, "label_conflict")
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            current = self._get(db, profile_id)
            if current["label"] == label:
                return {"profile": current}
            if current["label"] != expected_label:
                raise ProfileError("存档名称已变化，请刷新后重试", 409, "label_conflict")
            self._check_label(db, label, profile_id)
            now = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
            db.execute("""INSERT INTO profile_labels VALUES(?,?,?,?,?) ON CONFLICT(profile_id) DO UPDATE SET
                          label=excluded.label,label_key=excluded.label_key,updated_at=excluded.updated_at""",
                       (profile_id, label, label_key(label), now, now))
            return {"profile": self._get(db, profile_id)}
