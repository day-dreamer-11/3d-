# 构建 Windows 安装包（需已准备 build\python 便携运行时）
# 用法：powershell -ExecutionPolicy Bypass -File build_installer.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$iscc = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $iscc)) {
    throw "未找到 Inno Setup 6：$iscc"
}
if (-not (Test-Path "build\python\python.exe")) {
    throw "未找到便携运行时 build\python，请先执行 build_runtime.ps1"
}

# 清理运行期缓存，减小安装包并保证卸载干净
Get-ChildItem -Path "build\python" -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem -Path "build\python" -Recurse -Include "*.pyc", "*.pdb" -File -ErrorAction SilentlyContinue |
    Remove-Item -Force -ErrorAction SilentlyContinue
Get-ChildItem -Path "integration","models","network_sim","scheduler","experiments","generator","tester" `
    -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

New-Item -ItemType Directory -Force -Path "dist" | Out-Null
& $iscc "installer\AIIndustrialFactory.iss"
if ($LASTEXITCODE -ne 0) { throw "Inno Setup 编译失败" }

$output = Get-ChildItem "dist\AIIndustrialFactory-Setup-*.exe" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
Write-Host ""
Write-Host "安装包已生成：" $output.FullName -ForegroundColor Green
