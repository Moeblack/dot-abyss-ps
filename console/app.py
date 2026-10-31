from __future__ import annotations

import json
import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from urllib.parse import quote

from .client import AdminClient, ConsoleError, read_token
from .display import display_value, matches_query, record_identity_field, record_rows, row_identity, row_label
from .labels import RULE_LABELS, SYNC_LABELS
from .models import GROUP_LABELS, actions_from_schema, compact
from .widgets import COLORS, DataTable, FieldForm, JsonView, configure_style


class BackgroundTasks:
    def __init__(self):
        self.jobs = queue.Queue()
        self.results = queue.Queue()
        self.closed = False
        for _ in range(2):
            threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        while True:
            job = self.jobs.get()
            if job is None:
                return
            function, success, failure = job
            try:
                result = function()
            except Exception as exc:
                self.results.put((failure, exc))
            else:
                self.results.put((success, result))

    def submit(self, function, success, failure):
        if not self.closed:
            self.jobs.put((function, success, failure))

    def close(self):
        self.closed = True
        while True:
            try:
                self.jobs.get_nowait()
            except queue.Empty:
                break
        self.jobs.put(None)
        self.jobs.put(None)


class ConsoleApp:
    def __init__(self, root: tk.Tk, origin=None, data_dir: str | Path | None = None, *, auto_connect=True, settings_path: Path | None = None):
        self.root = root
        self.root.title("Dot Abyss · 本地控制台")
        self.root.geometry("1320x860")
        self.root.minsize(1040, 680)
        configure_style(root)
        self.settings_path = settings_path or Path(os.environ.get("LOCALAPPDATA", Path.home())) / "DotAbyssConsole" / "settings.json"
        settings = self._load_settings()
        self.origin = origin if origin is not None else settings.get("origin", "http://127.0.0.1:18083")
        self.data_dir = str(Path(data_dir).resolve()) if data_dir is not None else settings.get("data_dir", str(Path(__file__).resolve().parents[1] / "data-pc-1.15.0"))
        self.client = None
        self.tasks = BackgroundTasks()
        self.generation = 0
        self.pending = 0
        self.closed = False
        self.schema = {}
        self.actions = []
        self.profiles = []
        self.profile_id = None
        self.snapshot = {}
        self.group = "overview"
        self.current_action = None
        self.preview = None
        self.preview_endpoint = None
        self.editor_revision = 0
        self.rule_data = {}
        self.master_data = {}
        self.master_table = ""
        self.master_row = None
        self.master_offset = 0
        self.history_entries = []
        self.group_buttons = {}
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<F5>", lambda _: self.refresh())
        self.root.bind("<Control-f>", self._focus_search)
        self.root.after(50, self._drain)
        self.root.after(5000, self._poll)
        if auto_connect:
            self.root.after(100, self.connect)

    def _load_settings(self):
        try:
            if self.settings_path.stat().st_size < 16384:
                value = json.loads(self.settings_path.read_text(encoding="utf-8"))
                return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            pass
        return {}

    def _save_settings(self):
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.settings_path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"origin": self.origin, "data_dir": self.data_dir}, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.settings_path)

    def _build(self):
        top = ttk.Frame(self.root, padding=(18, 14))
        top.pack(fill="x")
        ttk.Label(top, text="Dot Abyss", font=("Bahnschrift", 21, "bold")).pack(side="left")
        ttk.Label(top, text="本地控制台", style="Muted.TLabel").pack(side="left", padx=(12, 24))
        ttk.Label(top, text="当前存档", style="Muted.TLabel").pack(side="left", padx=(0, 8))
        self.profile_var = tk.StringVar()
        self.profile_box = ttk.Combobox(top, textvariable=self.profile_var, state="readonly", width=24)
        self.profile_box.pack(side="left")
        self.profile_box.bind("<<ComboboxSelected>>", self._profile_selected)
        self.connection_label = ttk.Label(top, text="尚未连接", style="Muted.TLabel")
        self.connection_label.pack(side="right", padx=(14, 0))
        ttk.Button(top, text="连接设置", command=self.connection_dialog).pack(side="right")
        ttk.Button(top, text="刷新", command=self.refresh).pack(side="right", padx=8)
        self.progressbar = ttk.Progressbar(self.root, mode="determinate", length=100)
        self.progressbar.pack(fill="x")

        body = ttk.Frame(self.root)
        body.pack(fill="both", expand=True, padx=(0, 16), pady=(0, 8))
        self.sidebar = tk.Frame(body, background=COLORS["sidebar"], width=180)
        self.sidebar.pack(side="left", fill="y", padx=(0, 16))
        self.sidebar.pack_propagate(False)
        self.sidebar_title = tk.Label(self.sidebar, text="管理内容", background=COLORS["sidebar"], foreground="#CBC0E1", anchor="w", padx=20, pady=14, font=("Microsoft YaHei UI", 9))
        self.sidebar_title.pack(fill="x")
        nav_canvas = tk.Canvas(self.sidebar, background=COLORS["sidebar"], highlightthickness=0, width=180)
        nav_scroll = ttk.Scrollbar(self.sidebar, orient="vertical", command=nav_canvas.yview)
        nav_scroll.pack(side="right", fill="y")
        nav_canvas.pack(side="left", fill="both", expand=True)
        self.navigation = tk.Frame(nav_canvas, background=COLORS["sidebar"])
        nav_window = nav_canvas.create_window((0, 0), window=self.navigation, anchor="nw")
        nav_canvas.configure(yscrollcommand=nav_scroll.set)
        self.navigation.bind("<Configure>", lambda _: nav_canvas.configure(scrollregion=nav_canvas.bbox("all")))
        nav_canvas.bind("<Configure>", lambda event: nav_canvas.itemconfigure(nav_window, width=event.width))
        self.main = ttk.Frame(body)
        self.main.pack(side="left", fill="both", expand=True)
        heading = ttk.Frame(self.main, padding=(0, 13, 0, 10))
        heading.pack(fill="x")
        self.title = ttk.Label(heading, text="连接与概况", style="Title.TLabel")
        self.title.pack(anchor="w")
        self.subtitle = ttk.Label(heading, text="选择服务数据目录，连接后读取当前可用的管理能力。", style="Muted.TLabel", wraplength=1040)
        self.subtitle.pack(anchor="w", pady=(5, 0))
        self.workspace = ttk.Frame(self.main)
        self.workspace.pack(fill="both", expand=True)
        self.footer_text = tk.StringVar(value="尚未连接服务。此窗口不会自动启动游戏或服务。")
        footer = ttk.Frame(self.root, padding=(18, 8))
        footer.pack(fill="x")
        self.footer = ttk.Label(footer, textvariable=self.footer_text, wraplength=950)
        self.footer.pack(side="left", fill="x", expand=True)
        self.sync_text = tk.StringVar(value="客户端同步：未连接")
        ttk.Label(footer, textvariable=self.sync_text, style="Muted.TLabel").pack(side="right", padx=(12, 0))
        self._build_navigation()
        self.show_group("overview")

    def _build_navigation(self):
        for widget in self.navigation.winfo_children():
            widget.destroy()
        groups = [{"id": "overview", "label": "连接与概况"}]
        if self.schema.get("groups"):
            groups.extend(self.schema["groups"])
        else:
            groups.extend({"id": key, "label": GROUP_LABELS[key]} for key in ("wallet", "items", "characters", "growth", "skins", "equipment", "party", "facilities", "story", "missions", "gacha", "shop", "tavern", "idle", "modes", "nether", "events", "social"))
        seen = {group["id"] for group in groups}
        for action in self.actions:
            if action.group not in seen:
                groups.append({"id": action.group, "label": GROUP_LABELS.get(action.group, action.group)})
                seen.add(action.group)
        groups.extend({"id": key, "label": GROUP_LABELS[key]} for key in ("rules", "master", "history", "backups") if key not in seen)
        self.group_buttons = {}
        for group in groups:
            identity = group["id"]
            label = group.get("label", identity)
            if label == identity:
                label = GROUP_LABELS.get(identity, identity)
            button = tk.Button(self.navigation, text=label, anchor="w", padx=20, pady=8, borderwidth=0, relief="flat", background=COLORS["sidebar"], foreground="#F5F1FC", activebackground=COLORS["sidebar_active"], activeforeground="white", cursor="hand2", command=lambda selected=identity: self.show_group(selected))
            button.pack(fill="x", pady=1)
            self.group_buttons[identity] = button

    def _submit(self, function, success, *, quiet=False, scope=True):
        generation = self.generation
        self.pending += 1
        self.progressbar.configure(mode="indeterminate")
        self.progressbar.start(12)
        def complete(result):
            self._task_finished()
            if not scope or generation == self.generation:
                success(result)
        def failed(error):
            self._task_finished()
            if not scope or generation == self.generation:
                self._error(error, quiet=quiet)
        self.tasks.submit(function, complete, failed)

    def _task_finished(self):
        self.pending = max(0, self.pending - 1)
        if not self.pending:
            self.progressbar.stop()
            self.progressbar.configure(mode="determinate", value=0)

    def _drain(self):
        if self.closed:
            return
        for _ in range(20):
            try:
                callback, value = self.tasks.results.get_nowait()
            except queue.Empty:
                break
            try:
                callback(value)
            except Exception as exc:
                self._error(exc)
        self.root.after(50, self._drain)

    def _error(self, error, *, quiet=False):
        if self.client is None:
            self.connection_label.configure(text="未连接", foreground=COLORS["danger"])
            self.sync_text.set("客户端同步：未连接")
        self.footer_text.set(str(error))
        self.footer.configure(foreground=COLORS["danger"])
        if not quiet:
            messagebox.showerror("操作未完成", str(error), parent=self.root)

    def _notice(self, text):
        self.footer_text.set(text)
        self.footer.configure(foreground=COLORS["text"])

    def _invalidate_preview(self):
        self.editor_revision += 1
        self.preview = None
        self.preview_endpoint = None
        if hasattr(self, "apply_button") and self.apply_button.winfo_exists():
            self.apply_button.configure(state="disabled")

    def _focus_search(self, _=None):
        if hasattr(self, "search_entry") and self.search_entry.winfo_exists():
            self.search_entry.focus_set()
            self.search_entry.selection_range(0, "end")

    def connection_dialog(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("连接本地服务")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=22)
        frame.pack(fill="both", expand=True)
        origin = tk.StringVar(value=self.origin)
        directory = tk.StringVar(value=self.data_dir)
        ttk.Label(frame, text="服务地址").grid(row=0, column=0, sticky="w")
        ttk.Entry(frame, textvariable=origin, width=54).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 15))
        ttk.Label(frame, text="服务数据目录（包含 admin-token.txt）").grid(row=2, column=0, columnspan=2, sticky="w")
        ttk.Entry(frame, textvariable=directory, width=45).grid(row=3, column=0, sticky="ew", pady=6)
        def browse():
            result = filedialog.askdirectory(parent=dialog, initialdir=directory.get())
            if result:
                directory.set(result)
        ttk.Button(frame, text="浏览…", command=browse).grid(row=3, column=1, padx=(8, 0))
        ttk.Label(frame, text="凭据只用于本机请求，不会保存到窗口配置或写入日志。", style="Muted.TLabel").grid(row=4, column=0, columnspan=2, sticky="w", pady=12)
        def confirm():
            self.origin, self.data_dir = origin.get().strip(), directory.get().strip()
            dialog.destroy()
            self.connect()
        ttk.Button(frame, text="连接", style="Accent.TButton", command=confirm).grid(row=5, column=1, sticky="e", pady=(8, 0))
        dialog.grab_set()
        dialog.wait_visibility()
        dialog.focus_set()

    def connect(self):
        self.generation += 1
        self._invalidate_preview()
        self.client = None
        self.profile_id = None
        self.snapshot = {}
        self.connection_label.configure(text="正在连接…", foreground=COLORS["muted"])
        origin, directory = self.origin, self.data_dir
        def load():
            client = AdminClient(origin, read_token(directory))
            status = client.get("/admin/status")
            schema = client.get("/admin/schema")
            profiles = client.get("/admin/profiles")
            return client, status, schema, profiles
        def done(result):
            self.client, self.status, self.schema, profiles = result
            self.actions = actions_from_schema(self.schema)
            self.profiles = profiles.get("profiles", [])
            self.profile_box.configure(values=[self._profile_label(profile) for profile in self.profiles])
            self.connection_label.configure(text="已连接 " + self.origin, foreground=COLORS["good"])
            self._build_navigation()
            try:
                self._save_settings()
            except OSError:
                pass
            if self.profiles:
                self.profile_box.current(0)
                self._profile_selected()
            else:
                self.profile_var.set("")
                self.show_group("overview")
            self._notice("已连接。选择功能后填写参数，先预览再应用。")
        self._submit(load, done, quiet=True)

    @staticmethod
    def _profile_label(profile):
        suffix = "" if profile.get("has_state", True) else " · 尚未建档"
        return f"{profile.get('name', '存档')}  #{profile['id']}{suffix}"

    def _profile_selected(self, _=None):
        index = self.profile_box.current()
        if index < 0 or not self.client:
            return
        self.generation += 1
        self.profile_id = self.profiles[index]["id"]
        self.snapshot = {}
        self._invalidate_preview()
        self.refresh_snapshot(lambda: self.show_group(self.group))

    def refresh_snapshot(self, callback=None):
        if not self.client or self.profile_id is None:
            return
        client, identity = self.client, self.profile_id
        def done(result):
            self.snapshot = result
            self._show_sync(result.get("sync", {}))
            if callback:
                callback()
        self._submit(lambda: client.get(f"/admin/profiles/{identity}/snapshot"), done)

    def _show_sync(self, sync):
        status = sync.get("status", "unknown")
        text = SYNC_LABELS.get(status, status if status != "unknown" else "尚无同步记录")
        revision, acknowledged = sync.get("revision"), sync.get("ack_revision")
        suffix = f"  {acknowledged or 0}/{revision}" if revision is not None else ""
        if sync.get("client_connected") is False:
            suffix += " · 客户端未在线"
        self.sync_text.set("客户端同步：" + text + suffix)

    def _poll(self):
        if self.closed:
            return
        if self.client and self.profile_id is not None and not self.pending:
            client, identity = self.client, self.profile_id
            def done(result):
                self._show_sync(result.get("sync", {}))
                if result.get("version") != self.snapshot.get("version"):
                    self._invalidate_preview()
                self.snapshot = result
            self._submit(lambda: client.get(f"/admin/profiles/{identity}/snapshot"), done, quiet=True)
        self.root.after(5000, self._poll)

    def refresh(self):
        if not self.client:
            self.connect()
        elif self.profile_id is not None:
            self._invalidate_preview()
            self.refresh_snapshot(lambda: self.show_group(self.group))
        else:
            self.show_group(self.group)

    def show_group(self, identity):
        self.group = identity
        self.current_action = None
        self._invalidate_preview()
        for key, button in self.group_buttons.items():
            button.configure(background=COLORS["sidebar_active"] if key == identity else COLORS["sidebar"])
        for widget in self.workspace.winfo_children():
            widget.destroy()
        label = self.group_buttons[identity].cget("text") if identity in self.group_buttons else GROUP_LABELS.get(identity, identity)
        self.title.configure(text=label)
        handlers = {"overview": self._overview, "rules": self._rules, "master": self._master, "history": self._history, "backups": self._backups}
        handlers.get(identity, self._domain)()

    def _overview(self):
        self.subtitle.configure(text="本机服务与当前存档；所有修改均需先检查预览。")
        panel = ttk.Frame(self.workspace, style="Surface.TFrame", padding=26)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="从这里管理你的本地游戏", style="Subtitle.TLabel").pack(anchor="w", pady=(0, 16))
        text = "连接后，左侧会列出当前服务支持的操作。\n角色、装备、剧情、玩法进度分别编辑；规则与主数据在独立页面管理。\n\n每次操作先查看变化，再应用到当前选中的存档。\n客户端未在线时可以保存修改，生效状态会明确显示。"
        ttk.Label(panel, text=text, style="Surface.TLabel", wraplength=800, justify="left").pack(anchor="w")
        ttk.Button(panel, text="连接本地服务" if not self.client else "更换连接", style="Accent.TButton", command=self.connection_dialog).pack(anchor="w", pady=22)
        self.overview_view = JsonView(panel)
        self.overview_view.pack(fill="both", expand=True)
        self.overview_view.show({"服务地址": self.origin, "数据目录": self.data_dir, "服务": getattr(self, "status", "未连接"), "当前存档": {key: self.snapshot.get(key) for key in ("profile_id", "master_revision", "version", "sync")}, "可用操作": len(self.actions)})

    def _require_client(self):
        if self.client:
            return True
        self.subtitle.configure(text="请先连接已更新的本地服务。")
        ttk.Button(self.workspace, text="连接设置", style="Accent.TButton", command=self.connection_dialog).pack(anchor="w", pady=20)
        return False

    def _domain(self):
        self.subtitle.configure(text="选择操作并填写参数；切换到存档数据可核对当前值，内容目录用于查找名称与 ID。")
        panes = ttk.Panedwindow(self.workspace, orient="horizontal")
        panes.pack(fill="both", expand=True)
        left = ttk.Frame(panes)
        right = ttk.Frame(panes, style="Surface.TFrame")
        panes.add(left, weight=4)
        panes.add(right, weight=5)
        self.left_tabs = ttk.Notebook(left)
        self.left_tabs.pack(fill="both", expand=True)
        actions_tab = ttk.Frame(self.left_tabs, style="Surface.TFrame", padding=12)
        state_tab = ttk.Frame(self.left_tabs, style="Surface.TFrame", padding=12)
        catalog_tab = ttk.Frame(self.left_tabs, style="Surface.TFrame", padding=12)
        self.left_tabs.add(actions_tab, text="可用操作")
        self.left_tabs.add(state_tab, text="存档数据")
        self.left_tabs.add(catalog_tab, text="内容目录")
        self.action_search = tk.StringVar()
        self.search_entry = ttk.Entry(actions_tab, textvariable=self.action_search)
        self.search_entry.pack(fill="x", pady=(0, 10))
        self.action_table = DataTable(actions_tab, on_select=self._select_action)
        self.action_table.pack(fill="both", expand=True)
        self.action_search.trace_add("write", lambda *_: self._filter_actions())
        self._filter_actions()
        self._build_state_browser(state_tab)
        self._build_catalog(catalog_tab)
        self.editor_title = ttk.Label(right, text="选择一个操作", style="Subtitle.TLabel", padding=(16, 16))
        self.editor_title.pack(fill="x")
        self.editor_hint = ttk.Label(right, text="服务端会校验前置和关联数据。", style="Hint.TLabel", wraplength=450, padding=(16, 0, 16, 12))
        self.editor_hint.pack(fill="x")
        self.editor_tabs = ttk.Notebook(right)
        self.editor_tabs.pack(fill="both", expand=True, padx=8)
        self.form = FieldForm(self.editor_tabs, on_change=self._invalidate_preview, on_pick=self.pick_catalog, on_resolve=self._resolve_field_names)
        self.preview_view = JsonView(self.editor_tabs)
        self.detail_view = JsonView(self.editor_tabs)
        self.editor_tabs.add(self.form, text="填写参数")
        self.editor_tabs.add(self.preview_view, text="修改预览")
        self.editor_tabs.add(self.detail_view, text="数据明细")
        toolbar = ttk.Frame(right, style="Surface.TFrame", padding=14)
        toolbar.pack(fill="x")
        self.apply_button = ttk.Button(toolbar, text="应用修改", style="Accent.TButton", state="disabled", command=self.apply_preview)
        self.apply_button.pack(side="right")
        ttk.Button(toolbar, text="预览修改", command=self.preview_action).pack(side="right", padx=(0, 8))
        if not self.client:
            self.editor_hint.configure(text="连接后才会加载可用操作；不会显示未实现功能的虚假按钮。")

    def _filter_actions(self):
        query = self.action_search.get().strip().casefold()
        rows = [{"label": item.label, "timing": self._timing_label(item.timing), "id": item.id} for item in self.actions if item.group == self.group and (not query or query in (item.label + item.id + item.description).casefold())]
        self.action_table.show(rows, [("label", "操作"), ("timing", "生效方式")])

    def _timing_label(self, timing):
        defaults = {"live": "同步后生效", "page": "页面刷新", "next_battle": "下一场战斗", "next_round": "下一轮", "master": "重载主数据", "relogin": "重新登录"}
        return self.schema.get("timings", {}).get(timing, defaults.get(timing, timing))

    def _select_action(self, row):
        self._invalidate_preview()
        self.current_action = next(item for item in self.actions if item.id == row["id"])
        self.editor_title.configure(text=self.current_action.label)
        self.editor_hint.configure(text=self.current_action.description + ("\n生效方式：" + self._timing_label(self.current_action.timing) if self.current_action.timing else ""))
        self.form.set_fields(self.current_action.fields)
        self.preview_view.show("填写参数后点击「预览修改」。")
        self.editor_tabs.select(self.form)

    def _build_state_browser(self, parent):
        self.state_table_name = tk.StringVar()
        names = sorted(self.snapshot.get("state", {}).get("records", {}).keys() | self.snapshot.get("record_sources", {}).keys())
        names.extend("progress." + key for key in sorted(self.snapshot.get("progress", {})))
        box = ttk.Combobox(parent, textvariable=self.state_table_name, values=names, state="readonly")
        box.pack(fill="x", pady=(0, 10))
        self.state_table = DataTable(parent, on_select=self._inspect)
        self.state_table.pack(fill="both", expand=True)
        box.bind("<<ComboboxSelected>>", lambda _: self._show_state_table())
        if names:
            hints = {"characters": "character", "skins": "skin", "equipment": "equipment", "story": "novel", "wallet": "user", "items": "item", "party": "party", "gacha": "gacha", "events": "event"}
            match = hints.get(self.group, self.group)
            self.state_table_name.set(next((name for name in names if match in name), names[0]))
            self._show_state_table()

    def _show_state_table(self):
        name = self.state_table_name.get()
        labels = None
        if name.startswith("progress."):
            value = self.snapshot.get("progress", {}).get(name[9:])
            labels = self.snapshot.get("progress_display", {}).get(name[9:])
        else:
            value = record_rows(self.snapshot, name)
        if isinstance(value, dict):
            rows = [{"key": key, "value": item} for key, item in value.items()]
            if isinstance(labels, dict):
                rows = [{**row, "_display": {"value": labels[row["key"]]}} if row["key"] in labels else row for row in rows]
        elif isinstance(value, list):
            rows = [item if isinstance(item, dict) else {"index": index, "value": item} for index, item in enumerate(value)]
            if isinstance(labels, list):
                for index, annotation in enumerate(labels[:len(rows)]):
                    if annotation is None:
                        continue
                    display = annotation if isinstance(value[index], dict) else {"value": annotation}
                    if isinstance(display, dict):
                        rows[index] = {**rows[index], "_display": {**rows[index].get("_display", {}), **display}}
        else:
            rows = [{"value": value}]
            if labels is not None:
                rows[0]["_display"] = {"value": labels}
        self.state_table.columns = []
        self.state_table.show(rows[:1000])
        if len(rows) > 1000:
            self._notice("存档表显示前 1000 行；导出存档可查看全部内容。")

    def _inspect(self, row):
        if hasattr(self, "detail_view") and self.detail_view.winfo_exists():
            self.detail_view.show(display_value(row))
            self.editor_tabs.select(self.detail_view)

    def _resolve_field_names(self, source, identifiers, complete):
        if source.startswith("t_"):
            requested = {str(identifier) for identifier in identifiers}
            identity = record_identity_field(self.snapshot, source)
            complete({str(row_identity(row, identity)): row_label(row) for row in record_rows(self.snapshot, source)
                      if str(row_identity(row, identity)) in requested and row_label(row)})
        elif self.client and source.startswith("m_"):
            client = self.client
            def load():
                try:
                    return client.post("/admin/resolve", {"table": source, "ids": identifiers})
                except ConsoleError:
                    return {"labels": {}}
            self._submit(load, lambda result: complete(result.get("labels", {})), quiet=True)
        else:
            complete({})

    def _build_catalog(self, parent):
        top = ttk.Frame(parent, style="Surface.TFrame")
        top.pack(fill="x", pady=(0, 8))
        sources = set()
        for action in self.actions:
            if action.group == self.group:
                for field in action.fields:
                    if field.get("source"):
                        sources.add(field["source"])
                    sources.update(field.get("source_by", {}).get("values", {}).values())
        sources = sorted(sources)
        self.catalog_source = tk.StringVar(value=sources[0] if sources else "m_characters")
        ttk.Combobox(top, textvariable=self.catalog_source, values=sources, width=22).pack(side="left", fill="x", expand=True)
        self.catalog_query = tk.StringVar()
        ttk.Entry(parent, textvariable=self.catalog_query).pack(fill="x", pady=(0, 8))
        ttk.Button(top, text="查找", command=lambda: self._load_catalog(0)).pack(side="right", padx=(8, 0))
        self.catalog_rows = DataTable(parent, on_select=self._inspect)
        self.catalog_rows.pack(fill="both", expand=True)
        self.catalog_offset = 0
        navigation = ttk.Frame(parent, style="Surface.TFrame")
        navigation.pack(fill="x", pady=(8, 0))
        ttk.Button(navigation, text="上一页", command=lambda: self._load_catalog(max(0, self.catalog_offset - 100))).pack(side="left")
        ttk.Button(navigation, text="下一页", command=lambda: self._load_catalog(self.catalog_offset + 100)).pack(side="right")
        self.catalog_count = ttk.Label(navigation, text="每页 100 条", style="Hint.TLabel")
        self.catalog_count.pack(side="left", padx=8)

    def _load_catalog(self, offset):
        if not self.client:
            return
        client, table, query = self.client, self.catalog_source.get(), self.catalog_query.get()
        self.catalog_offset = offset
        target = self.catalog_rows
        def done(result):
            if (not target.winfo_exists() or table != self.catalog_source.get() or offset != self.catalog_offset
                    or query != self.catalog_query.get()):
                return
            target.columns = []
            target.show(result.get("rows", []))
            self.catalog_count.configure(text=f"{offset + 1 if result.get('rows') else 0}–{offset + len(result.get('rows', []))} / {result.get('total', 0)}")
        if table.startswith("t_"):
            rows = [row for row in record_rows(self.snapshot, table) if matches_query(row, query)]
            done({"rows": rows[offset:offset + 100], "total": len(rows)})
        else:
            self._submit(lambda: client.get("/admin/catalog", table=table, q=query, offset=offset, limit=100), done)

    def preview_action(self):
        if not self.client or self.profile_id is None or not self.current_action:
            self._notice("请先连接、选择存档和操作。")
            return
        try:
            arguments = self.form.values()
        except ValueError as exc:
            self._error(exc)
            return
        if not self.snapshot.get("version"):
            self._notice("存档尚未读取完成，请先刷新。")
            return
        payload = {"operation": self.current_action.id, "args": arguments, "expected_version": self.snapshot["version"]}
        self._request_preview(f"/admin/profiles/{self.profile_id}", payload)

    def _request_preview(self, endpoint, payload):
        self._invalidate_preview()
        client = self.client
        revision = self.editor_revision
        target = self.preview_view
        def done(result):
            if not target.winfo_exists() or self.editor_revision != revision:
                return
            self.preview, self.preview_endpoint = result, endpoint
            lines = [result.get("summary", "修改预览"), "", "生效方式：" + self._timing_label(result.get("timing", "")), ""]
            for change in result.get("changes", []):
                lines.append(f"{change.get('table', '数据')}：新增 {change.get('added', 0)}，修改 {change.get('updated', 0)}，删除 {change.get('removed', 0)}")
                if change.get("examples"):
                    lines.append(json.dumps(display_value(change)["examples"], ensure_ascii=False, indent=2))
            if result.get("warnings"):
                lines.extend(["", "请留意：", *["• " + str(warning) for warning in result["warnings"]]])
            visible_arguments = payload.get("args", payload.get("changes", {}))
            if visible_arguments and "snapshot" not in visible_arguments:
                displayed = self.form.display_arguments(visible_arguments) if hasattr(self, "form") and self.form.winfo_exists() else visible_arguments
                lines.extend(["", "提交内容（名称仅供核对，实际仍提交 ID）：", json.dumps(displayed, ensure_ascii=False, indent=2)])
            lines.extend(["", "确认内容后点击「应用修改」。预览过期或数据变化时需要重新预览。"])
            self.preview_view.show("\n".join(lines))
            self.editor_tabs.select(self.preview_view)
            self.apply_button.configure(state="normal" if result.get("ticket") else "disabled")
            self._notice("预览已生成，请核对变化和提示后应用。")
        self._submit(lambda: client.post(endpoint + "/preview", payload), done)

    def apply_preview(self):
        if not self.preview or not self.preview_endpoint or not self.client:
            return
        ticket, endpoint, client = self.preview["ticket"], self.preview_endpoint, self.client
        target = self.preview_view
        self._invalidate_preview()
        def done(result):
            status = {"saved": "已保存", "applied": "客户端已应用", "pending": "等待客户端同步"}.get(result.get("status"), result.get("status", ""))
            self._notice(result.get("summary", "修改已保存。") + "  " + str(status))
            if target.winfo_exists():
                target.show(result)
            if endpoint.endswith("/rules") and self.group == "rules":
                self._load_rules()
            elif endpoint.endswith("/master") and self.group == "master":
                self._load_master_rows(self.master_offset)
            elif endpoint.startswith("/admin/profiles/"):
                self.refresh_snapshot()
        self._submit(lambda: client.post(endpoint + "/apply", {"ticket": ticket}), done)

    def pick_catalog(self, form, name):
        if not self.client:
            return
        spec = form.controls[name][0]
        source = form.source(name)
        if not source:
            self._notice("请先选择内容类型，再打开对应目录。")
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("选择 " + spec.get("label", name))
        dialog.geometry("800x580")
        dialog.transient(self.root)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill="both", expand=True)
        query = tk.StringVar()
        ttk.Entry(frame, textvariable=query).pack(fill="x", pady=(0, 8))
        table = DataTable(frame)
        if spec.get("type") == "ids":
            table.tree.configure(selectmode="extended")
        table.pack(fill="both", expand=True)
        footer = ttk.Frame(frame)
        footer.pack(fill="x", pady=(12, 0))
        notice = ttk.Label(footer, text="选择一条记录后填入 ID。", style="Muted.TLabel")
        notice.pack(side="left")
        client = self.client
        identity = {"field": "id"}
        request_number = 0
        def search():
            nonlocal request_number
            request_number += 1
            request = request_number
            search_text = query.get()
            def done(result):
                if dialog.winfo_exists() and request == request_number:
                    identity["field"] = result.get("identity_field", "id")
                    table.columns = []
                    table.show(result.get("rows", []))
                    choose_button.configure(state="disabled" if identity["field"] is None else "normal")
                    table.tree.configure(selectmode="none" if identity["field"] is None else "extended" if spec.get("type") == "ids" else "browse")
                    notice.configure(text="此目录没有单列唯一 ID，仅供查看。" if identity["field"] is None else
                                     f"显示前 {len(result.get('rows', []))} / {result.get('total', 0)} 条；可用名称或 ID 缩小范围。")
            if source.startswith("t_"):
                rows = [row for row in record_rows(self.snapshot, source) if matches_query(row, search_text)]
                done({"rows": rows[:100], "total": len(rows), "identity_field": record_identity_field(self.snapshot, source)})
            else:
                self._submit(lambda: client.get("/admin/catalog", table=source, q=search_text, offset=0, limit=100), done)
        def choose():
            rows = table.selections()
            if not rows:
                return
            identities = [row_identity(row, identity["field"]) for row in rows] if identity["field"] else []
            if not identities or any(identifier is None for identifier in identities):
                self._error(ValueError("此记录没有可识别的 ID，请查看明细。"))
                return
            if spec.get("type") == "ids":
                try:
                    existing = form.field_value(name) or []
                except ValueError:
                    existing = []
                form.set_value(name, list(dict.fromkeys([*existing, *identities])))
            else:
                form.set_value(name, identities[0])
            form.remember_selection(name, rows, identity["field"])
            dialog.destroy()
        ttk.Button(footer, text="搜索", command=search).pack(side="right", padx=(8, 0))
        choose_button = ttk.Button(footer, text="填入 ID", style="Accent.TButton", command=choose)
        choose_button.pack(side="right")
        dialog.bind("<Return>", lambda _: search())
        search()

    def _editor_layout(self, left_title):
        panes = ttk.Panedwindow(self.workspace, orient="horizontal")
        panes.pack(fill="both", expand=True)
        left = ttk.Frame(panes, style="Surface.TFrame", padding=12)
        right = ttk.Frame(panes, style="Surface.TFrame", padding=8)
        panes.add(left, weight=4)
        panes.add(right, weight=5)
        ttk.Label(left, text=left_title, style="Subtitle.TLabel").pack(anchor="w", pady=(0, 12))
        self.editor_tabs = ttk.Notebook(right)
        self.editor_tabs.pack(fill="both", expand=True)
        self.form = FieldForm(self.editor_tabs, on_change=self._invalidate_preview, on_pick=self.pick_catalog, on_resolve=self._resolve_field_names)
        self.preview_view = JsonView(self.editor_tabs)
        self.editor_tabs.add(self.form, text="编辑")
        self.editor_tabs.add(self.preview_view, text="修改预览")
        toolbar = ttk.Frame(right, style="Surface.TFrame", padding=(6, 12, 6, 0))
        toolbar.pack(fill="x")
        self.apply_button = ttk.Button(toolbar, text="应用修改", state="disabled", style="Accent.TButton", command=self.apply_preview)
        self.apply_button.pack(side="right")
        self.editor_preview_button = ttk.Button(toolbar, text="预览修改")
        self.editor_preview_button.pack(side="right", padx=8)
        return left

    def _rules(self):
        self.subtitle.configure(text="修改服务端已有的本地规则。当前轮次和战斗保留原快照；初始赠送不会重复发给旧档。")
        if not self._require_client():
            return
        left = self._editor_layout("规则目录")
        self.rules_query = tk.StringVar()
        self.search_entry = ttk.Entry(left, textvariable=self.rules_query)
        self.search_entry.pack(fill="x", pady=(0, 10))
        self.rules_table = DataTable(left, on_select=self._select_rule)
        self.rules_table.pack(fill="both", expand=True)
        self.rules_query.trace_add("write", lambda *_: self._filter_rules())
        self.editor_preview_button.configure(command=self._preview_rule)
        self._load_rules()

    def _load_rules(self):
        if not self.client:
            return
        client = self.client
        target = self.rules_table
        def done(result):
            self.rule_data = result
            if target.winfo_exists():
                self._filter_rules()
        self._submit(lambda: client.get("/admin/rules"), done)

    def _filter_rules(self):
        query = self.rules_query.get().casefold()
        specs = {spec["name"]: spec for spec in self.rule_data.get("specs", [])}
        rows = [{"name": name, "label": RULE_LABELS.get(name, specs.get(name, {}).get("label", name)), "value": value} for name, value in self.rule_data.get("values", {}).items() if not query or query in (name + RULE_LABELS.get(name, specs.get(name, {}).get("label", ""))).casefold()]
        self.rules_table.show(rows, [("label", "规则"), ("value", "当前值")])

    def _select_rule(self, row):
        self._invalidate_preview()
        spec = dict(next((item for item in self.rule_data.get("specs", []) if item["name"] == row["name"]), {"name": row["name"], "type": "json", "readonly": True}))
        spec["label"] = RULE_LABELS.get(row["name"], spec.get("label", row["name"]))
        spec["description"] = row["name"]
        self.form.set_fields([spec], {row["name"]: row["value"]})
        self.editor_tabs.select(self.form)

    def _preview_rule(self):
        try:
            changes = {key: value for key, value in self.form.values().items() if value != self.rule_data.get("values", {}).get(key)}
            if not changes:
                raise ValueError("请先选择规则并修改值。")
            self._request_preview("/admin/rules", {"changes": changes, "expected_version": self.rule_data["version"]})
        except (ValueError, KeyError) as exc:
            self._error(exc)

    def _master(self):
        self.subtitle.configure(text="从原始主表生成覆盖版本。只开放经过服务端确认的字段，发布后按提示重新加载客户端数据。")
        if not self._require_client():
            return
        left = self._editor_layout("主数据目录")
        self.master_table_var = tk.StringVar()
        self.master_box = ttk.Combobox(left, textvariable=self.master_table_var, state="readonly")
        self.master_box.pack(fill="x", pady=(0, 8))
        self.master_box.bind("<<ComboboxSelected>>", lambda _: self._load_master_rows(0))
        search = ttk.Frame(left, style="Surface.TFrame")
        search.pack(fill="x", pady=(0, 8))
        self.master_query = tk.StringVar()
        self.search_entry = ttk.Entry(search, textvariable=self.master_query)
        self.search_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(search, text="搜索", command=lambda: self._load_master_rows(0)).pack(side="right", padx=(8, 0))
        self.master_rows = DataTable(left, on_select=self._select_master_row)
        self.master_rows.pack(fill="both", expand=True)
        navigation = ttk.Frame(left, style="Surface.TFrame")
        navigation.pack(fill="x", pady=(10, 0))
        ttk.Button(navigation, text="上一页", command=lambda: self._load_master_rows(max(0, self.master_offset - 100))).pack(side="left")
        ttk.Button(navigation, text="下一页", command=lambda: self._load_master_rows(self.master_offset + 100)).pack(side="right")
        self.master_count = ttk.Label(navigation, text="", style="Hint.TLabel")
        self.master_count.pack(side="left", padx=8)
        self.editor_preview_button.configure(command=self._preview_master)
        client, target = self.client, self.master_box
        def done(result):
            if not target.winfo_exists():
                return
            self.master_tables = result.get("tables", [])
            target.configure(values=[f"{item['name']} ({item.get('count', 0)})" for item in self.master_tables])
            if self.master_tables:
                target.current(0)
                self._load_master_rows(0)
        self._submit(lambda: client.get("/admin/master/tables"), done)

    def _load_master_rows(self, offset):
        if not self.client or not self.master_box.winfo_exists() or self.master_box.current() < 0:
            return
        self._invalidate_preview()
        table = self.master_tables[self.master_box.current()]["name"]
        self.master_table, self.master_offset = table, offset
        self.master_row = None
        self.form.set_fields([])
        client, query, target = self.client, self.master_query.get(), self.master_rows
        def done(result):
            if not target.winfo_exists() or table != self.master_table or offset != self.master_offset:
                return
            self.master_data = result
            rows = [{"index": row["index"], **row["values"], "_display": row.get("display", {})} for row in result.get("rows", [])]
            target.columns = []
            target.show(rows)
            self.master_count.configure(text=f"{offset + 1 if rows else 0}–{offset + len(rows)} / {result.get('total', 0)}")
        self._submit(lambda: client.get("/admin/master/table/" + quote(table, safe=""), q=query, offset=offset, limit=100), done)

    def _select_master_row(self, row):
        self._invalidate_preview()
        self.master_row = row
        fields = [dict(spec, readonly=not spec.get("editable", False)) for spec in self.master_data.get("fields", [])]
        self.form.set_fields(fields, row)
        self.editor_tabs.select(self.form)

    def _preview_master(self):
        if self.master_row is None:
            self._notice("请先选择一条主数据记录。")
            return
        try:
            editable = {spec["name"] for spec in self.master_data.get("fields", []) if spec.get("editable")}
            changes = {key: value for key, value in self.form.values().items() if key in editable and value != self.master_row.get(key)}
            if not changes:
                raise ValueError("尚未修改可编辑字段。")
            self._request_preview("/admin/master", {"table": self.master_table, "index": self.master_row["index"], "changes": changes, "expected_version": self.master_data["version"]})
        except (ValueError, KeyError) as exc:
            self._error(exc)

    def _history(self):
        self.subtitle.configure(text="查看当前存档的管理操作、保存结果和客户端同步情况。")
        if not self._require_client():
            return
        pane = ttk.Panedwindow(self.workspace, orient="vertical")
        pane.pack(fill="both", expand=True)
        detail = JsonView(pane)
        table = DataTable(pane, on_select=detail.show)
        pane.add(table, weight=3)
        pane.add(detail, weight=2)
        if self.profile_id is None:
            return
        client, identity = self.client, self.profile_id
        def done(result):
            self.history_entries = result.get("entries", [])
            if table.winfo_exists():
                table.show(self.history_entries, [("id", "编号"), ("at", "时间"), ("summary", "操作"), ("timing", "生效方式"), ("status", "状态")])
        self._submit(lambda: client.get(f"/admin/profiles/{identity}/history"), done)

    def _backups(self):
        self.subtitle.configure(text="导出当前存档，或从历史备份／导出文件生成恢复预览；不会直接覆盖当前进度。")
        if not self._require_client():
            return
        top = ttk.Frame(self.workspace)
        top.pack(fill="x", pady=(0, 12))
        ttk.Button(top, text="创建备份", command=self._create_backup).pack(side="left")
        ttk.Button(top, text="导出存档…", command=self._export_save).pack(side="left", padx=8)
        ttk.Button(top, text="导入并预览…", command=self._import_save).pack(side="left")
        ttk.Button(top, text="预览恢复到操作前", command=lambda: self._restore_history("before")).pack(side="right")
        panes = ttk.Panedwindow(self.workspace, orient="vertical")
        panes.pack(fill="both", expand=True)
        self.backup_table = DataTable(panes)
        lower = ttk.Frame(panes, style="Surface.TFrame", padding=8)
        panes.add(self.backup_table, weight=2)
        panes.add(lower, weight=3)
        self.editor_tabs = ttk.Notebook(lower)
        self.editor_tabs.pack(fill="both", expand=True)
        self.preview_view = JsonView(self.editor_tabs)
        self.editor_tabs.add(self.preview_view, text="恢复预览")
        self.apply_button = ttk.Button(lower, text="应用恢复", style="Accent.TButton", state="disabled", command=self.apply_preview)
        self.apply_button.pack(anchor="e", pady=(8, 0))
        self._load_backups()

    def _load_backups(self):
        if not self.client or self.profile_id is None:
            return
        client, identity, target = self.client, self.profile_id, self.backup_table
        def done(result):
            if target.winfo_exists():
                entries = [entry for entry in result.get("entries", []) if entry.get("operation") not in {"rules.update", "master.update"}]
                target.show(entries, [("id", "编号"), ("at", "时间"), ("summary", "操作／备份"), ("status", "状态")])
        self._submit(lambda: client.get(f"/admin/profiles/{identity}/history"), done)

    def _create_backup(self):
        if not self.client or self.profile_id is None or not self.snapshot.get("version"):
            return
        client, identity, version = self.client, self.profile_id, self.snapshot["version"]
        def done(result):
            self._notice(result.get("summary", "备份已创建。"))
            if self.group == "backups":
                self._load_backups()
        self._submit(lambda: client.post(f"/admin/profiles/{identity}/backup", {"expected_version": version}), done)

    def _export_save(self):
        if not self.client or self.profile_id is None:
            return
        path = filedialog.asksaveasfilename(parent=self.root, title="导出本地存档", defaultextension=".json", initialfile=f"dotabyss-profile-{self.profile_id}.json", filetypes=[("JSON 存档", "*.json")])
        if not path:
            return
        client, identity = self.client, self.profile_id
        def export():
            result = client.get(f"/admin/profiles/{identity}/export")
            Path(path).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            return path
        self._submit(export, lambda result: self._notice("存档已导出：" + result))

    def _import_save(self):
        if not self.client or self.profile_id is None:
            return
        path = filedialog.askopenfilename(parent=self.root, title="选择导出的本地存档", filetypes=[("JSON 存档", "*.json")])
        if not path:
            return
        identity, version = self.profile_id, self.snapshot.get("version")
        def read():
            if Path(path).stat().st_size > 32 * 1024 * 1024:
                raise ValueError("存档文件超过 32 MiB，请检查选择的文件。")
            return json.loads(Path(path).read_text(encoding="utf-8-sig"))
        def done(snapshot):
            if self.group == "backups":
                self._request_preview(f"/admin/profiles/{identity}", {"operation": "save.import", "args": {"snapshot": snapshot}, "expected_version": version})
        self._submit(read, done)

    def _restore_history(self, which):
        row = self.backup_table.selection()
        if row is None or self.profile_id is None:
            self._notice("请先选择一条备份或操作记录。")
            return
        self._request_preview(f"/admin/profiles/{self.profile_id}", {"operation": "save.restore", "args": {"history_id": row["id"], "which": which}, "expected_version": self.snapshot.get("version")})

    def close(self):
        self.closed = True
        self.tasks.close()
        self.root.destroy()
