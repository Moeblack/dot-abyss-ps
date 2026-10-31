import copy
import json

from fastapi.testclient import TestClient

from dotabyss_ps.app import Settings, create_app
from dotabyss_ps.admin_master import fingerprint
from test_gameplay import call, gameplay_master, setup_gameplay, snapshot
from test_economy_features import update


def headers(context):
    return {"X-Admin-Token": (context.settings.data_dir / "admin-token.txt").read_text().strip()}


def endpoint(context):
    return f"/admin/profiles/{context.identity['user_id']}"


def get(context, suffix="/snapshot"):
    response = context.client.get(endpoint(context) + suffix, headers=headers(context))
    assert response.status_code == 200, response.text
    return response.json()


def preview(context, operation="currency.balance", args=None):
    body = {"operation": operation, "args": args or {"kind": 50, "mode": "add", "amount": 25}, "expected_version": get(context)["version"]}
    response = context.client.post(endpoint(context) + "/preview", headers=headers(context), json=body)
    assert response.status_code == 200, response.text
    return response.json()


def apply(context, ticket, status=200):
    response = context.client.post(endpoint(context) + "/apply", headers=headers(context), json={"ticket": ticket})
    assert response.status_code == status, response.text
    return response.json()


def test_admin_is_local_authenticated_and_does_not_expose_token(tmp_path):
    app = create_app(Settings.from_directory(tmp_path))
    with TestClient(app) as client:
        token = (tmp_path / "admin-token.txt").read_text()
        assert client.get("/admin/status").status_code == 401
        assert client.get("/admin/status", headers={"X-Admin-Token": token, "Origin": "http://evil.invalid"}).status_code == 403
        response = client.get("/admin/status", headers={"X-Admin-Token": token})
        assert response.status_code == 200 and token not in response.text
        assert client.get("/local/admin-sync?client_id=unit&after=0").status_code == 401
        assert client.post("/admin/rules/preview", headers={"X-Admin-Token": token}, content="[]").status_code == 400
        assert client.post("/admin/rules/apply", headers={"X-Admin-Token": token}, json={"ticket": []}).status_code == 400


def test_preview_apply_retry_conflict_and_current_sync(setup_gameplay):
    c = setup_gameplay
    call(c, "api/user")
    schema = c.client.get("/admin/schema", headers=headers(c))
    assert schema.status_code == 200, schema.text
    assert len(schema.json()["actions"]) == 130
    assert any(group["id"] == "social" for group in schema.json()["groups"])
    before = snapshot(c)
    initial = get(c)["state"]["records"]["t_statuses"]["up"][0]["gold"]
    proposal = preview(c)
    assert snapshot(c) == before
    first = apply(c, proposal["ticket"])
    assert apply(c, proposal["ticket"])["revision"] == first["revision"]
    assert get(c)["state"]["records"]["t_statuses"]["up"][0]["gold"] == initial + 25
    stale = preview(c)
    update(c, lambda state, _: state["records"]["t_statuses"]["up"][0].update(gold=initial + 4))
    apply(c, stale["ticket"], 409)
    synced = c.client.get("/local/admin-sync?client_id=unit&after=0", headers=c.headers).json()
    assert synced["records"]["t_statuses"]["up"][0]["gold"] == initial + 4
    assert synced["pending"] is True
    response = c.client.post("/local/admin-sync/ack", headers=c.headers, json={"client_id": "unit", "revision": synced["revision"], "status": "applied", "scene": "Home"})
    assert response.status_code == 200
    assert get(c)["sync"]["status"] == "applied"
    with TestClient(create_app(c.settings)) as restarted:
        retried = restarted.post(endpoint(c) + "/apply", headers=headers(c), json={"ticket": proposal["ticket"]})
        assert retried.status_code == 200 and retried.json()["revision"] == first["revision"]


def test_backup_restore_profile_binding_and_deletion_sync(setup_gameplay):
    c = setup_gameplay
    call(c, "api/user")
    base = get(c)
    backed = c.client.post(endpoint(c) + "/backup", headers=headers(c), json={"expected_version": base["version"]})
    assert backed.status_code == 200
    altered = preview(c, "inventory.balance", {"kind": 30, "master_id": 130001, "mode": "set", "amount": 17})
    first = apply(c, altered["ticket"])
    restore = preview(c, "save.restore", {"history_id": backed.json()["id"], "which": "before"})
    apply(c, restore["ticket"])
    assert get(c)["state"] == base["state"]
    delta = c.client.get(f"/local/admin-sync?client_id=unit&after={first['revision']}", headers=c.headers).json()["records"]
    added = json.loads(snapshot(c)[0])["records"]["t_items"]["up"]
    if not any(row["m_item_id"] == 130001 for row in base["state"]["records"]["t_items"]["up"]):
        assert delta["t_items"]["down"]
    else:
        assert any(row["m_item_id"] == 130001 for row in added)
    foreign = copy.deepcopy(base)
    foreign["profile_id"] += 1
    response = c.client.post(endpoint(c) + "/preview", headers=headers(c), json={"operation": "save.import", "args": {"snapshot": foreign}, "expected_version": get(c)["version"]})
    assert response.status_code == 400
    invalid = copy.deepcopy(base)
    invalid["progress"]["features"]["battle"] = {"version": 1, "data": {"next_id": 0}}
    response = c.client.post(endpoint(c) + "/preview", headers=headers(c), json={"operation": "save.import", "args": {"snapshot": invalid}, "expected_version": get(c)["version"]})
    assert response.status_code == 400
    assert get(c)["state"] == base["state"]


def test_rules_validation_and_master_overlay_preserve_base(setup_gameplay):
    c = setup_gameplay
    call(c, "api/user")
    def legacy(state, progress):
        state["records"]["t_statuses"]["up"][0]["max_party_count"] = 1
        state["records"]["t_prologue_tutorials"]["up"][0]["status"] = 99
        progress["features"]["tutorial"]["data"]["mode"] = "legacy"
    update(c, legacy)
    client, auth = c.client, headers(c)
    rules = client.get("/admin/rules", headers=auth).json()
    invalid = client.post("/admin/rules/preview", headers=auth, json={"changes": {"character_level_cap": True}, "expected_version": rules["version"]})
    assert invalid.status_code == 400
    proposed = client.post("/admin/rules/preview", headers=auth, json={"changes": {"gacha_expired_token_gold_per_token": 2, "party_slots": 10}, "expected_version": rules["version"]})
    assert proposed.status_code == 200
    applied = client.post("/admin/rules/apply", headers=auth, json={"ticket": proposed.json()["ticket"]})
    assert applied.status_code == 200
    assert client.get("/admin/rules", headers=auth).json()["values"]["gacha_expired_token_gold_per_token"] == 2
    synchronized = client.get("/local/admin-sync?client_id=unit&after=0", headers=c.headers).json()
    assert synchronized["records"].get("t_statuses", {}).get("up", [{}])[0].get("max_party_count") == 10, {"changes": proposed.json()["changes"], "stored": get(c)["state"]["records"]["t_statuses"]["up"][0]["max_party_count"]}
    table = client.get("/admin/master/table/m_characters", headers=auth).json()
    row = table["rows"][0]
    original = fingerprint(c.tables)
    proposal = {"table": "m_characters", "index": row["index"], "changes": {"name": "Console test"}, "expected_version": table["version"]}
    result = client.post("/admin/master/preview", headers=auth, json=proposal)
    assert result.status_code == 200, result.text
    assert fingerprint(c.tables) == original
    saved = client.post("/admin/master/apply", headers=auth, json={"ticket": result.json()["ticket"]})
    assert saved.status_code == 200, saved.text
    assert fingerprint(c.tables) == original
    table = client.get("/admin/master/table/m_characters", headers=auth).json()
    assert table["rows"][0]["values"]["name"] == "Console test"
    proposal.update(changes={"id": 999}, expected_version=table["version"])
    assert client.post("/admin/master/preview", headers=auth, json=proposal).status_code == 400
    sync = client.get("/local/admin-sync?client_id=unit&after=0", headers=c.headers).json()
    assert sync["refresh"] == "relogin"
    assert sync["resource_version"].startswith("events-")


def test_browser_handoff_is_one_use_and_cannot_be_used_cross_origin(tmp_path):
    with TestClient(create_app(Settings.from_directory(tmp_path))) as client:
        assert client.get("/console/").status_code == 200
        assert "ConsoleFields" in client.get("/console/fields.js").text
        token = (tmp_path / "admin-token.txt").read_text()
        auth = {"X-Admin-Token": token}
        ticket = client.post("/admin/ui-ticket", headers=auth).json()["ticket"]
        response = client.post("/console/auth", json={"ticket": ticket}, headers={"Origin": "http://evil.invalid", "X-Console-Request": "1"})
        assert response.status_code == 403
        browser = {"Origin": "http://testserver", "X-Console-Request": "1"}
        response = client.post("/console/auth", json={"ticket": ticket}, headers=browser)
        assert response.status_code == 200
        assert "httponly" in response.headers["set-cookie"].lower()
        assert "samesite=strict" in response.headers["set-cookie"].lower()
        assert client.post("/console/auth", json={"ticket": ticket}, headers=browser).status_code == 401
        assert client.get("/admin/status", headers={"X-Console-Request": "1"}).status_code == 200
        assert client.get("/admin/status").status_code == 401
        assert client.post("/admin/rules/preview", json={}, headers={**browser, "Origin": "http://evil.invalid"}).status_code == 403


def test_concurrent_apply_executes_only_one_ticket(setup_gameplay):
    from concurrent.futures import ThreadPoolExecutor
    c = setup_gameplay
    call(c, "api/user")
    proposals = [preview(c), preview(c)]
    def submit(proposal):
        return c.client.post(endpoint(c) + "/apply", headers=headers(c), json={"ticket": proposal["ticket"]}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(submit, proposals)) == [200, 409]


def test_social_edits_are_previewed_versioned_and_restorable(setup_gameplay):
    c = setup_gameplay
    call(c, "api/user")
    peer = c.store.login("console-peer")
    c.client.app.state.admin.local.contents("api/user", peer["user_id"])
    proposal = preview(c, "social.follow", {"target_id": peer["user_id"]})
    assert not get(c)["social"]["following"]
    followed = apply(c, proposal["ticket"])
    assert get(c)["social"]["following"] == [peer["user_id"]]
    proposal = preview(c, "save.restore", {"history_id": followed["revision"], "which": "before"})
    apply(c, proposal["ticket"])
    assert not get(c)["social"]["following"]
    invalid = c.client.post(endpoint(c) + "/apply", headers=headers(c), json={"ticket": {}})
    assert invalid.status_code == 400
