
from __future__ import annotations

import copy
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QIcon, QPixmap, QUndoStack
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
    QInputDialog,
)

from .auto_annotator import AutoAnnotator, AutoAnnotatorError
from .exporter import export_passed_files
from .file_manager import PixmapCache, load_pixmap, scan_dataset
from .models import Annotation, DatasetItem, FileValidation
from .undo_commands import ChangeClassCommand, DeleteAnnotationCommand, UpdateAnnotationCommand
from .validator import validate_item
from .widgets.image_canvas import ImageCanvas

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None


ANOMALY_TYPES: list[tuple[str, str]] = [
    ("format_error", "格式错误"),
    ("label_missing", "缺失标签"),
    ("image_missing", "缺失图片"),
    ("empty_label", "空标签"),
]


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("YOLO 数据集查看与校验工具")
        self.resize(1720, 1000)

        self.dataset_roots: list[Path] = []
        self.items: list[DatasetItem] = []
        self.item_roots: list[Path] = []
        self.validation_map: dict[int, FileValidation] = {}
        self.image_size_cache: dict[int, str] = {}
        self.visible_indices: list[int] = []
        self.anomaly_index: dict[str, list[tuple[int, str]]] = {code: [] for code, _ in ANOMALY_TYPES}

        self.class_names: list[str] = []
        self.current_index: int = -1
        self.current_annotations: list[Annotation] = []
        self._updating_ui = False
        self._backed_up_files: set[Path] = set()

        self.cache = PixmapCache(max_items=28)
        self.undo_stack = QUndoStack(self)
        self.auto_model_path: Path | None = None
        self.auto_conf_threshold: float = 0.25

        self._setup_ui()
        self._apply_style()

    def _setup_ui(self) -> None:
        self.setStatusBar(QStatusBar(self))

        toolbar = QToolBar("主工具栏")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        import_action = QAction("导入文件夹", self)
        import_action.triggered.connect(self.import_folders)
        toolbar.addAction(import_action)

        append_action = QAction("追加文件夹", self)
        append_action.triggered.connect(self.append_folders)
        toolbar.addAction(append_action)

        validate_action = QAction("全量校验", self)
        validate_action.triggered.connect(self.validate_all)
        toolbar.addAction(validate_action)

        auto_annotate_action = QAction("加载模型自动标注", self)
        auto_annotate_action.triggered.connect(self.auto_annotate)
        toolbar.addAction(auto_annotate_action)

        export_passed_action = QAction("导出通过项", self)
        export_passed_action.triggered.connect(self.export_passed)
        toolbar.addAction(export_passed_action)

        screenshot_action = QAction("保存截图", self)
        screenshot_action.triggered.connect(self.save_screenshot)
        toolbar.addAction(screenshot_action)

        toolbar.addSeparator()
        toolbar.addAction(self.undo_stack.createUndoAction(self, "撤销"))
        toolbar.addAction(self.undo_stack.createRedoAction(self, "重做"))

        root = QWidget(self)
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter)

        left = QWidget()
        left_layout = QVBoxLayout(left)

        search_row = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索：文件名/路径/类别")
        self.search_edit.textChanged.connect(self._rebuild_file_table)
        search_row.addWidget(self.search_edit)

        clear_btn = QPushButton("清空")
        clear_btn.clicked.connect(lambda: self.search_edit.setText(""))
        search_row.addWidget(clear_btn)
        left_layout.addLayout(search_row)

        filter_row = QHBoxLayout()
        self.sort_combo = QComboBox()
        self.sort_combo.addItems([
            "名称升序",
            "名称降序",
            "是否标注（已标注在前）",
            "是否标注（未标注在前）",
        ])
        self.sort_combo.currentIndexChanged.connect(self._rebuild_file_table)
        filter_row.addWidget(self.sort_combo)

        self.mark_filter_combo = QComboBox()
        self.mark_filter_combo.addItems(["全部", "仅已标注", "仅未标注"])
        self.mark_filter_combo.currentIndexChanged.connect(self._rebuild_file_table)
        filter_row.addWidget(self.mark_filter_combo)

        self.status_filter_combo = QComboBox()
        self.status_filter_combo.addItems(["全部状态", "通过", "警告", "错误", "未校验"])
        self.status_filter_combo.currentIndexChanged.connect(self._rebuild_file_table)
        filter_row.addWidget(self.status_filter_combo)
        left_layout.addLayout(filter_row)

        self.file_table = QTableWidget(0, 6)
        self.file_table.setHorizontalHeaderLabels(["文件名", "尺寸", "标签数", "类别", "状态", "路径"])
        self.file_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.file_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.file_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.file_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.file_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.file_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.file_table.verticalHeader().setVisible(False)
        self.file_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.file_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.file_table.currentCellChanged.connect(self.on_file_row_changed)
        left_layout.addWidget(self.file_table, stretch=6)

        anomaly_head = QHBoxLayout()
        anomaly_head.addWidget(QLabel("异常分类"))

        self.anomaly_combo = QComboBox()
        self.anomaly_combo.currentIndexChanged.connect(self._refresh_anomaly_list)
        anomaly_head.addWidget(self.anomaly_combo)

        scan_anomaly_btn = QPushButton("扫描异常")
        scan_anomaly_btn.clicked.connect(self.scan_anomalies)
        anomaly_head.addWidget(scan_anomaly_btn)
        left_layout.addLayout(anomaly_head)

        self.anomaly_list = QListWidget()
        self.anomaly_list.itemClicked.connect(self.on_anomaly_clicked)
        left_layout.addWidget(self.anomaly_list, stretch=3)

        splitter.addWidget(left)

        middle = QWidget()
        middle_layout = QVBoxLayout(middle)

        self.canvas = ImageCanvas()
        self.canvas.annotation_selected.connect(self.on_canvas_selection_changed)
        self.canvas.annotation_geometry_changed.connect(self.on_canvas_geometry_changed)
        self.canvas.delete_requested.connect(self.on_delete_annotation)
        middle_layout.addWidget(self.canvas, stretch=8)

        tip_label = QLabel(
            "提示：拖动框体可移动，拖动边缘或角点可缩放，滚轮可缩放视图，按 Delete 可删除选中框。"
        )
        middle_layout.addWidget(tip_label, stretch=0)

        splitter.addWidget(middle)

        right = QWidget()
        right_layout = QVBoxLayout(right)

        self.annotation_table = QTableWidget(0, 8)
        self.annotation_table.setHorizontalHeaderLabels(
            ["序号", "类别ID", "类别名", "中心X", "中心Y", "宽度", "高度", "置信度"]
        )
        self.annotation_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.annotation_table.verticalHeader().setVisible(False)
        self.annotation_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.annotation_table.currentCellChanged.connect(self.on_table_selection_changed)
        right_layout.addWidget(self.annotation_table, stretch=5)

        btn_row = QHBoxLayout()
        self.class_btn = QPushButton("修改类别")
        self.class_btn.clicked.connect(self.on_change_class)
        btn_row.addWidget(self.class_btn)

        self.delete_btn = QPushButton("删除框")
        self.delete_btn.clicked.connect(self.delete_selected_box)
        btn_row.addWidget(self.delete_btn)
        right_layout.addLayout(btn_row)

        self.issue_text = QTextEdit()
        self.issue_text.setReadOnly(True)
        right_layout.addWidget(self.issue_text, stretch=4)

        splitter.addWidget(right)
        splitter.setSizes([640, 860, 420])

        self._refresh_anomaly_combo()

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow {
                background: #f4f7fb;
                font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", "Segoe UI";
            }
            QToolBar {
                background: #1f2937;
                border: none;
                spacing: 8px;
                padding: 6px;
            }
            QToolButton {
                color: #f8fafc;
                background: #374151;
                border-radius: 6px;
                padding: 6px 10px;
            }
            QToolButton:hover {
                background: #4b5563;
            }
            QListWidget, QTableWidget, QTextEdit, QLineEdit, QComboBox {
                background: white;
                border: 1px solid #d1d5db;
                border-radius: 8px;
            }
            QLabel {
                color: #111827;
            }
            QPushButton {
                background: #0f766e;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px;
            }
            QPushButton:hover {
                background: #115e59;
            }
            """
        )

    def _pick_folders(self) -> list[Path]:
        folders: list[Path] = []
        start_dir = str(self.dataset_roots[0]) if self.dataset_roots else ""

        while True:
            selected = QFileDialog.getExistingDirectory(
                self,
                "选择数据集文件夹（取消结束选择）",
                start_dir,
            )
            if not selected:
                break

            p = Path(selected).resolve()
            if p not in folders:
                folders.append(p)

            cont = QMessageBox.question(
                self,
                "继续添加",
                "是否继续添加下一个文件夹？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if cont != QMessageBox.StandardButton.Yes:
                break

        return folders

    def import_folders(self, _checked: bool = False) -> None:
        folders = self._pick_folders()
        if not folders:
            return
        self._load_folders(folders, append=False)

    def append_folders(self, _checked: bool = False) -> None:
        if not self.items:
            self.import_folders()
            return
        folders = self._pick_folders()
        if not folders:
            return
        self._load_folders(folders, append=True)

    def _load_folders(self, folders: list[Path], append: bool) -> None:
        valid_folders = [p for p in folders if p.exists()]
        if not valid_folders:
            QMessageBox.warning(self, "路径错误", "未找到可用文件夹。")
            return

        if not append:
            self.dataset_roots.clear()
            self.items.clear()
            self.item_roots.clear()
            self.validation_map.clear()
            self.visible_indices.clear()
            self.image_size_cache.clear()
            self._backed_up_files.clear()
            self.undo_stack.clear()
            self.current_index = -1
            self.current_annotations.clear()

        added_roots = 0
        added_items = 0

        progress = QProgressDialog("正在扫描数据集文件夹...", "取消", 0, len(valid_folders), self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)

        for i, root in enumerate(valid_folders, start=1):
            if progress.wasCanceled():
                break

            if root in self.dataset_roots:
                progress.setValue(i)
                QApplication.processEvents()
                continue

            scanned_items = scan_dataset(root)
            for item in scanned_items:
                self.items.append(item)
                self.item_roots.append(root)
                added_items += 1

            self.dataset_roots.append(root)
            added_roots += 1
            progress.setValue(i)
            QApplication.processEvents()

        progress.close()

        self.class_names = self._merge_class_names(self.dataset_roots)
        self._rebuild_file_table()
        self._rebuild_anomaly_index(full_scan=False)

        if self.visible_indices and self.current_index < 0:
            self._select_global_index(self.visible_indices[0])

        self.statusBar().showMessage(
            f"已加载 {len(self.items)} 个样本，来自 {len(self.dataset_roots)} 个文件夹，本次新增 {added_items} 个样本。"
        )

        if added_roots == 0:
            QMessageBox.information(self, "提示", "所选文件夹均已导入。")
    def _load_class_names(self, root: Path) -> list[str]:
        classes_txt = root / "classes.txt"
        if classes_txt.exists():
            names = [line.strip() for line in classes_txt.read_text(encoding="utf-8", errors="ignore").splitlines()]
            names = [n for n in names if n]
            if names:
                return names

        if yaml is not None:
            for yaml_name in ("data.yaml", "dataset.yaml"):
                yaml_path = root / yaml_name
                if not yaml_path.exists():
                    continue
                try:
                    parsed = yaml.safe_load(yaml_path.read_text(encoding="utf-8", errors="ignore"))
                except Exception:
                    continue
                names = parsed.get("names") if isinstance(parsed, dict) else None
                if isinstance(names, list):
                    return [str(v) for v in names]
                if isinstance(names, dict):
                    return [str(names[k]) for k in sorted(names)]
        return []

    def _merge_class_names(self, roots: list[Path]) -> list[str]:
        merged: list[str] = []
        for root in roots:
            names = self._load_class_names(root)
            if not names:
                continue
            if not merged:
                merged = list(names)
                continue
            for i, name in enumerate(names):
                if i >= len(merged):
                    merged.append(name)
                elif merged[i].startswith("cls_") and name:
                    merged[i] = name
        return merged

    def _ensure_class_name(self, class_id: int) -> None:
        while len(self.class_names) <= class_id:
            self.class_names.append(f"cls_{len(self.class_names)}")

    def _display_file_name(self, idx: int) -> str:
        root = self.item_roots[idx]
        return f"{root.name}/{self.items[idx].display_name()}"

    def _item_path_text(self, idx: int) -> str:
        item = self.items[idx]
        root = self.item_roots[idx]
        if item.image_path is not None:
            return str(item.image_path)
        if item.label_path is not None:
            return str(item.label_path)
        return str(root)

    def _item_status(self, idx: int) -> str:
        validation = self.validation_map.get(idx)
        item = self.items[idx]

        if validation is None:
            if item.image_path is None or (item.image_path and not item.image_path.exists()):
                return "错误"
            if item.label_path is None:
                return "警告"
            return "未校验"

        if validation.has_error:
            return "错误"
        if validation.issues:
            return "警告"
        return "通过"

    def _is_marked(self, idx: int) -> bool:
        validation = self.validation_map.get(idx)
        if validation is not None:
            return len(validation.annotations) > 0

        label_path = self.items[idx].label_path
        if label_path is None or (not label_path.exists()):
            return False
        try:
            return label_path.stat().st_size > 0
        except Exception:
            return False

    def _item_tag_count_text(self, idx: int) -> str:
        validation = self.validation_map.get(idx)
        if validation is None:
            label_path = self.items[idx].label_path
            if label_path is None:
                return "0"
            return "-"
        return str(len(validation.annotations))

    def _item_class_text(self, idx: int) -> str:
        validation = self.validation_map.get(idx)
        if validation is None:
            return "-"

        classes = sorted({ann.class_id for ann in validation.annotations})
        if not classes:
            return "-"

        names: list[str] = []
        for class_id in classes[:3]:
            self._ensure_class_name(class_id)
            names.append(self.class_names[class_id])
        if len(classes) > 3:
            names.append("...")
        return ",".join(names)

    def _item_size_text(self, idx: int) -> str:
        return self.image_size_cache.get(idx, "-")

    def _set_file_table_row(self, row: int, idx: int) -> None:
        values = [
            self._display_file_name(idx),
            self._item_size_text(idx),
            self._item_tag_count_text(idx),
            self._item_class_text(idx),
            self._item_status(idx),
            self._item_path_text(idx),
        ]
        for col, value in enumerate(values):
            cell = QTableWidgetItem(value)
            cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if col == 0:
                cell.setData(Qt.ItemDataRole.UserRole, idx)
            self.file_table.setItem(row, col, cell)

    def _refresh_visible_row_for_index(self, idx: int) -> None:
        if idx not in self.visible_indices:
            return
        row = self.visible_indices.index(idx)
        self._updating_ui = True
        try:
            self._set_file_table_row(row, idx)
        finally:
            self._updating_ui = False

    def _matches_filters(self, idx: int, keyword: str, mark_filter: str, status_filter: str) -> bool:
        if keyword:
            target = (
                self._display_file_name(idx)
                + "\n"
                + self._item_path_text(idx)
                + "\n"
                + self._item_class_text(idx)
            ).lower()
            if keyword not in target:
                return False

        marked = self._is_marked(idx)
        if mark_filter == "仅已标注" and not marked:
            return False
        if mark_filter == "仅未标注" and marked:
            return False

        if status_filter != "全部状态":
            if self._item_status(idx) != status_filter:
                return False

        return True

    def _sort_indices(self, indices: list[int], mode: str) -> list[int]:
        if mode == "名称升序":
            return sorted(indices, key=lambda i: self._display_file_name(i).lower())
        if mode == "名称降序":
            return sorted(indices, key=lambda i: self._display_file_name(i).lower(), reverse=True)
        if mode == "是否标注（已标注在前）":
            return sorted(indices, key=lambda i: (not self._is_marked(i), self._display_file_name(i).lower()))
        if mode == "是否标注（未标注在前）":
            return sorted(indices, key=lambda i: (self._is_marked(i), self._display_file_name(i).lower()))
        return indices

    def _rebuild_file_table(self) -> None:
        previous_idx = self.current_index

        keyword = self.search_edit.text().strip().lower()
        mark_filter = self.mark_filter_combo.currentText()
        status_filter = self.status_filter_combo.currentText()
        sort_mode = self.sort_combo.currentText()

        filtered = [
            i
            for i in range(len(self.items))
            if self._matches_filters(i, keyword, mark_filter, status_filter)
        ]
        self.visible_indices = self._sort_indices(filtered, sort_mode)

        self._updating_ui = True
        try:
            self.file_table.setRowCount(len(self.visible_indices))
            for row, idx in enumerate(self.visible_indices):
                self._set_file_table_row(row, idx)
        finally:
            self._updating_ui = False

        if previous_idx in self.visible_indices:
            row = self.visible_indices.index(previous_idx)
            self._updating_ui = True
            try:
                self.file_table.selectRow(row)
                self.file_table.setCurrentCell(row, 0)
            finally:
                self._updating_ui = False
        elif self.visible_indices and self.current_index < 0:
            self._select_global_index(self.visible_indices[0])

    def on_file_row_changed(self, current_row: int, *_args) -> None:
        if self._updating_ui:
            return
        if current_row < 0 or current_row >= len(self.visible_indices):
            return
        self._select_global_index(self.visible_indices[current_row])

    def _select_global_index(self, idx: int) -> None:
        if idx < 0 or idx >= len(self.items):
            return
        self.on_file_selected(idx, refresh_table=False)

        if idx in self.visible_indices:
            row = self.visible_indices.index(idx)
            self._updating_ui = True
            try:
                self.file_table.selectRow(row)
                self.file_table.setCurrentCell(row, 0)
            finally:
                self._updating_ui = False

    def _refresh_anomaly_combo(self) -> None:
        total = sum(len(v) for v in self.anomaly_index.values())

        current_code = self.anomaly_combo.currentData() if self.anomaly_combo.count() else "all"

        self.anomaly_combo.blockSignals(True)
        self.anomaly_combo.clear()
        self.anomaly_combo.addItem(f"全部异常 ({total})", "all")
        for code, label in ANOMALY_TYPES:
            self.anomaly_combo.addItem(f"{label} ({len(self.anomaly_index.get(code, []))})", code)
        self.anomaly_combo.blockSignals(False)

        if current_code is not None:
            for i in range(self.anomaly_combo.count()):
                if self.anomaly_combo.itemData(i) == current_code:
                    self.anomaly_combo.setCurrentIndex(i)
                    break

        self._refresh_anomaly_list()

    def _collect_anomalies_for_index(self, idx: int) -> list[tuple[str, str]]:
        result: list[tuple[str, str]] = []
        item = self.items[idx]
        validation = self.validation_map.get(idx)

        if validation is None:
            if item.image_path is None or (item.image_path and not item.image_path.exists()):
                result.append(("image_missing", "图片文件缺失。"))
            if item.label_path is None:
                result.append(("label_missing", "该图片缺少标签文件。"))
            return result

        for issue in validation.issues:
            if issue.code in self.anomaly_index:
                msg = issue.message
                if issue.line_number:
                    msg = f"第 {issue.line_number} 行：{msg}"
                result.append((issue.code, msg))

        return result

    def _update_anomaly_for_index(self, idx: int, refresh_combo: bool = True) -> None:
        for code in list(self.anomaly_index.keys()):
            self.anomaly_index[code] = [entry for entry in self.anomaly_index[code] if entry[0] != idx]

        for code, message in self._collect_anomalies_for_index(idx):
            self.anomaly_index[code].append((idx, message))

        if refresh_combo:
            self._refresh_anomaly_combo()

    def _rebuild_anomaly_index(self, full_scan: bool) -> None:
        self.anomaly_index = {code: [] for code, _ in ANOMALY_TYPES}

        if full_scan and self.items:
            progress = QProgressDialog("正在扫描异常项...", "取消", 0, len(self.items), self)
            progress.setWindowModality(Qt.WindowModality.WindowModal)

            for n, idx in enumerate(range(len(self.items)), start=1):
                if progress.wasCanceled():
                    break
                self.validation_map[idx] = validate_item(self.items[idx])
                progress.setValue(n)
                QApplication.processEvents()
            progress.close()

        for idx in range(len(self.items)):
            for code, message in self._collect_anomalies_for_index(idx):
                self.anomaly_index[code].append((idx, message))

        self._refresh_anomaly_combo()

    def scan_anomalies(self) -> None:
        if not self.items:
            QMessageBox.information(self, "未导入数据集", "请先导入数据集。")
            return
        self._rebuild_anomaly_index(full_scan=True)
        self._rebuild_file_table()
        QMessageBox.information(self, "扫描完成", "异常分类面板已更新。")

    def _refresh_anomaly_list(self) -> None:
        code = self.anomaly_combo.currentData()

        self.anomaly_list.clear()
        entries: list[tuple[str, int, str]] = []

        if code == "all" or code is None:
            for item_code, label in ANOMALY_TYPES:
                for idx, message in self.anomaly_index.get(item_code, []):
                    entries.append((label, idx, message))
        else:
            label = dict(ANOMALY_TYPES).get(code, code)
            for idx, message in self.anomaly_index.get(code, []):
                entries.append((label, idx, message))

        for label, idx, message in entries:
            text = f"[{label}] {self._display_file_name(idx)}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, idx)
            item.setToolTip(message)
            self.anomaly_list.addItem(item)

    def on_anomaly_clicked(self, item: QListWidgetItem) -> None:
        idx = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(idx, int):
            return

        if idx not in self.visible_indices:
            self.search_edit.setText("")
            self.mark_filter_combo.setCurrentText("全部")
            self.status_filter_combo.setCurrentText("全部状态")
            self._rebuild_file_table()

        self._select_global_index(idx)

    def on_file_selected(self, global_idx: int, refresh_table: bool = True) -> None:
        if global_idx < 0 or global_idx >= len(self.items):
            return

        self.current_index = global_idx
        self.undo_stack.clear()
        item = self.items[global_idx]

        self._updating_ui = True
        try:
            validation = self._ensure_validation(global_idx)
            self.current_annotations = copy.deepcopy(validation.annotations)
            for ann in self.current_annotations:
                self._ensure_class_name(ann.class_id)

            pixmap = QPixmap()
            if item.image_path and item.image_path.exists():
                cached = self.cache.get(item.image_path)
                pixmap = cached if cached else load_pixmap(item.image_path)
                if cached is None and not pixmap.isNull():
                    self.cache.put(item.image_path, pixmap)

            if pixmap.isNull():
                self.statusBar().showMessage("图片缺失或解码失败。")
                self.canvas.clear_content()
                self.image_size_cache.pop(global_idx, None)
            else:
                self.canvas.set_content(pixmap, self.current_annotations, self.class_names)
                self.image_size_cache[global_idx] = f"{pixmap.width()} x {pixmap.height()}"

            self._refresh_annotation_table(selected_index=0 if self.current_annotations else -1)
            self._show_validation_issues(validation)
            if refresh_table:
                self._refresh_visible_row_for_index(global_idx)
            self._update_anomaly_for_index(global_idx, refresh_combo=True)
        finally:
            self._updating_ui = False

    def _ensure_validation(self, idx: int) -> FileValidation:
        cached = self.validation_map.get(idx)
        if cached is not None:
            return cached
        checked = validate_item(self.items[idx])
        self.validation_map[idx] = checked
        return checked

    def _refresh_annotation_table(self, selected_index: int = -1) -> None:
        self.annotation_table.setRowCount(len(self.current_annotations))
        for row, ann in enumerate(self.current_annotations):
            self._ensure_class_name(ann.class_id)
            values = [
                str(row),
                str(ann.class_id),
                self.class_names[ann.class_id],
                f"{ann.x_center:.6f}",
                f"{ann.y_center:.6f}",
                f"{ann.width:.6f}",
                f"{ann.height:.6f}",
                f"{ann.confidence:.4f}" if ann.confidence is not None else "-",
            ]
            for col, text in enumerate(values):
                table_item = QTableWidgetItem(text)
                table_item.setFlags(table_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.annotation_table.setItem(row, col, table_item)

        if 0 <= selected_index < len(self.current_annotations):
            self.annotation_table.selectRow(selected_index)
            self.annotation_table.setCurrentCell(selected_index, 0)

    def _show_validation_issues(self, validation: FileValidation) -> None:
        if not validation.issues:
            self.issue_text.setText("未发现问题。")
            return

        lines = []
        severity_map = {"error": "错误", "warning": "警告", "info": "提示"}
        code_map = {
            "image_missing": "图片缺失",
            "label_missing": "标签缺失",
            "empty_label": "空标签",
            "format_error": "格式错误",
            "parse_error": "解析错误",
            "class_error": "类别错误",
            "size_error": "尺寸错误",
            "range_error": "范围错误",
            "confidence_range": "置信度越界",
            "bbox_out_of_bounds": "框越界",
            "empty_annotation": "无有效标注",
        }
        for issue in validation.issues:
            line_hint = f"（第 {issue.line_number} 行）" if issue.line_number else ""
            severity_text = severity_map.get(issue.severity, issue.severity)
            code_text = code_map.get(issue.code, issue.code)
            lines.append(f"[{severity_text}] {code_text}{line_hint}: {issue.message}")
        self.issue_text.setText("\n".join(lines))
    def on_table_selection_changed(self, current_row: int, *_args) -> None:
        if self._updating_ui:
            return
        if 0 <= current_row < len(self.current_annotations):
            self.canvas.select_annotation(current_row)
            self.canvas.flash_annotation(current_row)

    def on_canvas_selection_changed(self, index: int) -> None:
        if self._updating_ui:
            return
        if 0 <= index < self.annotation_table.rowCount():
            self.annotation_table.selectRow(index)
            self.annotation_table.setCurrentCell(index, 0)

    def on_canvas_geometry_changed(self, index: int, old_ann: Annotation, new_ann: Annotation) -> None:
        if self.current_index < 0 or index >= len(self.current_annotations):
            return
        old_ann.class_id = self.current_annotations[index].class_id
        new_ann.class_id = self.current_annotations[index].class_id
        old_ann.confidence = self.current_annotations[index].confidence
        new_ann.confidence = self.current_annotations[index].confidence

        cmd = UpdateAnnotationCommand(
            annotations=self.current_annotations,
            index=index,
            old_value=old_ann,
            new_value=new_ann,
            on_apply=self._on_annotations_applied,
        )
        self.undo_stack.push(cmd)

    def on_change_class(self) -> None:
        row = self.annotation_table.currentRow()
        if row < 0 or row >= len(self.current_annotations):
            QMessageBox.information(self, "未选择", "请先选择一个标注框。")
            return

        current = self.current_annotations[row].class_id
        new_class, ok = QInputDialog.getInt(
            self,
            "修改类别",
            "请输入新的类别ID：",
            value=current,
            min=0,
            max=100000,
        )
        if not ok or new_class == current:
            return

        self._ensure_class_name(new_class)
        cmd = ChangeClassCommand(
            annotations=self.current_annotations,
            index=row,
            old_class_id=current,
            new_class_id=new_class,
            on_apply=self._on_annotations_applied,
        )
        self.undo_stack.push(cmd)

    def delete_selected_box(self) -> None:
        row = self.annotation_table.currentRow()
        if row < 0:
            return
        self.on_delete_annotation(row)

    def on_delete_annotation(self, index: int) -> None:
        if self.current_index < 0 or index < 0 or index >= len(self.current_annotations):
            return
        cmd = DeleteAnnotationCommand(self.current_annotations, index, self._on_annotations_applied)
        self.undo_stack.push(cmd)

    def _on_annotations_applied(self, selected_index: int) -> None:
        self._updating_ui = True
        try:
            if self.current_index < 0:
                return
            item = self.items[self.current_index]
            if item.image_path and item.image_path.exists():
                pixmap = self.cache.get(item.image_path) or load_pixmap(item.image_path)
                if not pixmap.isNull():
                    self.canvas.update_annotations(self.current_annotations, self.class_names, selected_index)

            self._refresh_annotation_table(selected_index)
            self._save_current_annotations()
            validation = validate_item(item)
            validation.annotations = copy.deepcopy(self.current_annotations)
            self.validation_map[self.current_index] = validation
            self._show_validation_issues(validation)
            self._refresh_visible_row_for_index(self.current_index)
            self._update_anomaly_for_index(self.current_index, refresh_combo=True)
        finally:
            self._updating_ui = False

    def _ensure_item_label_path(self, idx: int, item: DatasetItem) -> Path | None:
        if item.label_path is not None:
            return item.label_path
        if item.image_path is None:
            return None

        item.label_path = item.image_path.with_suffix(".txt")
        root = self.item_roots[idx]
        try:
            item.label_rel = item.label_path.relative_to(root)
        except ValueError:
            item.label_rel = Path(item.label_path.name)
        return item.label_path

    def _backup_label_once(self, idx: int, item: DatasetItem, label_path: Path) -> None:
        root = self.item_roots[idx]
        if not label_path.exists() or label_path in self._backed_up_files:
            return

        backup_dir = root / "__label_backups__"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = backup_dir / f"{item.key.replace('/', '_')}_{stamp}.txt"
        backup_file.write_text(label_path.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
        self._backed_up_files.add(label_path)

    def _write_annotations_for_item(self, idx: int, item: DatasetItem, annotations: list[Annotation]) -> bool:
        label_path = self._ensure_item_label_path(idx, item)
        if label_path is None:
            return False

        label_path.parent.mkdir(parents=True, exist_ok=True)
        self._backup_label_once(idx, item, label_path)

        lines = [ann.to_yolo_line(include_confidence=True) for ann in annotations]
        text = "\n".join(lines)
        if text:
            text += "\n"
        label_path.write_text(text, encoding="utf-8")
        return True

    def _save_current_annotations(self) -> None:
        if self.current_index < 0:
            return
        item = self.items[self.current_index]
        self._write_annotations_for_item(self.current_index, item, self.current_annotations)

    def auto_annotate(self, _checked: bool = False) -> None:
        if not self.items:
            QMessageBox.information(self, "未导入数据集", "请先导入数据集。")
            return

        start_dir = str(self.dataset_roots[0]) if self.dataset_roots else ""
        model_file, _ = QFileDialog.getOpenFileName(
            self,
            "选择模型文件",
            start_dir,
            "模型文件 (*.pt *.onnx);;全部文件 (*.*)",
        )
        if not model_file:
            return

        conf_threshold, ok = QInputDialog.getDouble(
            self,
            "置信度阈值",
            "请输入置信度阈值 (0-1)：",
            value=self.auto_conf_threshold,
            min=0.0,
            max=1.0,
            decimals=2,
        )
        if not ok:
            return

        scope, ok = QInputDialog.getItem(
            self,
            "自动标注范围",
            "选择标注范围：",
            ["当前图片", "全部图片", "仅异常未标注图片"],
            0,
            False,
        )
        if not ok:
            return

        self.auto_model_path = Path(model_file)
        self.auto_conf_threshold = conf_threshold

        try:
            annotator = AutoAnnotator(self.auto_model_path, conf_threshold=self.auto_conf_threshold)
        except AutoAnnotatorError as exc:
            QMessageBox.warning(self, "模型加载失败", str(exc))
            return

        model_names = annotator.class_names()
        if model_names:
            self.class_names = model_names

        if scope == "当前图片":
            if self.current_index < 0:
                QMessageBox.information(self, "未选择", "请先选择一个样本。")
                return
            indices = [self.current_index]
        elif scope == "仅异常未标注图片":
            indices = [i for i in range(len(self.items)) if self._is_unlabeled_anomaly(i)]
        else:
            indices = [
                i for i, item in enumerate(self.items)
                if item.image_path is not None and item.image_path.exists()
            ]

        self._run_auto_annotate(annotator, indices)

    def _is_unlabeled_anomaly(self, idx: int) -> bool:
        item = self.items[idx]
        if item.image_path is None or (not item.image_path.exists()):
            return False

        validation = self.validation_map.get(idx)
        if validation is None:
            if item.label_path is None:
                return True
            try:
                if item.label_path.exists() and item.label_path.stat().st_size == 0:
                    return True
            except Exception:
                pass
            validation = self._ensure_validation(idx)

        if len(validation.annotations) == 0:
            return True

        target_codes = {"label_missing", "empty_label", "empty_annotation"}
        return any(issue.code in target_codes for issue in validation.issues)

    def _run_auto_annotate(self, annotator: AutoAnnotator, indices: list[int]) -> None:
        if not indices:
            QMessageBox.information(self, "无可处理文件", "没有可用于自动标注的图片。")
            return

        progress = QProgressDialog("正在执行自动标注...", "取消", 0, len(indices), self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)

        updated = 0
        failed = 0
        first_error: str | None = None

        for n, idx in enumerate(indices, start=1):
            if progress.wasCanceled():
                break

            item = self.items[idx]
            if item.image_path is None or (not item.image_path.exists()):
                progress.setValue(n)
                QApplication.processEvents()
                continue

            try:
                annotations = annotator.predict(item.image_path)
            except AutoAnnotatorError as exc:
                failed += 1
                if first_error is None:
                    first_error = str(exc)
                progress.setValue(n)
                QApplication.processEvents()
                continue

            if self._write_annotations_for_item(idx, item, annotations):
                updated += 1
                validation = validate_item(item)
                validation.annotations = copy.deepcopy(annotations)
                self.validation_map[idx] = validation

                if idx == self.current_index:
                    self.current_annotations = copy.deepcopy(annotations)

            progress.setValue(n)
            QApplication.processEvents()

        progress.close()

        if self.current_index >= 0:
            self.on_file_selected(self.current_index, refresh_table=False)

        self._rebuild_file_table()
        for idx in indices:
            self._update_anomaly_for_index(idx, refresh_combo=False)
        self._refresh_anomaly_combo()

        msg = f"自动标注完成：成功 {updated} 张"
        if failed > 0:
            msg += f"，失败 {failed} 张"
        if first_error:
            msg += f"\n首个错误：{first_error}"

        QMessageBox.information(self, "自动标注完成", msg)

    def validate_all(self, _checked: bool = False) -> None:
        self._run_validate_all(show_message=True)

    def _run_validate_all(self, show_message: bool) -> None:
        if not self.items:
            QMessageBox.information(self, "未导入数据集", "请先导入数据集。")
            return

        progress = QProgressDialog("正在校验数据集...", "取消", 0, len(self.items), self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)

        checked = 0
        for idx, item in enumerate(self.items):
            if progress.wasCanceled():
                break
            self.validation_map[idx] = validate_item(item)
            checked += 1
            progress.setValue(idx + 1)
            QApplication.processEvents()

        progress.close()
        self._rebuild_file_table()
        self._rebuild_anomaly_index(full_scan=False)

        if show_message:
            error_files = sum(1 for v in self.validation_map.values() if v.has_error)
            warn_files = sum(
                1
                for v in self.validation_map.values()
                if (not v.has_error and any(issue.severity != "error" for issue in v.issues))
            )
            QMessageBox.information(
                self,
                "校验完成",
                f"共校验 {checked} 个文件。错误文件：{error_files}，警告文件：{warn_files}",
            )

        if self.current_index >= 0 and self.current_index in self.validation_map:
            self._show_validation_issues(self.validation_map[self.current_index])

    def export_passed(self, _checked: bool = False) -> None:
        if not self.items:
            QMessageBox.information(self, "未导入数据集", "请先导入并校验数据集。")
            return

        if len(self.validation_map) < len(self.items):
            run_validate = QMessageBox.question(
                self,
                "导出前校验",
                "当前不是全量校验结果，是否先执行一次全量校验再导出？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if run_validate == QMessageBox.StandardButton.Yes:
                self._run_validate_all(show_message=False)

        target = QFileDialog.getExistingDirectory(self, "选择导出目录")
        if not target:
            return

        by_root: dict[Path, list[int]] = {}
        for idx, root in enumerate(self.item_roots):
            by_root.setdefault(root, []).append(idx)

        copied_total = 0
        target_dir = Path(target)

        for root, indices in by_root.items():
            sub_target = target_dir / root.name if len(by_root) > 1 else target_dir
            sub_items = [self.items[i] for i in indices]
            sub_validation: dict[str, FileValidation] = {}
            for i in indices:
                v = self.validation_map.get(i)
                if v is not None:
                    sub_validation[self.items[i].key] = v

            copied_total += export_passed_files(sub_target, root, sub_items, sub_validation)

        QMessageBox.information(self, "导出完成", f"已复制 {copied_total} 组校验通过的样本。")

    def save_screenshot(self, _checked: bool = False) -> None:
        if self.current_index < 0:
            QMessageBox.information(self, "未选择", "请先选择一个样本。")
            return

        selected, _ = QFileDialog.getSaveFileName(
            self,
            "保存带标注截图",
            "标注预览图.png",
            "PNG 文件 (*.png)",
        )
        if not selected:
            return

        image = self.canvas.grab_annotated_image()
        if image.isNull():
            QMessageBox.warning(self, "保存失败", "当前没有可保存的图像内容。")
            return

        ok = image.save(selected)
        if ok:
            QMessageBox.information(self, "已保存", f"截图已保存到：\n{selected}")
        else:
            QMessageBox.warning(self, "保存失败", "无法写入截图文件。")


def run() -> None:
    app = QApplication([])
    app.setWindowIcon(QIcon())
    window = MainWindow()
    window.show()
    app.exec()
