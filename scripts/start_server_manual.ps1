# 手动启动服务器脚本
Write-Host "Starting VoiceRAG Server..." -ForegroundColor Green
Write-Host ""

$backendPath = Join-Path $PSScriptRoot "..\app\backend"
Set-Location $backendPath

Write-Host "Current directory: $backendPath" -ForegroundColor Yellow
Write-Host ""

# 检查 Python
Write-Host "Checking Python..." -ForegroundColor Cyan
python --version
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Python not found!" -ForegroundColor Red
    exit 1
}

# 检查 .env 文件
if (Test-Path ".env") {
    Write-Host "Found .env file" -ForegroundColor Green
} else {
    Write-Host "WARNING: .env file not found!" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Starting server on http://localhost:8765" -ForegroundColor Green
Write-Host "Press Ctrl+C to stop the server" -ForegroundColor Yellow
Write-Host ""

# 启动服务器
python app.py

