# VoiceRAG 部署脚本（Windows PowerShell / PowerShell 7 均可）
# 目标：从任意目录运行都能正确定位到 VoiceRAG 根目录并部署

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# 仅改善中文输出显示，不影响执行
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

Write-Host "=== VoiceRAG Deploy to Azure ===" -ForegroundColor Cyan

# 以脚本位置推导项目根目录：.../VoiceRAG
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

Push-Location $ProjectRoot
try {
    # 1) 检查是否在正确目录
    $azureYaml = Join-Path $ProjectRoot "azure.yaml"
    if (-not (Test-Path $azureYaml)) {
        Write-Host "ERROR: azure.yaml not found under VoiceRAG project root." -ForegroundColor Red
        Write-Host ("ProjectRoot = {0}" -f $ProjectRoot) -ForegroundColor Yellow
        exit 1
    }

    # 2) 构建前端
    Write-Host ""
    Write-Host "[1/3] Build frontend ..." -ForegroundColor Yellow

    $FrontendDir = Join-Path $ProjectRoot "app\frontend"
    if (-not (Test-Path $FrontendDir)) {
        Write-Host ("ERROR: Frontend dir not found: {0}" -f $FrontendDir) -ForegroundColor Red
        exit 1
    }

    Push-Location $FrontendDir
    try {
        if (-not (Test-Path (Join-Path $FrontendDir "package.json"))) {
            Write-Host "ERROR: package.json not found in frontend directory." -ForegroundColor Red
            exit 1
        }

        if (-not (Test-Path (Join-Path $FrontendDir "node_modules"))) {
            Write-Host "node_modules not found, running npm install ..." -ForegroundColor Gray
            npm install
        }

        Write-Host "Running npm run build ..." -ForegroundColor Gray
        npm run build
    }
    finally {
        Pop-Location
    }

    Write-Host "Frontend build OK." -ForegroundColor Green

    # 3) 检查 azd 环境
    Write-Host ""
    Write-Host "[2/3] Check azd environment ..." -ForegroundColor Yellow

    $azd = Get-Command azd -ErrorAction SilentlyContinue
    if (-not $azd) {
        Write-Host "ERROR: azd command not found. Please install Azure Developer CLI (azd)." -ForegroundColor Red
        exit 1
    }

    # 不再依赖 `azd env list --output json` 的字段格式
    # 只要当前已选择环境，`azd env get-values` 就能正常返回
    $values = azd env get-values 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $values) {
        Write-Host "ERROR: No azd environment selected (or not initialized)." -ForegroundColor Red
        Write-Host "Please run: azd env select voicerag-prod  (or azd env new voicerag-prod)" -ForegroundColor Yellow
        exit 1
    }

    # 尝试显示当前环境名（如果能从输出里拿到）
    $currentEnv = azd env list | Select-String "true" | ForEach-Object { ($_ -split '\s+')[0] } | Select-Object -First 1
    if ($currentEnv) {
        Write-Host ("Using environment: {0}" -f $currentEnv) -ForegroundColor Green
    } else {
        Write-Host "Environment selected OK." -ForegroundColor Green
    }


    # 4) 部署
    Write-Host ""
    Write-Host "[3/3] Deploy to Azure ..." -ForegroundColor Yellow
    Write-Host "Running: azd deploy --service backend" -ForegroundColor Gray
    Write-Host ""

    azd deploy --service backend

    Write-Host ""
    Write-Host "=== Deploy Success ===" -ForegroundColor Green

    # 输出 BACKEND_URI（如果存在）
    $backendUri = $null
    try {
        $backendUri = azd env get-values |
            Select-String 'BACKEND_URI' |
            ForEach-Object { $_ -replace 'BACKEND_URI="(.+)"', '$1' }
    } catch {}

    if ($backendUri) {
        Write-Host ("Backend URL: {0}" -f $backendUri) -ForegroundColor Cyan
    } else {
        Write-Host "BACKEND_URI not found in azd env get-values output (may use a different variable name)." -ForegroundColor Yellow
    }
}
catch {
    Write-Host ""
    Write-Host "=== Deploy Failed ===" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Yellow
    exit 1
}
finally {
    Pop-Location
}
