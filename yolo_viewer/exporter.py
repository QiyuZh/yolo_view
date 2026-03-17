from __future__ import annotations

import json
import shutil
from pathlib import Path

from .models import DatasetItem, FileValidation


def generate_report(
    output_path: Path,
    dataset_root: Path,
    items: list[DatasetItem],
    validation_map: dict[str, FileValidation],
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    total = len(items)
    passed = 0
    errors = 0
    warnings = 0
    files: list[dict] = []

    for item in items:
        validation = validation_map.get(item.key)
        issue_entries = []
        if validation:
            for issue in validation.issues:
                issue_entries.append(
                    {
                        "severity": issue.severity,
                        "code": issue.code,
                        "message": issue.message,
                        "line": issue.line_number,
                    }
                )
                if issue.severity == "error":
                    errors += 1
                else:
                    warnings += 1
            if not validation.issues:
                passed += 1
        else:
            passed += 1

        files.append(
            {
                "key": item.key,
                "image": str(item.image_rel) if item.image_rel else None,
                "label": str(item.label_rel) if item.label_rel else None,
                "issues": issue_entries,
            }
        )

    report = {
        "dataset_root": str(dataset_root),
        "summary": {
            "total_items": total,
            "passed_items": passed,
            "error_count": errors,
            "warning_count": warnings,
        },
        "files": files,
    }

    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return output_path


def export_passed_files(
    target_dir: Path,
    dataset_root: Path,
    items: list[DatasetItem],
    validation_map: dict[str, FileValidation],
) -> int:
    target_dir.mkdir(parents=True, exist_ok=True)
    copied = 0

    for item in items:
        if item.image_path is None or item.label_path is None:
            continue

        validation = validation_map.get(item.key)
        if validation and validation.issues:
            continue

        image_target = target_dir / item.image_rel if item.image_rel else target_dir / item.image_path.name
        label_target = target_dir / item.label_rel if item.label_rel else target_dir / item.label_path.name

        image_target.parent.mkdir(parents=True, exist_ok=True)
        label_target.parent.mkdir(parents=True, exist_ok=True)

        shutil.copy2(item.image_path, image_target)
        shutil.copy2(item.label_path, label_target)
        copied += 1

    return copied
