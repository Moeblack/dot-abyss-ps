from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Callable


class GameStateError(ValueError):
    pass


class Store:
    def __init__(self, path: Path, session_ttl: int = 86400):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.session_ttl = session_ttl
        with self.connection() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS profiles (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    uuid TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    session_hash TEXT PRIMARY KEY,
                    api_hash TEXT NOT NULL,
                    profile_id INTEGER NOT NULL REFERENCES profiles(id),
                    expires_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS profile_presence (
                    profile_id INTEGER PRIMARY KEY REFERENCES profiles(id) ON DELETE CASCADE,
                    last_login_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS follows (
                    follower_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                    followed_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                    PRIMARY KEY(follower_id,followed_id),
                    CHECK(follower_id<>followed_id)
                );
                CREATE INDEX IF NOT EXISTS follows_by_target ON follows(followed_id,follower_id);
                CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS master_datasets (
                    revision TEXT PRIMARY KEY,
                    metadata TEXT NOT NULL,
                    imported_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS game_states (
                    profile_id INTEGER PRIMARY KEY REFERENCES profiles(id),
                    schema_version INTEGER NOT NULL,
                    master_revision TEXT NOT NULL REFERENCES master_datasets(revision),
                    contents TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS gameplay_progress (
                    profile_id INTEGER PRIMARY KEY REFERENCES profiles(id),
                    master_revision TEXT NOT NULL,
                    contents TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS observations (
                    id INTEGER PRIMARY KEY,
                    at INTEGER NOT NULL,
                    method TEXT NOT NULL,
                    path TEXT NOT NULL,
                    status INTEGER NOT NULL,
                    request_keys TEXT NOT NULL,
                    reason TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS client_events (
                    id INTEGER PRIMARY KEY,
                    at INTEGER NOT NULL,
                    component TEXT NOT NULL,
                    event TEXT NOT NULL,
                    details TEXT NOT NULL
                );
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def digest(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    def login(self, profile: str) -> dict:
        session, api_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now = int(time.time())
        with self.connection() as db:
            db.execute("INSERT OR IGNORE INTO profiles(name,uuid) VALUES (?,?)", (profile, str(uuid.uuid4())))
            user = db.execute("SELECT id,uuid FROM profiles WHERE name=?", (profile,)).fetchone()
            db.execute("DELETE FROM sessions WHERE expires_at<=?", (now,))
            db.execute("INSERT INTO sessions VALUES (?,?,?,?)", (self.digest(session), self.digest(api_token), user["id"], now + self.session_ttl))
            db.execute("INSERT INTO profile_presence VALUES (?,?) ON CONFLICT(profile_id) DO UPDATE SET last_login_at=excluded.last_login_at",
                       (user["id"], time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))))
        return {"user_id": user["id"], "uuid": user["uuid"], "token": session, "api_token": api_token}

    def authenticate(self, session: str, api_token: str = "") -> dict | None:
        if not session:
            return None
        with self.connection() as db:
            row = db.execute(
                "SELECT profiles.id,profiles.uuid,sessions.api_hash FROM sessions JOIN profiles ON profiles.id=sessions.profile_id WHERE session_hash=? AND expires_at>?",
                (self.digest(session), int(time.time())),
            ).fetchone()
        if row is None or (api_token and not secrets.compare_digest(row["api_hash"], self.digest(api_token))):
            return None
        return {"user_id": row["id"], "uuid": row["uuid"]}

    def runtime(self) -> dict[str, str]:
        with self.connection() as db:
            row = db.execute("SELECT value FROM settings WHERE name='runtime'").fetchone()
        return json.loads(row["value"]) if row else {}

    def set_runtime(self, values: dict[str, str]) -> None:
        self.update_runtime(values)

    def update_runtime(self, values: dict[str, str]) -> dict[str, str]:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT value FROM settings WHERE name='runtime'").fetchone()
            runtime = {**(json.loads(row["value"]) if row else {}), **values}
            active = db.execute("SELECT value FROM settings WHERE name='active_master'").fetchone()
            if active:
                dataset = db.execute("SELECT metadata FROM master_datasets WHERE revision=?", (active["value"],)).fetchone()
                if dataset is None:
                    raise ValueError("Active master metadata is missing")
                client = json.loads(dataset["metadata"])["client"]
                if runtime.get("resource_version") != active["value"]:
                    raise ValueError("Resource version is bound to the active master dataset")
                if runtime.get("application_version") and runtime["application_version"] != client["version"]:
                    raise ValueError("Application version does not match the active master schema")
            db.execute("INSERT INTO settings VALUES ('runtime',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value", (json.dumps(runtime),))
        return runtime

    def save_master(self, metadata: dict) -> None:
        with self.connection() as db:
            existing = db.execute("SELECT metadata FROM master_datasets WHERE revision=?", (metadata["revision"],)).fetchone()
            if existing:
                previous = json.loads(existing["metadata"])
                if any(previous[key] != metadata[key] for key in ("sha256", "schema_sha256", "client", "payload_path", "bytes")):
                    raise ValueError("Master revision conflicts with stored metadata")
                return
            db.execute("INSERT INTO master_datasets VALUES (?,?,?)", (metadata["revision"], json.dumps(metadata), int(time.time())))

    def master(self, revision: str | None = None) -> dict | None:
        with self.connection() as db:
            if revision is None:
                active = db.execute("SELECT value FROM settings WHERE name='active_master'").fetchone()
                if active is None:
                    return None
                revision = active["value"]
            row = db.execute("SELECT metadata FROM master_datasets WHERE revision=?", (revision,)).fetchone()
        return json.loads(row["metadata"]) if row else None

    def activate_master(self, revision: str) -> None:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            dataset = db.execute("SELECT metadata FROM master_datasets WHERE revision=?", (revision,)).fetchone()
            if dataset is None:
                raise ValueError("Unknown master revision")
            metadata = json.loads(dataset["metadata"])
            row = db.execute("SELECT value FROM settings WHERE name='runtime'").fetchone()
            runtime = json.loads(row["value"]) if row else {}
            if runtime.get("application_version") and runtime["application_version"] != metadata["client"]["version"]:
                raise ValueError("Application version does not match the master schema")
            runtime["resource_version"] = revision
            db.execute("INSERT INTO settings VALUES ('active_master',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value", (revision,))
            db.execute("INSERT INTO settings VALUES ('runtime',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value", (json.dumps(runtime),))

    def observe(self, method: str, path: str, status: int, keys: list[str], reason: str) -> None:
        with self.connection() as db:
            db.execute("INSERT INTO observations(at,method,path,status,request_keys,reason) VALUES (?,?,?,?,?,?)", (int(time.time()), method, path, status, json.dumps(keys), reason))
            db.execute("DELETE FROM observations WHERE id NOT IN (SELECT id FROM observations ORDER BY id DESC LIMIT 2000)")

    def game_state(self, user_id: int, revision: str, factory: Callable[[int, str, str], dict],
                   operation: Callable[[dict, dict], dict] | None = None, *,
                   transaction_operation: Callable[[dict, dict, sqlite3.Connection], dict] | None = None) -> tuple[dict, dict]:
        if operation is not None and transaction_operation is not None:
            raise ValueError("Only one game-state operation may be supplied")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            active = db.execute("SELECT value FROM settings WHERE name='active_master'").fetchone()
            if active is None or active["value"] != revision:
                raise GameStateError("Active master changed; retry against the current revision")
            runtime_row = db.execute("SELECT value FROM settings WHERE name='runtime'").fetchone()
            runtime = json.loads(runtime_row["value"]) if runtime_row else {}
            if runtime.get("resource_version") != revision:
                raise GameStateError("Runtime resource version does not match the active master")
            row = db.execute("SELECT * FROM game_states WHERE profile_id=?", (user_id,)).fetchone()
            if row:
                if row["schema_version"] != 1:
                    raise GameStateError("Stored game state requires a supported schema migration")
                if row["master_revision"] != revision:
                    raise GameStateError("Stored game state is bound to another master revision; migration is required")
                try:
                    state = json.loads(row["contents"])
                except (ValueError, RecursionError) as exc:
                    raise GameStateError("Stored game state is corrupt") from exc
            else:
                profile = db.execute("SELECT name FROM profiles WHERE id=?", (user_id,)).fetchone()
                if profile is None:
                    raise GameStateError("Local profile does not exist")
                created_at = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
                state = factory(user_id, profile["name"], created_at)
                encoded = self._state_json(state)
                db.execute("INSERT INTO game_states VALUES (?,?,?,?,?)", (user_id, 1, revision, encoded, created_at))
            if operation is not None or transaction_operation is not None:
                saved_progress = db.execute("SELECT master_revision,contents FROM gameplay_progress WHERE profile_id=?", (user_id,)).fetchone()
                if saved_progress and saved_progress["master_revision"] != revision:
                    raise GameStateError("Gameplay progress is bound to another master revision")
                try:
                    progress = json.loads(saved_progress["contents"]) if saved_progress else {}
                except (ValueError, RecursionError) as exc:
                    raise GameStateError("Stored gameplay progress is corrupt") from exc
                response = transaction_operation(state, progress, db) if transaction_operation is not None else operation(state, progress)
                db.execute("UPDATE game_states SET contents=? WHERE profile_id=?", (self._state_json(state), user_id))
                db.execute("INSERT INTO gameplay_progress VALUES (?,?,?) ON CONFLICT(profile_id) DO UPDATE SET contents=excluded.contents",
                           (user_id, revision, self._state_json(progress)))
                return response, runtime
            return state, runtime

    @staticmethod
    def _state_json(value: dict) -> str:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode("utf-8")) > 2 * 1024 * 1024:
            raise GameStateError("Stored game state exceeds 2 MiB")
        return encoded

    def game_state_summary(self) -> dict:
        with self.connection() as db:
            rows = db.execute("SELECT master_revision,COUNT(*) AS profiles FROM game_states GROUP BY master_revision ORDER BY master_revision").fetchall()
        return {"profile_count": sum(row["profiles"] for row in rows), "revisions": [dict(row) for row in rows]}

    def observations(self) -> list[dict]:
        with self.connection() as db:
            rows = db.execute("SELECT at,method,path,status,request_keys,reason FROM observations ORDER BY id DESC LIMIT 100").fetchall()
        return [{**dict(row), "request_keys": json.loads(row["request_keys"])} for row in rows]

    def add_event(self, component: str, event: str, details: dict) -> None:
        with self.connection() as db:
            db.execute("INSERT INTO client_events(at,component,event,details) VALUES (?,?,?,?)", (int(time.time()), component, event, json.dumps(details)))
            db.execute("DELETE FROM client_events WHERE id NOT IN (SELECT id FROM client_events ORDER BY id DESC LIMIT 2000)")

    def events(self) -> list[dict]:
        with self.connection() as db:
            rows = db.execute("SELECT at,component,event,details FROM client_events ORDER BY id DESC LIMIT 100").fetchall()
        return [{**dict(row), "details": json.loads(row["details"])} for row in rows]
