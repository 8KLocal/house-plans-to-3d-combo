@echo off
rem Open the House Plan 3D desktop app on Windows, using this folder's .venv.
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo.
  echo  House Plan 3D is not set up on this computer yet.
  echo  The folder ".venv" with the app's Python packages is missing.
  echo.
  echo  Fix: double-click setup-windows.bat once ^(needs internet, about 10-20 minutes^),
  echo  then double-click open-app.bat again.
  echo.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m hp3d.app %*
if errorlevel 1 (
  echo.
  echo  The app closed with an error ^(see the messages above^).
  echo  If it mentions a missing module, run setup-windows.bat again.
  pause
)
endlocal
