#!/bin/sh
# Hook entrypoint for every claude-voiceover event. Exits 0 no matter what:
# voice-over must never break a Claude Code session.

[ "${CLAUDE_VOICEOVER:-1}" = "0" ] && exit 0

DATA="${CLAUDE_VOICEOVER_HOME:-$HOME/.claude/voiceover}"
ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
PY="$DATA/venv/bin/python"

if [ ! -x "$PY" ]; then
  # Not set up yet: nothing to speak, so only a /voiceover command is worth starting
  # Python for (it answers "run /voiceover-setup", or runs setup itself).
  PAYLOAD="$(cat)"
  case "$PAYLOAD" in
    *'"UserPromptSubmit"'*voiceover*) ;;
    *) exit 0 ;;
  esac
  PY=""
  for candidate in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
    # On a Mac without Command Line Tools, /usr/bin/python3 is a stub that pops an install dialog.
    if [ "$candidate" = /usr/bin/python3 ] && [ "$(uname)" = Darwin ] && ! xcode-select -p >/dev/null 2>&1; then
      continue
    fi
    if [ -x "$candidate" ]; then PY="$candidate"; break; fi
  done
  [ -n "$PY" ] || PY="$(command -v python3 2>/dev/null)"
  [ -n "$PY" ] || exit 0
  printf '%s' "$PAYLOAD" | "$PY" "$ROOT/scripts/voiceover.py"
  exit 0
fi

"$PY" "$ROOT/scripts/voiceover.py"
exit 0
