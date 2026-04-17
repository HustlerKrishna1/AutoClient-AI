# AutoClient AI — start script
# Usage: .\start.ps1
# Optional env vars:
#   $env:OLLAMA_MODEL   = "llama3:8b"       (default)
#   $env:SENDER_NAME    = "Alex"             (default)
#   $env:SENDER_COMPANY = "GrowthForge"      (default)

Set-Location $PSScriptRoot

# Kill anything already on port 8000
$entry = netstat -ano | Select-String ":8000\s+\S+\s+LISTENING"
if ($entry) {
    $oldPid = ($entry -split '\s+')[-1]
    Write-Host "[AutoClient] Stopping existing process on port 8000 (PID $oldPid)..."
    taskkill /F /PID $oldPid | Out-Null
    Start-Sleep -Seconds 1
}

Write-Host "[AutoClient] Starting server at http://127.0.0.1:8000/"
Write-Host "[AutoClient] Press Ctrl+C to stop."
Write-Host ""

python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
