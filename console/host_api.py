from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import re
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.staticfiles import StaticFiles

from .client import ConsoleError, service_origin

HOST_WEB_DIR = Path(__file__).resolve().parent / "host_web"
DEMO_WEB_DIR = Path(__file__).resolve().parent / "web"
TICKET_TTL = 60
SESSION_TTL = 8 * 3600
AUTH_LIMIT = 20
BODY_LIMIT = 4096
logger = logging.getLogger(__name__)


def create_host_app(manager, native_secret, translation=None):
    if not isinstance(native_secret, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", native_secret):
        raise ValueError("Invalid native launcher credential")
    game_origin = service_origin(manager.origin)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.origin = None
    tickets, sessions = {}, {}
    auth_lock = threading.Lock()
    cookie_name = "dotabyss_host_" + hashlib.sha256(native_secret.encode("ascii")).hexdigest()[:12]

    def error_response(message, status=400):
        status = status if isinstance(status, int) and 400 <= status <= 599 else 503
        return JSONResponse({"error": {"message": str(message).replace(native_secret, "[凭据]")}}, status_code=status)

    @app.exception_handler(ConsoleError)
    async def console_error(request, error):
        return error_response(str(error), error.status)

    def configured_origin():
        value = app.state.origin
        if not isinstance(value, str):
            raise ConsoleError("启动器地址尚未就绪", 503)
        parsed = urlsplit(value)
        try:
            port = parsed.port
        except ValueError as error:
            raise ConsoleError("启动器地址无效", 503) from error
        if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or port is None
                or not 1 <= port <= 65535 or parsed.netloc != f"127.0.0.1:{port}"
                or parsed.path or parsed.query or parsed.fragment):
            raise ConsoleError("启动器地址无效", 503)
        return value, parsed.netloc

    @app.middleware("http")
    async def local_request(request, call_next):
        try:
            _, authority = configured_origin()
            hosts = [value.decode("latin1") for key, value in request.scope["headers"] if key.lower() == b"host"]
            if hosts != [authority]:
                raise ConsoleError("启动器请求地址不匹配", 403)
            try:
                local = request.client is not None and ipaddress.ip_address(request.client.host).is_loopback
            except ValueError:
                local = False
            if not local:
                raise ConsoleError("启动器仅允许本机访问", 403)
            response = await call_next(request)
        except ConsoleError as error:
            response = error_response(str(error), error.status)
        demo = request.url.path.startswith("/demo-console/")
        ancestors = "'self'" if demo else "'none'"
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "SAMEORIGIN" if demo else "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; "
            f"frame-src 'self' {game_origin}; frame-ancestors {ancestors}"
        )
        return response

    def native_authorized(request):
        supplied = request.headers.get("x-host-token", "")
        if not supplied:
            return False
        if any(name == "origin" or name.startswith("sec-fetch-") for name in request.headers):
            raise ConsoleError("启动器原生凭据不能从网页使用", 403)
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", supplied) or not secrets.compare_digest(native_secret, supplied):
            raise ConsoleError("启动器凭据无效", 401)
        return True

    def browser_request(request, require_origin=False):
        expected, _ = configured_origin()
        origin = request.headers.get("origin")
        if request.headers.get("x-console-request") != "1":
            raise ConsoleError("启动器网页请求标记无效", 403)
        if (require_origin and origin != expected) or (origin is not None and origin != expected):
            raise ConsoleError("启动器网页来源不匹配", 403)
        if request.headers.get("sec-fetch-site") not in (None, "same-origin", "none"):
            raise ConsoleError("启动器网页来源不匹配", 403)

    def prune(now):
        for values in (tickets, sessions):
            for token, expires in list(values.items()):
                if expires <= now:
                    del values[token]

    def authorize(request):
        if native_authorized(request):
            return
        browser_request(request)
        with auth_lock:
            now = time.monotonic()
            prune(now)
            if sessions.get(request.cookies.get(cookie_name, ""), 0) > now:
                return
        raise ConsoleError("启动器会话已失效，请重新打开窗口", 401)

    async def read_body(request, fields=()):
        if request.headers.get("content-encoding", "identity").lower() != "identity":
            raise ConsoleError("不支持压缩的启动器请求", 415)
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > BODY_LIMIT:
                raise ConsoleError("启动器请求超过大小限制", 413)

        def unique(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError("Duplicate field")
                result[key] = value
            return result

        def invalid_constant(value):
            raise ValueError("Non-finite value")

        try:
            data = json.loads(raw or b"{}", object_pairs_hook=unique, parse_constant=invalid_constant)
        except (ValueError, UnicodeError, RecursionError) as error:
            raise ConsoleError("启动器 JSON 参数无效", 400) from error
        if not isinstance(data, dict) or set(data) - set(fields):
            raise ConsoleError("启动器请求包含未知参数", 400)
        return data

    async def invoke(function):
        try:
            return await run_in_threadpool(function)
        except ConsoleError:
            raise
        except Exception as error:
            logger.exception("Launcher operation failed")
            raise ConsoleError("启动器操作失败，请查看启动器日志", 500) from error

    @app.get("/host/ping")
    async def ping():
        return {"service": "dotabyss-launcher", "version": 1}

    @app.post("/host/ticket")
    async def ticket(request: Request):
        if not native_authorized(request):
            raise ConsoleError("需要启动器原生凭据", 401)
        await read_body(request)
        with auth_lock:
            now = time.monotonic()
            prune(now)
            if len(tickets) >= AUTH_LIMIT:
                raise ConsoleError("待打开的启动器窗口过多，请稍后重试", 429)
            value = secrets.token_urlsafe(36)
            tickets[value] = now + TICKET_TTL
        return {"ticket": value}

    @app.post("/host/auth")
    async def authenticate(request: Request):
        browser_request(request, require_origin=True)
        data = await read_body(request, ("ticket",))
        value = data.get("ticket")
        if not isinstance(value, str) or not 16 <= len(value) <= 128:
            raise ConsoleError("启动器窗口票据无效", 401)
        with auth_lock:
            now = time.monotonic()
            prune(now)
            if tickets.pop(value, 0) <= now:
                raise ConsoleError("启动器窗口票据已过期或已使用", 401)
            if len(sessions) >= AUTH_LIMIT:
                raise ConsoleError("启动器会话过多，请稍后重试", 429)
            session = secrets.token_urlsafe(36)
            sessions[session] = now + SESSION_TTL
        response = JSONResponse({"status": "ok"})
        response.set_cookie(cookie_name, session, max_age=SESSION_TTL, httponly=True, samesite="strict", path="/host")
        return response

    @app.get("/host/status")
    async def status(request: Request):
        authorize(request)
        return await invoke(manager.status)

    @app.get("/host/logs")
    async def logs(request: Request):
        authorize(request)
        return await invoke(manager.logs)

    @app.post("/host/service/start")
    async def start(request: Request):
        authorize(request)
        await read_body(request)
        return await invoke(manager.start)

    @app.post("/host/service/stop")
    async def stop(request: Request):
        authorize(request)
        await read_body(request)
        return await invoke(manager.stop)

    @app.post("/host/service/restart")
    async def restart(request: Request):
        authorize(request)
        await read_body(request)
        return await invoke(manager.restart)

    @app.post("/host/console-ticket")
    async def console_ticket(request: Request):
        authorize(request)
        await read_body(request)
        return await invoke(manager.console_ticket)

    def translation_service():
        if translation is None:
            raise ConsoleError("启动器尚未加载翻译管理，请重新打开最新版启动器。", 503)
        return translation

    @app.get("/host/translation/status")
    async def translation_status(request: Request):
        authorize(request)
        return await invoke(translation_service().status)

    @app.get("/host/translation/logs")
    async def translation_logs(request: Request):
        authorize(request)
        return await invoke(translation_service().logs)

    @app.post("/host/translation/install", status_code=202)
    async def translation_install(request: Request):
        authorize(request)
        await read_body(request)
        return await invoke(translation_service().install)

    app.mount("/demo-console", StaticFiles(directory=DEMO_WEB_DIR, html=True, check_dir=False), name="demo-console")
    app.mount("/", StaticFiles(directory=HOST_WEB_DIR, html=True, check_dir=False), name="host-web")
    return app
