@echo off
rem One-time setup for House Plan 3D on Windows. No admin rights needed.
rem All the work is in setup-windows.ps1; this just runs it with a policy that allows local scripts.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup-windows.ps1" %*
set RC=%ERRORLEVEL%
echo.
pause
exit /b %RC%
