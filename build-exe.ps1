# 把 filetidy 打包成可以直接运行的 Windows 程序。
#
# 用法（在仓库根目录）：
#     powershell -ExecutionPolicy Bypass -File .\build-exe.ps1
#
# 默认产出目录模式：dist\filetidy\ 一整个文件夹 + dist\filetidy-win64.zip
# 把 zip 发给别人，对方解压后双击里面的 filetidy.exe 就能用。
#
# 单文件模式（-OneFile）每次启动都要解压到系统临时目录，环境受限时会启动失败，
# 所以默认用目录模式。想单文件就加 -OneFile。

param(
    [switch]$OneFile
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "找不到 py 命令。先装 Python 3.10 以上版本。"
}

Write-Host "==> 检查 PyInstaller" -ForegroundColor Cyan
py -m PyInstaller --version 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "    没装，正在安装（要联网）…" -ForegroundColor Yellow
    py -m pip install --upgrade pyinstaller
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller 安装失败" }
}

# 清掉上一次的产物。都用 SilentlyContinue：如果上次打的 exe 还开着窗口，
# 文件是被锁住的，删不掉也不该让打包中止。
if (Test-Path dist) {
    Write-Host "==> 清理旧的 dist" -ForegroundColor Cyan
    Get-ChildItem dist -Force -ErrorAction SilentlyContinue | ForEach-Object {
        Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path (Join-Path "dist" "filetidy.exe")) {
        Write-Host "    旧的 filetidy.exe 删不掉 —— 还有窗口开着？关掉再打更干净" -ForegroundColor Yellow
    }
}

$mode = if ($OneFile) { "--onefile" } else { "--onedir" }
Write-Host "==> 开始打包（$mode），第一次会慢几分钟" -ForegroundColor Cyan

# 只用 QtWidgets，这些模块一个都没 import。排掉能省下几十 MB。
$excludes = @(
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.QtQuickControls2",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtNetwork", "PySide6.QtSql",
    "PySide6.QtTest", "PySide6.QtXml", "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets", "PySide6.QtPrintSupport", "PySide6.QtDBus",
    "PySide6.QtDesigner", "PySide6.QtUiTools", "PySide6.QtHelp",
    "tkinter", "unittest", "pydoc", "doctest"
)
$excludeArgs = $excludes | ForEach-Object { "--exclude-module"; $_ }

# 图标要给绝对路径：PyInstaller 按 --specpath 的目录解析相对路径
$icon = Join-Path $PSScriptRoot "assets\filetidy.ico"

py -m PyInstaller `
    --noconfirm `
    --clean `
    $mode `
    --windowed `
    --name filetidy `
    --icon $icon `
    --paths . `
    --distpath dist `
    --workpath build `
    --specpath build `
    @excludeArgs `
    tools\exe_entry.py

if ($LASTEXITCODE -ne 0) { throw "打包失败，看上面的报错" }

if ($OneFile) {
    $exe = Join-Path "dist" "filetidy.exe"
    if (-not (Test-Path $exe)) { throw "没有生成 $exe" }
    $mb = [math]::Round((Get-Item $exe).Length / 1MB, 1)
    Write-Host ""
    Write-Host "==> 完成：$exe  ($mb MB)" -ForegroundColor Green
    Write-Host "    单文件模式。如果启动时报 could not create temporary directory，"
    Write-Host "    说明环境不让它解压，改用默认的目录模式重新打包。"
} else {
    $exe = Join-Path "dist\filetidy" "filetidy.exe"
    if (-not (Test-Path $exe)) { throw "没有生成 $exe" }

    # 清掉用不到的 Qt 组件。--exclude-module 只挡 Python 模块，
    # Qt 的 dll 是打包器按名字收集的，排不掉，只能在打包后删。
    # 如果双击闪退，先把 opengl32sw.dll 从下面这条列表里去掉再打一次。
    $drop = @(
        "Qt6Quick.dll", "Qt6Qml.dll", "Qt6QmlModels.dll", "Qt6QmlWorkerScript.dll",
        "Qt6QuickWidgets.dll", "Qt6QuickControls2.dll", "Qt6QuickControls2Impl.dll",
        "Qt6Pdf.dll", "Qt6Network.dll", "Qt6Sql.dll", "Qt6Test.dll", "Qt6Xml.dll",
        "Qt6OpenGL.dll", "Qt6OpenGLWidgets.dll", "Qt6Multimedia.dll",
        "Qt6MultimediaWidgets.dll", "opengl32sw.dll"
    )
    $freed = 0
    foreach ($name in $drop) {
        $f = Join-Path "dist\filetidy\_internal\PySide6" $name
        if (Test-Path $f) {
            $freed += (Get-Item $f).Length
            Remove-Item $f -Force -ErrorAction SilentlyContinue
        }
    }
    Write-Host ("==> 清掉 {0:N1} MB 用不到的 Qt 组件" -f ($freed / 1MB)) -ForegroundColor Cyan

    $zip = Join-Path "dist" "filetidy-win64.zip"
    Write-Host "==> 正在压缩成 zip" -ForegroundColor Cyan
    Compress-Archive -Path "dist\filetidy\*" -DestinationPath $zip -Force

    $mb = [math]::Round((Get-Item $exe).Length / 1MB, 1)
    $zipMb = [math]::Round((Get-Item $zip).Length / 1MB, 1)
    Write-Host ""
    Write-Host "==> 完成" -ForegroundColor Green
    Write-Host "    程序：$exe  ($mb MB)"
    Write-Host "    压缩包：$zip  ($zipMb MB)"
    Write-Host ""
    Write-Host "    把这个 zip 传给别人，对方解压后双击里面的 filetidy.exe。"
    Write-Host "    提醒他们解压整个文件夹 —— 只把 exe 拖出来会跑不起来。"
}

Write-Host ""
Write-Host "    启动失败的话，看 $env:USERPROFILE\filetidy-error.log"
