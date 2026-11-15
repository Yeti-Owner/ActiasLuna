@echo off
rem Starts the Moth server: tracking from the boards on the demo Wi-Fi + upload to Vultr over Ethernet.
cd /d "%~dp0"
py server\luna_server.py
pause
