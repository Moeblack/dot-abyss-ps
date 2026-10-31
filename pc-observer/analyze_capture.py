from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


MAX_RECORD_BYTES = 32 * 1024 * 1024
MAX_RECORDS = 10000


def route_matches(template: str, route: str) -> bool:
    parts = re.split(r"(\{[^{}]+\})", template.strip("/"))
    pattern = "/" + "".join("[^/]+" if part.startswith("{") else re.escape(part) for part in parts)
    return re.fullmatch(pattern, route) is not None


def _field_shapes(value, path: str = "$"):
    if value is None:
        yield path, "null"
    elif isinstance(value, bool):
        yield path, "boolean"
    elif isinstance(value, (int, float)):
        yield path, "number"
    elif isinstance(value, str):
        yield path, "string"
    elif isinstance(value, list):
        yield path, "array"
        for item in value:
            yield from _field_shapes(item, path + "[]")
    elif isinstance(value, dict):
        yield path, "object"
        for key, item in value.items():
            yield from _field_shapes(item, path + "/" + key.replace("~", "~0").replace("/", "~1"))


def response_shapes(run: Path, event: dict) -> list[tuple[str, str]]:
    digest = event.get("response_schema_sha256")
    if isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest):
        path = run / "schemas" / (digest + ".json")
        if path.is_symlink() or not path.resolve().is_relative_to(run) or path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("Invalid schema file")
        document = json.loads(path.read_text(encoding="utf-8-sig"))
        fields = document["fields"]
        if not isinstance(fields, dict) or len(fields) > 4096:
            raise ValueError("Invalid schema fields")
        names = {"Null": "null", "True": "boolean", "False": "boolean", "Number": "number",
                 "String": "string", "Object": "object", "Array": "array", "Redacted": "redacted"}
        shapes = []
        for field, kinds in fields.items():
            if not isinstance(field, str) or len(field) > 512 or not isinstance(kinds, list):
                raise ValueError("Invalid schema field")
            for kind in kinds:
                shapes.append((field, names[kind]))
        return shapes
    shapes = []
    for shape in _field_shapes(event["response"]):
        if len(shapes) >= 200000:
            raise ValueError("Record exceeds the field limit")
        shapes.append(shape)
    return shapes


def analyze(run: Path, protocol: Path) -> dict:
    run = run.resolve(strict=True)
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8-sig"))
    catalog = json.loads(protocol.read_text(encoding="utf-8-sig"))
    known = set(catalog["routes"])
    routes, covered, rejected, snapshots = {}, set(), [], {}
    paths = sorted((run / "records").glob("*.json"))
    if len(paths) > MAX_RECORDS:
        raise ValueError("Capture exceeds the analysis record limit")
    for path in paths:
        if path.is_symlink() or not path.resolve().is_relative_to(run) or not re.fullmatch(r"\d+\.json", path.name):
            rejected.append({"file": path.name, "reason": "invalid_record_path"})
            continue
        try:
            if path.stat().st_size > MAX_RECORD_BYTES:
                raise ValueError("Record exceeds its size limit")
            event = json.loads(path.read_text(encoding="utf-8-sig"))
            method, route = event["method"], event["route"]
            if method not in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
                raise ValueError("Invalid method")
            if not isinstance(route, str) or not route.startswith("/") or any(c in route for c in "?#") or any(ord(c) < 32 for c in route):
                raise ValueError("Invalid route")
            response = event["response"]
            shapes = response_shapes(run, event)
        except (ValueError, KeyError, TypeError, OSError, RecursionError):
            rejected.append({"file": path.name, "reason": "invalid_record"})
            continue
        key = method + " " + route
        entry = routes.setdefault(key, {"method": method, "route": route, "count": 0, "http_statuses": set(),
                                        "response_fields": {}, "samples": [], "record_tables": {}})
        entry["count"] += 1
        status = event.get("http_status")
        if type(status) is int:
            entry["http_statuses"].add(status)
        if len(entry["samples"]) < 5:
            entry["samples"].append("records/" + path.name)
        for field, kind in shapes:
            entry["response_fields"].setdefault(field, set()).add(kind)
        matched = {template for template in known if route_matches(template, route)}
        covered.update(matched)
        contents = response.get("contents") if isinstance(response, dict) else None
        records = contents.get("records") if isinstance(contents, dict) else None
        if isinstance(records, dict):
            total = 0
            for table, container in records.items():
                if not isinstance(container, dict) or not isinstance(container.get("up"), list):
                    continue
                count = len(container["up"])
                total += count
                values = entry["record_tables"].setdefault(table, {"min_up_rows": count, "max_up_rows": count})
                values["min_up_rows"] = min(values["min_up_rows"], count)
                values["max_up_rows"] = max(values["max_up_rows"], count)
            if route in ("/api/user", "/api/home") and status == 200:
                candidate = {"file": "records/" + path.name, "tables": len(records), "up_rows": total,
                             "sequence": event.get("sequence", 0)}
                if route not in snapshots or (total, candidate["sequence"]) > (snapshots[route]["up_rows"], snapshots[route]["sequence"]):
                    snapshots[route] = candidate
    for entry in routes.values():
        entry["http_statuses"] = sorted(entry["http_statuses"])
        entry["response_fields"] = {field: sorted(kinds) for field, kinds in sorted(entry["response_fields"].items())}
    return {"format_version": 1, "run_id": manifest.get("run_id"), "capture_state": manifest.get("state"),
            "capture_counters": manifest.get("counters", {}), "known_api_routes": len(known),
            "observed_api_routes": len(covered), "covered_routes": sorted(covered),
            "unobserved_routes": sorted(known - covered), "routes": list(routes.values()),
            "snapshot_candidates": snapshots, "rejected_records": rejected,
            "limitations": ["Unobserved routes are not proven unnecessary or unavailable",
                            "Responses describe observed state, not server implementation",
                            "Request objects are snapshots at response time, not verified wire bytes",
                            "Snapshot candidates require identity normalization and validation before import"]}


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize sanitized response captures and API coverage")
    parser.add_argument("run", type=Path)
    parser.add_argument("--protocol", type=Path, default=Path(__file__).resolve().parents[1] / "artifacts/pc-research/pc-native-protocol.json")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = analyze(args.run, args.protocol)
    output = args.output or args.run / "analysis.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(output.resolve()), "observed_api_routes": report["observed_api_routes"],
                      "known_api_routes": report["known_api_routes"], "rejected_records": len(report["rejected_records"])}))


if __name__ == "__main__":
    main()
