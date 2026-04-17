# Kills whatever process is holding port 8000
$entry = netstat -ano | Select-String ":8000\s+\S+\s+LISTENING"
if ($entry) {
    $pid = ($entry -split '\s+')[-1]
    Write-Host "Killing PID $pid on port 8000..."
    taskkill /F /PID $pid
    Write-Host "Done. Port 8000 is now free."
} else {
    Write-Host "Port 8000 is already free."
}
