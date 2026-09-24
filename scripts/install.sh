#!/bin/sh
# One-shot installer for claude-voiceover, safe to re-run.
#
#   scripts/install.sh [--source <owner/repo | path>] [--menubar] [--remove-legacy]
#
#   --source         marketplace source (default: ranjitjana027/claude-voiceover)
#   --menubar        also start the macOS menu bar app at every login (a LaunchAgent;
#                    endpoint security on managed Macs may flag this, so it is opt-in)
#   --remove-legacy  remove the pre-plugin hand-rolled setup (claude_speak*), which would
#                    otherwise speak every response a second time; files are moved to a
#                    backup folder and edited settings are backed up first
#
# Written for Claude Code to run on a user's behalf (see INSTALL.md), but works by hand too.
set -eu

SOURCE="ranjitjana027/claude-voiceover"
MENUBAR=0
REMOVE_LEGACY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --source) SOURCE="$2"; shift 2 ;;
    --menubar) MENUBAR=1; shift ;;
    --remove-legacy) REMOVE_LEGACY=1; shift ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

NAME="claude-voiceover"
PLUGIN="$NAME@$NAME"
export CLAUDE_VOICEOVER_HOME="${CLAUDE_VOICEOVER_HOME:-$HOME/.claude/voiceover}"
say() { printf '\n==> %s\n' "$*"; }
die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

say "Checking prerequisites"
case "$(uname)" in Darwin|Linux) ;; *) die "only macOS and Linux are supported" ;; esac
command -v claude >/dev/null 2>&1 || die "Claude Code CLI 'claude' not found on PATH"
PYTHON=""
for candidate in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3 python3; do
  # On a Mac without Command Line Tools, /usr/bin/python3 is a stub that pops an install dialog.
  if [ "$candidate" = /usr/bin/python3 ] && [ "$(uname)" = Darwin ] && ! xcode-select -p >/dev/null 2>&1; then
    continue
  fi
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then
    PYTHON="$candidate"; break
  fi
done
[ -n "$PYTHON" ] || die "Python 3.9+ not found (install python3, e.g. 'brew install python', then re-run)"
echo "claude: $(command -v claude)   python: $PYTHON ($("$PYTHON" -c 'import platform; print(platform.python_version())'))"

say "Marketplace"
if claude plugin marketplace list --json | NAME="$NAME" "$PYTHON" -c "import json, os, sys; sys.exit(0 if any(m.get('name') == os.environ['NAME'] for m in json.load(sys.stdin)) else 1)"; then
  claude plugin marketplace update "$NAME"
else
  claude plugin marketplace add "$SOURCE"
fi

say "Plugin (user scope, so every project gets it)"
installed_path() {
  claude plugin list --json | PLUGIN="$PLUGIN" "$PYTHON" -c "
import json, os, sys
paths = [p['installPath'] for p in json.load(sys.stdin) if p.get('id') == os.environ['PLUGIN'] and p.get('scope') == 'user']
print(paths[0] if paths else '')"
}
if [ -n "$(installed_path)" ]; then
  claude plugin update "$PLUGIN" || true   # no-op when already current
else
  claude plugin install "$PLUGIN" --scope user
fi
ROOT="$(installed_path)"
[ -n "$ROOT" ] && [ -f "$ROOT/scripts/voiceover.py" ] || die "installed plugin files not found (claude plugin list --json)"
echo "installed at $ROOT"

say "Speech engine setup (about a minute on first run)"
"$PYTHON" "$ROOT/scripts/setup.py"

if [ "$MENUBAR" = 1 ]; then
  say "Menu bar app at login"
  echo "Note: this registers a LaunchAgent. On managed Macs, endpoint security may flag that;"
  echo "      if it does, use /voiceover-menubar disable and start the app with /voiceover-menubar start."
  ROOT="$ROOT" "$CLAUDE_VOICEOVER_HOME/venv/bin/python" -c "
import os, sys; sys.path.insert(0, os.path.join(os.environ['ROOT'], 'scripts'))
import menubar_ctl; print(menubar_ctl.command('enable'))"
fi

say "Legacy setup check"
if [ "$REMOVE_LEGACY" = 1 ]; then
  "$PYTHON" "$ROOT/scripts/legacy.py" --remove --project "$PWD"
else
  "$PYTHON" "$ROOT/scripts/legacy.py" --project "$PWD"
fi

say "Done"
echo "Restart Claude Code, then control voice-over with:"
echo "  /voiceover on | off | status       (this session)"
echo "  /voiceover-menubar start           (macOS menu bar app, this login only)"
