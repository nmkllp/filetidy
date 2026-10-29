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

py -m PyInstaller `
    --noconfirm `
    --clean `
    $mode `
    --windowed `
    --name filetidy `
    --paths . `
    --distpath dist `
    --workpath build `
    --specpath build `
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
