@echo off
rem Double-click this to launch the Sync Tool.
rem Do NOT open ui.html directly - it must be served by server.py.
cd /d "%~dp0"
echo Starting Dual-System Sync Tool...
echo Browser should open at http://localhost:8765
echo Close this window to stop the tool.
echo.
python server.py
if errorlevel 1 (
  echo.
  echo ---------------------------------------------------------
  echo The server did not start. Common causes:
  echo   * numpy missing      ^-^>  pip install numpy
  echo   * ffmpeg not on PATH ^-^>  install ffmpeg 6.1+
  echo   * python not on PATH ^-^>  try:  py server.py
  echo ---------------------------------------------------------
)
pause
