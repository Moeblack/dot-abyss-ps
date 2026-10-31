import json

from dotabyss_ps.admin_display import DisplayTranslations
from dotabyss_ps.gameplay_catalog import GameplayError
from test_admin_console import headers
from test_gameplay import call, gameplay_master, setup_gameplay, snapshot


def test_profile_create_rename_and_launcher_route_share_selected_identity(setup_gameplay, monkeypatch):
    c = setup_gameplay
    call(c, "api/user")
    original = snapshot(c)
    client, auth = c.client, headers(c)
    payload = {"label": "新的冒险", "player_name": "测试司令", "operation_id": "new-profile-click"}
    created = client.post("/admin/profiles", headers=auth, json=payload)
    assert created.status_code == 200, created.text
    profile = created.json()["profile"]
    assert profile["id"] != c.identity["user_id"]
    assert profile["label"] == "新的冒险" and profile["player_name"] == "测试司令"
    assert snapshot(c) == original
    assert client.post("/admin/profiles", headers=auth, json=payload).json()["profile"]["id"] == profile["id"]
    duplicate = client.post("/admin/profiles", headers=auth, json={**payload, "operation_id": "another-click"})
    assert duplicate.status_code == 409
    renamed = client.post(f"/admin/profiles/{profile['id']}/label", headers=auth, json={"label": "主线试玩", "expected_label": "新的冒险"})
    assert renamed.status_code == 200
    assert renamed.json()["profile"]["name"] == profile["name"]
    assert renamed.json()["profile"]["player_name"] == "测试司令"
    launches = []
    def start(identifier, origin):
        launches.append((identifier, origin))
        return {"status": "started", "running": True, "profile_id": identifier, "profile_name": profile["name"], "pid": 12}
    monkeypatch.setattr(client.app.state.launcher, "start", start)
    launched = client.post("/admin/launcher/start", headers=auth, json={"profile_id": profile["id"]})
    assert launched.status_code == 200
    assert launched.json()["profile_name"] == "主线试玩"
    assert launched.json()["profile_key"] == profile["name"]
    assert launches == [(profile["id"], "http://testserver/")]


def test_launcher_security_and_conflict_are_explicit(setup_gameplay, monkeypatch):
    c = setup_gameplay
    client, auth = c.client, headers(c)
    def conflict(*_):
        raise GameplayError("请先退出当前游戏", 409, 4098)
    monkeypatch.setattr(client.app.state.launcher, "start", conflict)
    assert client.post("/admin/profiles", json={"label": "No"}).status_code == 401
    assert client.post("/admin/launcher/start", json={"profile_id": 1}).status_code == 401
    assert client.post("/admin/launcher/start", headers={**auth, "Origin": "http://external.invalid"}, json={"profile_id": 1}).status_code == 403
    assert client.post("/admin/launcher/start", headers=auth, json={"profile_id": 1, "executable": "other.exe"}).status_code == 400
    response = client.post("/admin/launcher/start", headers=auth, json={"profile_id": 1})
    assert response.status_code == 409 and "退出" in response.json()["error"]["message"]
    assert client.get("/admin/status", headers=auth).json()["capabilities"]["launcher"] is True


def test_catalog_chinese_search_keeps_original_edit_values(setup_gameplay, tmp_path):
    c = setup_gameplay
    name = "m_accessories"
    columns = [field["name"] for field in c.schema["tables"][name]]
    row = dict(zip(columns, c.tables[name][0]))
    path = tmp_path / "display" / name / "zh_Hant.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({row["name"]: "翻譯戒指"}, ensure_ascii=False), encoding="utf-8")
    c.client.app.state.admin.display = DisplayTranslations(path.parents[1])
    auth = headers(c)
    result = c.client.get("/admin/catalog", params={"table": name, "q": "翻譯戒指"}, headers=auth)
    assert result.status_code == 200, result.text
    found = next(item for item in result.json()["rows"] if item["id"] == row["id"])
    assert found["name"] == row["name"] and found["_display"]["name"] == "翻譯戒指"
    master = c.client.get("/admin/master/table/" + name, params={"q": "翻譯戒指"}, headers=auth).json()["rows"][0]
    assert master["values"]["name"] == row["name"] and master["display"]["name"] == "翻譯戒指"
