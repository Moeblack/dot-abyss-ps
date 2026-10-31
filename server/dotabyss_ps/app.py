from __future__ import annotations

import argparse
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import msgpack
import uvicorn
from fastapi import FastAPI, Request
from starlette.responses import FileResponse, JSONResponse, Response

from . import __version__
from .protocol import decode_json, encode_response, envelope, sanitize, validate_data
from .storage import GameStateError, Store
from .masterdata import MasterCatalog, MasterDataError
from .localstate import LocalState, STATE_ROUTES
from .newgame import NewGameError
from .gameplay import GAMEPLAY_METHODS, BLOCKED_ROUTES, is_gameplay_path
from .gameplay_catalog import GameplayError
from .assets import AssetCatalog, AssetDataError, MissingAssetError, safe_relative
from .admin_routes import install_admin

MAX_BODY_BYTES = 2 * 1024 * 1024
BOOTSTRAP_ROUTES = ("api/user", "api/home", "api/mypage/login")
RUNTIME_KEYS = {"application_version", "app_version_code", "bundle_version", "server_version", "resource_version"}


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    fixtures_dir: Path
    resources_dir: Path
    login_path: str | None = None
    session_ttl: int = 86400
    new_game_schema: Path | None = None

    @classmethod
    def from_directory(cls, root: Path) -> "Settings":
        return cls(root, root / "fixtures", root / "resources")


class RequestFailure(Exception):
    def __init__(self, status: int, code: int, message: str):
        self.status, self.code, self.message = status, code, message


def contained_path(root: Path, relative: str, suffix: str = "") -> Path:
    parts = relative.split("/")
    if not relative or any(not part or part in (".", "..") or not re.fullmatch(r"[\w.\-]+", part, flags=re.ASCII) for part in parts):
        raise RequestFailure(400, 4001, "Invalid resource path")
    candidate = root.joinpath(*parts)
    if suffix:
        candidate = candidate.with_name(candidate.name + suffix)
    candidate = candidate.resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise RequestFailure(400, 4001, "Resource path leaves configured directory")
    return candidate


async def request_body(request: Request) -> dict:
    if request.headers.get("content-encoding", "identity").lower() != "identity":
        raise RequestFailure(415, 4151, "Compressed or encrypted transport is not enabled; use the local plaintext adapter")
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > MAX_BODY_BYTES:
            raise RequestFailure(413, 4130, "Request exceeds 2 MiB")
    if not raw:
        return {}
    content_type = request.headers.get("content-type", "application/json").split(";", 1)[0].strip().lower()
    try:
        if content_type == "application/x-msgpack":
            body = msgpack.unpackb(raw, raw=False, strict_map_key=True)
            validate_data(body)
        elif content_type == "application/json":
            body = decode_json(raw)
        else:
            raise RequestFailure(415, 4150, "Only plaintext JSON and MessagePack are supported")
    except (ValueError, TypeError, UnicodeError, msgpack.UnpackException, RecursionError) as exc:
        raise RequestFailure(400, 4000, "Malformed request payload") from exc
    if not isinstance(body, dict) or any(not isinstance(key, str) for key in body):
        raise RequestFailure(400, 4000, "Request payload must be an object with string keys")
    request.state.request_keys = sorted(body)[:100]
    return body


def load_fixture(path: Path) -> dict:
    try:
        if path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("Fixture exceeds 16 MiB")
        fixture = decode_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as exc:
        raise RequestFailure(503, 5032, "Invalid fixture JSON") from exc
    if not isinstance(fixture, dict) or not isinstance(fixture.get("contents"), dict):
        raise RequestFailure(503, 5032, "Fixture must contain a contents object")
    return fixture


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_directory(Path("data"))
    for directory in (settings.data_dir, settings.fixtures_dir, settings.resources_dir):
        directory.mkdir(parents=True, exist_ok=True)
    if settings.login_path:
        if not settings.login_path.startswith("/") or settings.login_path.startswith(("/local", "/admin", "/console", "/diagnostics", "/healthz", "/resources", "/master", "/data")) or settings.login_path.lstrip("/") in (*BOOTSTRAP_ROUTES, "api/health-check", "maintenance") or is_gameplay_path(settings.login_path.lstrip("/")):
            raise ValueError("Login alias must be a non-reserved absolute path")
        try:
            contained_path(settings.data_dir, settings.login_path[1:])
        except RequestFailure as exc:
            raise ValueError("Invalid login alias") from exc
    store = Store(settings.data_dir / "local.sqlite3", settings.session_ttl)
    masters = MasterCatalog(settings.data_dir, store)
    assets = AssetCatalog(settings.data_dir, store)
    local_state = LocalState(store, masters, settings.new_game_schema) if settings.new_game_schema else None
    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            if local_state:
                local_state.close()

    app = FastAPI(title="Dot Abyss Local Server", version=__version__, docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.store = store
    app.state.settings = settings

    def game_response(request: Request, contents: Any, *, status: int = 200, error: tuple[int, str] | None = None, token: str = "", headers: dict | None = None, runtime: dict | None = None, profile_id: int | None = None):
        runtime = dict(store.runtime() if runtime is None else runtime)
        if error is None and local_state and local_state.event_master:
            try:
                if profile_id is None:
                    session, api_token = request.headers.get("x-olg-session", ""), request.headers.get("api-token", "")
                    identity = store.authenticate(session, api_token) if session and api_token else None
                    profile_id = identity["user_id"] if identity else None
                effective = local_state.client_master(profile_id)
                if effective:
                    runtime["resource_version"] = effective[0]["revision"]
            except (MasterDataError, GameplayError) as exc:
                raise RequestFailure(503, 5036, str(exc)) from exc
        return encode_response(envelope(contents, runtime, token, error=error), accept=request.headers.get("accept", ""), status=status, headers=headers)

    def local_resource(path: str, request: Request):
        if path.startswith("resources/"):
            try:
                safe_relative(path)
            except AssetDataError as exc:
                raise RequestFailure(400, 4001, "Invalid resource path") from exc
            try:
                asset = assets.read(path)
            except MissingAssetError as exc:
                raise RequestFailure(404, 4044, str(exc)) from exc
            except AssetDataError as exc:
                raise RequestFailure(503, 5035, str(exc)) from exc
            if asset is not None:
                entry, payload, revision = asset
                etag = '"' + entry["sha256"] + '"'
                headers = {"ETag": etag, "X-Asset-Revision": revision, "Cache-Control": entry.get("cache_control", "no-cache")}
                candidates = [part.strip().removeprefix("W/") for part in request.headers.get("if-none-match", "").split(",")]
                if etag in candidates or "*" in candidates:
                    return Response(status_code=304, headers=headers)
                return Response(payload, media_type=entry["media_type"], headers=headers)
        resource = contained_path(settings.resources_dir, path)
        if not resource.is_file():
            raise RequestFailure(404, 4042, "Missing local resource: " + path[:180])
        return FileResponse(resource)

    @app.middleware("http")
    async def audit(request: Request, call_next):
        request.state.request_keys = []
        request.state.reason = ""
        response = await call_next(request)
        if request.url.path.startswith(("/admin/", "/local/admin-sync", "/console/auth")):
            response.headers["Cache-Control"] = "no-store"
        if request.url.path not in ("/healthz", "/diagnostics"):
            store.observe(request.method, request.url.path[:300], response.status_code, request.state.request_keys, request.state.reason)
        return response

    @app.exception_handler(RequestFailure)
    async def failed(request: Request, error: RequestFailure):
        request.state.reason = error.message
        return game_response(request, {}, status=error.status, error=(error.code, error.message))

    @app.get("/healthz")
    async def health():
        return {"status": "ok", "service": "dotabyss-local", "version": __version__, "device_verified": False}

    @app.get("/diagnostics")
    async def diagnostics():
        fixtures = {}
        for route in BOOTSTRAP_ROUTES:
            state = "missing"
            try:
                candidate = contained_path(settings.fixtures_dir, route, ".json")
                if candidate.is_file():
                    load_fixture(candidate)
                    state = "available_unverified"
            except RequestFailure:
                state = "invalid"
            fixtures["/" + route] = state
        runtime = store.runtime()
        return {
            "device_verified": False,
            "bootstrap_ready": False,
            "runtime": runtime,
            "runtime_registered": bool(runtime.get("application_version") and runtime.get("app_version_code") and runtime.get("bundle_version")),
            "login_alias": settings.login_path,
            "fixtures": fixtures,
            "route_sources": {"/" + route: "persistent_local_state" if local_state and route in STATE_ROUTES else "fixture" for route in BOOTSTRAP_ROUTES},
            "resource_files": sum(1 for file in settings.resources_dir.rglob("*") if file.is_file()),
            "observations": store.observations(),
            "client_events": store.events(),
            "master": store.master(),
            "assets": assets.status(),
            "local_state": local_state.status() if local_state else {"enabled": False},
            "gameplay_routes": {"/" + route: {"method": method, "blocked_reason": BLOCKED_ROUTES.get(route)} for route, method in GAMEPLAY_METHODS.items()} if local_state else {},
            "limitations": ["Gameplay and event handlers require native client verification", "Fixtures require matching master data and platform assets", "Server-only rules use documented local policies; missing event backgrounds still require recovery"],
        }

    @app.post("/local/runtime")
    async def register_runtime(request: Request):
        body = await request_body(request)
        if not body or set(body) - RUNTIME_KEYS or any(not isinstance(value, str) or not value or len(value) > 128 for value in body.values()):
            raise RequestFailure(400, 4002, "Runtime values must be nonempty version strings using supported keys")
        try:
            runtime = store.update_runtime(body)
        except ValueError as exc:
            raise RequestFailure(409, 4090, str(exc)) from exc
        return JSONResponse({"status": "ok", "runtime": runtime})

    @app.get("/local/master")
    async def active_master():
        return JSONResponse({"active": store.master()})

    @app.get("/data/{revision}")
    @app.get("/local/master/{revision}/first.msgpack")
    async def master_payload(revision: str, request: Request):
        try:
            original = store.master(revision)
            effective = local_state.event_master.read(revision) if original is None and local_state and local_state.event_master else None
            if effective is None and original is None:
                if request.url.path.startswith("/data/"):
                    return local_resource("data/" + revision, request)
                raise RequestFailure(404, 4043, "Unknown master revision")
            record, payload = effective[:2] if effective else masters.read(revision)
        except (MasterDataError, GameplayError) as exc:
            raise RequestFailure(503, 5033, str(exc)) from exc
        etag = f'"{record["sha256"]}"'
        headers = {"ETag": etag, "X-Resource-Version": revision, "Cache-Control": "public, max-age=31536000, immutable"}
        candidates = [part.strip().removeprefix("W/") for part in request.headers.get("if-none-match", "").split(",")]
        if etag in candidates or "*" in candidates:
            return Response(status_code=304, headers=headers)
        return Response(payload, media_type="application/x-msgpack", headers=headers)

    @app.post("/local/diagnostics")
    async def client_event(request: Request):
        body = await request_body(request)
        if store.authenticate(request.headers.get("x-olg-session", ""), request.headers.get("api-token", "")) is None:
            raise RequestFailure(401, 4010, "A valid local session is required")
        if set(body) != {"component", "event", "details"} or not isinstance(body["details"], dict):
            raise RequestFailure(400, 4005, "Expected component, event and details")
        for field in ("component", "event"):
            if not isinstance(body[field], str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", body[field]):
                raise RequestFailure(400, 4005, "Invalid event name")
        store.add_event(body["component"], body["event"], sanitize(body["details"]))
        return JSONResponse({"status": "ok"})

    async def local_login(request: Request):
        body = await request_body(request)
        profile = body.get("profile", "default")
        if not isinstance(profile, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", profile):
            raise RequestFailure(400, 4003, "Invalid local profile name")
        if any(key in body for key in ("token", "token_object", "token_key_id", "password")):
            raise RequestFailure(400, 4004, "Use local profile bootstrap; external credentials are not accepted")
        identity = store.login(profile)
        token = identity.pop("api_token")
        identity["token_status"] = 0
        return game_response(request, identity, token=token, headers={"x-olg-session": identity["token"], "api-token": token}, profile_id=identity["user_id"])

    async def native_login(request: Request):
        body = await request_body(request)
        session = request.headers.get("x-olg-session", "")
        api_token = request.headers.get("api-token", "")
        identity = store.authenticate(session, api_token) if session and api_token else None
        if identity is None:
            raise RequestFailure(401, 4010, "A valid local session and API token are required")
        body_token = body.get("token")
        if body_token is not None and (not isinstance(body_token, str) or body_token not in ("", session)):
            raise RequestFailure(400, 4004, "Login token must match the local session")
        for field in ("token_object", "token_key_id", "password"):
            if body.get(field) not in (None, ""):
                raise RequestFailure(400, 4004, "External credentials are not accepted")
        if "token_status" in body and (type(body["token_status"]) is not int or not -(2 ** 31) <= body["token_status"] < 2 ** 31):
            raise RequestFailure(400, 4006, "Invalid login token status")
        agreements = body.get("terms_agreements")
        if agreements is not None and (not isinstance(agreements, list) or any(
            not isinstance(item, dict) or any(type(item.get(field)) is not int or not -(2 ** 31) <= item[field] < 2 ** 31
                                             for field in ("version", "terms_type"))
            for item in agreements
        )):
            raise RequestFailure(400, 4006, "Invalid login terms agreements")
        contents = {**identity, "token": session, "token_status": 0}
        return game_response(request, contents, token=api_token, headers={"x-olg-session": session, "api-token": api_token})

    app.add_api_route("/local/login", local_login, methods=["POST"])
    app.add_api_route("/login", native_login, methods=["POST"])
    if settings.login_path and settings.login_path != "/login":
        app.add_api_route(settings.login_path, native_login, methods=["POST"])

    @app.api_route("/maintenance", methods=["GET", "POST"])
    async def maintenance(request: Request):
        if request.method == "POST":
            await request_body(request)
        session = request.headers.get("x-olg-session", "")
        api_token = request.headers.get("api-token", "")
        valid_session = bool(session and api_token and store.authenticate(session, api_token))
        contents = {"maintenance_type": "", "message": {"title": "", "message": ""}, "is_debug_user": False, "configs": []}
        headers = {"x-olg-session": session, "api-token": api_token} if valid_session else None
        return game_response(request, contents, token=api_token if valid_session else "", headers=headers)

    @app.api_route("/api/health-check", methods=["GET", "POST"])
    async def game_health(request: Request):
        if request.method == "POST":
            await request_body(request)
        return game_response(request, {})

    install_admin(app, store, local_state)

    @app.api_route("/{path:path}", methods=["GET", "POST"])
    async def game_route(path: str, request: Request):
        if request.method == "GET" and path.startswith(("resources/", "master/", "data/")):
            return local_resource(path, request)
        body = await request_body(request) if request.method == "POST" else {}
        session = request.headers.get("x-olg-session", "")
        api_token = request.headers.get("api-token", "")
        identity = store.authenticate(session, api_token)
        if identity is None:
            raise RequestFailure(401, 4010, "A valid local session is required")
        if local_state and is_gameplay_path(path):
            if not session or not api_token:
                raise RequestFailure(401, 4010, "A valid local session and API token are required")
            try:
                contents, runtime = local_state.gameplay(path, request.method, identity["user_id"], body, request.headers.get("x-request-id", ""))
            except GameplayError as exc:
                raise RequestFailure(exc.status, exc.code, str(exc)) from exc
            except GameStateError as exc:
                raise RequestFailure(409, 4091, str(exc)) from exc
            except (MasterDataError, NewGameError) as exc:
                raise RequestFailure(503, 5034, str(exc)) from exc
            return game_response(request, contents, token=api_token, runtime=runtime)
        if local_state and path in STATE_ROUTES:
            try:
                contents, runtime = local_state.contents(path, identity["user_id"])
            except GameplayError as exc:
                raise RequestFailure(exc.status, exc.code, str(exc)) from exc
            except GameStateError as exc:
                raise RequestFailure(409, 4091, str(exc)) from exc
            except (MasterDataError, NewGameError) as exc:
                raise RequestFailure(503, 5034, str(exc)) from exc
            return game_response(request, contents, token=api_token, runtime=runtime)
        fixture_path = contained_path(settings.fixtures_dir, path, ".json")
        if not fixture_path.is_file():
            if path in BOOTSTRAP_ROUTES:
                raise RequestFailure(503, 5031, "Missing bootstrap fixture: " + path)
            raise RequestFailure(404, 4040, "Endpoint is not implemented: " + path[:180])
        fixture = load_fixture(fixture_path)
        return game_response(request, fixture["contents"], token=api_token)

    return app


def main():
    parser = argparse.ArgumentParser(description="Run the local Dot Abyss protocol server")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18081)
    parser.add_argument("--login-path", help="Add a verified client login alias that requires an existing local session")
    parser.add_argument("--new-game-schema", type=Path, help="Enable persistent local user/home state with a matching master schema")
    args = parser.parse_args()
    base = Settings.from_directory(args.data_dir.resolve())
    settings = Settings(base.data_dir, base.fixtures_dir, base.resources_dir, args.login_path, new_game_schema=args.new_game_schema)
    uvicorn.run(create_app(settings), host=args.host, port=args.port, access_log=False)


if __name__ == "__main__":
    main()
