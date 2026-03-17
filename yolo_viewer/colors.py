from __future__ import annotations

from PyQt6.QtGui import QColor


PALETTE = [
    QColor("#E63946"),
    QColor("#2A9D8F"),
    QColor("#1D3557"),
    QColor("#F4A261"),
    QColor("#457B9D"),
    QColor("#8D99AE"),
    QColor("#06D6A0"),
    QColor("#118AB2"),
    QColor("#EF476F"),
    QColor("#FFD166"),
]


def class_color(class_id: int) -> QColor:
    return PALETTE[class_id % len(PALETTE)]
