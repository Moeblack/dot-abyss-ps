from __future__ import annotations

import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class ConsoleError(Exception):
    def __init__(self, message: str, status: int = 0, details=None):
        super().__init__(message)
        self.status = status
        self.details = details


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def service_origin(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ConsoleError("服务地址必须是本机 HTTP 地址，例如 http://127.0.0.1:18083")
    if parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ConsoleError("服务地址不能包含账号、路径或查询参数。")
    try:
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError
    except ValueError as exc:
        raise ConsoleError("服务端口无效。") from exc
    return f"http://{parsed.netloc}".rstrip("/")


def read_token(data_directory: str | Path) -> str:
    path = Path(data_directory) / "admin-token.txt"
    try:
        token = path.read_text(encoding="utf-8-sig").strip()
    except OSError as exc:
        raise ConsoleError(f"无法读取管理凭据：{path}\n请确认数据目录，并在完成服务更新后启动服务。") from exc
    if not token or len(token) > 4096 or any(char.isspace() for char in token):
        raise ConsoleError("管理凭据文件格式无效，请检查服务的数据目录。")
    return token


class AdminClient:
    def __init__(self, origin: str, token: str, timeout: float = 30):
        self.origin = service_origin(origin)
        if not token or any(char.isspace() for char in token):
            raise ConsoleError("管理凭据不能为空或包含空白字符。")
        self._token = token
        self.timeout = timeout
        self._opener = build_opener(ProxyHandler({}), NoRedirect())

    def request(self, method: str, path: str, body=None, **query):
        if not path.startswith("/admin/") or ".." in path or "?" in path or "#" in path:
            raise ConsoleError("管理接口路径无效。")
        url = self.origin + path
        if query:
            url += "?" + urlencode({key: value for key, value in query.items() if value is not None})
        headers = {"Accept": "application/json", "X-Admin-Token": self._token}
        payload = None
        if body is not None:
            payload = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = Request(url, data=payload, headers=headers, method=method)
        try:
            with self._opener.open(req, timeout=self.timeout) as response:
                return self._decode(response.read(64 * 1024 * 1024 + 1))
        except HTTPError as exc:
            try:
                details = self._decode(exc.read(1024 * 1024))
            except ConsoleError:
                details = None
            message = self._error_message(details, exc.code)
            raise ConsoleError(message.replace(self._token, "[凭据]"), exc.code, details) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ConsoleError("无法连接本地服务。请确认服务已启动、端口正确，并已加载控制台版本。") from exc

    @staticmethod
    def _decode(raw: bytes):
        if len(raw) > 64 * 1024 * 1024:
            raise ConsoleError("服务返回的数据过大，请缩小查询范围。")
        try:
            return json.loads(raw)
        except (UnicodeError, ValueError) as exc:
            raise ConsoleError("服务返回的内容不是有效 JSON，请检查服务版本与端口。") from exc

    @staticmethod
    def _error_message(details, status: int) -> str:
        if isinstance(details, dict):
            value = details.get("detail", details.get("message", details.get("error")))
            if isinstance(value, dict):
                value = value.get("message", json.dumps(value, ensure_ascii=False))
            if value:
                return str(value)
        return {
            401: "管理凭据不匹配，请重新选择服务数据目录。",
            403: "服务拒绝管理访问，请确认连接的是本机管理接口。",
            404: "服务还没有这个管理接口，请先更新并重启服务。",
            409: "数据已经变化，请重新读取并预览后再应用。",
            413: "提交的数据过大，请减小批量范围。",
            422: "填写的数据不符合要求，请检查字段。",
        }.get(status, f"服务请求失败（HTTP {status}），请查看服务日志。")

    def get(self, path: str, **query):
        return self.request("GET", path, **query)

    def post(self, path: str, body):
        return self.request("POST", path, body)
