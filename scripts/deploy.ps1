# VoiceRAG 部署脚本
# 用于将本地更改部署到 Azure

Write-Host "=== VoiceRAG 部署到 Azure ===" -ForegroundColor Cyan

# 步骤 1: 检查是否在正确的目录
if (-not (Test-Path "azure.yaml")) {
    Write-Host "错误: 请在项目根目录运行此脚本" -ForegroundColor Red
    exit 1
}

# 步骤 2: 构建前端
Write-Host "`n[1/3] 构建前端..." -ForegroundColor Yellow
Push-Location app/frontend
npm run build
if ($LASTEXITCODE -ne 0) {
    Write-Host "前端构建失败!" -ForegroundColor Red
    Pop-Location
    exit 1
}
Pop-Location
Write-Host "前端构建成功!" -ForegroundColor Green

# 步骤 3: 检查 azd 环境
Write-Host "`n[2/3] 检查 azd 环境..." -ForegroundColor Yellow
$envList = azd env list --output json | ConvertFrom-Json
$hasEnv = $false
foreach ($env in $envList) {
    if ($env.IsDefault -eq $true) {
        Write-Host "使用环境: $($env.Name)" -ForegroundColor Green
        $hasEnv = $true
        break
    }
}

if (-not $hasEnv) {
    Write-Host "错误: 没有找到 azd 环境" -ForegroundColor Red
    Write-Host "请先运行: azd env select <环境名称>" -ForegroundColor Yellow
    exit 1
}

# 步骤 4: 部署到 Azure
Write-Host "`n[3/3] 部署到 Azure..." -ForegroundColor Yellow
Write-Host "这可能需要 3-5 分钟，请耐心等待..." -ForegroundColor Yellow
Write-Host ""

azd deploy --service backend

if ($LASTEXITCODE -eq 0) {
    Write-Host "`n=== 部署成功! ===" -ForegroundColor Green
    Write-Host ""
    
    # 获取应用 URL
    $backendUri = azd env get-values | Select-String "BACKEND_URI" | ForEach-Object { $_ -replace 'BACKEND_URI="(.+)"', '$1' }
    
    Write-Host "应用 URL: $backendUri" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "请访问应用并验证:" -ForegroundColor Yellow
    Write-Host "  1. 页面右下角显示版本号 v2.1.0" -ForegroundColor Gray
    Write-Host "  2. 测试智能回答模式" -ForegroundColor Gray
    Write-Host "  3. 确认语言保持英语" -ForegroundColor Gray
} else {
    Write-Host "`n=== 部署失败 ===" -ForegroundColor Red
    Write-Host "请检查错误信息并重试" -ForegroundColor Yellow
    exit 1
}











