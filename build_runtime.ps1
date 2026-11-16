# 下载并组装便携 Python 运行时（只需执行一次；需要联网）
# 用法：powershell -ExecutionPolicy Bypass -File build_runtime.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$url = "https://github.com/astral-sh/python-build-standalone/releases/download/20261026/cpython-3.12.16%2B20261026-x86_64-pc-windows-msvc-install_only.tar.gz"
$archive = "build\cpython.tar.gz"

New-Item -ItemType Directory -Force -Path "build" | Out-Null
if (-not (Test-Path "build\python\python.exe")) {
    Write-Host "下载便携 Python..."
    Invoke-WebRequest -Uri $url -OutFile $archive
    tar -xzf $archive -C build
}
if (-not (Test-Path "build\python\python.exe")) {
    throw "便携 Python 解压失败"
}

Write-Host "安装依赖到便携运行时..."
& "build\python\python.exe" -m pip install --no-cache-dir -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "依赖安装失败" }

Write-Host "便携运行时已就绪：build\python" -ForegroundColor Green
