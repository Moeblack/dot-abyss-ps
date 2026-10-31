import copy
import io
import time
import tkinter as tk
from urllib.error import HTTPError

import pytest

from console.app import ConsoleApp
from console.client import AdminClient, ConsoleError, read_token, service_origin
from console.display import cell_value
from console.models import actions_from_schema, parse_field
from console.widgets import DataTable, FieldForm


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


def test_table_displays_name_but_selection_retains_numeric_source(tk_root):
    row = {"id": 12, "m_weapon_id": 44, "_display": {"name": "炎之劍", "m_weapon_id": "炎之劍"}}
    table = DataTable(tk_root)
    try:
        table.show([row])
        assert "_display" not in table.tree["columns"] and "name" in table.tree["columns"]
        assert "炎之劍（ID: 44）" in table.tree.item("0", "values")
        table.tree.selection_set("0")
        assert table.selection() is row and table.selection()["m_weapon_id"] == 44
    finally:
        table.destroy()


def test_duplicate_choice_labels_preserve_distinct_typed_values(tk_root):
    form = FieldForm(tk_root)
    try:
        form.set_fields([{"name": "kind", "type": "choice", "choices": [
            {"value": 1, "label": "同名"}, {"value": 2, "label": "同名"},
            {"value": "1", "label": "同名"}, {"value": True, "label": "同名"}]}])
        assert len(form.controls["kind"][2]) == 4
        for value in (1, 2, "1", True):
            form.set_value("kind", value)
            actual = form.values()["kind"]
            assert type(actual) is type(value) and actual == value
    finally:
        form.destroy()


def test_field_name_lookup_discards_stale_ids_and_sources(tk_root):
    calls = []
    form = FieldForm(tk_root, on_resolve=lambda source, ids, done: calls.append((source, ids, done)))
    def request():
        job = form.reference_jobs.pop("id")
        form.after_cancel(job)
        form._resolve_reference("id", form.source("id"), [form.field_value("id")], form.reference_revisions["id"])
    try:
        form.set_fields([
            {"name": "kind", "type": "choice", "default": 1, "choices": [{"value": 1, "label": "角色"}, {"value": 2, "label": "装备"}]},
            {"name": "id", "type": "integer", "source_by": {"field": "kind", "values": {"1": "m_characters", "2": "m_weapons"}}}])
        form.set_value("id", 11)
        request()
        form.set_value("id", 22)
        request()
        calls[0][2]({"11": "旧角色"})
        assert "旧角色" not in form.hint_vars["id"].get()
        calls[1][2]({"22": "新角色"})
        assert "新角色（ID: 22）" in form.hint_vars["id"].get()
        assert form.values() == {"kind": 1, "id": 22}
        assert form.display_arguments(form.values())["id"] == "新角色（ID: 22）"
        form.set_value("kind", 2)
        assert form.values() == {"kind": 2} and "新角色" not in form.hint_vars["id"].get()
        calls[1][2]({"22": "旧来源迟到"})
        assert "旧来源迟到" not in form.hint_vars["id"].get()
    finally:
        form.destroy()


def test_master_labels_never_enter_edit_submission(app):
    class NamedClient(FakeClient):
        def get(self, path, **query):
            result = super().get(path, **query)
            if path.startswith("/admin/master/table/"):
                result["rows"][0]["display"] = {"name": "游戏译名"}
            return result
    app.client = NamedClient()
    app.show_group("master")
    drain(app)
    row = app.master_rows.rows[0]
    assert row["_display"]["name"] == "游戏译名" and row["name"] == "测试角色"
    app._select_master_row(row)
    assert "游戏译名" in app.form.hint_vars["name"].get()
    app.form.set_value("hp", 140)
    captured = []
    app._request_preview = lambda endpoint, body: captured.append(body)
    app._preview_master()
    assert captured[0]["changes"] == {"hp": 140}


def test_restore_preview_without_form_keeps_raw_arguments(app):
    app.show_group("backups")
    drain(app)
    app._request_preview("/admin/profiles/1", {"operation": "save.restore", "args": {"history_id": 1, "which": "before"}, "expected_version": "v1"})
    drain(app)
    assert app.preview["ticket"] == "preview1"
    assert '"history_id": 1' in app.preview_view.text.get("1.0", "end")


def test_progress_browser_annotations_preserve_all_raw_shapes(app):
    app.snapshot["progress"] = {
        "features": {"tavern": {"members": [{"m_character_id": 17}, {"m_character_id": 99}]}},
        "rows": [{"m_character_id": 17}, {"m_character_id": 99}],
        "ids": [17, 99], "selected": 17,
    }
    app.snapshot["progress_display"] = {
        "features": {"tavern": {"members": [{"m_character_id": "角色甲"}, None]}},
        "rows": [{"m_character_id": "角色甲"}, None],
        "ids": ["角色甲", None], "selected": "角色甲",
    }
    original = copy.deepcopy(app.snapshot)
    app.show_group("characters")
    for source in ("features", "rows", "ids", "selected"):
        app.state_table_name.set("progress." + source)
        app._show_state_table()
        row = app.state_table.rows[0]
        field = "m_character_id" if source == "rows" else "value"
        assert "角色甲" in str(cell_value(row, field))
        if source in {"rows", "ids"}:
            assert "角色甲" not in str(cell_value(app.state_table.rows[1], field))
    assert app.snapshot == original
    app.snapshot.pop("progress_display")
    app.state_table_name.set("progress.selected")
    app._show_state_table()
    assert app.state_table.rows == [{"value": 17}]


def test_owned_picker_uses_non_id_primary_key_and_retains_label(app, monkeypatch):
    app.snapshot = {"state": {"records": {"t_event_currencies": {"up": [
        {"m_event_id": 900, "m_event_currency_id": 17, "amount": 2}], "down": []}}},
        "record_identity_fields": {"t_event_currencies": "m_event_currency_id"},
        "record_display": {"t_event_currencies": {"17": {"name": "活動硬幣"}}}}
    dialogs = []
    original = tk.Toplevel
    def hidden_dialog(*args, **kwargs):
        dialog = original(*args, **kwargs)
        dialog.withdraw()
        dialog.grab_set = lambda: None
        dialogs.append(dialog)
        return dialog
    monkeypatch.setattr("console.app.tk.Toplevel", hidden_dialog)
    form = FieldForm(app.root)
    form.set_fields([{"name": "id", "type": "integer", "source": "t_event_currencies"}])
    try:
        app.pick_catalog(form, "id")
        widgets = []
        def collect(widget):
            for child in widget.winfo_children():
                widgets.append(child)
                collect(child)
        collect(dialogs[0])
        table = next(widget for widget in widgets if isinstance(widget, DataTable))
        table.tree.selection_set("0")
        next(widget for widget in widgets if widget.winfo_class() == "TButton" and widget.cget("text") == "填入 ID").invoke()
        assert form.values() == {"id": 17}
        assert "活動硬幣（ID: 17）" in form.hint_vars["id"].get()
        labels = []
        app._resolve_field_names("t_event_currencies", [17], labels.append)
        assert labels == [{"17": "活動硬幣"}]
        form.set_fields([{"name": "id", "type": "integer", "source": "m_non_unique"}])
        monkeypatch.setattr(app.client, "get", lambda *args, **kwargs: {
            "rows": [{"id": 1, "name": "不可唯一选择"}], "total": 1, "identity_field": None})
        monkeypatch.setattr(app, "_submit", lambda operation, complete, **kwargs: complete(operation()))
        app.pick_catalog(form, "id")
        widgets.clear()
        collect(dialogs[-1])
        button = next(widget for widget in widgets if widget.winfo_class() == "TButton" and widget.cget("text") == "填入 ID")
        assert str(button.cget("state")) == "disabled"
        assert str(next(widget for widget in widgets if isinstance(widget, DataTable)).tree.cget("selectmode")) == "none"
    finally:
        form.destroy()
        for dialog in dialogs:
            if dialog.winfo_exists():
                dialog.destroy()
