
from __future__ import annotations

import copy
import traceback
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QObject, QSize, QThread, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QBrush, QColor, QIcon, QPixmap, QUndoStack
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
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
    QSizePolicy,
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
from .colors import class_color
from .crash_logger import append_log, install_global_exception_handler
from .exporter import export_passed_files
from .file_manager import PixmapCache, load_pixmap, scan_dataset
from .models import Annotation, DatasetItem, FileValidation
from .undo_commands import AddAnnotationCommand, ChangeClassCommand, DeleteAnnotationCommand, UpdateAnnotationCommand
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
    ("label_read_error", "标签读取失败"),
]




class AutoAnnotateWorker(QObject):
    progress = pyqtSignal(int, int)
    finished = pyqtSignal(object)

    def __init__(self, model_path: Path, conf_threshold: float, tasks: list[tuple[int, str]]) -> None:
        super().__init__()
        self.model_path = model_path
        self.conf_threshold = conf_threshold
        self.tasks = tasks
        self._canceled = False

    def cancel(self) -> None:
        self._canceled = True

    def run(self) -> None:
        try:
            annotator = AutoAnnotator(self.model_path, conf_threshold=self.conf_threshold)
        except AutoAnnotatorError as exc:
            self.finished.emit({
                "fatal_error": str(exc),
                "results": [],
                "model_names": [],
                "failed": 0,
                "first_error": None,
                "canceled": False,
            })
            return

        model_names = annotator.class_names()
        results: list[dict[str, object]] = []
        failed = 0
        first_error: str | None = None

        total = len(self.tasks)
        done = 0

        for idx, image_path in self.tasks:
            if self._canceled:
                break

            try:
                annotations = annotator.predict(Path(image_path))
                results.append({"idx": idx, "annotations": annotations, "error": None})
            except AutoAnnotatorError as exc:
                failed += 1
                if first_error is None:
                    first_error = str(exc)
                results.append({"idx": idx, "annotations": None, "error": str(exc)})

            done += 1
            self.progress.emit(done, total)

        self.finished.emit({
            "fatal_error": None,
            "results": results,
            "model_names": model_names,
            "failed": failed,
            "first_error": first_error,
            "canceled": self._canceled,
        })


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
        self._auto_thread: QThread | None = None
        self._auto_worker: AutoAnnotateWorker | None = None
        self._auto_progress: QProgressDialog | None = None
        self._auto_running: bool = False

        self.theme_mode: str = "light"
        self.theme_toggle_action: QAction | None = None

        self._setup_ui()
        self._apply_style()

    def _setup_ui(self) -> None:
        status_bar = QStatusBar(self)
        status_bar.setSizeGripEnabled(False)
        status_bar.setMinimumHeight(30)
        self.setStatusBar(status_bar)

        toolbar = QToolBar("主工具栏")
        toolbar.setObjectName("mainToolbar")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(16, 16))
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.addToolBar(toolbar)

        import_action = QAction("导入文件夹", self)
        import_action.setShortcut("Ctrl+O")
        import_action.triggered.connect(self.import_folders)
        toolbar.addAction(import_action)

        append_action = QAction("追加文件夹", self)
        append_action.setShortcut("Ctrl+Shift+O")
        append_action.triggered.connect(self.append_folders)
        toolbar.addAction(append_action)

        validate_action = QAction("全量校验", self)
        validate_action.setShortcut("F5")
        validate_action.triggered.connect(self.validate_all)
        toolbar.addAction(validate_action)

        auto_annotate_action = QAction("加载模型自动标注", self)
        auto_annotate_action.setShortcut("Ctrl+M")
        auto_annotate_action.triggered.connect(self.auto_annotate)
        toolbar.addAction(auto_annotate_action)

        export_passed_action = QAction("导出通过项", self)
        export_passed_action.triggered.connect(self.export_passed)
        toolbar.addAction(export_passed_action)

        screenshot_action = QAction("保存截图", self)
        screenshot_action.setShortcut("Ctrl+S")
        screenshot_action.triggered.connect(self.save_screenshot)
        toolbar.addAction(screenshot_action)

        self.theme_toggle_action = QAction("极简暗色", self)
        self.theme_toggle_action.setCheckable(True)
        self.theme_toggle_action.toggled.connect(self.toggle_theme)
        toolbar.addAction(self.theme_toggle_action)

        toolbar.addSeparator()
        toolbar.addAction(self.undo_stack.createUndoAction(self, "撤销"))
        toolbar.addAction(self.undo_stack.createRedoAction(self, "重做"))

        self.add_annotation_shortcut = QAction(self)
        self.add_annotation_shortcut.setShortcut("Ctrl+N")
        self.add_annotation_shortcut.triggered.connect(self.start_add_box_mode)
        self.addAction(self.add_annotation_shortcut)

        root = QWidget(self)
        root.setObjectName("rootContainer")
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("mainSplitter")
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(8)
        layout.addWidget(splitter)

        left = QFrame()
        left.setObjectName("panelLeft")
        left.setMinimumWidth(460)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(14, 14, 14, 14)
        left_layout.setSpacing(10)

        left_title = QLabel("文件列表")
        left_title.setObjectName("sectionTitle")
        left_layout.addWidget(left_title)

        self.empty_guide_label = QLabel("空状态引导：先点击顶部“导入文件夹”，再在左侧列表选择样本。")
        self.empty_guide_label.setObjectName("emptyGuide")
        self.empty_guide_label.setWordWrap(True)
        left_layout.addWidget(self.empty_guide_label)

        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索：文件名/路径/类别")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setMinimumHeight(36)
        self.search_edit.textChanged.connect(self._rebuild_file_table)
        search_row.addWidget(self.search_edit)

        clear_btn = QPushButton("清空")
        clear_btn.setMinimumHeight(36)
        clear_btn.setMinimumWidth(68)
        clear_btn.clicked.connect(lambda: self.search_edit.setText(""))
        search_row.addWidget(clear_btn)
        left_layout.addLayout(search_row)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        self.sort_combo = QComboBox()
        self.sort_combo.addItems([
            "名称升序",
            "名称降序",
            "是否标注（已标注在前）",
            "是否标注（未标注在前）",
        ])
        self.sort_combo.setMinimumHeight(34)
        self.sort_combo.currentIndexChanged.connect(self._rebuild_file_table)
        filter_row.addWidget(self.sort_combo)

        self.mark_filter_combo = QComboBox()
        self.mark_filter_combo.addItems(["全部", "仅已标注", "仅未标注"])
        self.mark_filter_combo.setMinimumHeight(34)
        self.mark_filter_combo.currentIndexChanged.connect(self._rebuild_file_table)
        filter_row.addWidget(self.mark_filter_combo)

        self.status_filter_combo = QComboBox()
        self.status_filter_combo.addItems(["全部状态", "通过", "警告", "错误", "未校验"])
        self.status_filter_combo.setMinimumHeight(34)
        self.status_filter_combo.currentIndexChanged.connect(self._rebuild_file_table)
        filter_row.addWidget(self.status_filter_combo)
        left_layout.addLayout(filter_row)

        self.file_table = QTableWidget(0, 1)
        self.file_table.setHorizontalHeaderLabels(["文件名"])
        self.file_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.file_table.verticalHeader().setVisible(False)
        self.file_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.file_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.file_table.setAlternatingRowColors(True)
        self.file_table.setShowGrid(False)
        self.file_table.setWordWrap(False)
        self.file_table.setMinimumHeight(360)
        self.file_table.currentCellChanged.connect(self.on_file_row_changed)
        left_layout.addWidget(self.file_table, stretch=6)
        self.empty_guide_label.setVisible(True)

        anomaly_head = QHBoxLayout()
        anomaly_head.setSpacing(8)
        anomaly_label = QLabel("异常分类")
        anomaly_label.setObjectName("sectionSubTitle")
        anomaly_head.addWidget(anomaly_label)

        self.anomaly_combo = QComboBox()
        self.anomaly_combo.setMinimumHeight(32)
        self.anomaly_combo.currentIndexChanged.connect(self._refresh_anomaly_list)
        anomaly_head.addWidget(self.anomaly_combo)
        left_layout.addLayout(anomaly_head)

        self.anomaly_list = QListWidget()
        self.anomaly_list.setAlternatingRowColors(True)
        self.anomaly_list.itemClicked.connect(self.on_anomaly_clicked)
        left_layout.addWidget(self.anomaly_list, stretch=3)

        splitter.addWidget(left)

        middle = QFrame()
        middle.setObjectName("panelMiddle")
        middle_layout = QVBoxLayout(middle)
        middle_layout.setContentsMargins(14, 14, 14, 14)
        middle_layout.setSpacing(10)

        middle_title = QLabel("图像预览")
        middle_title.setObjectName("sectionTitle")
        middle_layout.addWidget(middle_title)

        self.canvas = ImageCanvas()
        self.canvas.annotation_selected.connect(self.on_canvas_selection_changed)
        self.canvas.annotation_geometry_changed.connect(self.on_canvas_geometry_changed)
        self.canvas.annotation_created.connect(self.on_canvas_annotation_created)
        self.canvas.delete_requested.connect(self.on_delete_annotation)
        middle_layout.addWidget(self.canvas, stretch=8)

        tip_label = QLabel(
            "提示：新增矩形=拖拽；新增旋转框=依次点击4个点；新增多边形=逐点点击，右键或回车完成。"
        )
        tip_label.setObjectName("hintLabel")
        tip_label.setWordWrap(True)
        middle_layout.addWidget(tip_label, stretch=0)

        shortcut_label = QLabel("快捷键：Ctrl+O 导入 | F5 校验 | Ctrl+M 自动标注 | Ctrl+N 新增 | Delete 删除 | Ctrl+Z/Ctrl+Y 撤销重做")
        shortcut_label.setObjectName("shortcutLabel")
        shortcut_label.setWordWrap(True)
        middle_layout.addWidget(shortcut_label, stretch=0)

        splitter.addWidget(middle)

        right = QFrame()
        right.setObjectName("panelRight")
        right.setMinimumWidth(340)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(14, 14, 14, 14)
        right_layout.setSpacing(10)

        right_title = QLabel("标注明细")
        right_title.setObjectName("sectionTitle")
        right_layout.addWidget(right_title)

        self.annotation_table = QTableWidget(0, 5)
        self.annotation_table.setHorizontalHeaderLabels(
            ["序号", "形状", "类别ID", "类别名", "点数"]
        )
        header_tips = [
            "当前标注在图片中的行号",
            "标注几何类型（矩形/旋转框/多边形）",
            "YOLO 类别 ID",
            "类别名称",
            "点数量（矩形默认 4）",
        ]
        for i, tip in enumerate(header_tips):
            header_item = self.annotation_table.horizontalHeaderItem(i)
            if header_item is not None:
                header_item.setToolTip(tip)
        header = self.annotation_table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setMinimumSectionSize(56)
        header.setDefaultSectionSize(88)
        self.annotation_table.verticalHeader().setVisible(False)
        self.annotation_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.annotation_table.setAlternatingRowColors(True)
        self.annotation_table.setShowGrid(False)
        self.annotation_table.setWordWrap(False)
        self.annotation_table.currentCellChanged.connect(self.on_table_selection_changed)
        right_layout.addWidget(self.annotation_table, stretch=5)

        create_row = QHBoxLayout()
        create_row.setSpacing(8)
        create_label = QLabel("新增类型")
        create_label.setObjectName("sectionSubTitle")
        create_row.addWidget(create_label)

        self.shape_mode_combo = QComboBox()
        self.shape_mode_combo.addItems(["矩形", "旋转框", "多边形"])
        self.shape_mode_combo.setMinimumHeight(34)
        create_row.addWidget(self.shape_mode_combo)

        self.add_btn = QPushButton("新增标注")
        self.add_btn.setMinimumHeight(38)
        self.add_btn.clicked.connect(self.start_add_box_mode)
        create_row.addWidget(self.add_btn)
        right_layout.addLayout(create_row)

        self.center_marker_check = QCheckBox("显示中心标记（白圈+十字）")
        self.center_marker_check.setChecked(True)
        self.center_marker_check.toggled.connect(self.on_center_marker_toggled)
        right_layout.addWidget(self.center_marker_check)
        self.canvas.set_center_marker_visible(self.center_marker_check.isChecked())

        btn_row_top = QHBoxLayout()
        btn_row_top.setSpacing(8)

        self.class_btn = QPushButton("改类别")
        self.class_btn.setMinimumHeight(38)
        self.class_btn.setMinimumWidth(94)
        self.class_btn.setToolTip("修改当前标注的类别")
        self.class_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.class_btn.clicked.connect(self.on_change_class)
        btn_row_top.addWidget(self.class_btn)

        self.class_name_btn = QPushButton("改类名")
        self.class_name_btn.setMinimumHeight(38)
        self.class_name_btn.setMinimumWidth(94)
        self.class_name_btn.setToolTip("编辑类别名称（可输入类别ID或类别名）")
        self.class_name_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.class_name_btn.clicked.connect(self.on_edit_class_name)
        btn_row_top.addWidget(self.class_name_btn)
        right_layout.addLayout(btn_row_top)

        btn_row_bottom = QHBoxLayout()
        btn_row_bottom.setSpacing(8)

        self.delete_btn = QPushButton("删当前")
        self.delete_btn.setMinimumHeight(38)
        self.delete_btn.setMinimumWidth(94)
        self.delete_btn.setToolTip("删除当前选中的标注")
        self.delete_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.delete_btn.clicked.connect(self.delete_selected_box)
        btn_row_bottom.addWidget(self.delete_btn)

        self.batch_delete_btn = QPushButton("删同类")
        self.batch_delete_btn.setMinimumHeight(38)
        self.batch_delete_btn.setMinimumWidth(94)
        self.batch_delete_btn.setToolTip("按类别ID批量删除当前图片中的标注")
        self.batch_delete_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.batch_delete_btn.clicked.connect(self.batch_delete_same_class)
        btn_row_bottom.addWidget(self.batch_delete_btn)
        right_layout.addLayout(btn_row_bottom)

        self.issue_text = QTextEdit()
        self.issue_text.setReadOnly(True)
        self.issue_text.setPlaceholderText("此处显示当前文件的校验信息。")
        right_layout.addWidget(self.issue_text, stretch=4)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 5)
        splitter.setStretchFactor(2, 2)
        splitter.setSizes([516, 860, 344])

        self._refresh_anomaly_combo()

    def _build_light_stylesheet(self) -> str:
        return """
            QMainWindow {
                background: #edf2f7;
                font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", "Segoe UI";
                color: #0f172a;
            }
            QWidget#rootContainer {
                background: transparent;
            }
            QStatusBar {
                background: #ffffff;
                color: #475569;
                border-top: 1px solid #e2e8f0;
                font-size: 12px;
                padding-left: 8px;
            }
            QToolBar#mainToolbar {
                background: #0f172a;
                border: none;
                spacing: 8px;
                padding: 8px 10px;
            }
            QToolBar#mainToolbar::separator {
                background: #334155;
                width: 1px;
                margin: 6px 8px;
            }
            QToolButton {
                color: #e2e8f0;
                background: #1e293b;
                border: 1px solid #334155;
                border-radius: 8px;
                padding: 7px 12px;
                min-height: 34px;
                min-width: 96px;
                font-size: 13px;
                font-weight: 600;
            }
            QToolButton:hover {
                background: #334155;
                border-color: #475569;
            }
            QToolButton:pressed {
                background: #475569;
            }
            QFrame#panelLeft, QFrame#panelMiddle, QFrame#panelRight {
                background: #ffffff;
                border: 1px solid #dbe3ee;
                border-radius: 12px;
            }
            QSplitter::handle {
                background: transparent;
            }
            QSplitter::handle:hover {
                background: #d5deeb;
                border-radius: 4px;
            }
            QLabel#sectionTitle {
                font-size: 15px;
                font-weight: 700;
                color: #0f172a;
                padding-left: 2px;
            }
            QLabel#sectionSubTitle {
                font-size: 13px;
                font-weight: 600;
                color: #334155;
                padding-left: 2px;
            }
            QLabel#hintLabel {
                font-size: 12px;
                color: #475569;
                padding: 2px 2px 0 2px;
            }
            QLabel#shortcutLabel {
                font-size: 12px;
                color: #0f766e;
                background: #ecfeff;
                border: 1px solid #bae6fd;
                border-radius: 8px;
                padding: 6px 8px;
            }
            QLabel#emptyGuide {
                font-size: 12px;
                color: #475569;
                background: #f8fafc;
                border: 1px dashed #cbd5e1;
                border-radius: 8px;
                padding: 6px 8px;
            }
            QCheckBox {
                color: #334155;
                font-size: 12px;
                spacing: 6px;
                padding: 2px 2px;
            }
            QCheckBox::indicator {
                width: 14px;
                height: 14px;
            }
            QLineEdit, QComboBox {
                background: #f8fafc;
                border: 1px solid #d6deea;
                border-radius: 8px;
                padding: 0 10px;
                color: #0f172a;
                font-size: 13px;
            }
            QLineEdit:focus, QComboBox:focus {
                border: 1px solid #0f766e;
                background: #ffffff;
            }
            QPushButton {
                background: #0f766e;
                color: #f8fafc;
                border: none;
                border-radius: 8px;
                padding: 0 14px;
                min-height: 34px;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #0d9488;
            }
            QPushButton:pressed {
                background: #0f766e;
            }
            QPushButton:disabled {
                background: #9aa9bc;
                color: #e2e8f0;
            }
            QTableWidget, QListWidget, QTextEdit {
                background: #ffffff;
                border: 1px solid #d8e0eb;
                border-radius: 10px;
                color: #0f172a;
                alternate-background-color: #f8fbff;
                selection-background-color: #c8f2e9;
                selection-color: #0f5132;
                outline: none;
                font-size: 12px;
            }
            QTableWidget {
                gridline-color: #eef2f7;
            }
            QHeaderView::section {
                background: #f3f6fb;
                color: #334155;
                border: none;
                border-bottom: 1px solid #e2e8f0;
                padding: 8px 10px;
                font-size: 12px;
                font-weight: 700;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 10px;
                margin: 2px;
            }
            QScrollBar::handle:vertical {
                background: #c7d2e1;
                border-radius: 5px;
                min-height: 26px;
            }
            QScrollBar::handle:vertical:hover {
                background: #94a3b8;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: transparent;
                border: none;
            }
        """

    def _build_dark_stylesheet(self) -> str:
        return """
            QMainWindow {
                background: #0b1220;
                font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", "Segoe UI";
                color: #e2e8f0;
            }
            QWidget#rootContainer {
                background: transparent;
            }
            QStatusBar {
                background: #0f172a;
                color: #94a3b8;
                border-top: 1px solid #1e293b;
                font-size: 12px;
                padding-left: 8px;
            }
            QToolBar#mainToolbar {
                background: #020617;
                border: none;
                spacing: 8px;
                padding: 8px 10px;
            }
            QToolBar#mainToolbar::separator {
                background: #1e293b;
                width: 1px;
                margin: 6px 8px;
            }
            QToolButton {
                color: #e2e8f0;
                background: #111827;
                border: 1px solid #1f2937;
                border-radius: 8px;
                padding: 7px 12px;
                min-height: 34px;
                min-width: 96px;
                font-size: 13px;
                font-weight: 600;
            }
            QToolButton:hover {
                background: #1f2937;
                border-color: #334155;
            }
            QToolButton:pressed {
                background: #334155;
            }
            QFrame#panelLeft, QFrame#panelMiddle, QFrame#panelRight {
                background: #111827;
                border: 1px solid #1f2937;
                border-radius: 12px;
            }
            QSplitter::handle {
                background: transparent;
            }
            QSplitter::handle:hover {
                background: #1f2937;
                border-radius: 4px;
            }
            QLabel#sectionTitle {
                font-size: 15px;
                font-weight: 700;
                color: #f8fafc;
                padding-left: 2px;
            }
            QLabel#sectionSubTitle {
                font-size: 13px;
                font-weight: 600;
                color: #cbd5e1;
                padding-left: 2px;
            }
            QLabel#hintLabel {
                font-size: 12px;
                color: #94a3b8;
                padding: 2px 2px 0 2px;
            }
            QLabel#shortcutLabel {
                font-size: 12px;
                color: #99f6e4;
                background: #0b1d2b;
                border: 1px solid #164e63;
                border-radius: 8px;
                padding: 6px 8px;
            }
            QLabel#emptyGuide {
                font-size: 12px;
                color: #94a3b8;
                background: #0f172a;
                border: 1px dashed #334155;
                border-radius: 8px;
                padding: 6px 8px;
            }
            QCheckBox {
                color: #cbd5e1;
                font-size: 12px;
                spacing: 6px;
                padding: 2px 2px;
            }
            QCheckBox::indicator {
                width: 14px;
                height: 14px;
            }
            QLineEdit, QComboBox {
                background: #0f172a;
                border: 1px solid #334155;
                border-radius: 8px;
                padding: 0 10px;
                color: #e2e8f0;
                font-size: 13px;
            }
            QLineEdit:focus, QComboBox:focus {
                border: 1px solid #14b8a6;
                background: #111827;
            }
            QPushButton {
                background: #115e59;
                color: #f0fdfa;
                border: none;
                border-radius: 8px;
                padding: 0 14px;
                min-height: 34px;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #0f766e;
            }
            QPushButton:pressed {
                background: #134e4a;
            }
            QPushButton:disabled {
                background: #475569;
                color: #94a3b8;
            }
            QTableWidget, QListWidget, QTextEdit {
                background: #0f172a;
                border: 1px solid #273449;
                border-radius: 10px;
                color: #e2e8f0;
                alternate-background-color: #111f35;
                selection-background-color: #164e63;
                selection-color: #e0f2fe;
                outline: none;
                font-size: 12px;
            }
            QTableWidget {
                gridline-color: #1e293b;
            }
            QHeaderView::section {
                background: #111827;
                color: #cbd5e1;
                border: none;
                border-bottom: 1px solid #273449;
                padding: 8px 10px;
                font-size: 12px;
                font-weight: 700;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 10px;
                margin: 2px;
            }
            QScrollBar::handle:vertical {
                background: #334155;
                border-radius: 5px;
                min-height: 26px;
            }
            QScrollBar::handle:vertical:hover {
                background: #475569;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: transparent;
                border: none;
            }
        """

    def _apply_style(self) -> None:
        if self.theme_mode == "dark":
            self.setStyleSheet(self._build_dark_stylesheet())
        else:
            self.setStyleSheet(self._build_light_stylesheet())

        if self.theme_toggle_action is not None:
            should_checked = self.theme_mode == "dark"
            if self.theme_toggle_action.isChecked() != should_checked:
                self.theme_toggle_action.blockSignals(True)
                self.theme_toggle_action.setChecked(should_checked)
                self.theme_toggle_action.blockSignals(False)

    def toggle_theme(self, checked: bool) -> None:
        self.theme_mode = "dark" if checked else "light"
        self._apply_style()
        self.statusBar().showMessage("已切换为极简暗色主题。" if checked else "已切换为浅色主题。", 2500)

    def _pick_folders(self) -> list[Path]:
        start_dir = str(self.dataset_roots[0]) if self.dataset_roots else ""
        selected = QFileDialog.getExistingDirectory(
            self,
            "选择数据集文件夹",
            start_dir,
        )
        if not selected:
            return []
        return [Path(selected).resolve()]

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
        failed_roots: list[str] = []

        progress = QProgressDialog("正在扫描数据集文件夹...", "取消", 0, len(valid_folders), self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)

        try:
            for i, root in enumerate(valid_folders, start=1):
                if progress.wasCanceled():
                    break

                if root in self.dataset_roots:
                    progress.setValue(i)
                    QApplication.processEvents()
                    continue

                try:
                    scanned_items = scan_dataset(root)
                except Exception as exc:
                    traceback.print_exc()
                    failed_roots.append(f"{root}: {exc}")
                    progress.setValue(i)
                    QApplication.processEvents()
                    continue

                for item in scanned_items:
                    self.items.append(item)
                    self.item_roots.append(root)
                    added_items += 1

                self.dataset_roots.append(root)
                added_roots += 1
                progress.setValue(i)
                QApplication.processEvents()
        finally:
            progress.close()

        try:
            self.class_names = self._merge_class_names(self.dataset_roots)
            self._rebuild_file_table()
            self._rebuild_anomaly_index(full_scan=False)

            if self.current_index >= 0 and self.current_index in self.visible_indices:
                self._select_global_index(self.current_index)
        except Exception as exc:
            traceback.print_exc()
            QMessageBox.critical(self, "导入失败", f"导入后刷新界面失败：{exc}")
            return

        if failed_roots:
            details = "\n".join(failed_roots[:5])
            more = "\n..." if len(failed_roots) > 5 else ""
            QMessageBox.warning(self, "部分目录扫描失败", f"以下目录已跳过：\n{details}{more}")

        self.statusBar().showMessage(
            f"已加载 {len(self.items)} 个样本，来自 {len(self.dataset_roots)} 个文件夹，本次新增 {added_items} 个样本。"
        )

        if added_roots == 0:
            QMessageBox.information(self, "提示", "所选文件夹均已导入。")

    def _load_class_names(self, root: Path) -> list[str]:
        classes_txt = root / "classes.txt"
        if classes_txt.exists():
            try:
                names = [line.strip() for line in classes_txt.read_text(encoding="utf-8", errors="ignore").splitlines()]
            except Exception:
                names = []
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

    def _save_class_names_for_root(self, root: Path) -> bool:
        classes_txt = root / "classes.txt"
        normalized = [name.strip() if name.strip() else f"cls_{idx}" for idx, name in enumerate(self.class_names)]
        content = "\n".join(normalized)
        if content:
            content += "\n"
        try:
            classes_txt.write_text(content, encoding="utf-8")
            return True
        except Exception:
            return False

    def _persist_class_names(self) -> tuple[int, int]:
        if not self.dataset_roots:
            return 0, 0
        ok_count = 0
        fail_count = 0
        for root in self.dataset_roots:
            if self._save_class_names_for_root(root):
                ok_count += 1
            else:
                fail_count += 1
        return ok_count, fail_count

    def _find_class_id_by_name(self, name: str) -> int | None:
        target = name.strip()
        if not target:
            return None

        for idx, class_name in enumerate(self.class_names):
            if class_name == target:
                return idx

        low = target.lower()
        for idx, class_name in enumerate(self.class_names):
            if class_name.lower() == low:
                return idx

        return None

    def _prompt_text_dialog(
        self,
        title: str,
        label: str,
        default_text: str = "",
        placeholder: str = "",
    ) -> str | None:
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setModal(True)
        dialog.setMinimumWidth(460)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        hint = QLabel(label)
        hint.setWordWrap(True)
        layout.addWidget(hint)

        editor = QLineEdit()
        editor.setMinimumHeight(36)
        editor.setText(default_text)
        if placeholder:
            editor.setPlaceholderText(placeholder)
        editor.selectAll()
        layout.addWidget(editor)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        cancel_btn = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if ok_btn is not None:
            ok_btn.setText("确定")
            ok_btn.setMinimumWidth(94)
        if cancel_btn is not None:
            cancel_btn.setText("取消")
            cancel_btn.setMinimumWidth(94)

        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None

        value = editor.text().strip()
        return value if value else None

    def _resolve_class_token(self, token: str, create_if_missing_name: bool = True) -> int | None:
        raw = token.strip()
        if not raw:
            return None

        found = self._find_class_id_by_name(raw)
        if found is not None:
            return found

        if raw.lower().startswith("id:"):
            id_part = raw[3:].strip()
            if id_part.isdigit():
                class_id = int(id_part)
                self._ensure_class_name(class_id)
                return class_id
            return None

        if raw.isdigit():
            class_id = int(raw)
            self._ensure_class_name(class_id)
            return class_id

        if not create_if_missing_name:
            return None

        self.class_names.append(raw)
        self._persist_class_names()
        return len(self.class_names) - 1

    def _prompt_class_id_or_name(self, title: str, label: str, default_text: str = "") -> int | None:
        text = self._prompt_text_dialog(
            title=title,
            label=label,
            default_text=default_text,
            placeholder="示例：0 / point / 缺陷A / id:3",
        )
        if text is None:
            return None

        class_id = self._resolve_class_token(text, create_if_missing_name=True)
        if class_id is None:
            QMessageBox.warning(self, "输入无效", "请输入有效的类别ID或类别名称。")
            return None
        return class_id

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
        cell = QTableWidgetItem(self._display_file_name(idx))
        cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
        cell.setData(Qt.ItemDataRole.UserRole, idx)
        self.file_table.setItem(row, 0, cell)

    def _refresh_visible_row_for_index(self, idx: int) -> None:
        if idx not in self.visible_indices:
            return
        row = self.visible_indices.index(idx)
        self._updating_ui = True
        try:
            self._set_file_table_row(row, idx)
        finally:
            self._updating_ui = False

    def _needs_file_table_full_rebuild_after_auto(self) -> bool:
        """Decide whether auto-annotate must rebuild full table."""
        if self.search_edit.text().strip():
            return True
        if self.mark_filter_combo.currentText() != "全部":
            return True
        if self.status_filter_combo.currentText() != "全部状态":
            return True
        if "是否标注" in self.sort_combo.currentText():
            return True
        return False

    def _refresh_after_auto_annotate(self, touched: list[int]) -> None:
        if not touched:
            return

        if self._needs_file_table_full_rebuild_after_auto():
            self._rebuild_file_table()
        else:
            for idx in touched:
                self._refresh_visible_row_for_index(idx)

        # touched small -> incremental anomaly update; touched large -> one-pass rebuild
        if len(touched) <= 40:
            for idx in touched:
                self._update_anomaly_for_index(idx, refresh_combo=False)
            self._refresh_anomaly_combo()
        else:
            self._rebuild_anomaly_index(full_scan=False)

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

        if hasattr(self, "empty_guide_label"):
            self.empty_guide_label.setVisible(len(self.items) == 0)

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
                self.statusBar().showMessage("Image missing or decode failed.")
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
        except Exception:
            traceback.print_exc()
            log_path = append_log(
                f"[on_file_selected] global_idx={global_idx}\n" + traceback.format_exc()
            )
            self.canvas.clear_content()
            self.statusBar().showMessage(f"Preview failed. Log: {log_path}")
            QMessageBox.warning(self, "Preview Error", f"Failed to load sample.\nLog:\n{log_path}")
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
        shape_map = {"bbox": "矩形", "rotated": "旋转框", "polygon": "多边形"}
        self.annotation_table.setRowCount(len(self.current_annotations))
        for row, ann in enumerate(self.current_annotations):
            self._ensure_class_name(ann.class_id)
            point_count = ann.point_count() if hasattr(ann, "point_count") else (len(ann.points) if ann.points else 4)
            values = [
                str(row),
                shape_map.get(ann.shape_type, ann.shape_type),
                str(ann.class_id),
                self.class_names[ann.class_id],
                str(point_count),
            ]
            detail_tooltip = "\n".join(
                [
                    f"序号: {row}",
                    f"形状: {shape_map.get(ann.shape_type, ann.shape_type)}",
                    f"类别ID: {ann.class_id}",
                    f"类别名: {self.class_names[ann.class_id]}",
                    f"中心X: {ann.x_center:.6f}",
                    f"中心Y: {ann.y_center:.6f}",
                    f"宽度: {ann.width:.6f}",
                    f"高度: {ann.height:.6f}",
                    f"点数: {point_count}",
                    f"置信度: {ann.confidence:.4f}" if ann.confidence is not None else "置信度: -",
                ]
            )
            cls_color = QColor(class_color(ann.class_id))
            cls_color.setAlpha(58)
            for col, text in enumerate(values):
                table_item = QTableWidgetItem(text)
                table_item.setFlags(table_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                table_item.setTextAlignment(int(Qt.AlignmentFlag.AlignCenter))
                table_item.setToolTip(detail_tooltip)
                if col in (2, 3):
                    table_item.setBackground(QBrush(cls_color))
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
    def on_center_marker_toggled(self, checked: bool) -> None:
        self.canvas.set_center_marker_visible(checked)
        self.statusBar().showMessage("已显示中心标记。" if checked else "已隐藏中心标记。", 1800)

    def start_add_box_mode(self) -> None:
        if self.current_index < 0:
            QMessageBox.information(self, "未选择", "请先选择一个样本。")
            return

        default_class = 0
        current_row = self.annotation_table.currentRow()
        if 0 <= current_row < len(self.current_annotations):
            default_class = self.current_annotations[current_row].class_id

        self._ensure_class_name(default_class)
        default_text = self.class_names[default_class]
        class_id = self._prompt_class_id_or_name(
            "新增标注",
            "请输入类别ID（数字）或类别名称（文本）：",
            default_text=default_text,
        )
        if class_id is None:
            return

        mode_index = self.shape_mode_combo.currentIndex() if hasattr(self, "shape_mode_combo") else 0
        shape_mode = {0: "bbox", 1: "rotated", 2: "polygon"}.get(mode_index, "bbox")
        self.canvas.start_create_mode(class_id, shape_mode)

        if shape_mode == "bbox":
            msg = "新增矩形：在图片中按住左键拖动并松开完成，Esc 取消。"
        elif shape_mode == "rotated":
            msg = "新增旋转框：依次点击4个角点，或按 Enter 完成，Esc 取消。"
        else:
            msg = "新增多边形：逐点点击，右键/Enter 完成，Esc 取消。"
        self.statusBar().showMessage(msg)

    def on_canvas_annotation_created(self, ann_obj: object) -> None:
        if self.current_index < 0:
            return
        if not isinstance(ann_obj, Annotation):
            return

        new_ann = copy.deepcopy(ann_obj)
        self._ensure_class_name(new_ann.class_id)
        insert_index = len(self.current_annotations)
        cmd = AddAnnotationCommand(
            annotations=self.current_annotations,
            index=insert_index,
            value=new_ann,
            on_apply=self._on_annotations_applied,
        )
        self.undo_stack.push(cmd)
        self.statusBar().showMessage("新增标注已添加并写入标签文件。")

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
        self._ensure_class_name(current)
        default_text = self.class_names[current]

        new_class = self._prompt_class_id_or_name(
            "修改类别",
            "请输入新的类别ID或类别名称：",
            default_text=default_text,
        )
        if new_class is None or new_class == current:
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

    def on_edit_class_name(self) -> None:
        if self.current_index < 0:
            QMessageBox.information(self, "未选择", "请先导入并选择一个样本。")
            return

        default_text = ""
        row = self.annotation_table.currentRow()
        if 0 <= row < len(self.current_annotations):
            class_id = self.current_annotations[row].class_id
            self._ensure_class_name(class_id)
            default_text = self.class_names[class_id]

        token = self._prompt_text_dialog(
            title="编辑类别名",
            label="请先输入类别ID或类别名称，用于定位要修改的类别：",
            default_text=default_text,
            placeholder="示例：0 / point / 缺陷A / id:3",
        )
        if token is None:
            return

        class_id = self._resolve_class_token(token, create_if_missing_name=False)
        if class_id is None:
            QMessageBox.warning(self, "未找到类别", "未匹配到类别，请检查输入的ID或名称。")
            return

        current_name = self.class_names[class_id]
        new_name = self._prompt_text_dialog(
            title="编辑类别名",
            label=f"请输入类别 ID {class_id} 的新名称：",
            default_text=current_name,
            placeholder="例如：point / scratch / 缺陷A",
        )
        if new_name is None:
            return

        new_name = new_name.strip()
        if not new_name:
            QMessageBox.warning(self, "无效名称", "类别名不能为空。")
            return
        if new_name == current_name:
            return

        self.class_names[class_id] = new_name
        selected_row = self.annotation_table.currentRow()
        self._refresh_annotation_table(selected_row)
        if self.current_index >= 0:
            self.canvas.update_annotations(self.current_annotations, self.class_names, selected_row)
            self._refresh_visible_row_for_index(self.current_index)

        ok_count, fail_count = self._persist_class_names()
        if fail_count > 0:
            QMessageBox.warning(
                self,
                "部分保存失败",
                f"已写入 {ok_count} 个数据集，失败 {fail_count} 个。",
            )
        else:
            self.statusBar().showMessage(f"类别ID {class_id} 已更新为：{new_name}", 2500)

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

    def batch_delete_same_class(self) -> None:
        if self.current_index < 0 or not self.current_annotations:
            QMessageBox.information(self, "无可删标注", "当前图片没有可批量删除的标注。")
            return

        default_class = 0
        row = self.annotation_table.currentRow()
        if 0 <= row < len(self.current_annotations):
            default_class = self.current_annotations[row].class_id

        class_id, ok = QInputDialog.getInt(
            self,
            "批量删除同类",
            "请输入要删除的类别ID：",
            value=default_class,
            min=0,
            max=100000,
        )
        if not ok:
            return

        indices = [i for i, ann in enumerate(self.current_annotations) if ann.class_id == class_id]
        if not indices:
            QMessageBox.information(self, "无匹配", f"当前图片中没有类别ID={class_id}的标注。")
            return

        self.undo_stack.beginMacro(f"批量删除类别 {class_id}")
        for idx in reversed(indices):
            self.undo_stack.push(DeleteAnnotationCommand(self.current_annotations, idx, self._on_annotations_applied))
        self.undo_stack.endMacro()
        self.statusBar().showMessage(f"已批量删除 {len(indices)} 个类别 {class_id} 的标注。", 2500)


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
        if self._auto_running:
            QMessageBox.information(self, "自动标注进行中", "已有自动标注任务在执行，请稍候。")
            return

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

        self._run_auto_annotate_async(self.auto_model_path, self.auto_conf_threshold, indices)

    def _is_unlabeled_anomaly(self, idx: int) -> bool:
        item = self.items[idx]
        if item.image_path is None or (not item.image_path.exists()):
            return False

        label_path = item.label_path
        if label_path is None or (not label_path.exists()):
            return True

        try:
            if label_path.stat().st_size == 0:
                return True
        except Exception:
            return True

        # 性能优化：仅使用已有缓存结果，不主动触发全量解析。
        validation = self.validation_map.get(idx)
        if validation is None:
            return False

        if len(validation.annotations) == 0:
            return True

        target_codes = {"label_missing", "empty_label", "empty_annotation"}
        return any(issue.code in target_codes for issue in validation.issues)

    def _run_auto_annotate_async(self, model_path: Path, conf_threshold: float, indices: list[int]) -> None:
        if not indices:
            QMessageBox.information(self, "无可处理文件", "没有可用于自动标注的图片。")
            return

        tasks: list[tuple[int, str]] = []
        for idx in indices:
            image_path = self.items[idx].image_path
            if image_path is None or (not image_path.exists()):
                continue
            tasks.append((idx, str(image_path)))

        if not tasks:
            QMessageBox.information(self, "无可处理文件", "没有可用于自动标注的图片。")
            return

        self._auto_running = True
        self.statusBar().showMessage("自动标注执行中，请稍候...")

        progress = QProgressDialog("正在执行自动标注（后台）...", "取消", 0, len(tasks), self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        self._auto_progress = progress

        thread = QThread(self)
        worker = AutoAnnotateWorker(model_path=model_path, conf_threshold=conf_threshold, tasks=tasks)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        progress.canceled.connect(worker.cancel)
        worker.progress.connect(self._on_auto_progress)
        worker.finished.connect(self._on_auto_annotate_finished)

        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        self._auto_thread = thread
        self._auto_worker = worker
        thread.start()
        progress.show()

    def _on_auto_progress(self, done: int, total: int) -> None:
        if self._auto_progress is None:
            return
        self._auto_progress.setMaximum(total)
        self._auto_progress.setValue(done)

    def _on_auto_annotate_finished(self, payload: object) -> None:
        if self._auto_progress is not None:
            self._auto_progress.close()
            self._auto_progress.deleteLater()
            self._auto_progress = None

        self._auto_worker = None
        self._auto_thread = None

        data = payload if isinstance(payload, dict) else {}
        fatal_error = data.get("fatal_error")
        if fatal_error:
            self._auto_running = False
            self.statusBar().showMessage("自动标注失败。")
            QMessageBox.warning(self, "自动标注失败", str(fatal_error))
            return

        model_names = data.get("model_names")
        if isinstance(model_names, list) and model_names:
            self.class_names = [str(v) for v in model_names]

        results = data.get("results") if isinstance(data.get("results"), list) else []

        self._auto_apply_state = {
            "results": results,
            "pos": 0,
            "updated": 0,
            "touched": [],
            "failed": int(data.get("failed") or 0),
            "first_error": data.get("first_error"),
            "canceled": bool(data.get("canceled")),
        }

        progress = QProgressDialog("正在应用标注结果...", "取消", 0, len(results), self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)
        self._auto_apply_progress = progress
        progress.show()

        QTimer.singleShot(0, self._apply_auto_results_chunk)

    def _apply_auto_results_chunk(self) -> None:
        state = getattr(self, "_auto_apply_state", None)
        if not isinstance(state, dict):
            return

        results = state.get("results")
        if not isinstance(results, list):
            results = []

        pos = int(state.get("pos") or 0)
        total = len(results)
        chunk_size = 16

        progress = getattr(self, "_auto_apply_progress", None)

        processed = 0
        while pos < total and processed < chunk_size:
            if progress is not None and progress.wasCanceled():
                state["canceled"] = True
                pos = total
                break

            entry = results[pos]
            pos += 1
            processed += 1

            if not isinstance(entry, dict):
                continue

            idx = entry.get("idx")
            annotations = entry.get("annotations")
            if not isinstance(idx, int) or not isinstance(annotations, list):
                continue

            item = self.items[idx]
            if self._write_annotations_for_item(idx, item, annotations):
                state["updated"] = int(state.get("updated") or 0) + 1
                touched = state.get("touched")
                if not isinstance(touched, list):
                    touched = []
                    state["touched"] = touched
                touched.append(idx)

                self.validation_map[idx] = FileValidation(
                    item_key=item.key,
                    issues=[],
                    annotations=copy.deepcopy(annotations),
                )

                if idx == self.current_index:
                    self.current_annotations = copy.deepcopy(annotations)

        state["pos"] = pos

        if progress is not None:
            progress.setMaximum(total)
            progress.setValue(pos)

        if pos < total:
            QTimer.singleShot(0, self._apply_auto_results_chunk)
            return

        self._finish_auto_apply()

    def _finish_auto_apply(self) -> None:
        state = getattr(self, "_auto_apply_state", None)
        if not isinstance(state, dict):
            self._auto_running = False
            return

        progress = getattr(self, "_auto_apply_progress", None)
        if progress is not None:
            progress.close()
            progress.deleteLater()
            self._auto_apply_progress = None

        updated = int(state.get("updated") or 0)
        failed = int(state.get("failed") or 0)
        canceled = bool(state.get("canceled"))
        first_error = state.get("first_error")
        touched = state.get("touched")
        if not isinstance(touched, list):
            touched = []

        self._refresh_after_auto_annotate(touched)
        # list/anomaly refresh already handled above
        # avoid duplicate full refresh here

        if self.current_index >= 0 and self.current_index in touched:
            self.on_file_selected(self.current_index, refresh_table=False)

        msg = f"自动标注完成：成功 {updated} 张"
        if failed > 0:
            msg += f"，失败 {failed} 张"
        if canceled:
            msg += "\n任务已取消，未完成全部图片。"
        if first_error:
            msg += f"\n首个错误：{first_error}"

        self._auto_apply_state = None
        self._auto_running = False
        self.statusBar().showMessage("自动标注完成。")
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
    install_global_exception_handler()
    app.setWindowIcon(QIcon())
    window = MainWindow()
    window.show()
    app.exec()
