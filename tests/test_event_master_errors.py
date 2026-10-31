import hashlib

from test_gameplay import gameplay_master, setup_gameplay


def test_invalid_rules_preserve_immutable_master_downloads_and_report_uncached_errors(setup_gameplay):
    context = setup_gameplay
    health = context.client.get("/api/health-check")
    assert health.status_code == 200
    derived_revision = health.json()["versions"]["resource"][0]
    assert derived_revision.startswith("events-")
    derived = context.client.get("/data/" + derived_revision)
    assert derived.status_code == 200
    assert derived.headers["etag"] == '"' + hashlib.sha256(derived.content).hexdigest() + '"'
    (context.settings.data_dir / "gameplay-rules.json").write_text("{invalid", encoding="utf-8")
    assert context.client.get("/api/health-check").status_code == 503
    cached = context.client.get("/data/" + derived_revision)
    assert cached.status_code == 200 and cached.content == derived.content
    unchanged = context.client.get("/data/" + derived_revision, headers={"If-None-Match": derived.headers["etag"]})
    assert unchanged.status_code == 304
    original = context.client.get("/data/" + context.record["revision"])
    assert original.status_code == 200 and hashlib.sha256(original.content).hexdigest() == context.record["sha256"]
    missing = context.client.get("/data/events-00000000000000000000000000000000")
    assert missing.status_code == 503 and missing.json()["status"] == "error"
    assert context.store.master()["revision"] == context.record["revision"]
