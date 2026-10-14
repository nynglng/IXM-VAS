@echo off
rem Installs or repairs IXM-VAS for this Windows user. See setup.ps1.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
echo.
pause
