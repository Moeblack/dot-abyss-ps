from __future__ import annotations

import argparse
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def check(base_url: str) -> dict:
    base_url = base_url.rstrip("/")

    def request(path: str, body: dict | None = None, headers: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        query = Request(base_url + path, data=data, headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urlopen(query, timeout=10) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    status, health = request("/healthz")
    if status != 200 or health.get("service") != "dotabyss-local":
        raise RuntimeError("The endpoint is not a healthy Dot Abyss local server")
    status, login = request("/local/login", {"profile": "connection-check"})
    if status != 200 or login.get("status") != "success":
        raise RuntimeError("Local session bootstrap failed")
    headers = {"x-olg-session": login["contents"]["token"], "api-token": login["api_token"]}
    status, event = request("/local/diagnostics", {"component": "host-probe", "event": "transport_checked", "details": {"scope": "local-http-session"}}, headers)
    if status != 200 or event.get("status") != "ok":
        raise RuntimeError("The local session was not accepted")
    status, home = request("/api/home", {}, headers)
    if status not in (200, 409, 503):
        raise RuntimeError(f"Unexpected home response: HTTP {status}")
    _, diagnostics = request("/diagnostics")
    return {"local_http": "passed", "local_session": "passed", "home_http_status": status, "home_error": home.get("errors", {}).get("message", ""), "fixtures": diagnostics["fixtures"], "local_state": diagnostics.get("local_state", {"enabled": False}), "device_verified": diagnostics["device_verified"]}


def main():
    parser = argparse.ArgumentParser(description="Verify local transport and session bootstrap without printing tokens")
    parser.add_argument("--url", default="http://127.0.0.1:18081")
    args = parser.parse_args()
    print(json.dumps(check(args.url), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
