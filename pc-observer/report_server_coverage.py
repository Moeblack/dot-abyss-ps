from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from dotabyss_ps.gameplay import BLOCKED_ROUTES, GAMEPLAY_METHODS
from dotabyss_ps.protocol import decode_json

SUPPORT = {
    "api/user": ("local_complete", "Validated persistent local profile; official account import is not performed."),
    "api/tutorial/feature-exec": ("local_complete", "Persistent tutorial flags 1-19, idempotent bit updates."),
    "api/character/new/check": ("local_complete", "Owned character validation and persistent new flags."),
    "api/exchange/read/lineup": ("local_complete", "Active lineup validation and per-profile read markers."),
    "api/terms/fetch-agreements": ("fixture", "Local empty agreements; no official terms history."),
    "api/battle-session/status": ("local_complete", "Persistent local battle session and restart recovery; no active session returns the native empty status."),
    "api/main-story/start-battle": ("partial", "Dungeon/defence construction and persistent local sessions implemented; native battle acceptance pending."),
    "api/main-story/clear-battle": ("partial", "Session-bound drops, first-clear receipts and story progression implemented using documented local rules; native acceptance pending."),
    "api/home": ("partial", "Persistent records and mission badges; only greeting IDs 3 and 175 have verified rewards. Visitors, presents and recommendations remain absent."),
    "api/mypage/login": ("partial", "Local login view; official daily/special login campaigns, announcements and expiring entitlements are not implemented."),
    "api/party/exploration/edit": ("partial", "Persistent owned members, gear and mana slots with removal deltas; class-specific gear eligibility, formation restrictions and combat statistics remain unverified."),
    "api/main-story/update-sequence": ("local_complete", "Sequential story progression, battle completion gates and pending chapter release notifications."),
    "api/feature-lock/unlock": ("local_complete", "Master chapter, sequence and tavern-staff conditions; unsupported condition types remain explicit errors."),
    "api/exchange/lineup": ("partial", "Active master lineups and read markers; purchase counts remain zero because exchange execution is not implemented."),
    "api/shop/top": ("partial", "Exchange badge and empty payment sections; paid products, purchases and subscriptions are not implemented."),
    "api/quest/top": ("partial", "Initial chapter quest record; event quest progression and event mission claims are not implemented."),
    "api/gacha": ("partial", "Active master groups and gachas; execution counts remain zero, wishlist and token balances are not implemented."),
    "api/mission/list": ("partial", "Login days, PC login, owned characters, lifetime owned gear and player rank; other counters are shown as zero and cannot be claimed."),
    "api/mission/reward/receive": ("partial", "Verified conditions, prerequisite checks, transactional grants and receipts; unsupported counters and reward types are rejected."),
    "api/special-mission/top": ("partial", "Master mission days, local start state and badges; unsupported progress counters remain zero, campaign completion rewards are not implemented."),
    "api/special-mission/mission-reward/receive": ("partial", "Day gating and transactional individual rewards; unsupported conditions/reward types and campaign completion remain incomplete."),
    "api/home/daily-greeting/receive-reward": ("partial", "Owned-character/intimacy checks and daily receipts for IDs 3 and 175; other greetings and visitor rewards are rejected."),
    **{route: ("blocked", reason) for route, reason in BLOCKED_ROUTES.items()},
}


def losses(value, path="$", output=None):
    result = [] if output is None else output
    if isinstance(value, str) and (value == "[redacted]" or value.startswith("[omitted:")):
        result.append({"path": path, "reason": value})
    elif isinstance(value, dict):
        for key, item in value.items():
            losses(item, path + "/" + key, result)
    elif isinstance(value, list):
        for item in value:
            losses(item, path + "[]", result)
    return result


def report(capture: Path, protocol: Path):
    files = sorted((capture / "records").glob("*.json"))
    if not files or len(files) > 10000:
        raise ValueError("Capture record count is invalid")
    manifest = decode_json((capture / "manifest.json").read_bytes())
    counts, methods, missing = Counter(), {}, {}
    for path in files:
        if path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError("Capture record exceeds 32 MiB")
        event = decode_json(path.read_bytes())
        route = event["route"].lstrip("/")
        counts[route] += 1
        methods.setdefault(route, set()).add(event["method"])
        missing.setdefault(route, set()).update((item["path"], item["reason"]) for item in losses(event))
    native = set(decode_json(protocol.read_bytes())["routes"])
    routes = []
    for route in sorted(counts):
        status, detail = SUPPORT.get(route, ("not_implemented", "No reviewed server implementation."))
        routes.append({"route": "/" + route, "methods": sorted(methods[route]), "samples": counts[route],
                       "status": status, "scope": detail, "native_verified_this_change": False,
                       "capture_losses": [{"path": path, "reason": reason} for path, reason in sorted(missing[route])]})
    return {"capture": capture.name, "manifest_state": manifest.get("state"),
            "sample_count": len(files), "sampled_route_count": len(routes), "native_route_count": len(native),
            "status_counts": dict(Counter(r["status"] for r in routes)), "routes": routes,
            "native_routes_without_samples": sorted("/" + route for route in native - counts.keys()),
            "registered_gameplay_without_samples": sorted("/" + route for route in GAMEPLAY_METHODS.keys() - counts.keys()),
            "notes": ["Route dispatch coverage is not gameplay completeness.",
                      "Redacted credentials are expected; omitted battle details and redacted business fields require new capture.",
                      "The original capture manifest is not modified by this report."]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = report(args.capture, args.protocol)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("sample_count", "sampled_route_count", "status_counts")}))


if __name__ == "__main__":
    main()
