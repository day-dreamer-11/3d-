# 将本地仓库推送到你的 GitHub（需先安装 Git 并登录/配置凭据）
param(
    [Parameter(Mandatory = $true)]
    [string]$RepoUrl
)

Set-Location (Split-Path $PSScriptRoot -Parent)

$existing = git remote get-url origin 2>$null
if ($LASTEXITCODE -eq 0) {
    git remote set-url origin $RepoUrl
} else {
    git remote add origin $RepoUrl
}

git push -u origin main
if ($LASTEXITCODE -eq 0) {
    Write-Host "已推送。接下来在 Render 打开 Blueprint 页面并连接该仓库：" -ForegroundColor Green
    Write-Host "https://dashboard.render.com/blueprints"
    Start-Process "https://dashboard.render.com/blueprints"
}
