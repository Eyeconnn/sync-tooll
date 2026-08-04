#!/bin/bash
# macOS / Linux launcher. Double-click on a Mac (you may need to run
# `chmod +x start-sync-tool.command` once), or run ./start-sync-tool.command
cd "$(dirname "$0")" || exit 1

PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  echo "Python 3 not found."
  echo "  macOS:  brew install python3      (or install from python.org)"
  read -r -p "Press return to close."
  exit 1
fi

if ! "$PY" -c "import numpy" >/dev/null 2>&1; then
  echo "numpy is missing - installing it now..."
  "$PY" -m pip install --user numpy || {
    echo "Could not install numpy. Try: $PY -m pip install numpy"
    read -r -p "Press return to close."
    exit 1
  }
fi

echo "Starting Sync Tool..."
echo "Your browser should open at http://localhost:8765"
echo "Close this window (or press Ctrl+C) to stop."
echo
"$PY" server.py
status=$?
if [ $status -ne 0 ]; then
  echo
  echo "---------------------------------------------------------"
  echo "The server stopped unexpectedly. Common causes:"
  echo "  * ffmpeg not installed  ->  brew install ffmpeg"
  echo "  * port 8765 already in use"
  echo "---------------------------------------------------------"
  read -r -p "Press return to close."
fi
