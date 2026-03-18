from __future__ import annotations

from collections import OrderedDict
import os
from pathlib import Path

from .models import DatasetItem

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
LABEL_SUFFIX = ".txt"
IMAGE_ROOT_HINTS = {"images", "image", "imgs"}
LABEL_ROOT_HINTS = {"labels", "label", "anns", "annotations"}
ALL_ROOT_HINTS = IMAGE_ROOT_HINTS | LABEL_ROOT_HINTS
IGNORED_DIR_SEGMENTS = {"__label_backups__", "__pycache__", ".git", "build", "dist"}


def _is_bucket_segment(segment: str) -> bool:
    low = segment.lower()
    if low in ALL_ROOT_HINTS:
        return True

    prefixes = (
        "image",
        "images",
        "img",
        "imgs",
        "label",
        "labels",
        "ann",
        "anns",
        "annotation",
        "annotations",
    )
    for prefix in prefixes:
        if low.startswith(prefix + "_") or low.startswith(prefix + "-"):
            return True
    return False


def _normalize_key(root: Path, path: Path, is_label: bool) -> str:
    """Build a robust matching key from file path."""
    rel = path.relative_to(root)
    parts = list(rel.with_suffix("").parts)

    if not parts:
        return path.stem

    dir_parts = parts[:-1]
    stem_part = parts[-1]

    cleaned_dirs: list[str] = []
    for part in dir_parts:
        if _is_bucket_segment(part):
            continue
        cleaned_dirs.append(part)

    cleaned = cleaned_dirs + [stem_part]
    return "/".join(cleaned)



def _is_ignored_path(root: Path, path: Path) -> bool:
    try:
        rel_parts = [p.lower() for p in path.relative_to(root).parts[:-1]]
    except Exception:
        rel_parts = [p.lower() for p in path.parts]
    return any(seg in IGNORED_DIR_SEGMENTS for seg in rel_parts)


def _iter_files_safe(root_dir: Path):
    """Yield files under root_dir while ignoring access errors."""
    root_text = str(root_dir)

    def _on_error(_exc: OSError) -> None:
        # Ignore unreadable folders/files and continue scanning.
        return None

    for current_root, dir_names, file_names in os.walk(
        root_text,
        topdown=True,
        onerror=_on_error,
        followlinks=False,
    ):
        dir_names[:] = [d for d in dir_names if d.lower() not in IGNORED_DIR_SEGMENTS]
        for file_name in file_names:
            try:
                yield Path(current_root) / file_name
            except Exception:
                continue


def _safe_relative(path: Path, root: Path) -> Path | None:
    try:
        return path.relative_to(root)
    except Exception:
        return None


def scan_dataset(root_dir: Path) -> list[DatasetItem]:
    """Scan folder recursively and build image/label pairs lazily."""
    image_map: dict[str, list[Path]] = {}
    label_map: dict[str, list[Path]] = {}

    for file_path in _iter_files_safe(root_dir):
        try:
            if not file_path.is_file():
                continue
        except Exception:
            continue
        if _is_ignored_path(root_dir, file_path):
            continue
        try:
            suffix = file_path.suffix.lower()
        except Exception:
            continue
        if suffix in IMAGE_SUFFIXES:
            try:
                key = _normalize_key(root_dir, file_path, is_label=False)
            except Exception:
                key = file_path.stem
            image_map.setdefault(key, []).append(file_path)
        elif suffix == LABEL_SUFFIX:
            try:
                key = _normalize_key(root_dir, file_path, is_label=True)
            except Exception:
                key = file_path.stem
            label_map.setdefault(key, []).append(file_path)

    all_images = sorted({p for paths in image_map.values() for p in paths})
    all_labels = sorted({p for paths in label_map.values() for p in paths})

    image_by_stem: dict[str, list[Path]] = {}
    label_by_stem: dict[str, list[Path]] = {}
    for p in all_images:
        image_by_stem.setdefault(p.stem, []).append(p)
    for p in all_labels:
        label_by_stem.setdefault(p.stem, []).append(p)

    all_keys = sorted(set(image_map) | set(label_map))
    items: list[DatasetItem] = []

    for key in all_keys:
        image_path = sorted(image_map.get(key, []))[0] if image_map.get(key) else None
        label_path = sorted(label_map.get(key, []))[0] if label_map.get(key) else None

        if image_path is None and label_path is not None:
            candidates = image_by_stem.get(label_path.stem, [])
            if len(candidates) == 1:
                image_path = candidates[0]
        if label_path is None and image_path is not None:
            candidates = label_by_stem.get(image_path.stem, [])
            if len(candidates) == 1:
                label_path = candidates[0]

        items.append(
            DatasetItem(
                key=key,
                image_path=image_path,
                label_path=label_path,
                image_rel=_safe_relative(image_path, root_dir) if image_path else None,
                label_rel=_safe_relative(label_path, root_dir) if label_path else None,
            )
        )

    return items


class PixmapCache:
    """Small LRU cache to avoid decoding large images repeatedly."""

    def __init__(self, max_items: int = 24) -> None:
        self.max_items = max_items
        self._cache: OrderedDict[Path, object] = OrderedDict()

    def get(self, path: Path):
        cached = self._cache.get(path)
        if cached is None:
            return None
        self._cache.move_to_end(path)
        return cached

    def put(self, path: Path, pixmap) -> None:
        self._cache[path] = pixmap
        self._cache.move_to_end(path)
        while len(self._cache) > self.max_items:
            self._cache.popitem(last=False)


def _load_with_qimage(path: Path, qimage_cls, qpixmap_cls):
    image = qimage_cls(str(path))
    if not image.isNull():
        return qpixmap_cls.fromImage(image)
    return qpixmap_cls()


def _load_with_pillow(path: Path, qimage_cls, qpixmap_cls):
    try:
        from PIL import Image
    except Exception:
        return qpixmap_cls()

    try:
        with Image.open(path) as img:
            if getattr(img, "n_frames", 1) > 1:
                try:
                    img.seek(0)
                except Exception:
                    pass

            if img.mode in ("F", "I", "I;16", "I;16B", "I;16L"):
                img = img.convert("L")

            if img.mode not in ("RGB", "RGBA", "L"):
                img = img.convert("RGB")

            if img.mode == "L":
                img = img.convert("RGB")

            mode = img.mode
            data = img.tobytes("raw", mode)
            if mode == "RGB":
                fmt = qimage_cls.Format.Format_RGB888
                bpl = img.width * 3
            else:
                fmt = qimage_cls.Format.Format_RGBA8888
                bpl = img.width * 4

            qimg = qimage_cls(data, img.width, img.height, bpl, fmt).copy()
            return qpixmap_cls.fromImage(qimg)
    except Exception:
        return qpixmap_cls()


def load_pixmap(path: Path):
    """Decode image into QPixmap.

    GUI/image dependencies are imported lazily so scanning/tests can run without PyQt6/cv2.
    """
    try:
        from PyQt6.QtGui import QImage, QPixmap
    except Exception as exc:
        raise RuntimeError("缺少 PyQt6，无法加载图片。") from exc

    if not path.exists():
        return QPixmap()
    try:
        if path.stat().st_size <= 0:
            return QPixmap()
    except Exception:
        pass

    suffix = path.suffix.lower()

    # 明确排除 TIFF，避免解码异常和噪声日志。
    if suffix in {".tif", ".tiff"}:
        return QPixmap()


    try:
        import cv2
        import numpy as np
    except Exception:
        cv2 = None
        np = None

    if cv2 is not None and np is not None:
        try:
            if hasattr(cv2, "utils") and hasattr(cv2.utils, "logging"):
                cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_SILENT)
            elif hasattr(cv2, "setLogLevel"):
                cv2.setLogLevel(0)
        except Exception:
            pass

        try:
            file_bytes = np.fromfile(str(path), dtype=np.uint8)
            if file_bytes.size > 0:
                image_bgr = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
                if image_bgr is not None:
                    image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
                    height, width, channels = image_rgb.shape
                    bytes_per_line = channels * width
                    q_image = QImage(
                        image_rgb.data,
                        width,
                        height,
                        bytes_per_line,
                        QImage.Format.Format_RGB888,
                    ).copy()
                    return QPixmap.fromImage(q_image)
        except Exception:
            pass

    fallback = _load_with_qimage(path, QImage, QPixmap)
    if not fallback.isNull():
        return fallback

    fallback = _load_with_pillow(path, QImage, QPixmap)
    if not fallback.isNull():
        return fallback

    return QPixmap()
