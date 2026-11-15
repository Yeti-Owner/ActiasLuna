@echo off
rem Backend laptop: boards on the demo Wi-Fi -> tracking, dashboard (room scan, boards, calibration), phone page,
rem and live upload to Vultr over Ethernet. The frontend laptop runs FRONTEND.cmd and reads the room back from Vultr.
cd /d "%~dp0"
if not exist config\cloud.json (
  echo.
  echo  WARNING: config\cloud.json is missing, so nothing will upload to Vultr.
  echo  Copy config\cloud.example.json to config\cloud.json and fill in the url and tokens.
  echo.
)
rem Open the dashboard once the server has had a moment to bind its port.
start "" /min powershell -NoProfile -Command "Start-Sleep 3; Start-Process http://127.0.0.1:8080/"
py server\luna_server.py %*
pause
