# One command for a test call: checks everything (tools\preflight.py), starts the agent in its own window, then puts it
# on your Chert line here. FaceTime the line once it says it is listening. Ctrl+C here ends the call setup and closes
# the agent window. Only the "line" step uses Chert. CALL_ACTIAS.cmd in the repo root runs this.
param([switch]$SkipCheck)
$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$run = Join-Path $PSScriptRoot "run.ps1"

# chert dev refuses to start without a saved login (~/.chert/credentials.json); sign in before starting anything.
$creds = Join-Path $env:USERPROFILE ".chert\credentials.json"
if (-not (Test-Path $creds)) {
    Write-Host "== Chert isn't signed in on this laptop yet. Finish the sign-in it opens, then this carries on."
    & powershell -ExecutionPolicy Bypass -File $run login
    if (-not (Test-Path $creds)) { throw "Chert sign-in didn't finish. Run: powershell -ExecutionPolicy Bypass -File chert\run.ps1 login" }
}

if (-not $SkipCheck) {
    & py (Join-Path $Repo "tools\preflight.py")
    if ($LASTEXITCODE -ne 0) {
        $go = Read-Host "Some checks FAILED (see above). Call anyway? [y/N]"
        if ($go -notmatch "^[yY]") { exit 1 }
    }
}

Write-Host "`n== starting the agent in its own window"
$agent = Start-Process powershell -PassThru -ArgumentList "-NoExit", "-ExecutionPolicy", "Bypass", "-File", "`"$run`"", "agent"
Start-Sleep -Seconds 8
if ($agent.HasExited) { throw "The agent window closed straight away; run 'chert\run.ps1 agent' to see why." }

Write-Host ""
Write-Host "================ READY TO CALL ================"
Write-Host " When the line below says it's listening, FaceTime your Chert line from the verified iPhone."
Write-Host " Try, in order:"
Write-Host "   1. 'Is anyone in the room right now?'"
Write-Host "   2. 'What happened in the last five minutes?'"
Write-Host "   3. 'Where are they standing?'"
Write-Host "   4. 'Who was it?'   (it should say it can't know who)"
Write-Host " Ctrl+C here when you're done, so no Chert time is used while idle."
Write-Host "==============================================="
try {
    & powershell -ExecutionPolicy Bypass -File $run line
} finally {
    if (-not $agent.HasExited) {
        Get-CimInstance Win32_Process -Filter "ParentProcessId=$($agent.Id)" | ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
        Stop-Process -Id $agent.Id -Force -ErrorAction SilentlyContinue
    }
    Write-Host "agent stopped."
}
