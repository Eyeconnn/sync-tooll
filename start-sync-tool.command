#!/bin/bash
# macOS / Linux launcher for Sync Tool.
#
# First time on a Mac:
#   chmod +x start-sync-tool.command
# then double-click. If macOS blocks it, right-click the file and choose Open.

cd "$(dirname "$0")" || exit 1

die() { echo; echo "$1"; echo; read -r -p "Press return to close."; exit 1; }

# ---- find python 3 ----------------------------------------------------------
PY=""
for c in python3 python3.12 python3.11 python3.10 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys;exit(0 if sys.version_info>=(3,9) else 1)' 2>/dev/null; then
    PY="$(command -v "$c")"; break
  fi
done
[ -z "$PY" ] && die "Python 3.9+ not found.
  macOS:  brew install python3     (Homebrew: https://brew.sh)
          or install from https://www.python.org/downloads/"

# ---- make sure numpy is importable -----------------------------------------
# Modern Python installs are 'externally managed' (PEP 668) and refuse a plain
# pip install. If that happens we fall back to a local virtual environment,
# which needs no admin rights and touches nothing outside this folder.
if ! "$PY" -c "import numpy" >/dev/null 2>&1; then
  echo "numpy is missing - installing it..."
  if ! "$PY" -m pip install --user numpy >/tmp/synctool_pip.log 2>&1; then
    echo "System install refused (this is normal on newer Python)."
    echo "Setting up a local environment in .venv instead..."
    "$PY" -m venv .venv >/dev/null 2>&1 || die "Could not create .venv.
  Try:  $PY -m pip install --break-system-packages numpy"
    VENV_PY="$PWD/.venv/bin/python"
    "$VENV_PY" -m pip install --upgrade pip >/dev/null 2>&1
    "$VENV_PY" -m pip install numpy >/tmp/synctool_pip.log 2>&1 \
      || die "numpy install failed. Last lines:
$(tail -5 /tmp/synctool_pip.log)"
    PY="$VENV_PY"
  fi
  echo "numpy ready."
fi
# reuse the venv on later runs
[ -x "$PWD/.venv/bin/python" ] && "$PWD/.venv/bin/python" -c "import numpy" >/dev/null 2>&1 \
  && PY="$PWD/.venv/bin/python"

# ---- ffmpeg check (the app can also install it for you) ---------------------
if ! command -v ffmpeg >/dev/null 2>&1 \
   && [ ! -x /opt/homebrew/bin/ffmpeg ] && [ ! -x /usr/local/bin/ffmpeg ]; then
  echo
  echo "Note: ffmpeg was not found. The app will offer to install it,"
  echo "or you can run:  brew install ffmpeg"
  echo
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
  echo "  * ffmpeg not installed   ->  brew install ffmpeg"
  echo "  * port 8765 already in use"
  echo "---------------------------------------------------------"
  read -r -p "Press return to close."
fi
