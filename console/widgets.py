from __future__ import annotations

import json
import tkinter as tk
from collections import Counter
from tkinter import ttk

from .display import cell_value, display_value, preferred_columns, row_identity, row_label
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
        text = value if isinstance(value, str) else json.dumps(display_value(value), ensure_ascii=False, indent=2, allow_nan=False)
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", text)
        self.text.configure(state="disabled")


class FieldForm(ScrollFrame):
    def __init__(self, master, on_change=None, on_pick=None, on_resolve=None):
        super().__init__(master)
        self.fields = []
        self.controls = {}
        self.on_change = on_change or (lambda: None)
        self.on_pick = on_pick
        self.on_resolve = on_resolve
        self.loading = False
        self.reference_jobs = {}
        self.reference_serial = 0

    def set_fields(self, fields: list[dict], values: dict | None = None):
        self.loading = True
        for job in self.reference_jobs.values():
            self.after_cancel(job)
        self.reference_jobs = {}
        self.reference_serial += 1
        self.clear()
        self.fields = fields
        self.controls = {}
        self.reference_labels = {}
        self.reference_sources = {}
        self.reference_revisions = {}
        self.hint_vars = {}
        self.base_hints = {}
        values = values or {}
        self.initial_values = values
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
                selected = next((label for label, item in choice_map.items() if type(item) is type(value) and item == value), "")
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
            self.base_hints[name] = hint
            hint_variable = tk.StringVar(value=hint)
            self.hint_vars[name] = hint_variable
            if hint or spec.get("source") or spec.get("source_by") or name in values.get("_display", {}):
                ttk.Label(self.content, textvariable=hint_variable, style="Hint.TLabel", wraplength=390).grid(row=position * 3 + 2, column=0, sticky="w", pady=(4, 2))
        self.loading = False
        for name in self.controls:
            self._refresh_reference(name)

    @staticmethod
    def choice_map(choices):
        if isinstance(choices, dict):
            entries = [(value, str(label)) for value, label in choices.items()]
        else:
            entries = []
            for choice in choices:
                if isinstance(choice, dict):
                    value = choice.get("value", choice.get("id"))
                    label = choice.get("label", choice.get("name", str(value)))
                else:
                    value, label = choice, str(choice)
                entries.append((value, str(label)))
        counts = Counter(label for _, label in entries)
        mapping = {}
        for index, (value, label) in enumerate(entries):
            if counts[label] > 1:
                label += "（值: " + json.dumps(value, ensure_ascii=False) + "）"
            if label in mapping:
                label += f" [{index + 1}]"
            mapping[label] = value
        return mapping

    def _text_changed(self, event):
        if event.widget.edit_modified():
            event.widget.edit_modified(False)
            name = next((name for name, (_, control, _) in self.controls.items() if control is event.widget), None)
            self._field_changed(name) if name is not None else self._changed()

    def _changed(self):
        if not self.loading:
            self.on_change()

    def _field_changed(self, name):
        if self.loading:
            return
        for field in self.fields:
            if field.get("source_by", {}).get("field") == name and field["name"] != name:
                self.reference_labels.pop(field["name"], None)
                self.set_value(field["name"], [] if field.get("type") == "ids" else "")
        self._refresh_reference(name)
        self._changed()

    def field_value(self, name):
        spec, control, choices = self.controls[name]
        raw = control.get("1.0", "end-1c") if isinstance(control, tk.Text) else control.get()
        return choices.get(raw) if choices is not None else parse_field(spec, raw)

    def _refresh_reference(self, name):
        if self.loading or name not in self.controls:
            return
        if name in self.reference_jobs:
            self.after_cancel(self.reference_jobs.pop(name))
        self.reference_serial += 1
        self.reference_revisions[name] = self.reference_serial
        try:
            value = self.field_value(name)
        except (ValueError, TypeError):
            value = None
        source = self.source(name)
        if source != self.reference_sources.get(name):
            self.reference_labels[name] = {}
            self.reference_sources[name] = source
        identifiers = value if isinstance(value, list) else [value]
        identifiers = list(dict.fromkeys(item for item in identifiers if type(item) is int and item > 0))
        self._show_reference(name, value, identifiers)
        if source and identifiers and self.on_resolve:
            revision = self.reference_revisions[name]
            self.reference_jobs[name] = self.after(250, lambda: self._resolve_reference(name, source, identifiers[:100], revision))

    def _show_reference(self, name, value, identifiers):
        parts = [self.base_hints[name]] if self.base_hints[name] else []
        labels = self.reference_labels.get(name, {})
        if self.reference_sources.get(name) and identifiers:
            names = [f"{labels[str(item)]}（ID: {item}）" if labels.get(str(item)) else f"ID: {item}" for item in identifiers[:8]]
            parts.append("对应内容：" + "；".join(names) + (f"；共 {len(identifiers)} 项" if len(identifiers) > 8 else ""))
        elif value == self.initial_values.get(name) and name in self.initial_values.get("_display", {}):
            parts.append("显示：" + compact(display_value(value, self.initial_values["_display"][name]), 500))
        self.hint_vars[name].set("\n".join(parts))

    def _resolve_reference(self, name, source, identifiers, revision):
        self.reference_jobs.pop(name, None)
        def done(labels):
            if not self.winfo_exists() or self.reference_revisions.get(name) != revision or self.source(name) != source:
                return
            self.reference_labels[name] = {str(key): label for key, label in labels.items() if isinstance(label, str) and label}
            current = self.field_value(name)
            all_identifiers = current if isinstance(current, list) else [current]
            all_identifiers = list(dict.fromkeys(item for item in all_identifiers if type(item) is int and item > 0))
            self._show_reference(name, current, all_identifiers)
        self.on_resolve(source, identifiers, done)

    def remember_selection(self, name, selected, identity=None):
        source = self.source(name)
        if source != self.reference_sources.get(name):
            self.reference_labels[name] = {}
        self.reference_sources[name] = source
        self.reference_labels.setdefault(name, {}).update({str(row_identity(row, identity)): row_label(row)
                                                          for row in selected if row_label(row)})
        self._refresh_reference(name)

    def display_arguments(self, values):
        labels = {}
        for name, value in values.items():
            names = self.reference_labels.get(name, {})
            if self.reference_sources.get(name):
                labels[name] = [names.get(str(item)) for item in value] if isinstance(value, list) else names.get(str(value))
        return display_value(values, labels)

    def source(self, name):
        spec = self.controls[name][0]
        dependency = spec.get("source_by")
        if dependency:
            if dependency["field"] not in self.controls:
                return ""
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
            control.set(next((label for label, item in choices.items() if type(item) is type(value) and item == value), ""))
        elif isinstance(control, tk.Text):
            control.delete("1.0", "end")
            control.insert("1.0", json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value))
        else:
            control.set(value)
        self._refresh_reference(name)

    def destroy(self):
        for job in self.reference_jobs.values():
            self.after_cancel(job)
        self.reference_jobs.clear()
        super().destroy()


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
            self.columns = [(name, name) for name in preferred_columns(rows)]
        self.tree.configure(columns=[name for name, _ in self.columns])
        for name, label in self.columns:
            self.tree.heading(name, text=label)
            self.tree.column(name, width=160 if name not in {"id", "index"} else 80, minwidth=60, stretch=True)
        for index, row in enumerate(rows):
            self.tree.insert("", "end", iid=str(index), values=[compact(cell_value(row, name), 500) for name, _ in self.columns])

    def _selected(self, _):
        selection = self.tree.selection()
        if selection and self.on_select:
            self.on_select(self.rows[int(selection[0])])

    def selection(self):
        items = self.tree.selection()
        return self.rows[int(items[0])] if items else None

    def selections(self):
        return [self.rows[int(item)] for item in self.tree.selection()]
