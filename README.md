# YOLO 数据集查看与校验工具（PyQt6）

这是一个用于 YOLO 数据集可视化、校验与轻量编辑的桌面工具。

## 功能概览

- 文件夹级导入数据集。
- 自动匹配同名图片与标签：
  - 图片后缀：`.jpg .jpeg .png .bmp .webp .tif .tiff`
  - 标签后缀：`.txt`
- 可视化查看：
  - 中间单栏显示“图片+标注框”编辑画布。
  - 标注按类别分色，支持高亮闪烁定位。
  - 悬浮提示显示类别与坐标。
- 自动校验：
  - 图片/标签缺失。
  - 空标签文件。
  - YOLO 行格式错误。
  - 数值解析失败、数值越界。
  - 边界框超出图像范围。
  - 支持可选置信度列（第 6 列）并校验范围。
- 轻量编辑：
  - 拖动框体移动。
  - 拖动边缘或角点缩放。
  - 修改类别 ID。
  - 删除标注框（按钮或 `Delete` 键）。
  - 每次编辑后自动写回 YOLO `txt`。
  - 首次写回前自动备份原标签到 `__label_backups__`。
- 撤销/重做：
  - 支持移动、缩放、改类、删除的撤销与重做。
- 自动标注：
  - 工具栏 `加载模型自动标注`。
  - 支持选择 `当前图片` 或 `全部图片`。
  - 支持设置置信度阈值。
  - 自动写入 YOLO 标签（含可选置信度列）。
- 导出能力：
  - 导出校验通过的图片/标签对。
  - 保存带标注框截图（PNG）。
- 大数据集优化：
  - 懒校验（按需校验 + 全量校验）。
  - 图片 LRU 缓存，减少重复解码卡顿。

## 项目结构

```text
main.py
requirements.txt
tests/
  test_file_manager.py
  test_validator.py
yolo_viewer/
  __init__.py
  app.py
  auto_annotator.py
  colors.py
  exporter.py
  file_manager.py
  models.py
  undo_commands.py
  validator.py
  widgets/
    image_canvas.py
```

## 环境配置

1. 创建并激活虚拟环境（可选）

```bash
python -m venv .venv
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
# macOS/Linux
source .venv/bin/activate
```

2. 安装依赖

```bash
pip install -r requirements.txt
```

3. 运行程序

```bash
python main.py
```

## 使用教程

1. 点击 `导入文件夹`，选择数据集根目录。
2. 在左侧文件列表选择样本。
3. 中间查看并编辑标注框，右侧查看表格与校验结果。
4. 编辑标注：
   - 移动：拖动框内部。
   - 缩放：拖动边缘或角点。
   - 删除：选中后按 `Delete` 或点 `删除框`。
   - 改类：选中后点 `修改类别`。
5. 编辑后会自动同步更新对应 `txt` 标签。
6. 可在顶部使用 `撤销` / `重做` 避免误操作。
7. 点击 `全量校验` 进行整个数据集校验。
8. 点击 `加载模型自动标注` 执行模型推理并写回标签。
9. 点击 `导出通过项` 导出无问题样本。
10. 点击 `保存截图` 保存带框预览图。

## 匹配规则说明

工具会对路径进行归一化匹配，并自动忽略常见桶目录（如 `images/`、`labels/`、`images_all/`、`labels_all/`）。
在目录结构不一致时，会回退到“同名文件 stem 唯一匹配”。

## 测试

运行单元测试：

```bash
python -m unittest discover -s tests -p "test_*.py"
```

## 打包为可执行文件

先安装打包工具：

```bash
pip install pyinstaller
```

### Windows

```bash
pyinstaller --noconfirm --clean --windowed --name yolo-viewer main.py
```

产物：

```text
dist/yolo-viewer/yolo-viewer.exe
```

### macOS

```bash
pyinstaller --noconfirm --clean --windowed --name yolo-viewer main.py
```

产物：

```text
dist/yolo-viewer/yolo-viewer.app
```

## 轻量化打包（推荐）

为了把可执行文件体积尽可能做小，建议使用 `lite` 构建：

- 不内置 `ultralytics/torch`（自动标注功能不可用）
- 不内置 `opencv/numpy`（走 Qt 解码，体积更小）

### Windows （最小体积）

```powershell
# onefile（默认，单文件）
.\scripts\build_windows_lite.ps1

# onedir（启动更快，目录形式）
.\scripts\build_windows_lite.ps1 -OneDir

# 如已安装 UPX，可进一步压缩
.\scripts\build_windows_lite.ps1 -UpxDir "C:\tools\upx"
```

### macOS （最小体积）

```bash
chmod +x ./scripts/build_macos_lite.sh
./scripts/build_macos_lite.sh
# 目录模式
./scripts/build_macos_lite.sh --onedir
```

### 完整版（含自动标注依赖，体积更大）

Windows:

```powershell
.\scripts\build_windows_full.ps1
```

macOS:

```bash
chmod +x ./scripts/build_macos_full.sh
./scripts/build_macos_full.sh
```

### 体积优化建议

1. 追求最小体积：`lite + onefile + UPX`
2. 追求启动速度：`lite + onedir`
3. 必须使用自动标注：使用 `full` 构建

## GitHub Actions 自动打包

仓库已内置工作流：`.github/workflows/build-packages.yml`。

- `push/main` 或 `PR` 时：自动运行测试，并构建 `Windows Lite` 包。
- `Actions -> Build Packages -> Run workflow` 可手动触发：
  - 勾选 `build_full`：额外构建 `Windows Full` 包。
  - 勾选 `build_macos`：额外构建 `macOS Lite` 包。
- 构建完成后在对应 Workflow Run 的 `Artifacts` 中下载构建目录包（下载后解压一次即可运行）。

建议发布流程：

1. 先在 PR 看 `Windows Lite` 产物是否可运行。
2. 发版前手动触发一次，按需勾选 `build_full / build_macos`。

