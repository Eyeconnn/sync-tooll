#!/bin/bash
# One-time macOS setup. Run this once, then Sync Tool is a normal double-click app.
#
#   bash mac-setup.sh
#
# (Note the "bash" - that way it runs even though the file isn't executable yet,
# which is exactly the problem this script exists to fix.)

set -u
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" || exit 1

BOLD=$'\033[1m'; GREEN=$'\033[32m'; YEL=$'\033[33m'; RED=$'\033[31m'; OFF=$'\033[0m'
ok(){ echo "  ${GREEN}✓${OFF} $1"; }
warn(){ echo "  ${YEL}!${OFF} $1"; }
bad(){ echo "  ${RED}✗${OFF} $1"; }

echo
echo "${BOLD}Sync Tool - macOS setup${OFF}"
echo "-------------------------------------------"

# 1 - permissions ------------------------------------------------------------
chmod +x "SyncTool.app/Contents/MacOS/SyncTool" 2>/dev/null \
  && ok "made the app launchable" \
  || bad "could not set permissions on SyncTool.app"
chmod +x start-sync-tool.command 2>/dev/null && ok "made the Terminal launcher runnable"

# 2 - Gatekeeper -------------------------------------------------------------
# Files copied from another machine or unzipped from a download carry a
# quarantine flag; clearing it stops the "unidentified developer" block.
if xattr -dr com.apple.quarantine "SyncTool.app" 2>/dev/null; then
  ok "cleared the macOS quarantine flag"
else
  warn "no quarantine flag to clear (fine)"
fi

# 3 - line endings -----------------------------------------------------------
# A checkout made on Windows can arrive with CRLF, which macOS cannot run.
if grep -q $'\r' "SyncTool.app/Contents/MacOS/SyncTool" 2>/dev/null; then
  for f in "SyncTool.app/Contents/MacOS/SyncTool" start-sync-tool.command mac-setup.sh; do
    [ -f "$f" ] && perl -pi -e 's/\r$//' "$f" 2>/dev/null
  done
  ok "fixed Windows line endings"
else
  ok "line endings are correct"
fi

# 4 - python -----------------------------------------------------------------
PY=""
for c in python3 python3.13 python3.12 python3.11 python3.10; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys;exit(0 if sys.version_info>=(3,9) else 1)' 2>/dev/null; then
    PY="$(command -v "$c")"; break
  fi
done
if [ -n "$PY" ]; then
  ok "found Python $("$PY" -c 'import sys;print(".".join(map(str,sys.version_info[:3])))')"
else
  bad "Python 3.9+ not found"
  echo "     Installing Apple's command line tools (a system dialog will appear)…"
  xcode-select --install 2>/dev/null
  echo "     When that finishes, run this script again."
  exit 1
fi

# 5 - numpy in a local venv --------------------------------------------------
if [ -x ".venv/bin/python" ] && .venv/bin/python -c "import numpy" >/dev/null 2>&1; then
  ok "python environment already set up"
elif "$PY" -c "import numpy" >/dev/null 2>&1; then
  ok "numpy already available"
else
  echo "  … creating a local Python environment (this takes a minute)"
  if "$PY" -m venv .venv >/dev/null 2>&1 && .venv/bin/pip install --upgrade pip >/dev/null 2>&1 \
     && .venv/bin/pip install numpy >/tmp/synctool_setup.log 2>&1; then
    ok "installed numpy into .venv"
  else
    bad "numpy install failed - see /tmp/synctool_setup.log"
  fi
fi

# 6 - ffmpeg -----------------------------------------------------------------
export PATH="/opt/homebrew/bin:/usr/local/bin:/opt/local/bin:$PATH"
if command -v ffmpeg >/dev/null 2>&1 && command -v ffprobe >/dev/null 2>&1; then
  ok "found $(ffmpeg -version 2>/dev/null | head -1 | cut -c1-40)"
elif command -v brew >/dev/null 2>&1; then
  echo "  … installing ffmpeg with Homebrew (a few minutes)"
  if brew install ffmpeg >/tmp/synctool_ffmpeg.log 2>&1; then
    ok "installed ffmpeg"
  else
    bad "ffmpeg install failed - see /tmp/synctool_ffmpeg.log"
  fi
else
  warn "ffmpeg and Homebrew are both missing"
  echo "     Install Homebrew first (one line, from https://brew.sh):"
  echo
  echo '     /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
  echo
  echo "     then run this script again. (Sync Tool can also install ffmpeg"
  echo "     for you from its own window once Homebrew exists.)"
fi

echo "-------------------------------------------"
echo "${BOLD}Done.${OFF} Double-click ${BOLD}SyncTool.app${OFF} whenever you want to use it."
echo
