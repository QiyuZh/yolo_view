from __future__ import annotations

import datetime
import faulthandler
import os
import sys
import traceback
from pathlib import Path
from typing import TextIO

_LOG_FILE_HANDLE: TextIO | None = None


def _log_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "yolo-viewer"
        return Path.home() / "AppData" / "Local" / "yolo-viewer"
    return Path.home() / ".yolo-viewer"


def _log_path() -> Path:
    path = _log_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path / "crash.log"


def append_log(content: str) -> Path:
    path = _log_path()
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with path.open("a", encoding="utf-8", errors="ignore") as fp:
        fp.write(f"\n[{stamp}]\n")
        fp.write(content)
        if not content.endswith("\n"):
            fp.write("\n")
    return path


def install_global_exception_handler() -> Path:
    global _LOG_FILE_HANDLE

    path = _log_path()

    try:
        if _LOG_FILE_HANDLE is None or _LOG_FILE_HANDLE.closed:
            _LOG_FILE_HANDLE = path.open("a", encoding="utf-8", errors="ignore")
        faulthandler.enable(_LOG_FILE_HANDLE, all_threads=True)
    except Exception:
        pass

    previous_hook = sys.excepthook

    def _hook(exc_type, exc_value, exc_traceback) -> None:
        details = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        log_path = append_log(details)
        try:
            from PyQt6.QtWidgets import QMessageBox

            from .icons import load_app_icon

            box = QMessageBox()
            app_icon = load_app_icon()
            if not app_icon.isNull():
                box.setWindowIcon(app_icon)
            box.setIcon(QMessageBox.Icon.Critical)
            box.setWindowTitle("Unhandled Exception")
            box.setText(f"Application crashed. Log saved to:\n{log_path}")
            box.exec()
        except Exception:
            pass

        try:
            previous_hook(exc_type, exc_value, exc_traceback)
        except Exception:
            pass

    sys.excepthook = _hook
    return path
