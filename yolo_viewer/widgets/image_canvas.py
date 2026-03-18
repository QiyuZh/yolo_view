from __future__ import annotations

from dataclasses import replace

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QImage,
    QKeyEvent,
    QPainter,
    QPen,
    QPixmap,
    QPolygonF,
)
from PyQt6.QtWidgets import (
    QGraphicsPolygonItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QStyleOptionGraphicsItem,
    QWidget,
)

from ..colors import class_color
from ..models import Annotation


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(value, high))


class EditableBoxItem(QGraphicsRectItem):
    HANDLE_MARGIN = 8.0
    MIN_SIZE = 6.0

    def __init__(
        self,
        index: int,
        annotation: Annotation,
        rect: QRectF,
        image_rect: QRectF,
        class_name: str,
        on_selected,
        on_geometry_change,
        center_marker_visible: bool = True,
    ) -> None:
        super().__init__(rect)
        self.index = index
        self.annotation = replace(annotation)
        self.class_name = class_name
        self.image_rect = image_rect
        self.on_selected = on_selected
        self.on_geometry_change = on_geometry_change

        self.setAcceptHoverEvents(True)
        self.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsFocusable, True)

        self._drag_mode = "none"
        self._drag_origin_scene = QPointF()
        self._drag_origin_rect = QRectF()
        self._color = class_color(annotation.class_id)
        self._flash_on = False
        self._center_marker_visible = bool(center_marker_visible)
        self._update_visuals()

    def set_class(self, class_id: int, class_name: str) -> None:
        self.annotation.class_id = class_id
        self.class_name = class_name
        self._color = class_color(class_id)
        self._update_visuals()
        self.update()

    def set_flash(self, on: bool) -> None:
        self._flash_on = on
        self.update()

    def set_center_marker_visible(self, visible: bool) -> None:
        self._center_marker_visible = bool(visible)
        self.update()

    def _update_visuals(self) -> None:
        color = self._color
        self.setPen(QPen(color, 3))
        fill = QColor(color)
        fill.setAlpha(45)
        self.setBrush(QBrush(fill))
        self._update_tooltip()

    def _update_tooltip(self) -> None:
        rect = self.rect()
        text = (
            f"类型：矩形\n"
            f"类别：{self.class_name} ({self.annotation.class_id})\n"
            f"x={rect.x():.1f}, y={rect.y():.1f}, w={rect.width():.1f}, h={rect.height():.1f}"
        )
        self.setToolTip(text)

    def _handle_for_pos(self, pos: QPointF) -> str:
        r = self.rect()
        near_left = abs(pos.x() - r.left()) <= self.HANDLE_MARGIN
        near_right = abs(pos.x() - r.right()) <= self.HANDLE_MARGIN
        near_top = abs(pos.y() - r.top()) <= self.HANDLE_MARGIN
        near_bottom = abs(pos.y() - r.bottom()) <= self.HANDLE_MARGIN

        if near_left and near_top:
            return "top_left"
        if near_right and near_top:
            return "top_right"
        if near_left and near_bottom:
            return "bottom_left"
        if near_right and near_bottom:
            return "bottom_right"
        if near_left:
            return "left"
        if near_right:
            return "right"
        if near_top:
            return "top"
        if near_bottom:
            return "bottom"
        if r.contains(pos):
            return "move"
        return "none"

    def _cursor_for_handle(self, handle: str) -> Qt.CursorShape:
        mapping = {
            "top_left": Qt.CursorShape.SizeFDiagCursor,
            "bottom_right": Qt.CursorShape.SizeFDiagCursor,
            "top_right": Qt.CursorShape.SizeBDiagCursor,
            "bottom_left": Qt.CursorShape.SizeBDiagCursor,
            "left": Qt.CursorShape.SizeHorCursor,
            "right": Qt.CursorShape.SizeHorCursor,
            "top": Qt.CursorShape.SizeVerCursor,
            "bottom": Qt.CursorShape.SizeVerCursor,
            "move": Qt.CursorShape.SizeAllCursor,
        }
        return mapping.get(handle, Qt.CursorShape.ArrowCursor)

    def hoverMoveEvent(self, event) -> None:
        handle = self._handle_for_pos(event.pos())
        self.setCursor(self._cursor_for_handle(handle))
        super().hoverMoveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.setSelected(True)
            self.on_selected(self.index)
            self._drag_mode = self._handle_for_pos(event.pos())
            self._drag_origin_scene = event.scenePos()
            self._drag_origin_rect = QRectF(self.rect())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_mode == "none":
            super().mouseMoveEvent(event)
            return

        delta = event.scenePos() - self._drag_origin_scene
        rect = QRectF(self._drag_origin_rect)

        if self._drag_mode == "move":
            rect.translate(delta)
        else:
            if "left" in self._drag_mode:
                rect.setLeft(rect.left() + delta.x())
            if "right" in self._drag_mode:
                rect.setRight(rect.right() + delta.x())
            if "top" in self._drag_mode:
                rect.setTop(rect.top() + delta.y())
            if "bottom" in self._drag_mode:
                rect.setBottom(rect.bottom() + delta.y())

        rect = rect.normalized()
        if rect.width() < self.MIN_SIZE:
            rect.setWidth(self.MIN_SIZE)
        if rect.height() < self.MIN_SIZE:
            rect.setHeight(self.MIN_SIZE)

        rect.setLeft(clamp(rect.left(), self.image_rect.left(), self.image_rect.right() - self.MIN_SIZE))
        rect.setTop(clamp(rect.top(), self.image_rect.top(), self.image_rect.bottom() - self.MIN_SIZE))
        rect.setRight(clamp(rect.right(), rect.left() + self.MIN_SIZE, self.image_rect.right()))
        rect.setBottom(clamp(rect.bottom(), rect.top() + self.MIN_SIZE, self.image_rect.bottom()))

        self.setRect(rect)
        self._update_tooltip()
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if self._drag_mode != "none":
            new_rect = QRectF(self.rect())
            old_rect = QRectF(self._drag_origin_rect)
            if new_rect != old_rect:
                QTimer.singleShot(
                    0,
                    lambda idx=self.index, o=QRectF(old_rect), n=QRectF(new_rect): self.on_geometry_change(idx, o, n),
                )
        self._drag_mode = "none"
        super().mouseReleaseEvent(event)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        super().paint(painter, option, widget)
        r = self.rect()

        if self._flash_on:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(QColor("#FFD60A"), 5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(r)

        label = f"矩形 | {self.class_name} ({self.annotation.class_id})"
        _draw_label_and_center(painter, r, self.image_rect, label, self._color, self._center_marker_visible)


class StaticPolygonItem(QGraphicsPolygonItem):
    def __init__(
        self,
        index: int,
        annotation: Annotation,
        polygon: QPolygonF,
        image_rect: QRectF,
        class_name: str,
        on_selected,
        center_marker_visible: bool = True,
    ) -> None:
        super().__init__(polygon)
        self.index = index
        self.annotation = replace(annotation)
        self.image_rect = image_rect
        self.class_name = class_name
        self.on_selected = on_selected
        self._color = class_color(annotation.class_id)
        self._flash_on = False
        self._center_marker_visible = bool(center_marker_visible)

        self.setFlag(QGraphicsPolygonItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsPolygonItem.GraphicsItemFlag.ItemIsFocusable, True)

        pen = QPen(self._color, 3)
        self.setPen(pen)
        fill = QColor(self._color)
        fill.setAlpha(38)
        self.setBrush(QBrush(fill))
        self._update_tooltip()

    def set_flash(self, on: bool) -> None:
        self._flash_on = on
        self.update()

    def set_center_marker_visible(self, visible: bool) -> None:
        self._center_marker_visible = bool(visible)
        self.update()

    def _shape_text(self) -> str:
        if self.annotation.shape_type == "rotated":
            return "旋转框"
        if self.annotation.shape_type == "polygon":
            return "多边形"
        return "标注"

    def _update_tooltip(self) -> None:
        br = self.boundingRect()
        text = (
            f"类型：{self._shape_text()}\n"
            f"类别：{self.class_name} ({self.annotation.class_id})\n"
            f"包围框 x={br.x():.1f}, y={br.y():.1f}, w={br.width():.1f}, h={br.height():.1f}"
        )
        self.setToolTip(text)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.setSelected(True)
            self.on_selected(self.index)
            event.accept()
            return
        super().mousePressEvent(event)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        super().paint(painter, option, widget)
        br = self.boundingRect()

        if self._flash_on:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(QColor("#FFD60A"), 5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPolygon(self.polygon())

        shape_text = "旋转框" if self.annotation.shape_type == "rotated" else "多边形"
        label = f"{shape_text} | {self.class_name} ({self.annotation.class_id})"
        _draw_label_and_center(painter, br, self.image_rect, label, self._color, self._center_marker_visible)


def _draw_label_and_center(
    painter: QPainter,
    r: QRectF,
    image_rect: QRectF,
    label: str,
    color: QColor,
    center_marker_visible: bool = True,
) -> None:
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setFont(QFont("Segoe UI", 9))

    text_rect = painter.boundingRect(r.toRect(), Qt.AlignmentFlag.AlignLeft, label)
    text_rect.adjust(-4, -2, 4, 2)

    label_margin = 4.0
    candidate_top = r.top() - text_rect.height() - label_margin
    if candidate_top >= image_rect.top():
        text_rect.moveTopLeft(QPointF(r.left(), candidate_top).toPoint())
    else:
        below_top = min(r.bottom() + label_margin, image_rect.bottom() - text_rect.height())
        text_rect.moveTopLeft(QPointF(r.left(), below_top).toPoint())

    if text_rect.left() < image_rect.left():
        text_rect.moveLeft(int(image_rect.left()))
    if text_rect.right() > image_rect.right():
        text_rect.moveRight(int(image_rect.right()))

    bg = QColor(color)
    bg.setAlpha(180)
    painter.fillRect(text_rect, bg)
    painter.setPen(QPen(Qt.GlobalColor.white))
    painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, label)

    if center_marker_visible:
        cx = r.center().x()
        cy = r.center().y()
        min_dim = max(1.0, min(r.width(), r.height()))
        marker_radius = 2 if min_dim < 14 else 4
        cross_half = 4 if min_dim < 14 else 6
        line_w = 1 if min_dim < 14 else 2

        painter.setPen(QPen(Qt.GlobalColor.white, line_w))
        painter.drawEllipse(QPointF(cx, cy), marker_radius, marker_radius)
        painter.setPen(QPen(color, line_w))
        painter.drawLine(QPointF(cx - cross_half, cy), QPointF(cx + cross_half, cy))
        painter.drawLine(QPointF(cx, cy - cross_half), QPointF(cx, cy + cross_half))


class ImageCanvas(QGraphicsView):
    annotation_geometry_changed = pyqtSignal(int, object, object)
    annotation_selected = pyqtSignal(int)
    annotation_created = pyqtSignal(object)
    delete_requested = pyqtSignal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)

        self._pixmap_item = self._scene.addPixmap(QPixmap())
        self._annotations: list[Annotation] = []
        self._class_names: list[str] = []
        self._anno_items: list[QGraphicsRectItem | QGraphicsPolygonItem] = []
        self._image_rect = QRectF()

        self._flash_timer = QTimer(self)
        self._flash_timer.setInterval(140)
        self._flash_timer.timeout.connect(self._on_flash_tick)
        self._flash_index = -1
        self._flash_ticks_left = 0
        self._flash_state = False
        self._center_marker_visible = True

        self._create_mode = False
        self._create_class_id = 0
        self._create_shape_type = "bbox"
        self._creating = False
        self._create_start_scene = QPointF()
        self._create_preview_item: QGraphicsRectItem | None = None

        self._poly_points_scene: list[QPointF] = []
        self._poly_preview_item: QGraphicsPolygonItem | None = None

    def set_content(self, pixmap: QPixmap, annotations: list[Annotation], class_names: list[str]) -> None:
        self.cancel_create_mode()
        self._stop_flash()
        self._scene.clear()
        self._pixmap_item = self._scene.addPixmap(pixmap)
        self._pixmap_item.setZValue(0)

        self._image_rect = QRectF(self._pixmap_item.boundingRect())
        self._scene.setSceneRect(self._image_rect)

        self._annotations = [replace(ann) for ann in annotations]
        self._class_names = class_names
        self._anno_items = []

        for index, ann in enumerate(self._annotations):
            class_name = self._class_name_for_id(ann.class_id)
            if ann.shape_type == "bbox" and not ann.points:
                rect = self._normalized_to_rect(ann)
                item = EditableBoxItem(
                    index=index,
                    annotation=ann,
                    rect=rect,
                    image_rect=self._image_rect,
                    class_name=class_name,
                    on_selected=self._on_item_selected,
                    on_geometry_change=self._on_item_geometry_change,
                    center_marker_visible=self._center_marker_visible,
                )
                item.setZValue(1)
                self._scene.addItem(item)
                self._anno_items.append(item)
                continue

            poly = self._normalized_points_to_polygon(ann.normalized_points())
            if poly.count() < 3:
                rect = self._normalized_to_rect(ann)
                item2 = EditableBoxItem(
                    index=index,
                    annotation=ann,
                    rect=rect,
                    image_rect=self._image_rect,
                    class_name=class_name,
                    on_selected=self._on_item_selected,
                    on_geometry_change=self._on_item_geometry_change,
                    center_marker_visible=self._center_marker_visible,
                )
                item2.setZValue(1)
                self._scene.addItem(item2)
                self._anno_items.append(item2)
                continue

            p_item = StaticPolygonItem(
                index=index,
                annotation=ann,
                polygon=poly,
                image_rect=self._image_rect,
                class_name=class_name,
                on_selected=self._on_item_selected,
                center_marker_visible=self._center_marker_visible,
            )
            p_item.setZValue(1)
            self._scene.addItem(p_item)
            self._anno_items.append(p_item)

        self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def set_center_marker_visible(self, visible: bool) -> None:
        self._center_marker_visible = bool(visible)
        for item in self._anno_items:
            if hasattr(item, "set_center_marker_visible"):
                item.set_center_marker_visible(self._center_marker_visible)
            else:
                item.update()

    def _class_name_for_id(self, class_id: int) -> str:
        if 0 <= class_id < len(self._class_names):
            return self._class_names[class_id]
        return f"cls_{class_id}"

    def clear_content(self) -> None:
        self.cancel_create_mode()
        self._stop_flash()
        self._scene.clear()
        self._pixmap_item = self._scene.addPixmap(QPixmap())
        self._image_rect = QRectF()
        self._annotations.clear()
        self._anno_items.clear()
        hint = self._scene.addText("空状态：请先在左侧选择文件进行预览")
        hint.setDefaultTextColor(QColor("#64748b"))
        hint.setPos(20, 20)

    def _normalized_to_rect(self, ann: Annotation) -> QRectF:
        width = self._image_rect.width()
        height = self._image_rect.height()
        x = (ann.x_center - ann.width / 2) * width
        y = (ann.y_center - ann.height / 2) * height
        w = ann.width * width
        h = ann.height * height
        return QRectF(x, y, w, h)

    def _rect_to_normalized(self, rect: QRectF, class_id: int) -> Annotation:
        width = self._image_rect.width() if self._image_rect.width() else 1.0
        height = self._image_rect.height() if self._image_rect.height() else 1.0

        x_center = rect.center().x() / width
        y_center = rect.center().y() / height
        w = rect.width() / width
        h = rect.height() / height

        return Annotation(class_id=class_id, x_center=x_center, y_center=y_center, width=w, height=h, shape_type="bbox")

    def _normalized_points_to_polygon(self, points: list[tuple[float, float]]) -> QPolygonF:
        poly = QPolygonF()
        width = self._image_rect.width() if self._image_rect.width() else 1.0
        height = self._image_rect.height() if self._image_rect.height() else 1.0
        for x, y in points:
            poly.append(QPointF(x * width, y * height))
        return poly

    def _scene_points_to_normalized(self, points: list[QPointF]) -> list[tuple[float, float]]:
        width = self._image_rect.width() if self._image_rect.width() else 1.0
        height = self._image_rect.height() if self._image_rect.height() else 1.0
        out: list[tuple[float, float]] = []
        for p in points:
            out.append((p.x() / width, p.y() / height))
        return out

    def _on_item_selected(self, index: int) -> None:
        self.annotation_selected.emit(index)

    def _on_item_geometry_change(self, index: int, old_rect: QRectF, new_rect: QRectF) -> None:
        old_ann = self._rect_to_normalized(old_rect, self._annotations[index].class_id)
        new_ann = self._rect_to_normalized(new_rect, self._annotations[index].class_id)
        old_ann.shape_type = "bbox"
        new_ann.shape_type = "bbox"
        self.annotation_geometry_changed.emit(index, old_ann, new_ann)

    def select_annotation(self, index: int) -> None:
        if not (0 <= index < len(self._anno_items)):
            return
        for i, item in enumerate(self._anno_items):
            item.setSelected(i == index)
        self.annotation_selected.emit(index)

    def flash_annotation(self, index: int, flashes: int = 3) -> None:
        if not (0 <= index < len(self._anno_items)):
            return

        self._stop_flash()
        self._flash_index = index
        self._flash_ticks_left = max(1, flashes * 2)
        self._flash_state = False
        self._on_flash_tick()
        self._flash_timer.start()

    def _on_flash_tick(self) -> None:
        if not (0 <= self._flash_index < len(self._anno_items)):
            self._stop_flash()
            return

        self._flash_state = not self._flash_state
        item = self._anno_items[self._flash_index]
        if hasattr(item, "set_flash"):
            item.set_flash(self._flash_state)
        self._flash_ticks_left -= 1

        if self._flash_ticks_left <= 0:
            self._stop_flash()

    def _stop_flash(self) -> None:
        self._flash_timer.stop()
        if 0 <= self._flash_index < len(self._anno_items):
            item = self._anno_items[self._flash_index]
            if hasattr(item, "set_flash"):
                item.set_flash(False)
        self._flash_index = -1
        self._flash_ticks_left = 0
        self._flash_state = False

    def start_create_mode(self, class_id: int, shape_type: str = "bbox") -> None:
        self.cancel_create_mode()
        self._create_mode = True
        self._create_class_id = max(0, class_id)
        self._create_shape_type = shape_type if shape_type in ("bbox", "rotated", "polygon") else "bbox"
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def cancel_create_mode(self) -> None:
        self._create_mode = False
        self._creating = False
        if self._create_preview_item is not None:
            self._scene.removeItem(self._create_preview_item)
            self._create_preview_item = None
        if self._poly_preview_item is not None:
            self._scene.removeItem(self._poly_preview_item)
            self._poly_preview_item = None
        self._poly_points_scene.clear()
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def _clamp_point_to_image(self, p: QPointF) -> QPointF:
        if self._image_rect.isNull():
            return p
        x = clamp(p.x(), self._image_rect.left(), self._image_rect.right())
        y = clamp(p.y(), self._image_rect.top(), self._image_rect.bottom())
        return QPointF(x, y)

    def _create_preview_rect(self, start: QPointF, end: QPointF) -> QRectF:
        a = self._clamp_point_to_image(start)
        b = self._clamp_point_to_image(end)
        return QRectF(a, b).normalized()

    def _update_polygon_preview(self, current: QPointF | None = None) -> None:
        if not self._poly_points_scene:
            if self._poly_preview_item is not None:
                self._scene.removeItem(self._poly_preview_item)
                self._poly_preview_item = None
            return

        preview_points = list(self._poly_points_scene)
        if current is not None:
            preview_points.append(self._clamp_point_to_image(current))

        poly = QPolygonF(preview_points)
        if self._poly_preview_item is None:
            self._poly_preview_item = QGraphicsPolygonItem(poly)
            color = class_color(self._create_class_id)
            self._poly_preview_item.setPen(QPen(color, 2, Qt.PenStyle.DashLine))
            self._poly_preview_item.setBrush(QBrush(Qt.BrushStyle.NoBrush))
            self._poly_preview_item.setZValue(2)
            self._scene.addItem(self._poly_preview_item)
        else:
            self._poly_preview_item.setPolygon(poly)

    def _finalize_polygon_creation(self) -> None:
        if not self._create_mode or self._create_shape_type not in ("rotated", "polygon"):
            return

        min_points = 4 if self._create_shape_type == "rotated" else 3
        if len(self._poly_points_scene) < min_points:
            self.cancel_create_mode()
            return

        if self._create_shape_type == "rotated" and len(self._poly_points_scene) > 4:
            pts = self._poly_points_scene[:4]
        else:
            pts = self._poly_points_scene

        norm_points = self._scene_points_to_normalized(pts)
        xs = [p[0] for p in norm_points]
        ys = [p[1] for p in norm_points]
        x_min = min(xs)
        x_max = max(xs)
        y_min = min(ys)
        y_max = max(ys)

        ann = Annotation(
            class_id=self._create_class_id,
            x_center=(x_min + x_max) / 2,
            y_center=(y_min + y_max) / 2,
            width=max(0.0, x_max - x_min),
            height=max(0.0, y_max - y_min),
            shape_type=self._create_shape_type,
            points=norm_points,
        )

        self.cancel_create_mode()
        self.annotation_created.emit(ann)

    def update_annotations(self, annotations: list[Annotation], class_names: list[str], selected_index: int = -1) -> None:
        pixmap = self._pixmap_item.pixmap()
        self.set_content(pixmap, annotations, class_names)
        if 0 <= selected_index < len(self._anno_items):
            self._anno_items[selected_index].setSelected(True)

    def mousePressEvent(self, event) -> None:
        if self._create_mode and not self._image_rect.isNull():
            scene_pos = self.mapToScene(event.position().toPoint())
            clamped = self._clamp_point_to_image(scene_pos)

            if self._create_shape_type == "bbox":
                if event.button() == Qt.MouseButton.LeftButton:
                    if not self._image_rect.contains(scene_pos):
                        event.accept()
                        return

                    self._creating = True
                    self._create_start_scene = clamped
                    rect = QRectF(self._create_start_scene, self._create_start_scene)

                    if self._create_preview_item is None:
                        self._create_preview_item = QGraphicsRectItem(rect)
                        color = class_color(self._create_class_id)
                        pen = QPen(color, 2, Qt.PenStyle.DashLine)
                        self._create_preview_item.setPen(pen)
                        self._create_preview_item.setBrush(QBrush(Qt.BrushStyle.NoBrush))
                        self._create_preview_item.setZValue(2)
                        self._scene.addItem(self._create_preview_item)
                    else:
                        self._create_preview_item.setRect(rect)

                    event.accept()
                    return
            else:
                if event.button() == Qt.MouseButton.LeftButton:
                    if not self._image_rect.contains(scene_pos):
                        event.accept()
                        return

                    self._poly_points_scene.append(clamped)
                    if self._create_shape_type == "rotated" and len(self._poly_points_scene) >= 4:
                        self._finalize_polygon_creation()
                    else:
                        self._update_polygon_preview()
                    event.accept()
                    return

                if event.button() == Qt.MouseButton.RightButton:
                    self._finalize_polygon_creation()
                    event.accept()
                    return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._create_mode and self._create_shape_type == "bbox" and self._creating and self._create_preview_item is not None:
            scene_pos = self.mapToScene(event.position().toPoint())
            rect = self._create_preview_rect(self._create_start_scene, scene_pos)
            self._create_preview_item.setRect(rect)
            event.accept()
            return

        if self._create_mode and self._create_shape_type in ("rotated", "polygon") and self._poly_points_scene:
            scene_pos = self.mapToScene(event.position().toPoint())
            self._update_polygon_preview(scene_pos)
            event.accept()
            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._create_mode and self._create_shape_type == "bbox" and self._creating and event.button() == Qt.MouseButton.LeftButton:
            self._creating = False
            created_ann = None
            if self._create_preview_item is not None:
                rect = self._create_preview_item.rect().normalized()
                self._scene.removeItem(self._create_preview_item)
                self._create_preview_item = None
                if rect.width() >= 6 and rect.height() >= 6:
                    created_ann = self._rect_to_normalized(rect, self._create_class_id)

            self.cancel_create_mode()

            if created_ann is not None:
                self.annotation_created.emit(created_ann)
            event.accept()
            return

        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape and self._create_mode:
            self.cancel_create_mode()
            event.accept()
            return

        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self._create_mode and self._create_shape_type in ("rotated", "polygon"):
            self._finalize_polygon_creation()
            event.accept()
            return

        if event.key() == Qt.Key.Key_Delete:
            selected = [item for item in self._anno_items if item.isSelected()]
            if selected:
                idx = getattr(selected[0], "index", -1)
                if idx >= 0:
                    self.delete_requested.emit(idx)
                    event.accept()
                    return
        super().keyPressEvent(event)

    def wheelEvent(self, event) -> None:
        factor = 1.15 if event.angleDelta().y() > 0 else 0.87
        self.scale(factor, factor)

    def grab_annotated_image(self) -> QImage:
        if self._scene.sceneRect().isNull():
            return QImage()
        image = QImage(
            int(self._scene.sceneRect().width()),
            int(self._scene.sceneRect().height()),
            QImage.Format.Format_ARGB32,
        )
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        self._scene.render(painter)
        painter.end()
        return image
