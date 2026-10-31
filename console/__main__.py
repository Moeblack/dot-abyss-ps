from __future__ import annotations

import argparse
import ctypes
import sys
import tkinter as tk
from tkinter import messagebox
from pathlib import Path

from .app import ConsoleApp
from .web_launcher import launch


def main():
    parser = argparse.ArgumentParser(description="Dot Abyss 本地控制台")
    parser.add_argument("--origin")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--no-connect", action="store_true")
    parser.add_argument("--classic", action="store_true")
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()
    if not args.classic:
        try:
            launch(args.origin, args.data_dir, demo=args.demo or args.no_connect)
        except Exception as exc:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("工作台未能启动", str(exc), parent=root)
            root.destroy()
            raise SystemExit(1)
        return
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    root = tk.Tk()
    ConsoleApp(root, args.origin, args.data_dir, auto_connect=not args.no_connect)
    root.mainloop()


if __name__ == "__main__":
    main()
