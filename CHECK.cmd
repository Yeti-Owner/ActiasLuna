@echo off
rem Checks Wi-Fi, Ethernet, Vultr, live data and the Chert agent. Makes no Chert call.
cd /d "%~dp0"
py tools\preflight.py
pause
