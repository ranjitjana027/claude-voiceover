#!/bin/sh
# Hook entrypoint for every claude-voiceover event.
# Uses the private virtualenv once /voiceover-setup has created it; before that, any
# system python3 is enough to handle /voiceover-setup itself (standard library only).
# Exits 0 no matter what: voice-over must never break a Claude Code session.

DATA="${CLAUDE_VOICEOVER_HOME:-$HOME/.claude/voiceover}"
ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"

PY="$DATA/venv/bin/python"
if [ ! -x "$PY" ]; then
  PY=""
  for candidate in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then PY="$candidate"; break; fi
  done
  [ -n "$PY" ] || exit 0
fi

# Keep a copy of the scripts in the data dir for the menu bar login item: plugin updates
# move the plugin dir, and launchd agents can't read TCC-protected dirs like ~/Documents.
mkdir -p "$DATA/app"
if [ "$(cat "$DATA/plugin_root" 2>/dev/null)" != "$ROOT" ] || [ ! -f "$DATA/app/menubar.py" ]; then
  cp "$ROOT"/scripts/*.py "$DATA/app/" 2>/dev/null && printf '%s' "$ROOT" > "$DATA/plugin_root"
fi

"$PY" "$ROOT/scripts/voiceover.py"
exit 0
