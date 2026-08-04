#!/bin/bash
# Sync Tool - diagnostics.
#
# Double-click this (or run: bash Diagnose.command). It checks everything the
# app needs, prints the result here, and saves a copy to your Desktop as
# "SyncTool-diagnosis.txt" so it can be shared.

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" || exit 1
if [ -d "$HOME/Desktop" ]; then OUT="$HOME/Desktop/SyncTool-diagnosis.txt"
else OUT="$HOME/SyncTool-diagnosis.txt"; fi
( : > "$OUT" ) 2>/dev/null || OUT="/tmp/SyncTool-diagnosis.txt"

{
echo "Sync Tool diagnosis - $(date)"
echo "======================================================"
echo
echo "-- this Mac --"
echo "macOS      : $(sw_vers -productVersion 2>/dev/null) ($(uname -m))"
echo "folder     : $(pwd)"
echo "writable   : $( ( : > .synctool_write_test ) 2>/dev/null && { echo yes; rm -f .synctool_write_test; } || echo "NO - this is a problem" )"
echo "filesystem : $(df -T . 2>/dev/null | tail -1 | awk '{print $2}' || df . | tail -1 | awk '{print $1}')"
echo

echo "-- app files present --"
for f in server.py syncengine.py exporters.py ui.html resolve_template.py; do
  [ -f "$f" ] && echo "  ok      $f" || echo "  MISSING $f"
done
if [ -d SyncTool.app ]; then
  X="SyncTool.app/Contents/MacOS/SyncTool"
  [ -x "$X" ] && echo "  ok      SyncTool.app (executable)" \
              || echo "  PROBLEM SyncTool.app is not executable  ->  unzip SyncTool.app.zip instead"
  if grep -q $'\r' "$X" 2>/dev/null; then echo "  PROBLEM app has Windows line endings"; fi
  q=$(xattr "SyncTool.app" 2>/dev/null | grep -c quarantine)
  [ "$q" != "0" ] && echo "  note    app is quarantined -> right-click it and choose Open, once"
else
  echo "  MISSING SyncTool.app"
fi
echo

echo "-- python --"
FOUND=""; SEEN=""
for c in python3 /usr/bin/python3 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
  p="$(command -v "$c" 2>/dev/null || echo "$c")"
  p="$(cd "$(dirname "$p")" 2>/dev/null && pwd)/$(basename "$p")"
  case " $SEEN " in *" $p "*) continue;; esac
  if [ -x "$p" ]; then
    SEEN="$SEEN $p"
    echo "  $p  ->  $("$p" -V 2>&1)"
    [ -z "$FOUND" ] && "$p" -c 'import sys;exit(0 if sys.version_info>=(3,9) else 1)' 2>/dev/null && FOUND="$p"
  fi
done
[ -z "$FOUND" ] && echo "  PROBLEM no Python 3.9+ found  ->  run: xcode-select --install"
echo "  chosen  : ${FOUND:-none}"
echo

echo "-- numpy --"
if [ -x ".venv/bin/python" ]; then
  if .venv/bin/python -c "import numpy;print('  ok      .venv numpy',numpy.__version__)" 2>/dev/null; then :; else
    echo "  PROBLEM .venv exists but numpy is broken  ->  delete the .venv folder and reopen the app"
  fi
elif [ -n "$FOUND" ] && "$FOUND" -c "import numpy" 2>/dev/null; then
  echo "  ok      numpy in system python"
else
  echo "  missing (the app installs it on first run)"
fi
echo

echo "-- ffmpeg --"
if command -v ffmpeg >/dev/null 2>&1; then
  echo "  ok      $(command -v ffmpeg)"
  echo "          $(ffmpeg -version 2>/dev/null | head -1)"
else
  echo "  MISSING ffmpeg   ->  brew install ffmpeg"
fi
command -v ffprobe >/dev/null 2>&1 && echo "  ok      $(command -v ffprobe)" || echo "  MISSING ffprobe"
command -v brew >/dev/null 2>&1 && echo "  ok      homebrew at $(command -v brew)" || echo "  note    homebrew not installed (https://brew.sh)"
echo

echo "-- external drives (/Volumes) --"
if [ -d /Volumes ]; then
  for v in /Volumes/*; do
    [ -d "$v" ] || continue
    [ "$(readlink -f "$v" 2>/dev/null || echo "$v")" = "/" ] && continue
    n=$(ls -1 "$v" 2>/dev/null | wc -l | tr -d ' ')
    if [ "$n" = "0" ]; then
      echo "  BLOCKED $v  (macOS is withholding access - see below)"
    else
      echo "  ok      $v  ($n items)"
    fi
  done
  if ls -1 /Volumes/*/ >/dev/null 2>&1; then
    echo
    echo "  If a drive says BLOCKED, grant Full Disk Access:"
    echo "    System Settings > Privacy & Security > Full Disk Access"
    echo "    turn on Terminal (and SyncTool if listed), then reopen Sync Tool."
  fi
else
  echo "  no /Volumes on this system"
fi
echo

echo "-- port 8765 --"
if command -v lsof >/dev/null 2>&1 && lsof -ti tcp:8765 >/dev/null 2>&1; then
  echo "  IN USE by pid(s): $(lsof -ti tcp:8765 | tr '\n' ' ')"
  echo "  -> close the other Sync Tool window, or run: kill $(lsof -ti tcp:8765 | head -1)"
else
  echo "  free"
fi
echo

echo "-- can the engine start? --"
if [ -n "$FOUND" ]; then
  PYRUN="$FOUND"; [ -x ".venv/bin/python" ] && PYRUN=".venv/bin/python"
  ERR="$("$PYRUN" -c "import sys;sys.path.insert(0,'.');import server" 2>&1 | tail -5)"
  if [ -z "$ERR" ]; then echo "  ok      server imports cleanly"; else
    echo "  PROBLEM server failed to load:"; echo "$ERR" | sed 's/^/          /'
  fi
fi
echo

echo "-- last launch log --"
for L in SyncTool-log.txt "$HOME/Desktop/SyncTool-log.txt" /tmp/SyncTool-log.txt .synctool-launch.log; do
  if [ -f "$L" ]; then echo "  ($L)"; tail -25 "$L" | sed 's/^/    /'; break; fi
done
echo
echo "======================================================"
} 2>&1 | tee "$OUT"

echo
echo "Saved to: $OUT"
echo "Press return to close."
read -r _
