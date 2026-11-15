# One-time setup of the Actias FaceTime agent (Chert). Run from the repo root in PowerShell:
#     powershell -ExecutionPolicy Bypass -File chert\setup.ps1
#     powershell -ExecutionPolicy Bypass -File chert\setup.ps1 -Dir C:\chert-moth
# Builds the agent outside the repo (Chert's Windows support breaks under OneDrive and with spaces in paths): Chert's
# starter supplies greet.mjs / chert-rooms.mjs / node_modules, our files from chert\moth-room go on top. Keys come from
# chert\moth-room\.env.local (git-ignored) when it exists; blank MOTH_* lines are filled from config\cloud.json.
# Re-running updates everything; it never starts a call, so it costs no Chert allowance.
param(
    [string]$Agent = "",
    [string]$Dir = (Join-Path $env:USERPROFILE "chert-moth"),
    [string]$ChertVersion = "0.1.0-beta.21"
)
$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$Src = Join-Path $PSScriptRoot "moth-room"
$Dst = Join-Path $Dir "moth-room"

# A portable Node 24 in %USERPROFILE%\node24 wins over the system one (chert\run.ps1 does the same).
$portable = Join-Path $env:USERPROFILE "node24"
if (Test-Path (Join-Path $portable "node.exe")) { $env:Path = "$portable;" + $env:Path }
$nodeVer = (& node --version) 2>$null
if (-not $nodeVer) { throw "Node.js is not installed. Install Node 24 from nodejs.org, then re-run." }
$parts = $nodeVer.TrimStart("v").Split(".")
$major = [int]$parts[0]; $minor = [int]$parts[1]
if ($major -lt 22 -or ($major -eq 22 -and $minor -lt 23) -or $major -eq 23 -or $major -eq 25) {
    throw "Node $nodeVer is too old or unsupported. Chert needs Node 24 (or 22.23+)."
}
Write-Host "== using Node $nodeVer"
if ($Dir -match "OneDrive") { Write-Warning "$Dir is inside OneDrive; Chert on Windows may fail there. Use -Dir C:\chert-moth." }

$repoEnv = Join-Path $Src ".env.local"
$seed = if (Test-Path $repoEnv) { $repoEnv } else { Join-Path $Src ".env.example" }
$lines = [IO.File]::ReadAllLines($seed)
function Get-EnvValue([string]$key) {
    $hit = $script:lines | Where-Object { $_ -match "^$key=" } | Select-Object -First 1
    if ($hit) { return $hit.Substring($key.Length + 1).Trim() } else { return "" }
}
function Set-EnvIfBlank([string]$key, [string]$value) {
    $cur = Get-EnvValue $key
    if ($cur -and $cur -notmatch "YOUR|paste") { return }
    $script:lines = @($script:lines | Where-Object { $_ -notmatch "^$key=" }) + "$key=$value"
}
if ($Agent) { $script:lines = @($lines | Where-Object { $_ -notmatch "^AGENT_NAME=" }) + "AGENT_NAME=$Agent" }
Set-EnvIfBlank "AGENT_NAME" "actias-room"
$Agent = Get-EnvValue "AGENT_NAME"

New-Item -ItemType Directory -Force $Dir | Out-Null
if (-not (Test-Path (Join-Path $Dst "chert-rooms.mjs"))) {
    Write-Host "== creating the Chert starter in $Dst"
    Push-Location $Dir
    try { & npx --yes "@trychert/facetime@$ChertVersion" new moth-room --agent $Agent } finally { Pop-Location }
    if (-not (Test-Path (Join-Path $Dst "chert-rooms.mjs"))) { throw "Chert's starter did not create $Dst\chert-rooms.mjs" }
}

Write-Host "== copying the Actias agent files"
foreach ($f in "agent.mjs", "moth-tools.mjs", "screen.mjs", "moth.png", "test-tools.mjs", "check-keys.mjs",
               "check-gemini-live.mjs", "call-report.mjs", "package.json", ".env.example") {
    Copy-Item (Join-Path $Src $f) (Join-Path $Dst $f) -Force
}

# The agent's health server defaults to 8081, which is the Moth phone page's port.
$rooms = Join-Path $Dst "chert-rooms.mjs"
$text = [IO.File]::ReadAllText($rooms)
if ($text -notmatch "port:\s*8091") {
    if ($text -match "new ServerOptions\(\{") {
        $text = $text -replace "new ServerOptions\(\{", "new ServerOptions({ port: 8091,"
        [IO.File]::WriteAllText($rooms, $text)
    } else {
        Write-Warning "Could not set port 8091 in chert-rooms.mjs; add 'port: 8091' to new ServerOptions({...}) by hand."
    }
}

$cloudCfg = Join-Path $Repo "config\cloud.json"
if (Test-Path $cloudCfg) {
    $c = Get-Content $cloudCfg -Raw | ConvertFrom-Json
    if ($c.url -and $c.url -notmatch "YOUR-") { Set-EnvIfBlank "MOTH_API_URL" $c.url }
    if ($c.room) { Set-EnvIfBlank "MOTH_ROOM" $c.room }
    if ($c.query_token -and $c.query_token -notmatch "paste") { Set-EnvIfBlank "MOTH_QUERY_TOKEN" $c.query_token }
}
if (-not (Get-EnvValue "MOTH_QUERY_TOKEN") -or (Get-EnvValue "MOTH_QUERY_TOKEN") -match "paste") {
    Write-Warning "No MOTH_QUERY_TOKEN: paste the QUERY token from deploy.sh into chert\moth-room\.env.local, re-run."
}
[IO.File]::WriteAllLines((Join-Path $Dst ".env.local"), $lines)

Write-Host "== npm install"
Push-Location $Dst
try { & npm install --no-fund --no-audit } finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { throw "npm install failed (see above); the agent is not ready." }

Write-Host ""
Write-Host "================ ACTIAS AGENT READY ================"
Write-Host " Folder : $Dst   (agent name: $Agent)"
Write-Host " Keys   : edit chert\moth-room\.env.local in the repo, then re-run this script"
Write-Host " Check  : powershell -ExecutionPolicy Bypass -File chert\run.ps1 check    (no Chert calls)"
Write-Host " Run    : powershell -ExecutionPolicy Bypass -File chert\run.ps1 agent    (terminal 1)"
Write-Host "          powershell -ExecutionPolicy Bypass -File chert\run.ps1 line     (terminal 2)"
Write-Host "===================================================="
