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
)
from PyQt6.QtWidgets import QGraphicsRectItem, QGraphicsScene, QGraphicsView, QStyleOptionGraphicsItem, QWidget

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
                # Defer callback to avoid rebuilding scene while this item is still in release event.
                QTimer.singleShot(0, lambda idx=self.index, o=QRectF(old_rect), n=QRectF(new_rect): self.on_geometry_change(idx, o, n))
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

        label = f"{self.class_name} ({self.annotation.class_id})"
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(QFont("Segoe UI", 9))

        # Place label outside the bbox to avoid covering tiny boxes.
        text_rect = painter.boundingRect(r.toRect(), Qt.AlignmentFlag.AlignLeft, label)
        text_rect.adjust(-4, -2, 4, 2)

        label_margin = 4.0
        candidate_top = r.top() - text_rect.height() - label_margin
        if candidate_top >= self.image_rect.top():
            text_rect.moveTopLeft(QPointF(r.left(), candidate_top).toPoint())
        else:
            below_top = min(r.bottom() + label_margin, self.image_rect.bottom() - text_rect.height())
            text_rect.moveTopLeft(QPointF(r.left(), below_top).toPoint())

        if text_rect.left() < self.image_rect.left():
            text_rect.moveLeft(int(self.image_rect.left()))
        if text_rect.right() > self.image_rect.right():
            text_rect.moveRight(int(self.image_rect.right()))

        bg = QColor(self._color)
        bg.setAlpha(180)
        painter.fillRect(text_rect, bg)
        painter.setPen(QPen(Qt.GlobalColor.white))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, label)

        # Draw a high-contrast center marker; shrink it for tiny boxes.
        cx = r.center().x()
        cy = r.center().y()
        min_dim = max(1.0, min(r.width(), r.height()))
        marker_radius = 2 if min_dim < 14 else 4
        cross_half = 4 if min_dim < 14 else 6
        line_w = 1 if min_dim < 14 else 2

        painter.setPen(QPen(Qt.GlobalColor.white, line_w))
        painter.drawEllipse(QPointF(cx, cy), marker_radius, marker_radius)
        painter.setPen(QPen(self._color, line_w))
        painter.drawLine(cx - cross_half, cy, cx + cross_half, cy)
        painter.drawLine(cx, cy - cross_half, cx, cy + cross_half)


class ImageCanvas(QGraphicsView):
    annotation_geometry_changed = pyqtSignal(int, object, object)
    annotation_selected = pyqtSignal(int)
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
        self._box_items: list[EditableBoxItem] = []
        self._image_rect = QRectF()

        self._flash_timer = QTimer(self)
        self._flash_timer.setInterval(140)
        self._flash_timer.timeout.connect(self._on_flash_tick)
        self._flash_index = -1
        self._flash_ticks_left = 0
        self._flash_state = False

    def set_content(self, pixmap: QPixmap, annotations: list[Annotation], class_names: list[str]) -> None:
        self._stop_flash()
        self._scene.clear()
        self._pixmap_item = self._scene.addPixmap(pixmap)
        self._pixmap_item.setZValue(0)

        self._image_rect = QRectF(self._pixmap_item.boundingRect())
        self._scene.setSceneRect(self._image_rect)

        self._annotations = [replace(ann) for ann in annotations]
        self._class_names = class_names
        self._box_items = []

        for index, ann in enumerate(self._annotations):
            rect = self._normalized_to_rect(ann)
            class_name = self._class_name_for_id(ann.class_id)
            item = EditableBoxItem(
                index=index,
                annotation=ann,
                rect=rect,
                image_rect=self._image_rect,
                class_name=class_name,
                on_selected=self._on_item_selected,
                on_geometry_change=self._on_item_geometry_change,
            )
            item.setZValue(1)
            self._scene.addItem(item)
            self._box_items.append(item)

        self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def _class_name_for_id(self, class_id: int) -> str:
        if 0 <= class_id < len(self._class_names):
            return self._class_names[class_id]
        return f"cls_{class_id}"

    def clear_content(self) -> None:
        self._stop_flash()
        self._scene.clear()
        self._pixmap_item = self._scene.addPixmap(QPixmap())
        self._annotations.clear()
        self._box_items.clear()

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

        x_center = (rect.center().x()) / width
        y_center = (rect.center().y()) / height
        w = rect.width() / width
        h = rect.height() / height

        return Annotation(class_id=class_id, x_center=x_center, y_center=y_center, width=w, height=h)

    def _on_item_selected(self, index: int) -> None:
        self.annotation_selected.emit(index)

    def _on_item_geometry_change(self, index: int, old_rect: QRectF, new_rect: QRectF) -> None:
        old_ann = self._rect_to_normalized(old_rect, self._annotations[index].class_id)
        new_ann = self._rect_to_normalized(new_rect, self._annotations[index].class_id)
        self.annotation_geometry_changed.emit(index, old_ann, new_ann)

    def select_annotation(self, index: int) -> None:
        if not (0 <= index < len(self._box_items)):
            return
        for i, item in enumerate(self._box_items):
            item.setSelected(i == index)
        self.annotation_selected.emit(index)

    def flash_annotation(self, index: int, flashes: int = 3) -> None:
        if not (0 <= index < len(self._box_items)):
            return

        self._stop_flash()
        self._flash_index = index
        self._flash_ticks_left = max(1, flashes * 2)
        self._flash_state = False
        self._on_flash_tick()
        self._flash_timer.start()

    def _on_flash_tick(self) -> None:
        if not (0 <= self._flash_index < len(self._box_items)):
            self._stop_flash()
            return

        self._flash_state = not self._flash_state
        self._box_items[self._flash_index].set_flash(self._flash_state)
        self._flash_ticks_left -= 1

        if self._flash_ticks_left <= 0:
            self._stop_flash()

    def _stop_flash(self) -> None:
        self._flash_timer.stop()
        if 0 <= self._flash_index < len(self._box_items):
            self._box_items[self._flash_index].set_flash(False)
        self._flash_index = -1
        self._flash_ticks_left = 0
        self._flash_state = False

    def update_annotations(self, annotations: list[Annotation], class_names: list[str], selected_index: int = -1) -> None:
        pixmap = self._pixmap_item.pixmap()
        self.set_content(pixmap, annotations, class_names)
        if 0 <= selected_index < len(self._box_items):
            self._box_items[selected_index].setSelected(True)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Delete:
            selected = [item for item in self._box_items if item.isSelected()]
            if selected:
                self.delete_requested.emit(selected[0].index)
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
