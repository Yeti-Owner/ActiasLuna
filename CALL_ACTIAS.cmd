@echo off
rem Checks everything, starts the agent, puts it on your Chert line. FaceTime it when ready.
cd /d "%~dp0"
powershell -ExecutionPolicy Bypass -File chert\call.ps1
pause
