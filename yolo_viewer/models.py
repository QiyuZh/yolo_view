from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Annotation:
    class_id: int
    x_center: float
    y_center: float
    width: float
    height: float
    confidence: float | None = None
    source_line: int = 0
    shape_type: str = "bbox"  # bbox / rotated / polygon
    points: list[tuple[float, float]] = field(default_factory=list)

    def normalized_points(self) -> list[tuple[float, float]]:
        if self.points:
            return [(float(x), float(y)) for x, y in self.points]

        left = self.x_center - self.width / 2
        right = self.x_center + self.width / 2
        top = self.y_center - self.height / 2
        bottom = self.y_center + self.height / 2
        return [(left, top), (right, top), (right, bottom), (left, bottom)]

    def point_count(self) -> int:
        pts = self.normalized_points()
        return len(pts)

    def to_yolo_line(self, include_confidence: bool = True) -> str:
        if self.shape_type == "bbox" and not self.points:
            base = (
                f"{self.class_id} "
                f"{self.x_center:.6f} {self.y_center:.6f} "
                f"{self.width:.6f} {self.height:.6f}"
            )
            if include_confidence and self.confidence is not None:
                return f"{base} {self.confidence:.6f}"
            return base

        coords: list[str] = []
        for x, y in self.normalized_points():
            coords.append(f"{x:.6f}")
            coords.append(f"{y:.6f}")

        base = f"{self.class_id} " + " ".join(coords)
        if include_confidence and self.confidence is not None:
            return f"{base} {self.confidence:.6f}"
        return base

    def as_tuple(self) -> tuple[int, float, float, float, float]:
        return (
            self.class_id,
            self.x_center,
            self.y_center,
            self.width,
            self.height,
        )


@dataclass
class DatasetItem:
    key: str
    image_path: Path | None
    label_path: Path | None
    image_rel: Path | None = None
    label_rel: Path | None = None

    def display_name(self) -> str:
        return self.key.replace("/", "\\")


@dataclass
class ValidationIssue:
    code: str
    message: str
    severity: str = "error"
    line_number: int | None = None


@dataclass
class FileValidation:
    item_key: str
    issues: list[ValidationIssue] = field(default_factory=list)
    annotations: list[Annotation] = field(default_factory=list)

    @property
    def has_error(self) -> bool:
        return any(issue.severity == "error" for issue in self.issues)
