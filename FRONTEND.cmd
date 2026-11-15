@echo off
rem Frontend laptop: builds the website and opens it, with the 3D room showing live people from Vultr.
rem Needs config\cloud.json (url + query_token) copied from the backend laptop. No boards or Wi-Fi setup here.
rem --last-known keeps drawing the last position when Vultr has had no upload for 5 min (debug; drop it for the demo).
cd /d "%~dp0"
if not exist web\node_modules (
  pushd web & call npm install & popd
)
pushd web
call npm run build
set BUILD_ERR=%errorlevel%
popd
if not "%BUILD_ERR%"=="0" (
  echo Website build failed.
  pause
  exit /b 1
)
py server\room_viewer.py --last-known
pause
