from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap

_ICON_CACHE: QIcon | None = None


def _assets_dir() -> Path:
    return Path(__file__).resolve().parent / "assets"


def _build_fallback_icon() -> QIcon:
    size = 256
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor("#0f172a"))

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)

    painter.setBrush(QColor("#14b8a6"))
    painter.drawEllipse(18, 18, 220, 220)

    painter.setBrush(QColor("#0f172a"))
    painter.drawEllipse(56, 56, 144, 144)

    painter.setPen(QColor("#f0fdfa"))
    painter.setFont(QFont("Segoe UI", 108, QFont.Weight.Bold))
    painter.drawText(pixmap.rect(), int(Qt.AlignmentFlag.AlignCenter), "Y")

    painter.end()
    return QIcon(pixmap)


def load_app_icon() -> QIcon:
    global _ICON_CACHE

    if _ICON_CACHE is not None and not _ICON_CACHE.isNull():
        return _ICON_CACHE

    assets = _assets_dir()
    for name in ("app_icon.ico", "app_icon.png"):
        p = assets / name
        if p.exists():
            icon = QIcon(str(p))
            if not icon.isNull():
                _ICON_CACHE = icon
                return icon

    _ICON_CACHE = _build_fallback_icon()
    return _ICON_CACHE


def app_icon_path() -> Path:
    assets = _assets_dir()
    ico = assets / "app_icon.ico"
    if ico.exists():
        return ico
    return assets / "app_icon.png"
