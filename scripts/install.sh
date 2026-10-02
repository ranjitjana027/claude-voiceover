#!/bin/sh
# One-shot installer for agent-voiceover, safe to re-run.
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

MARKETPLACE="claude-voiceover"
PLUGIN="agent-voiceover@$MARKETPLACE"
OLD_PLUGIN="claude-voiceover@$MARKETPLACE"  # the plugin's id before it was renamed
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
if claude plugin marketplace list --json | NAME="$MARKETPLACE" "$PYTHON" -c "import json, os, sys; sys.exit(0 if any(m.get('name') == os.environ['NAME'] for m in json.load(sys.stdin)) else 1)"; then
  claude plugin marketplace update "$MARKETPLACE"
else
  claude plugin marketplace add "$SOURCE"
fi

say "Plugin (user scope, so every project gets it)"
installed_scopes() {  # one scope per line where plugin $1 is installed; fails if the CLI does
  claude plugin list --json 2>/dev/null | PLUGIN="$1" "$PYTHON" -c "
import json, os, sys
print('\n'.join(p.get('scope', '') for p in json.load(sys.stdin) if p.get('id') == os.environ['PLUGIN']))"
}
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
# Removed only once the new id is in place. Left installed, the old id would run its hooks
# next to the new one and speak every response twice.
# (A plain assignment keeps the lookup's exit status; a for-loop over $(...) would drop it.)
if ! OLD_SCOPES="$(installed_scopes "$OLD_PLUGIN" 2>/dev/null)"; then
  echo "WARNING: could not check for $OLD_PLUGIN (claude plugin list --json failed); if /plugin"
  echo "         still lists it, uninstall it or every response is spoken twice"
  OLD_SCOPES=""
fi
for scope in $OLD_SCOPES; do
  if [ "$scope" = user ]; then
    echo "removing $OLD_PLUGIN, the plugin's previous name"
    claude plugin uninstall "$OLD_PLUGIN" --scope user ||
      echo "WARNING: could not remove it; run: claude plugin uninstall $OLD_PLUGIN"
  else
    echo "WARNING: $OLD_PLUGIN is also installed at $scope scope; remove it there with"
    echo "         claude plugin uninstall $OLD_PLUGIN --scope $scope   (or responses are spoken twice)"
  fi
done

say "Speech engine setup (about a minute on first run)"
"$PYTHON" "$ROOT/scripts/setup.py"

# An existing login item gets a fresh copy too: an old copy can mistake the renamed plugin for
# an uninstalled one and remove itself.
LOGIN_ITEM="$HOME/Library/LaunchAgents/local.claude-voiceover.menubar.plist"
if [ "$MENUBAR" = 1 ] || [ -f "$LOGIN_ITEM" ]; then
  say "Menu bar app at login"
  echo "Note: this registers a LaunchAgent. On managed Macs, endpoint security may flag that;"
  echo "      if it does, use /voiceover-menubar disable and start the app with /voiceover-menubar start."
  # The plugin is installed either way, so a failure here is a warning, not an abort.
  ROOT="$ROOT" "$CLAUDE_VOICEOVER_HOME/venv/bin/python" -c "
import os, sys; sys.path.insert(0, os.path.join(os.environ['ROOT'], 'scripts'))
import menubar_ctl; print(menubar_ctl.command('enable')); sys.exit(0 if os.path.exists(menubar_ctl.PLIST_PATH) else 1)" ||
    echo "WARNING: the menu bar login item is not registered; run /voiceover-menubar enable to retry"
fi

say "Legacy setup check"
# Optional cleanup: if it fails (only Ctrl-C should), the plugin itself is still installed.
if [ "$REMOVE_LEGACY" = 1 ]; then
  "$PYTHON" "$ROOT/scripts/legacy.py" --remove --project "$PWD" || echo "WARNING: legacy check did not finish (see above)"
else
  "$PYTHON" "$ROOT/scripts/legacy.py" --project "$PWD" || echo "WARNING: legacy check did not finish (see above)"
fi

say "Done"
echo "Restart Claude Code, then control voice-over with:"
echo "  /voiceover on | off | status       (this session)"
echo "  /voiceover-menubar start           (macOS menu bar app, this login only)"
