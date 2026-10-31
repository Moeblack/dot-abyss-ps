from __future__ import annotations

import ipaddress
import re
import secrets
import threading
import time
from pathlib import Path

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.staticfiles import StaticFiles

from .admin_service import AdminError, AdminService
from .admin_profiles import ProfileError
from .admin_launcher import GameLauncher
from .gameplay_catalog import GameplayError
from .masterdata import MasterDataError
from .newgame import NewGameError
from .protocol import decode_json, validate_data
from .storage import GameStateError


def load_admin_token(path):
    try:
        with path.open("x", encoding="ascii") as stream:
            stream.write(secrets.token_urlsafe(36))
    except FileExistsError:
        pass
    token = path.read_text(encoding="ascii").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token):
        raise ValueError("Invalid admin-token.txt; restore a valid local console credential")
    return token


def install_admin(app, store, local_state):
    service = AdminService(store, local_state)
    app.state.admin = service
    launcher = GameLauncher(store, local_state)
    app.state.launcher = launcher
    token = load_admin_token(store.path.parent / "admin-token.txt")
    router = APIRouter()
    handoffs, browsers = {}, {}
    auth_lock = threading.Lock()
    cookie_name = "dotabyss_console_" + store.digest(token)[:12]

    @app.exception_handler(AdminError)
    async def failure(request, error):
        return JSONResponse({"error": {"code": error.code, "message": str(error)}}, status_code=error.status)

    def authorize(request):
        host = request.client.host if request.client else ""
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = host == "testclient"
        if not loopback:
            raise AdminError("管理接口仅允许本机控制台访问", 403, "local_only")
        supplied = request.headers.get("x-admin-token", "")
        origin, site = request.headers.get("origin"), request.headers.get("sec-fetch-site")
        if supplied:
            if origin or site:
                raise AdminError("管理凭据不能从外部网页使用", 403, "local_only")
            if secrets.compare_digest(token, supplied):
                return
        elif request.headers.get("x-console-request") == "1":
            if (origin and origin != str(request.base_url).rstrip("/")) or site not in (None, "same-origin", "none"):
                raise AdminError("控制台来源不匹配", 403, "invalid_origin")
            with auth_lock:
                expires = browsers.get(request.cookies.get(cookie_name, ""), 0)
            if expires > time.time():
                return
        raise AdminError("控制台凭据无效，请重新打开控制台", 401, "unauthorized")

    def identity(request):
        session, api = request.headers.get("x-olg-session", ""), request.headers.get("api-token", "")
        result = store.authenticate(session, api) if session and api else None
        if result is None:
            raise AdminError("需要有效的本地游戏会话", 401, "unauthorized")
        return result["user_id"]

    def invoke(function, *args, **kwargs):
        try:
            return function(*args, **kwargs)
        except AdminError:
            raise
        except ProfileError as error:
            raise AdminError(str(error), error.status, str(error.code)) from error
        except (GameplayError, MasterDataError, NewGameError, GameStateError, ValueError, TypeError, KeyError) as error:
            raise AdminError(str(error), 400, "validation_failed") from error

    def launcher_call(function, *args):
        try:
            result = function(*args)
        except GameplayError as error:
            raise AdminError(str(error), error.status, str(error.code)) from error
        for prefix in ("", "active_", "configured_"):
            identifier = result.get(prefix + "profile_id")
            if identifier is not None:
                try:
                    profile = service.profile_manager.get(identifier)
                    result[prefix + "profile_key"] = profile["name"]
                    result[prefix + "profile_name"] = profile["label"]
                except ProfileError:
                    pass
        return result

    async def body(request, fields):
        if request.headers.get("content-encoding", "identity").lower() != "identity":
            raise AdminError("不支持压缩请求", 415)
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 6 * 1024 * 1024:
                raise AdminError("请求超过大小限制", 413)
        try:
            data = decode_json(raw or b"{}")
            validate_data(data)
        except (ValueError, TypeError, RecursionError) as error:
            raise AdminError("JSON 参数无效") from error
        if not isinstance(data, dict) or set(data) - set(fields):
            raise AdminError("请求包含未知参数")
        return data

    def page(request):
        try:
            offset, limit = int(request.query_params.get("offset", "0")), int(request.query_params.get("limit", "100"))
        except ValueError as error:
            raise AdminError("分页参数无效") from error
        query = request.query_params.get("q", "")
        if not 0 <= offset <= 500000 or not 1 <= limit <= 500 or len(query) > 200:
            raise AdminError("分页参数超出范围")
        return offset, limit, query

    @router.get("/admin/status")
    def status(request: Request):
        authorize(request)
        return invoke(service.status)

    @router.post("/admin/ui-ticket")
    def ui_ticket(request: Request):
        authorize(request)
        with auth_lock:
            now = time.time()
            for key in list(handoffs):
                if handoffs[key] <= now:
                    del handoffs[key]
            if len(handoffs) >= 20:
                raise AdminError("待打开的控制台过多，请稍后重试", 429)
            handoff = secrets.token_urlsafe(36)
            handoffs[handoff] = now + 60
        return {"ticket": handoff}

    @router.post("/console/auth")
    async def browser_auth(request: Request):
        if request.headers.get("origin") != str(request.base_url).rstrip("/") or request.headers.get("x-console-request") != "1":
            raise AdminError("控制台来源不匹配", 403)
        data = await body(request, ("ticket",))
        ticket_value = data.get("ticket")
        if not isinstance(ticket_value, str) or len(ticket_value) > 128:
            raise AdminError("窗口凭据无效", 401)
        with auth_lock:
            if handoffs.pop(ticket_value, 0) <= time.time():
                raise AdminError("窗口凭据已过期或已使用，请重新打开控制台", 401)
            for key in list(browsers):
                if browsers[key] <= time.time():
                    del browsers[key]
            if len(browsers) >= 20:
                raise AdminError("控制台窗口过多", 429)
            session = secrets.token_urlsafe(36)
            browsers[session] = time.time() + 8 * 3600
        response = JSONResponse({"status": "ok"})
        response.set_cookie(cookie_name, session, max_age=8 * 3600, httponly=True, samesite="strict", path="/admin")
        response.headers["Cache-Control"] = "no-store"
        return response

    @router.get("/admin/profiles")
    def profiles(request: Request):
        authorize(request)
        return invoke(service.profiles)

    @router.get("/admin/launcher/status")
    def launcher_status(request: Request):
        authorize(request)
        return invoke(launcher_call, launcher.status)

    @router.post("/admin/launcher/start")
    async def start_game(request: Request):
        authorize(request)
        data = await body(request, ("profile_id",))
        return await run_in_threadpool(invoke, launcher_call, launcher.start, data.get("profile_id"), str(request.base_url))

    @router.post("/admin/profiles")
    async def create_profile(request: Request):
        authorize(request)
        data = await body(request, ("label", "player_name", "operation_id"))
        return await run_in_threadpool(invoke, service.profile_manager.create, data.get("label"), data.get("player_name"), data.get("operation_id"))

    @router.post("/admin/profiles/{profile_id}/label")
    async def rename_profile(profile_id: int, request: Request):
        authorize(request)
        data = await body(request, ("label", "expected_label"))
        return await run_in_threadpool(invoke, service.profile_manager.rename, profile_id, data.get("label"), data.get("expected_label"))

    @router.get("/admin/schema")
    def schema(request: Request):
        authorize(request)
        return invoke(service.schema)

    @router.get("/admin/profiles/{profile_id}/snapshot")
    def snapshot(profile_id: int, request: Request):
        authorize(request)
        return invoke(service.snapshot, profile_id)

    @router.get("/admin/profiles/{profile_id}/export")
    def export(profile_id: int, request: Request):
        authorize(request)
        return {"format": "dotabyss-console-save-1", **invoke(service.snapshot, profile_id, False)}

    @router.get("/admin/profiles/{profile_id}/history")
    def history(profile_id: int, request: Request):
        authorize(request)
        return invoke(service.journal.history, profile_id)

    @router.post("/admin/profiles/{profile_id}/preview")
    async def preview(profile_id: int, request: Request):
        authorize(request)
        data = await body(request, ("operation", "args", "expected_version"))
        return await run_in_threadpool(invoke, service.preview, profile_id, data.get("operation"), data.get("args", {}), data.get("expected_version"))

    @router.post("/admin/profiles/{profile_id}/apply")
    async def apply(profile_id: int, request: Request):
        authorize(request)
        data = await body(request, ("ticket",))
        return await run_in_threadpool(invoke, service.apply, profile_id, data.get("ticket"))

    @router.post("/admin/profiles/{profile_id}/backup")
    async def backup(profile_id: int, request: Request):
        authorize(request)
        data = await body(request, ("expected_version",))
        return await run_in_threadpool(invoke, service.backup, profile_id, data.get("expected_version"))

    @router.get("/admin/rules")
    def rules(request: Request):
        authorize(request)
        return invoke(service.rules)

    @router.post("/admin/rules/preview")
    async def preview_rules(request: Request):
        authorize(request)
        data = await body(request, ("changes", "expected_version"))
        return await run_in_threadpool(invoke, service.preview_rules, data.get("changes"), data.get("expected_version"))

    @router.post("/admin/rules/apply")
    async def apply_rules(request: Request):
        authorize(request)
        data = await body(request, ("ticket",))
        return await run_in_threadpool(invoke, service.apply_config, "rules", data.get("ticket"))

    @router.get("/admin/catalog")
    def catalog(request: Request):
        authorize(request)
        return invoke(service.catalog, request.query_params.get("table", "m_characters"), *page(request))

    @router.get("/admin/master/tables")
    def tables(request: Request):
        authorize(request)
        return invoke(service.master_tables)

    @router.get("/admin/master/table/{name}")
    def table(name: str, request: Request):
        authorize(request)
        return invoke(service.master_table, name, *page(request))

    @router.post("/admin/master/preview")
    async def preview_master(request: Request):
        authorize(request)
        data = await body(request, ("table", "index", "changes", "expected_version"))
        return await run_in_threadpool(invoke, service.preview_master, data.get("table"), data.get("index"), data.get("changes"), data.get("expected_version"))

    @router.post("/admin/master/apply")
    async def apply_master(request: Request):
        authorize(request)
        data = await body(request, ("ticket",))
        return await run_in_threadpool(invoke, service.apply_config, "master", data.get("ticket"))

    @router.get("/local/admin-sync")
    def sync(request: Request):
        profile_id = identity(request)
        try:
            after = int(request.query_params.get("after", "0"))
        except ValueError as error:
            raise AdminError("Invalid synchronization revision") from error
        client_id = request.query_params.get("client_id", "")
        if not 0 <= after < 2 ** 63 or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", client_id):
            raise AdminError("Invalid synchronization identity")
        result = invoke(service.journal.sync, profile_id, after)
        invoke(service.journal.touch, profile_id, client_id)
        if local_state and local_state.event_master:
            current = invoke(local_state.client_master, profile_id)
            if current:
                result["resource_version"] = current[0]["revision"]
        return result

    @router.post("/local/admin-sync/ack")
    async def ack(request: Request):
        profile_id = identity(request)
        data = await body(request, ("client_id", "revision", "status", "scene", "reason"))
        client_id, revision, status = data.get("client_id", ""), data.get("revision"), data.get("status")
        if (not isinstance(client_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", client_id)
                or type(revision) is not int or not 0 <= revision < 2 ** 63
                or status not in ("applied", "waiting_safe_point", "relogin_required", "failed")):
            raise AdminError("Invalid synchronization acknowledgement")
        scene, reason = data.get("scene", ""), data.get("reason", "")
        if not isinstance(scene, str) or len(scene) > 128 or not isinstance(reason, str) or len(reason) > 500:
            raise AdminError("Invalid synchronization status")
        return await run_in_threadpool(invoke, service.journal.ack, profile_id, client_id, revision, status, scene, reason)

    app.include_router(router)
    web_root = Path(__file__).resolve().parents[2] / "console" / "web"
    if web_root.is_dir():
        app.mount("/console", StaticFiles(directory=web_root, html=True), name="console")
