# 上传新数据文件到 Azure AI Search
# 使用方法: 将新文件放入 data/ 目录，然后运行此脚本

Write-Host "=== 上传新数据文件到 VoiceRAG ===" -ForegroundColor Cyan

# 加载 Python 虚拟环境
Write-Host "`n[1/3] 加载 Python 虚拟环境..." -ForegroundColor Yellow
./scripts/load_python_env.ps1

# 检测 Python 路径
$venvPythonPath = "./.venv/scripts/python.exe"
if (Test-Path -Path "/usr") {
  $venvPythonPath = "./.venv/bin/python"
}

# 检查 data/ 目录是否存在
if (-not (Test-Path -Path "data")) {
    Write-Host "错误: data/ 目录不存在!" -ForegroundColor Red
    exit 1
}

# 显示 data/ 目录中的文件
Write-Host "`n[2/3] data/ 目录中的文件:" -ForegroundColor Yellow
Get-ChildItem -Path "data" | Format-Table Name, Length, LastWriteTime

# 运行上传脚本
Write-Host "`n[3/3] 上传文件并触发索引..." -ForegroundColor Yellow
& $venvPythonPath app/backend/setup_intvect.py

Write-Host "`n✅ 完成! 文件已上传到 Azure Blob Storage。" -ForegroundColor Green
Write-Host "   索引器将在几分钟内自动处理新文件。" -ForegroundColor Green
Write-Host "`n💡 提示: 可以在 Azure Portal 中查看索引进度" -ForegroundColor Cyan
Write-Host "   Azure Portal > AI Search > 索引器 > 运行历史记录" -ForegroundColor Gray




