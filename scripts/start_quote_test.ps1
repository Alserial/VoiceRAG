# Quick start script for testing quote functionality
# This script starts the backend server for quote testing

Write-Host ""
Write-Host "========================================="
Write-Host "  Starting Quote Test Server"
Write-Host "========================================="
Write-Host ""

# Check if virtual environment exists
if (-not (Test-Path ".venv\Scripts\activate.bat")) {
    Write-Host "Virtual environment not found. Creating..."
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Failed to create virtual environment"
        exit $LASTEXITCODE
    }
}

# Activate virtual environment
Write-Host "Activating virtual environment..."
& ".venv\Scripts\activate.bat"

# Check if frontend is built
if (-not (Test-Path "app\backend\static\index.html")) {
    Write-Host ""
    Write-Host "Frontend not built. Building now..."
    Write-Host ""
    Set-Location app\frontend
    npm install
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Failed to install frontend dependencies"
        exit $LASTEXITCODE
    }
    npm run build
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Failed to build frontend"
        exit $LASTEXITCODE
    }
    Set-Location ..\..
}

# Install Python dependencies if needed
Write-Host ""
Write-Host "Checking Python dependencies..."
$venvPythonPath = ".venv\Scripts\python.exe"
& $venvPythonPath -m pip install -q -r app\backend\requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "Warning: Some dependencies may not be installed"
}

# Start backend server
Write-Host ""
Write-Host "========================================="
Write-Host "  Starting backend server..."
Write-Host "  Server will be available at:"
Write-Host "  http://localhost:8765"
Write-Host ""
Write-Host "  Press Ctrl+C to stop the server"
Write-Host "========================================="
Write-Host ""

Set-Location app\backend
& $venvPythonPath app.py


