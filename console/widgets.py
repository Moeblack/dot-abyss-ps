from __future__ import annotations

import json
import tkinter as tk
from tkinter import ttk

from .models import compact, parse_field


COLORS = {
    "background": "#F1EEF8", "surface": "#FFFFFF", "sidebar": "#30284D", "sidebar_active": "#51416F",
    "text": "#28233D", "muted": "#6F687F", "line": "#D9D2E8", "accent": "#6541A5",
    "gold": "#C58C26", "good": "#287568", "danger": "#AA364D", "selected": "#E7DEF7",
}


def configure_style(root: tk.Tk):
    root.configure(background=COLORS["background"])
    root.option_add("*Font", ("Microsoft YaHei UI", 10))
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", font=("Microsoft YaHei UI", 10), foreground=COLORS["text"], background=COLORS["background"])
    style.configure("TFrame", background=COLORS["background"])
    style.configure("Surface.TFrame", background=COLORS["surface"])
    style.configure("Surface.TLabel", background=COLORS["surface"])
    style.configure("TLabel", background=COLORS["background"])
    style.configure("Muted.TLabel", foreground=COLORS["muted"])
    style.configure("Hint.TLabel", foreground=COLORS["muted"], background=COLORS["surface"], font=("Microsoft YaHei UI", 9))
    style.configure("Title.TLabel", font=("Microsoft YaHei UI", 17, "bold"))
    style.configure("Subtitle.TLabel", font=("Microsoft YaHei UI", 12, "bold"), background=COLORS["surface"])
    style.configure("TButton", padding=(12, 7), borderwidth=1, relief="flat", background=COLORS["surface"])
    style.map("TButton", background=[("active", COLORS["selected"]), ("pressed", COLORS["line"])])
    style.configure("Accent.TButton", foreground="white", background=COLORS["accent"], borderwidth=0)
    style.map("Accent.TButton", background=[("disabled", COLORS["line"]), ("active", "#7854B8")], foreground=[("disabled", COLORS["muted"])])
    style.configure("TEntry", fieldbackground="white", padding=6)
    style.configure("TCombobox", padding=6, fieldbackground="white")
    style.map("TCombobox", fieldbackground=[("readonly", "white")])
    style.configure("Treeview", background="white", fieldbackground="white", rowheight=31, borderwidth=0)
    style.configure("Treeview.Heading", background=COLORS["background"], font=("Microsoft YaHei UI", 9, "bold"), padding=(8, 6))
    style.map("Treeview", background=[("selected", COLORS["selected"])], foreground=[("selected", COLORS["text"])])
    style.configure("TNotebook", borderwidth=0, background=COLORS["background"])
    style.configure("TNotebook.Tab", padding=(15, 8))
    style.map("TNotebook.Tab", background=[("selected", "white")], foreground=[("selected", COLORS["accent"])])
    style.configure("TCheckbutton", background="white")
    style.configure("Horizontal.TProgressbar", background=COLORS["accent"], troughcolor=COLORS["line"], borderwidth=0)


class ScrollFrame(ttk.Frame):
    def __init__(self, master, **kwargs):
        super().__init__(master, style="Surface.TFrame", **kwargs)
        self.canvas = tk.Canvas(self, background=COLORS["surface"], highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.content = ttk.Frame(self.canvas, style="Surface.TFrame", padding=16)
        self.window_id = self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.content.bind("<Configure>", lambda _: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(self.window_id, width=event.width))
        self.canvas.bind("<MouseWheel>", lambda event: self.canvas.yview_scroll(-int(event.delta / 120), "units"))

    def clear(self):
        for widget in self.content.winfo_children():
            widget.destroy()
        self.canvas.yview_moveto(0)


class JsonView(ttk.Frame):
    def __init__(self, master, height: int = 12):
        super().__init__(master, style="Surface.TFrame")
        self.text = tk.Text(self, wrap="none", height=height, background="white", foreground=COLORS["text"], relief="flat", padx=12, pady=10, font=("Consolas", 10), state="disabled")
        vertical = ttk.Scrollbar(self, command=self.text.yview)
        horizontal = ttk.Scrollbar(self, orient="horizontal", command=self.text.xview)
        self.text.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.text.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

    def show(self, value):
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", text)
        self.text.configure(state="disabled")


class FieldForm(ScrollFrame):
    def __init__(self, master, on_change=None, on_pick=None):
        super().__init__(master)
        self.fields = []
        self.controls = {}
        self.on_change = on_change or (lambda: None)
        self.on_pick = on_pick
        self.loading = False

    def set_fields(self, fields: list[dict], values: dict | None = None):
        self.loading = True
        self.clear()
        self.fields = fields
        self.controls = {}
        values = values or {}
        self.content.columnconfigure(0, weight=1)
        for position, spec in enumerate(fields):
            name = spec["name"]
            kind = spec.get("type", "string")
            value = values.get(name, spec.get("default", ""))
            label = spec.get("label", name)
            ttk.Label(self.content, text=label + (" *" if spec.get("required") else ""), style="Surface.TLabel").grid(row=position * 3, column=0, sticky="w", pady=(12 if position else 0, 5))
            frame = ttk.Frame(self.content, style="Surface.TFrame")
            frame.grid(row=position * 3 + 1, column=0, sticky="ew")
            frame.columnconfigure(0, weight=1)
            choices = spec.get("choices", spec.get("enum"))
            if kind in {"boolean", "bool"} and not spec.get("required") and "default" not in spec:
                choice_map = {"保持不变": None, "是": True, "否": False}
                selected = next((label for label, item in choice_map.items() if type(item) is type(value) and item == value), "保持不变")
                variable = tk.StringVar(value=selected)
                widget = ttk.Combobox(frame, textvariable=variable, values=list(choice_map), state="readonly")
                self.controls[name] = (spec, variable, choice_map)
            elif kind in {"boolean", "bool"}:
                variable = tk.BooleanVar(value=bool(value))
                widget = ttk.Checkbutton(frame, text="启用", variable=variable)
                self.controls[name] = (spec, variable, None)
            elif choices:
                choice_map = self.choice_map(choices)
                selected = next((label for label, item in choice_map.items() if item == value), "")
                variable = tk.StringVar(value=selected)
                widget = ttk.Combobox(frame, textvariable=variable, values=list(choice_map), state="readonly")
                self.controls[name] = (spec, variable, choice_map)
            elif kind in {"object", "json", "array", "list", "ids", "integer[]"}:
                widget = tk.Text(frame, height=4 if kind in {"object", "json"} else 2, wrap="word", font=("Consolas", 10), relief="solid", borderwidth=1, background="white", foreground=COLORS["text"], padx=7, pady=7, undo=True)
                if isinstance(value, (dict, list)):
                    value = json.dumps(value, ensure_ascii=False, indent=2)
                widget.insert("1.0", str(value))
                widget.edit_modified(False)
                widget.bind("<<Modified>>", self._text_changed)
                self.controls[name] = (spec, widget, None)
                variable = None
            else:
                variable = tk.StringVar(value=str(value) if value is not None else "")
                widget = ttk.Entry(frame, textvariable=variable)
                self.controls[name] = (spec, variable, None)
            widget.grid(row=0, column=0, sticky="ew")
            if variable is not None:
                variable.trace_add("write", lambda *_, field_name=name: self._field_changed(field_name))
            if spec.get("readonly") or spec.get("editable") is False:
                widget.configure(state="disabled")
            elif (spec.get("source") or spec.get("source_by")) and self.on_pick:
                ttk.Button(frame, text="选择…", command=lambda item=name: self.on_pick(self, item)).grid(row=0, column=1, padx=(6, 0))
            hint = spec.get("description", spec.get("help", ""))
            bounds = []
            if "min" in spec or "minimum" in spec:
                bounds.append(f"最小 {spec.get('min', spec.get('minimum'))}")
            if "max" in spec or "maximum" in spec:
                bounds.append(f"最大 {spec.get('max', spec.get('maximum'))}")
            if bounds:
                hint += ("；" if hint else "") + "，".join(bounds)
            if not hint and kind == "ids":
                hint = "多个 ID 用逗号分隔，也可从目录选择。"
            if hint:
                ttk.Label(self.content, text=hint, style="Hint.TLabel", wraplength=390).grid(row=position * 3 + 2, column=0, sticky="w", pady=(4, 2))
        self.loading = False

    @staticmethod
    def choice_map(choices):
        if isinstance(choices, dict):
            return {str(label): value for value, label in choices.items()}
        mapping = {}
        for choice in choices:
            if isinstance(choice, dict):
                value = choice.get("value", choice.get("id"))
                label = choice.get("label", choice.get("name", str(value)))
            else:
                value, label = choice, str(choice)
            mapping[str(label)] = value
        return mapping

    def _text_changed(self, event):
        if event.widget.edit_modified():
            event.widget.edit_modified(False)
            self._changed()

    def _changed(self):
        if not self.loading:
            self.on_change()

    def _field_changed(self, name):
        if self.loading:
            return
        for field in self.fields:
            if field.get("source_by", {}).get("field") == name and field["name"] != name:
                self.set_value(field["name"], [] if field.get("type") == "ids" else "")
        self._changed()

    def source(self, name):
        spec = self.controls[name][0]
        dependency = spec.get("source_by")
        if dependency:
            _, control, choices = self.controls[dependency["field"]]
            value = control.get()
            if choices is not None:
                value = choices.get(value)
            return dependency.get("values", {}).get(str(value), "")
        return spec.get("source", "")

    def values(self) -> dict:
        result = {}
        for name, (spec, control, choices) in self.controls.items():
            if spec.get("readonly") or spec.get("editable") is False:
                continue
            raw = control.get("1.0", "end-1c") if isinstance(control, tk.Text) else control.get()
            if choices is not None:
                if raw not in choices:
                    if spec.get("required"):
                        raise ValueError(f"{spec.get('label', name)}：请选择一个值。")
                    continue
                if choices[raw] is not None:
                    result[name] = choices[raw]
                continue
            parsed = parse_field(spec, raw)
            if parsed is not None:
                result[name] = parsed
        return result

    def set_value(self, name: str, value):
        spec, control, choices = self.controls[name]
        if choices is not None:
            control.set(next((label for label, item in choices.items() if item == value), ""))
        elif isinstance(control, tk.Text):
            control.delete("1.0", "end")
            control.insert("1.0", json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value))
        else:
            control.set(value)


class DataTable(ttk.Frame):
    def __init__(self, master, on_select=None, columns=None):
        super().__init__(master, style="Surface.TFrame")
        self.tree = ttk.Treeview(self, show="headings", selectmode="browse")
        vertical = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        horizontal = ttk.Scrollbar(self, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.rows = []
        self.columns = columns or []
        self.on_select = on_select
        self.tree.bind("<<TreeviewSelect>>", self._selected)

    def show(self, rows: list[dict], columns=None):
        self.rows = rows
        self.tree.delete(*self.tree.get_children())
        if columns is not None:
            self.columns = columns
        if not self.columns:
            self.columns = [(name, name) for name in dict.fromkeys(key for row in rows[:20] for key in row)][:8]
        self.tree.configure(columns=[name for name, _ in self.columns])
        for name, label in self.columns:
            self.tree.heading(name, text=label)
            self.tree.column(name, width=160 if name not in {"id", "index"} else 80, minwidth=60, stretch=True)
        for index, row in enumerate(rows):
            self.tree.insert("", "end", iid=str(index), values=[compact(row.get(name)) for name, _ in self.columns])

    def _selected(self, _):
        selection = self.tree.selection()
        if selection and self.on_select:
            self.on_select(self.rows[int(selection[0])])

    def selection(self):
        items = self.tree.selection()
        return self.rows[int(items[0])] if items else None

    def selections(self):
        return [self.rows[int(item)] for item in self.tree.selection()]
