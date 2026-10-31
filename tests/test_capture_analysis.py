import importlib.util
import json
from pathlib import Path


spec = importlib.util.spec_from_file_location("capture_analysis", Path(__file__).parents[1] / "pc-observer/analyze_capture.py")
analysis = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analysis)


def test_coverage_keeps_unknown_routes_and_missing_fields_visible(tmp_path):
    (tmp_path / "records").mkdir()
    (tmp_path / "manifest.json").write_text(json.dumps({"run_id": "test", "state": "capturing", "counters": {"dropped": 2}}))
    protocol = tmp_path / "protocol.json"
    protocol.write_text(json.dumps({"routes": ["api/user", "api/quest/{0}", "api/home"]}))
    responses = [
        {"method": "GET", "route": "/api/user", "http_status": 200, "sequence": 1,
         "response": {"contents": {"records": {"t_prologue_tutorials": {"up": []}}, "optional": None}}},
        {"method": "GET", "route": "/api/user", "http_status": 200, "sequence": 2,
         "response": {"contents": {"records": {"t_prologue_tutorials": {"up": [{"status": 99}]}}, "optional": "present"}}},
        {"method": "POST", "route": "/api/quest/42", "http_status": 409, "response": {"contents": {}}},
        {"method": "GET", "route": "/version", "http_status": 200, "response": {"contents": {}}},
    ]
    for i, response in enumerate(responses):
        (tmp_path / "records" / f"{i:08}.json").write_text(json.dumps(response))
    report = analysis.analyze(tmp_path, protocol)
    assert report["observed_api_routes"] == 2
    assert report["unobserved_routes"] == ["api/home"]
    user = next(route for route in report["routes"] if route["route"] == "/api/user")
    assert user["response_fields"]["$/contents/optional"] == ["null", "string"]
    assert user["record_tables"]["t_prologue_tutorials"] == {"min_up_rows": 0, "max_up_rows": 1}
    assert report["snapshot_candidates"]["/api/user"]["sequence"] == 2
    assert report["capture_counters"] == {"dropped": 2}
    assert any(route["route"] == "/version" for route in report["routes"])


def test_invalid_records_do_not_enter_coverage(tmp_path):
    (tmp_path / "records").mkdir()
    (tmp_path / "manifest.json").write_text("{}")
    protocol = tmp_path / "protocol.json"
    protocol.write_text('{"routes":["api/home"]}')
    (tmp_path / "records/00000001.json").write_text('{"method":"GET","route":"/api/home?token=secret","response":{}}')
    (tmp_path / "records/00000002.json").write_text("{")
    report = analysis.analyze(tmp_path, protocol)
    assert report["observed_api_routes"] == 0
    assert len(report["rejected_records"]) == 2
    assert "secret" not in json.dumps(report)


def test_route_templates_do_not_match_prefixes_or_extra_segments():
    assert analysis.route_matches("api/item/{0}", "/api/item/123")
    assert not analysis.route_matches("api/item/{0}", "/api/item/123/secret")
    assert not analysis.route_matches("api/home", "/api/home-extra")


def test_original_schema_type_survives_redaction(tmp_path):
    (tmp_path / "records").mkdir()
    (tmp_path / "schemas").mkdir()
    (tmp_path / "manifest.json").write_text("{}")
    protocol = tmp_path / "protocol.json"
    protocol.write_text('{"routes":["api/user"]}')
    digest = "a" * 64
    (tmp_path / "schemas" / (digest + ".json")).write_text(json.dumps({"fields": {"$.contents.user_id": ["Number", "Redacted"]}}))
    (tmp_path / "records/00000001.json").write_text(json.dumps({"method": "GET", "route": "/api/user", "http_status": 200,
        "response": {"contents": {"user_id": "[redacted]"}}, "response_schema_sha256": digest}))
    report = analysis.analyze(tmp_path, protocol)
    assert report["routes"][0]["response_fields"]["$.contents.user_id"] == ["number", "redacted"]
