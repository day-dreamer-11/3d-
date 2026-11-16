# AIC Factory Studio 一键启动（Windows PowerShell）
# 启动 Streamlit 工作台 + 场景 FastAPI；OIP 编辑器在首次进入「场景编辑」页时自动启动。
Set-Location $PSScriptRoot

if (Test-Path ".venv\Scripts\Activate.ps1") {
    . .\.venv\Scripts\Activate.ps1
}

Write-Host "检查 Python 依赖..." -ForegroundColor Cyan
python -c "import streamlit, fastapi, uvicorn, httpx, plotly, pandas" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "缺少依赖，请执行：pip install -r requirements.txt" -ForegroundColor Yellow
    exit 1
}

if (-not (Test-Path "frontend\oip\dist\index.html")) {
    Write-Host "未找到 OIP 静态构建产物。" -ForegroundColor Yellow
    Write-Host "首次部署请在 frontend\oip 执行：npm ci ; npm run build" -ForegroundColor Yellow
    Write-Host "（开发模式也可设置 AIC_OIP_DEV=1 使用 Node/Vite，但需要 Node 24）" -ForegroundColor Yellow
}

python run_platform.py
