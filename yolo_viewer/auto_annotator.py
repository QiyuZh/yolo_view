from __future__ import annotations

from pathlib import Path

from .models import Annotation


class AutoAnnotatorError(RuntimeError):
    pass


class AutoAnnotator:
    """Wraps an Ultralytics YOLO model for auto-labeling images."""

    def __init__(self, model_path: Path, conf_threshold: float = 0.25, iou_threshold: float = 0.7) -> None:
        self.model_path = model_path
        self.conf_threshold = conf_threshold
        self.iou_threshold = iou_threshold

        try:
            from ultralytics import YOLO  # type: ignore
        except Exception as exc:  # pragma: no cover - dependency missing at runtime
            raise AutoAnnotatorError(
                "未安装 ultralytics。请先执行: pip install ultralytics"
            ) from exc

        try:
            self._model = YOLO(str(model_path))
        except Exception as exc:  # pragma: no cover
            raise AutoAnnotatorError(f"模型加载失败: {exc}") from exc

    def class_names(self) -> list[str]:
        names = getattr(self._model, "names", None)
        if isinstance(names, dict):
            return [str(names[k]) for k in sorted(names)]
        if isinstance(names, list):
            return [str(n) for n in names]
        return []

    def predict(self, image_path: Path) -> list[Annotation]:
        try:
            results = self._model.predict(
                source=str(image_path),
                conf=self.conf_threshold,
                iou=self.iou_threshold,
                verbose=False,
            )
        except Exception as exc:  # pragma: no cover
            raise AutoAnnotatorError(f"模型推理失败: {exc}") from exc

        annotations: list[Annotation] = []
        for result in results:
            boxes = getattr(result, "boxes", None)
            if boxes is None or len(boxes) == 0:
                continue

            xywhn = boxes.xywhn.cpu().tolist()
            cls_ids = boxes.cls.cpu().tolist()
            confs = boxes.conf.cpu().tolist() if getattr(boxes, "conf", None) is not None else []

            for i, coords in enumerate(xywhn):
                if len(coords) < 4:
                    continue
                x_center, y_center, width, height = [float(v) for v in coords[:4]]
                class_id = int(cls_ids[i]) if i < len(cls_ids) else 0
                confidence = float(confs[i]) if i < len(confs) else None
                annotations.append(
                    Annotation(
                        class_id=class_id,
                        x_center=x_center,
                        y_center=y_center,
                        width=width,
                        height=height,
                        confidence=confidence,
                    )
                )

        return annotations
