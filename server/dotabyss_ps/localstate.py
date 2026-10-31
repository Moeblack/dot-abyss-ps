from __future__ import annotations

import hashlib
import threading
from pathlib import Path

import msgpack

from .masterdata import MasterCatalog, MasterDataError, canonical_json, unique_map, validate_schema
from .newgame import POLICY_VERSION, REQUIRED_TABLES, SUPPORTED_CLIENTS, NewGameError, build_initial_state, home_contents, login_announcement_contents, user_contents
from .protocol import decode_json, validate_data
from .storage import GameStateError, Store
from .gameplay import Gameplay, prepare_progress
from .gameplay_catalog import GAMEPLAY_TABLES, GameplayCatalog, GameplayError
from .gameplay_rules import load_rules
from .community import Community
from .gameplay_tutorial import initialize_tutorial_profile
from .event_master import EventMasterView

STATE_ROUTES = {"api/user": user_contents, "api/home": home_contents, "api/mypage/login": login_announcement_contents}


class LocalState:
    def __init__(self, store: Store, masters: MasterCatalog, schema_path: Path):
        if schema_path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("New game schema exceeds 2 MiB")
        self.schema = decode_json(schema_path.read_bytes())
        validate_schema(self.schema)
        if self.schema["client"] not in SUPPORTED_CLIENTS:
            raise ValueError("Local new game policy requires a verified client package, version and metadata hash")
        self.schema_hash = hashlib.sha256(canonical_json(self.schema)).hexdigest()
        self.store = store
        self.masters = masters
        self.event_master = EventMasterView(masters, self.schema, self.store.path.parent / "gameplay-rules.json") if self.schema["client"]["package"] == "dotabyss_x_cl" else None
        self._revision = None
        self._tables = None
        self._gameplay_catalog = None
        self._lock = threading.Lock()

    def _active(self) -> dict:
        record = self.store.master()
        if record is None:
            raise MasterDataError("An active master dataset is required for local game state")
        if record["client"] != self.schema["client"] or record["schema_sha256"] != self.schema_hash:
            raise MasterDataError("Active master does not match the configured new game schema")
        return record

    def _master_data(self, revision: str) -> tuple[dict, GameplayCatalog]:
        with self._lock:
            effective = self.event_master.current() if self.event_master else None
            cache_revision = effective[0]["revision"] if effective else revision
            if self._revision != cache_revision:
                _, payload = self.masters.read(revision)
                tables = effective[2] if effective else msgpack.unpackb(payload, raw=False, strict_map_key=True, object_pairs_hook=unique_map)
                if not isinstance(tables, dict):
                    raise MasterDataError("Master payload must be a table map")
                self._tables = {name: tables[name] for name in REQUIRED_TABLES if name in tables}
                self._gameplay_catalog = GameplayCatalog({name: tables[name] for name in GAMEPLAY_TABLES if name in tables}, self.schema)
                self._revision = cache_revision
            return self._tables, self._gameplay_catalog

    def client_master(self, user_id=None):
        if self.event_master is None:
            return None
        campaign_id = 0
        if user_id is not None:
            with self.store.connection() as db:
                row = db.execute("SELECT contents FROM gameplay_progress WHERE profile_id=?", (user_id,)).fetchone()
            if row:
                try:
                    data = decode_json(row[0].encode("utf-8"))
                    for key in ("features", "encounter", "data", "campaigns"):
                        data = data.get(key, {})
                    campaign_id = data.get("current_id", 0)
                    if type(campaign_id) is not int or not 0 <= campaign_id < 2 ** 63:
                        raise ValueError("Invalid campaign identifier")
                except (ValueError, TypeError, AttributeError) as error:
                    raise MasterDataError("Stored campaign selection is invalid") from error
        return self.event_master.current(campaign_id)

    def close(self):
        with self._lock:
            self._revision = self._tables = self._gameplay_catalog = None
            if self.event_master:
                self.event_master.clear()

    def gameplay(self, route: str, method: str, user_id: int, body: dict, operation_id: str = "") -> tuple[dict, dict]:
        if self.schema["client"]["package"] != "dotabyss_x_cl":
            raise GameplayError("Gameplay contracts require the verified PC client", 503, 5036)
        record = self._active()
        tables, catalog = self._master_data(record["revision"])
        created = False

        def create(identity: int, name: str, created_at: str) -> dict:
            nonlocal created
            created = True
            return build_initial_state(tables, self.schema, identity, name, created_at)

        def operate(state, progress, db):
            _, active_catalog = self._master_data(record["revision"])
            game = Gameplay(state, progress, active_catalog, user_id,
                            rules=load_rules(self.store.path.parent / "gameplay-rules.json"),
                            community=Community(db, record["revision"], user_id))
            if created:
                prepare_progress(progress, game.now)
                initialize_tutorial_profile(game)
            return game.execute(route, method, body, operation_id)

        return self.store.game_state(user_id, record["revision"], create,
                                     transaction_operation=operate)

    def contents(self, route: str, user_id: int) -> tuple[dict, dict]:
        record = self._active()
        tables, catalog = self._master_data(record["revision"])
        created = False

        def create(identity: int, name: str, created_at: str) -> dict:
            nonlocal created
            created = True
            state = build_initial_state(tables, self.schema, identity, name, created_at)
            validate_data(state)
            return state

        def visit(state, progress, db):
            _, active_catalog = self._master_data(record["revision"])
            game = Gameplay(state, progress, active_catalog, user_id,
                            rules=load_rules(self.store.path.parent / "gameplay-rules.json"),
                            community=Community(db, record["revision"], user_id))
            if created:
                prepare_progress(progress, game.now)
                initialize_tutorial_profile(game)
            return game.visit(STATE_ROUTES[route], route == "api/home", route == "api/mypage/login")

        if self.schema["client"]["package"] == "dotabyss_x_cl":
            return self.store.game_state(user_id, record["revision"], create,
                                         transaction_operation=visit)

        state, runtime = self.store.game_state(user_id, record["revision"], create)
        try:
            validate_data(state)
        except (ValueError, TypeError, RecursionError) as exc:
            raise GameStateError("Stored game state is corrupt") from exc
        if not isinstance(state, dict) or state.get("policy_version") != POLICY_VERSION:
            raise GameStateError("Stored game state requires a supported policy migration")
        try:
            contents = STATE_ROUTES[route](state)
        except NewGameError as exc:
            raise GameStateError("Stored game state is corrupt: " + str(exc)) from exc
        owners = [row["user_id"] for collection in contents["records"].values()
                  for row in collection["up"] if "user_id" in row]
        if not owners or any(owner != user_id for owner in owners):
            raise GameStateError("Stored game state does not belong to the authenticated profile")
        return contents, runtime

    def status(self) -> dict:
        try:
            active = self._active()
            error = ""
        except MasterDataError as exc:
            active, error = None, str(exc)
        return {"enabled": True, "policy_version": POLICY_VERSION, "master_revision": active["revision"] if active else None,
                "error": error, "device_verified": False, **self.store.game_state_summary()}
