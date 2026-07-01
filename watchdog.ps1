# Penta-Bot watchdog — keeps bot.py running 24/7, restarts on crash.
# Launched hidden at logon by start_bot.vbs (Startup folder). Logs to logs\bot.log.

$botDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$logDir = Join-Path $botDir "logs"
$logFile = Join-Path $logDir "bot.log"
$maxLogBytes = 5MB

# Only one watchdog instance allowed
$mutex = New-Object System.Threading.Mutex($false, "PentaBotWatchdog")
if (-not $mutex.WaitOne(0)) { exit }

if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
Set-Location $botDir

function Write-Log($msg) {
    Add-Content -Path $logFile -Value "$(Get-Date -Format s) [watchdog] $msg"
}

Write-Log "=== Watchdog started ==="

while ($true) {
    # Rotate log if it gets big
    if ((Test-Path $logFile) -and (Get-Item $logFile).Length -gt $maxLogBytes) {
        Move-Item -Force $logFile "$logFile.old"
    }

    Write-Log "Starting bot.py"
    & python -u bot.py 2>&1 | ForEach-Object { "$(Get-Date -Format s) $_" } | Add-Content -Path $logFile
    Write-Log "bot.py exited (code $LASTEXITCODE) — restarting in 15s"
    Start-Sleep -Seconds 15
}
