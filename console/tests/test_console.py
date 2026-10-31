import io
import time
import tkinter as tk
from urllib.error import HTTPError

import pytest

from console.app import ConsoleApp
from console.client import AdminClient, ConsoleError, read_token, service_origin
from console.models import actions_from_schema, parse_field
from console.widgets import FieldForm


@pytest.mark.parametrize("origin", ["https://127.0.0.1:18083", "http://example.com", "http://user:pass@localhost", "http://127.0.0.1/api", "http://localhost?token=secret", "http://localhost:70000"])
def test_external_or_ambiguous_origins_rejected(origin):
    with pytest.raises(ConsoleError):
        service_origin(origin)


def test_loopback_origins():
    assert service_origin("http://127.0.0.1:18083/") == "http://127.0.0.1:18083"
    assert service_origin("http://[::1]:18083") == "http://[::1]:18083"


def test_token_file_and_request_header(tmp_path):
    (tmp_path / "admin-token.txt").write_text("test-token\n", encoding="utf-8")
    client = AdminClient("http://127.0.0.1:18083", read_token(tmp_path))
    class Opener:
        def open(self, request, timeout):
            assert request.get_header("X-admin-token") == "test-token"
            assert "test-token" not in request.full_url
            assert request.full_url.endswith("/admin/catalog?table=m_characters&q=%E5%90%8D%E7%A7%B0")
            return io.BytesIO(b'{"rows":[]}')
    client._opener = Opener()
    assert client.get("/admin/catalog", table="m_characters", q="名称") == {"rows": []}


def test_server_error_token_redacted():
    client = AdminClient("http://localhost:18083", "test-token")
    class Opener:
        def open(self, request, timeout):
            raise HTTPError(request.full_url, 409, "Conflict", {}, io.BytesIO(b'{"error":{"code":"conflict","message":"test-token changed"}}'))
    client._opener = Opener()
    with pytest.raises(ConsoleError, match=r"\[凭据\] changed"):
        client.get("/admin/status")


def test_field_types_and_bounds():
    assert parse_field({"type": "integer", "min": 0, "max": 30}, "30") == 30
    assert parse_field({"type": "long"}, "1234567890123") == 1234567890123
    assert parse_field({"type": "ids"}, "1，2\n3") == [1, 2, 3]
    assert parse_field({"type": "json"}, '{"hello":[1]}') == {"hello": [1]}
    assert parse_field({"type": "integer", "required": False}, "") is None
    assert parse_field({"type": "boolean"}, False) is False
    for value in ("1.1", "x"):
        with pytest.raises(ValueError):
            parse_field({"type": "integer"}, value)
    for value in ("NaN", "Infinity"):
        with pytest.raises(ValueError):
            parse_field({"type": "number"}, value)
    with pytest.raises(ValueError):
        parse_field({"type": "integer", "max": 30}, "31")


SCHEMA = {
    "groups": [{"id": "characters", "label": "角色与成长"}],
    "actions": [{"id": "character.edit", "group": "characters", "label": "修改角色", "description": "修改已有角色", "timing": "page", "fields": [
        {"name": "ids", "label": "角色", "type": "ids", "source": "m_characters", "required": True},
        {"name": "level", "label": "等级", "type": "integer", "min": 1, "max": 30},
        {"name": "all", "label": "全部角色", "type": "boolean", "default": False},
    ]}],
}


class FakeClient:
    def get(self, path, **query):
        if path.endswith("/snapshot"):
            return {"profile_id": 1, "version": "v1", "state": {"records": {"t_characters": {"up": [{"id": 101, "level": 1}], "down": []}}}, "progress": {"story": {"chapter": 1}}, "sync": {"revision": 0, "ack_revision": 0, "status": "idle"}}
        if path.endswith("/history"):
            return {"entries": [{"id": 1, "at": "2026-10-31", "summary": "测试备份", "status": "saved"}]}
        if path == "/admin/rules":
            return {"version": "rules1", "values": {"character_level_cap": 30}, "specs": [{"name": "character_level_cap", "label": "等级上限", "type": "integer", "min": 1, "max": 30}]}
        if path == "/admin/master/tables":
            return {"tables": [{"name": "m_characters", "count": 1, "editable": True}]}
        if path.startswith("/admin/master/table/"):
            return {"version": "master1", "rows": [{"index": 0, "values": {"id": 101, "name": "测试角色", "hp": 100}}], "fields": [{"name": "id", "type": "integer", "editable": False}, {"name": "name", "type": "string", "editable": False}, {"name": "hp", "type": "integer", "editable": True}], "total": 1}
        if path == "/admin/catalog":
            return {"rows": [{"id": 101, "name": "测试角色"}], "total": 1}
        raise AssertionError(path)

    def post(self, path, body):
        return {"ticket": "preview1", "summary": "测试修改", "changes": [], "timing": "page"}


@pytest.fixture(scope="module")
def tk_root():
    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def app(tmp_path, tk_root):
    root = tk.Toplevel(tk_root)
    root.withdraw()
    app = ConsoleApp(root, auto_connect=False, settings_path=tmp_path / "settings.json")
    app.client = FakeClient()
    app.schema = SCHEMA
    app.actions = actions_from_schema(SCHEMA)
    app.profiles = [{"id": 1, "name": "隔离测试", "has_state": True}]
    app.profile_id = 1
    app.snapshot = app.client.get("/admin/profiles/1/snapshot")
    app.errors = []
    app._error = lambda error, **kwargs: app.errors.append(error)
    app._build_navigation()
    yield app
    app.close()


def drain(app):
    deadline = time.monotonic() + 3
    while app.pending and time.monotonic() < deadline:
        app.root.update()
        time.sleep(0.01)
    app.root.update()
    assert not app.pending
    assert not app.errors


def test_desktop_views_use_isolated_client(app):
    for name in ("overview", "characters", "rules", "master", "history", "backups"):
        app.show_group(name)
        drain(app)
    assert app.client.get("/admin/profiles/1/snapshot")["version"] == "v1"


def test_form_omits_optional_fields_and_preserves_choices(app):
    form = FieldForm(app.root)
    form.set_fields([
        {"name": "level", "type": "integer"},
        {"name": "id", "type": "integer", "editable": False},
        {"name": "premium", "type": "boolean", "required": False},
        {"name": "mode", "type": "choice", "required": True, "choices": [{"value": 0, "label": "保留"}, {"value": 1, "label": "重置"}], "default": 0},
    ], {"id": 101})
    assert form.values() == {"mode": 0}
    form.set_value("premium", False)
    assert form.values() == {"mode": 0, "premium": False}
    form.destroy()


def test_edit_after_preview_request_discards_stale_ticket(app):
    app.show_group("characters")
    app._select_action({"id": "character.edit"})
    captured = []
    app._submit = lambda function, success, **kwargs: captured.append(success)
    app._request_preview("/admin/profiles/1", {"operation": "character.edit", "args": {"ids": [101]}, "expected_version": "v1"})
    app.form.set_value("level", 5)
    captured[0]({"ticket": "stale"})
    assert app.preview is None
    assert str(app.apply_button["state"]) == "disabled"


def test_master_only_submits_editable_changes(app):
    app.show_group("master")
    drain(app)
    app._select_master_row({"index": 0, "id": 101, "name": "测试角色", "hp": 100})
    app.form.set_value("hp", 125)
    captured = []
    app._request_preview = lambda endpoint, body: captured.append((endpoint, body))
    app._preview_master()
    assert captured == [("/admin/master", {"table": "m_characters", "index": 0, "changes": {"hp": 125}, "expected_version": "master1"})]


def test_switching_content_kind_clears_dependent_ids(app):
    form = FieldForm(app.root)
    form.set_fields([
        {"name": "kind", "type": "choice", "choices": [{"value": "character", "label": "角色"}, {"value": "weapon", "label": "武器"}], "default": "character"},
        {"name": "ids", "type": "ids", "source_by": {"field": "kind", "values": {"character": "m_character_skins", "weapon": "m_weapon_skins"}}},
    ])
    form.set_value("ids", [101])
    assert form.source("ids") == "m_character_skins"
    form.set_value("kind", "weapon")
    assert form.source("ids") == "m_weapon_skins"
    assert form.values()["ids"] == []
    form.destroy()
