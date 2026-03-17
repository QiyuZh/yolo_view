from __future__ import annotations

from dataclasses import replace

from PyQt6.QtGui import QUndoCommand

from .models import Annotation


class UpdateAnnotationCommand(QUndoCommand):
    def __init__(
        self,
        annotations: list[Annotation],
        index: int,
        old_value: Annotation,
        new_value: Annotation,
        on_apply,
    ) -> None:
        super().__init__("移动/缩放标注")
        self.annotations = annotations
        self.index = index
        self.old_value = replace(old_value)
        self.new_value = replace(new_value)
        self.on_apply = on_apply

    def undo(self) -> None:
        self.annotations[self.index] = replace(self.old_value)
        self.on_apply(self.index)

    def redo(self) -> None:
        self.annotations[self.index] = replace(self.new_value)
        self.on_apply(self.index)


class ChangeClassCommand(QUndoCommand):
    def __init__(
        self,
        annotations: list[Annotation],
        index: int,
        old_class_id: int,
        new_class_id: int,
        on_apply,
    ) -> None:
        super().__init__("修改类别")
        self.annotations = annotations
        self.index = index
        self.old_class_id = old_class_id
        self.new_class_id = new_class_id
        self.on_apply = on_apply

    def undo(self) -> None:
        self.annotations[self.index].class_id = self.old_class_id
        self.on_apply(self.index)

    def redo(self) -> None:
        self.annotations[self.index].class_id = self.new_class_id
        self.on_apply(self.index)


class DeleteAnnotationCommand(QUndoCommand):
    def __init__(self, annotations: list[Annotation], index: int, on_apply) -> None:
        super().__init__("删除标注")
        self.annotations = annotations
        self.index = index
        self.deleted = replace(annotations[index])
        self.on_apply = on_apply

    def undo(self) -> None:
        self.annotations.insert(self.index, replace(self.deleted))
        self.on_apply(self.index)

    def redo(self) -> None:
        self.annotations.pop(self.index)
        self.on_apply(min(self.index, len(self.annotations) - 1))
