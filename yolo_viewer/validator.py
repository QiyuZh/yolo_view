from __future__ import annotations

from pathlib import Path

from .models import Annotation, DatasetItem, FileValidation, ValidationIssue


def _in_range(value: float, low: float = 0.0, high: float = 1.0) -> bool:
    return low <= value <= high


def _bbox_from_points(points: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    x_min = min(xs)
    x_max = max(xs)
    y_min = min(ys)
    y_max = max(ys)
    return x_min, y_min, x_max - x_min, y_max - y_min


def parse_yolo_label(label_path: Path) -> tuple[list[Annotation], list[ValidationIssue]]:
    """Parse YOLO txt labels.

    Supported formats:
    1) Axis-aligned bbox:
       class_id x_center y_center width height [confidence]
    2) Polygon / OBB-like points:
       class_id x1 y1 x2 y2 x3 y3 [... xn yn] [confidence]
       - 4 points -> treated as "rotated"
       - >=3 points -> treated as "polygon"
    """
    annotations: list[Annotation] = []
    issues: list[ValidationIssue] = []

    if not label_path.exists():
        issues.append(ValidationIssue(code="label_missing", message="标签文件不存在。"))
        return annotations, issues

    try:
        content = label_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception as exc:
        issues.append(
            ValidationIssue(code="label_read_error", message=f"标签文件读取失败：{exc}")
        )
        return annotations, issues
    if not content:
        issues.append(ValidationIssue(code="empty_label", message="标签文件为空。", severity="warning"))
        return annotations, issues

    for line_number, line in enumerate(content, start=1):
        raw = line.strip()
        if not raw:
            continue

        parts = raw.split()
        if len(parts) < 5:
            issues.append(
                ValidationIssue(
                    code="format_error",
                    message=f"第 {line_number} 行：字段数不足，至少需要 5 个。",
                    line_number=line_number,
                )
            )
            continue

        class_text = parts[0]
        try:
            class_id = int(class_text)
        except ValueError:
            issues.append(
                ValidationIssue(
                    code="parse_error",
                    message=f"第 {line_number} 行：类别ID必须为整数。",
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

        # Axis-aligned bbox (legacy YOLO)
        if len(parts) in (5, 6):
            class_text2, x_text, y_text, w_text, h_text = parts[:5]
            conf_text = parts[5] if len(parts) == 6 else None

            confidence: float | None = None
            try:
                _ = int(class_text2)
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
                    shape_type="bbox",
                )
            )
            continue

        # Polygon / rotated points format
        try:
            values = [float(v) for v in parts[1:]]
        except ValueError:
            issues.append(
                ValidationIssue(
                    code="parse_error",
                    message=f"第 {line_number} 行：坐标必须为数字。",
                    line_number=line_number,
                )
            )
            continue

        confidence2: float | None = None
        coord_values = values
        if len(coord_values) % 2 == 1:
            confidence2 = coord_values[-1]
            coord_values = coord_values[:-1]

        if len(coord_values) < 6 or len(coord_values) % 2 != 0:
            issues.append(
                ValidationIssue(
                    code="format_error",
                    message=f"第 {line_number} 行：点坐标格式错误，应为偶数个 x/y。",
                    line_number=line_number,
                )
            )
            continue

        points = [(coord_values[i], coord_values[i + 1]) for i in range(0, len(coord_values), 2)]
        if len(points) < 3:
            issues.append(
                ValidationIssue(
                    code="format_error",
                    message=f"第 {line_number} 行：多边形至少需要 3 个点。",
                    line_number=line_number,
                )
            )
            continue

        if confidence2 is not None and not _in_range(confidence2):
            issues.append(
                ValidationIssue(
                    code="confidence_range",
                    message=f"第 {line_number} 行：置信度必须在 [0, 1] 范围内。",
                    line_number=line_number,
                    severity="warning",
                )
            )

        if not all(_in_range(v) for p in points for v in p):
            issues.append(
                ValidationIssue(
                    code="range_error",
                    message=f"第 {line_number} 行：点坐标必须在 [0, 1] 范围内。",
                    line_number=line_number,
                )
            )

        x_min, y_min, width2, height2 = _bbox_from_points(points)
        x_center2 = x_min + width2 / 2
        y_center2 = y_min + height2 / 2

        if width2 <= 0 or height2 <= 0:
            issues.append(
                ValidationIssue(
                    code="size_error",
                    message=f"第 {line_number} 行：标注包围盒尺寸必须大于 0。",
                    line_number=line_number,
                )
            )

        if x_min < 0 or y_min < 0 or (x_min + width2) > 1 or (y_min + height2) > 1:
            issues.append(
                ValidationIssue(
                    code="bbox_out_of_bounds",
                    message=f"第 {line_number} 行：标注超出图像范围。",
                    line_number=line_number,
                )
            )

        shape_type = "rotated" if len(points) == 4 else "polygon"
        annotations.append(
            Annotation(
                class_id=class_id,
                x_center=x_center2,
                y_center=y_center2,
                width=width2,
                height=height2,
                confidence=confidence2,
                source_line=line_number,
                shape_type=shape_type,
                points=points,
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

    try:
        annotations, issues = parse_yolo_label(item.label_path)
        result.annotations = annotations
        result.issues.extend(issues)
    except Exception as exc:
        result.issues.append(
            ValidationIssue(code="label_read_error", message=f"标签解析失败：{exc}")
        )
    return result


def validate_dataset(items: list[DatasetItem]) -> dict[str, FileValidation]:
    """Batch validation for all dataset entries."""
    return {item.key: validate_item(item) for item in items}
