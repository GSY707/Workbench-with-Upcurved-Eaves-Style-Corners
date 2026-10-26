@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_ui.ps1" %*
exit /b %errorlevel%
