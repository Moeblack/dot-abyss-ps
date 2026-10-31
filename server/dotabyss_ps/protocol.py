from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from typing import Any

import msgpack
from starlette.responses import Response

from . import __version__

PLATFORMS = (
    "WebDmmGeneral", "WebDmmR18", "AndroidDmmGeneral", "AndroidDmmR18",
    "AndroidGooglePlayGeneral", "StandaloneDmmGeneral", "StandaloneDmmR18",
    "IosDmmGeneral", "IosDmmR18", "IosAppStoreGeneral",
)
SUBSCRIPTIONS = ("SubscriptionNoteAppStore", "SubscriptionNoteGamePlayer", "SubscriptionNoteGooglePlay", "SubscriptionNoteWeb")
REDACTED = "[redacted]"


def validate_data(value: Any) -> None:
    pending = [(value, 0)]
    count = 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if depth > 64 or count > 100000:
            raise ValueError("Payload structure exceeds limits")
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise ValueError("Object keys must be strings")
                key.encode("utf-8", errors="strict")
                pending.append((child, depth + 1))
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
        elif isinstance(item, str):
            item.encode("utf-8", errors="strict")
        elif isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError("Non-finite number")
        elif item is not None and not isinstance(item, (bool, int)):
            raise ValueError("Unsupported data value")


def decode_json(value: bytes | str) -> Any:
    result = json.loads(value)
    validate_data(result)
    return result


def sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            sensitive = any(part in normalized for part in ("token", "session", "password", "secret", "authorization", "verification", "cookie"))
            is_check = isinstance(item, bool) and normalized.endswith(("_matches", "_populated", "_present"))
            result[str(key)] = REDACTED if sensitive and not is_check else sanitize(item)
        return result
    if isinstance(value, (list, tuple)):
        return [sanitize(item) for item in value]
    if isinstance(value, bytes):
        return {"binary_bytes": len(value)}
    return value


def version_fields(runtime: dict[str, str]) -> dict[str, list[str]]:
    def values(key: str) -> list[str]:
        value = runtime.get(key, "")
        return [value] if value else []

    result = {"server": values("server_version") or [f"local-{__version__}"], "resource": values("resource_version")}
    for platform in PLATFORMS:
        result["ClientVersion" + platform] = values("application_version")
        result["AssetVersion" + platform] = values("bundle_version")
    for name in SUBSCRIPTIONS:
        result[name] = values("bundle_version")
    result["FeatureAppIntegrity"] = []
    return result


def envelope(contents: Any, runtime: dict[str, str], api_token: str = "", *, error: tuple[int, str] | None = None) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "status": "error" if error else "success",
        "server_time": now.astimezone(timezone(timedelta(hours=9))).isoformat(timespec="seconds"),
        "timestamp": int(now.timestamp()),
        "errors": {
            "code": error[0] if error else 0,
            "message": error[1] if error else "",
            "severity": "error" if error else "",
            "app_transition": "",
        },
        "api_token": api_token,
        "versions": version_fields(runtime),
        "contents": contents,
    }


def encode_response(payload: Any, *, accept: str = "", status: int = 200, headers: dict[str, str] | None = None) -> Response:
    if "application/x-msgpack" in accept.lower():
        return Response(msgpack.packb(payload, use_bin_type=True), status_code=status, media_type="application/x-msgpack", headers=headers)
    return Response(json.dumps(payload, ensure_ascii=False, allow_nan=False), status_code=status, media_type="application/json", headers=headers)
