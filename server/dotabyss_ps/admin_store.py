from __future__ import annotations

import json
import time

from .admin_master import fingerprint
from .gameplay_records import identity_field


def record_delta(before, after):
    result = {}
    for table in before.keys() | after.keys():
        field = identity_field(table)
        old = {row[field]: row for row in before.get(table, {}).get("up", [])}
        new = {row[field]: row for row in after.get(table, {}).get("up", [])}
        up = [row for key, row in new.items() if old.get(key) != row]
        down = sorted(old.keys() - new.keys())
        if up or down:
            result[table] = {"up": up, "down": down}
    return result


class AdminJournal:
    def __init__(self, store):
        self.store = store
        with store.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS admin_changes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_id INTEGER REFERENCES profiles(id),
                    ticket TEXT UNIQUE NOT NULL,
                    at INTEGER NOT NULL,
                    operation TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    timing TEXT NOT NULL,
                    before_data TEXT NOT NULL,
                    after_data TEXT NOT NULL,
                    delta TEXT NOT NULL,
                    changes_state INTEGER NOT NULL DEFAULT 1
                );
                CREATE INDEX IF NOT EXISTS admin_changes_profile ON admin_changes(profile_id,id);
                CREATE TABLE IF NOT EXISTS admin_clients (
                    profile_id INTEGER NOT NULL REFERENCES profiles(id),
                    client_id TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0,
                    reported_revision INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    scene TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    at INTEGER NOT NULL,
                    PRIMARY KEY(profile_id,client_id)
                );
            """)

    def append(self, db, profile_id, ticket, operation, summary, timing, before, after, delta, changes_state=True):
        cursor = db.execute("INSERT INTO admin_changes(profile_id,ticket,at,operation,summary,timing,before_data,after_data,delta,changes_state) VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (profile_id, ticket, int(time.time()), operation, summary, timing,
                             json.dumps(before, ensure_ascii=False, allow_nan=False),
                             json.dumps(after, ensure_ascii=False, allow_nan=False),
                             json.dumps(delta, ensure_ascii=False, allow_nan=False), int(changes_state)))
        return cursor.lastrowid

    @staticmethod
    def revision(db, profile_id):
        return db.execute("SELECT COALESCE(MAX(id),0) FROM admin_changes WHERE changes_state=1 AND (profile_id=? OR profile_id IS NULL)", (profile_id,)).fetchone()[0]

    def status(self, db, profile_id):
        revision = self.revision(db, profile_id)
        client = db.execute("SELECT * FROM admin_clients WHERE profile_id=? ORDER BY at DESC LIMIT 1", (profile_id,)).fetchone()
        result = {"revision": revision, "ack_revision": client["revision"] if client else 0,
                  "status": "pending" if revision else "unchanged", "client_connected": False}
        if client:
            result.update(scene=client["scene"], reason=client["reason"], last_seen=client["at"],
                          client_connected=time.time() - client["at"] < 20)
            if client["reported_revision"] == revision:
                result["status"] = client["status"]
        return result

    def history(self, profile_id, limit=100):
        with self.store.connection() as db:
            rows = db.execute("SELECT id,at,operation,summary,timing,changes_state FROM admin_changes WHERE profile_id=? OR profile_id IS NULL ORDER BY id DESC LIMIT ?",
                              (profile_id, limit)).fetchall()
        return {"entries": [{**dict(row), "revision": row["id"], "status": "saved" if row["changes_state"] else "backup"} for row in rows]}

    def touch(self, profile_id, client_id):
        with self.store.connection() as db:
            db.execute("""INSERT INTO admin_clients VALUES(?,?,0,0,'connected','','',?)
                       ON CONFLICT(profile_id,client_id) DO UPDATE SET at=excluded.at""", (profile_id, client_id, int(time.time())))

    def sync(self, profile_id, after):
        with self.store.connection() as db:
            db.execute("BEGIN")
            revision = self.revision(db, profile_id)
            if after > revision:
                after = 0
            changes = db.execute("SELECT delta,timing FROM admin_changes WHERE changes_state=1 AND (profile_id=? OR profile_id IS NULL) AND id>? ORDER BY id", (profile_id, after)).fetchall()
            row = db.execute("SELECT contents FROM game_states WHERE profile_id=?", (profile_id,)).fetchone()
            state = json.loads(row[0]) if row else {"records": {}}
            keys, relogin = {}, False
            for change in changes:
                relogin |= change["timing"] == "master"
                for table, values in json.loads(change["delta"]).items():
                    field = identity_field(table)
                    keys.setdefault(table, set()).update(values["down"])
                    keys[table].update(item[field] for item in values["up"])
            records = {}
            for table, ids in keys.items():
                field = identity_field(table)
                current = {row[field]: row for row in state["records"].get(table, {}).get("up", [])}
                records[table] = {"up": [current[key] for key in sorted(ids) if key in current], "down": sorted(ids - current.keys())}
            return {"revision": revision, "pending": revision > after, "refresh": "relogin" if relogin else "records",
                    "records": records, "reason": "主数据或存档已更换，请重新登录" if relogin else "", "version": fingerprint(state)}

    def ack(self, profile_id, client_id, revision, status, scene, reason):
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if revision > self.revision(db, profile_id):
                raise ValueError("Cannot acknowledge a future revision")
            applied = revision if status == "applied" else 0
            db.execute("""INSERT INTO admin_clients VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(profile_id,client_id) DO UPDATE SET
                       revision=MAX(admin_clients.revision,excluded.revision), reported_revision=excluded.reported_revision,
                       status=excluded.status,scene=excluded.scene,reason=excluded.reason,at=excluded.at""",
                       (profile_id, client_id, applied, revision, status, scene, reason, int(time.time())))
        return {"status": "ok"}
