param(
    [switch]$OneDir,
    [string]$UpxDir = ""
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

python -m pip install --upgrade pip
python -m pip install -r requirements-full.txt
python -m pip install pyinstaller

$buildArgs = @(
    "--noconfirm",
    "--clean",
    "--windowed",
    "--name", "yolo-viewer-full",
    "--icon", "yolo_viewer/assets/app_icon.ico",
    "--add-data", "yolo_viewer/assets;yolo_viewer/assets",
    "main.py"
)

if ($OneDir) {
    # Keep runtime files next to exe (avoid _internal layout issues on some Windows setups).
    $buildArgs = @("--onedir", "--contents-directory", ".") + $buildArgs
} else {
    $buildArgs = @("--onefile") + $buildArgs
}

if ($UpxDir -and (Test-Path $UpxDir)) {
    $buildArgs = @("--upx-dir", $UpxDir) + $buildArgs
}

python -m PyInstaller @buildArgs

Write-Host "构建完成。输出目录：dist\\" -ForegroundColor Green
