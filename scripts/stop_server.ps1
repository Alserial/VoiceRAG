# Stop script for VoiceRAG server
# This script stops the backend server running on port 8765

Write-Host ""
Write-Host "========================================="
Write-Host "  Stopping VoiceRAG Server"
Write-Host "========================================="
Write-Host ""

# Find processes using port 8765
$connections = Get-NetTCPConnection -LocalPort 8765 -ErrorAction SilentlyContinue

if ($connections) {
    $processIds = $connections | Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($processId in $processIds) {
        $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
        if ($process) {
            Write-Host "Stopping process: $($process.ProcessName) (PID: $processId)"
            Stop-Process -Id $processId -Force
            Write-Host "Process stopped successfully"
        }
    }
    Write-Host ""
    Write-Host "Server stopped!"
} else {
    Write-Host "No server found running on port 8765"
}

Write-Host ""

