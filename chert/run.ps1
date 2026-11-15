# Runs the Actias agent that chert\setup.ps1 built, with Node 24 on PATH. From the repo root:
#     powershell -ExecutionPolicy Bypass -File chert\run.ps1 check   # Vultr tools + LiveKit/Gemini keys; no Chert calls
#     powershell -ExecutionPolicy Bypass -File chert\run.ps1 agent   # the agent process (npm start)
#     powershell -ExecutionPolicy Bypass -File chert\run.ps1 line    # chert dev: puts the agent on your Chert line
#     powershell -ExecutionPolicy Bypass -File chert\run.ps1 login   # one-time Chert sign-in on this laptop
# "line" is the only one that uses Chert call time; leave it off when you are not about to call.
param(
    [Parameter(Mandatory = $true)][ValidateSet("check", "agent", "line", "login")][string]$What,
    [string]$Dir = (Join-Path $env:USERPROFILE "chert-moth"),
    [string]$ChertVersion = "0.1.0-beta.21",
    [int]$Minutes = 20,
    [switch]$Takeover
)
$ErrorActionPreference = "Stop"
$portable = Join-Path $env:USERPROFILE "node24"
if (Test-Path (Join-Path $portable "node.exe")) { $env:Path = "$portable;" + $env:Path }
$Dst = Join-Path $Dir "moth-room"
if (-not (Test-Path (Join-Path $Dst "chert-rooms.mjs"))) { throw "No agent in $Dst yet. Run chert\setup.ps1 first." }
Set-Location $Dst
$agent = (Get-Content .env.local | Where-Object { $_ -match "^AGENT_NAME=" } | Select-Object -First 1) -replace "^AGENT_NAME=", ""
switch ($What) {
    "check" {
        & node --env-file=.env.local test-tools.mjs
        & node --env-file=.env.local check-keys.mjs
    }
    "agent" { & npm start }
    "line" {
        # --minutes releases the line on its own, so a forgotten window can't run up Chert time.
        $extra = @()
        if ($Minutes -gt 0) { $extra += "--minutes", $Minutes }
        if ($Takeover) { $extra += "--takeover" }     # kicks whoever else holds the line (line_in_use)
        & npx "@trychert/facetime@$ChertVersion" dev --agent $agent @extra
    }
    "login" { & npx "@trychert/facetime@$ChertVersion" login }
}
