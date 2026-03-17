from __future__ import annotations

from pathlib import Path

from .models import Annotation, DatasetItem, FileValidation, ValidationIssue


def _in_range(value: float, low: float = 0.0, high: float = 1.0) -> bool:
    return low <= value <= high


def parse_yolo_label(label_path: Path) -> tuple[list[Annotation], list[ValidationIssue]]:
    """Parse one YOLO txt file and collect validation issues per line.

    Expected line format:
      class_id x_center y_center width height [confidence]

    Coordinates are normalized values in [0, 1].
    """
    annotations: list[Annotation] = []
    issues: list[ValidationIssue] = []

    if not label_path.exists():
        issues.append(ValidationIssue(code="label_missing", message="标签文件不存在。"))
        return annotations, issues

    content = label_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    if not content:
        issues.append(ValidationIssue(code="empty_label", message="标签文件为空。", severity="warning"))
        return annotations, issues

    for line_number, line in enumerate(content, start=1):
        raw = line.strip()
        if not raw:
            continue

        parts = raw.split()
        if len(parts) not in (5, 6):
            issues.append(
                ValidationIssue(
                    code="format_error",
                    message=f"第 {line_number} 行：应为 5 或 6 个字段，实际为 {len(parts)} 个。",
                    line_number=line_number,
                )
            )
            continue

        class_text, x_text, y_text, w_text, h_text = parts[:5]
        conf_text = parts[5] if len(parts) == 6 else None

        confidence: float | None = None
        try:
            class_id = int(class_text)
            x_center = float(x_text)
            y_center = float(y_text)
            width = float(w_text)
            height = float(h_text)
            if conf_text is not None:
                confidence = float(conf_text)
        except ValueError:
            issues.append(
                ValidationIssue(
                    code="parse_error",
                    message=f"第 {line_number} 行：类别ID和坐标必须为数字。",
                    line_number=line_number,
                )
            )
            continue

        if class_id < 0:
            issues.append(
                ValidationIssue(
                    code="class_error",
                    message=f"第 {line_number} 行：类别ID必须大于等于 0。",
                    line_number=line_number,
                )
            )

        if width <= 0 or height <= 0:
            issues.append(
                ValidationIssue(
                    code="size_error",
                    message=f"第 {line_number} 行：宽度和高度必须大于 0。",
                    line_number=line_number,
                )
            )

        if confidence is not None and not _in_range(confidence):
            issues.append(
                ValidationIssue(
                    code="confidence_range",
                    message=f"第 {line_number} 行：置信度必须在 [0, 1] 范围内。",
                    line_number=line_number,
                    severity="warning",
                )
            )

        if not all(_in_range(v) for v in (x_center, y_center, width, height)):
            issues.append(
                ValidationIssue(
                    code="range_error",
                    message=f"第 {line_number} 行：各项数值必须在 [0, 1] 范围内。",
                    line_number=line_number,
                )
            )

        left = x_center - width / 2
        right = x_center + width / 2
        top = y_center - height / 2
        bottom = y_center + height / 2
        if left < 0 or right > 1 or top < 0 or bottom > 1:
            issues.append(
                ValidationIssue(
                    code="bbox_out_of_bounds",
                    message=f"第 {line_number} 行：边界框超出图像范围。",
                    line_number=line_number,
                )
            )

        annotations.append(
            Annotation(
                class_id=class_id,
                x_center=x_center,
                y_center=y_center,
                width=width,
                height=height,
                confidence=confidence,
                source_line=line_number,
            )
        )

    if not annotations:
        issues.append(
            ValidationIssue(
                code="empty_annotation",
                message="未找到有效标注行。",
                severity="warning",
            )
        )

    return annotations, issues


def validate_item(item: DatasetItem) -> FileValidation:
    """Validate one dataset pair and return both parsed labels and issue list."""
    result = FileValidation(item_key=item.key)

    if item.image_path is None:
        result.issues.append(ValidationIssue(code="image_missing", message="图片文件缺失。"))
    elif not item.image_path.exists():
        result.issues.append(
            ValidationIssue(code="image_missing", message=f"图片路径不存在：{item.image_path}")
        )

    if item.label_path is None:
        result.issues.append(
            ValidationIssue(code="label_missing", message="该图片缺少标签文件。", severity="warning")
        )
        return result

    annotations, issues = parse_yolo_label(item.label_path)
    result.annotations = annotations
    result.issues.extend(issues)
    return result


def validate_dataset(items: list[DatasetItem]) -> dict[str, FileValidation]:
    """Batch validation for all dataset entries."""
    return {item.key: validate_item(item) for item in items}
