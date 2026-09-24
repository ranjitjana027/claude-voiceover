#!/bin/sh
# One-shot installer for claude-voiceover, safe to re-run.
#
#   scripts/install.sh [--source <owner/repo | path>] [--menubar] [--remove-legacy]
#
#   --source         marketplace source (default: ranjitjana027/claude-voiceover)
#   --menubar        also start the macOS menu bar app at every login
#   --remove-legacy  remove the pre-plugin hand-rolled setup (claude_speak*), which would
#                    otherwise speak every response a second time; settings files are backed up
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
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

NAME="claude-voiceover"
PLUGIN="$NAME@$NAME"
DATA="${CLAUDE_VOICEOVER_HOME:-$HOME/.claude/voiceover}"
say() { printf '\n==> %s\n' "$*"; }
die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

say "Checking prerequisites"
command -v claude >/dev/null 2>&1 || die "Claude Code CLI 'claude' not found on PATH"
PYTHON=""
for candidate in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3 python3; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then
    PYTHON="$candidate"; break
  fi
done
[ -n "$PYTHON" ] || die "Python 3.9+ not found (install python3, then re-run)"
echo "claude: $(command -v claude)   python: $PYTHON ($("$PYTHON" -c 'import platform; print(platform.python_version())'))"

say "Marketplace"
if claude plugin marketplace list --json | "$PYTHON" -c "import json,sys; sys.exit(0 if any(m.get('name') == '$NAME' for m in json.load(sys.stdin)) else 1)"; then
  claude plugin marketplace update "$NAME"
else
  claude plugin marketplace add "$SOURCE"
fi

say "Plugin (user scope, so every project gets it)"
if claude plugin list --json | "$PYTHON" -c "import json,sys; sys.exit(0 if any(p.get('id') == '$PLUGIN' and p.get('scope') == 'user' for p in json.load(sys.stdin)) else 1)"; then
  echo "$PLUGIN already installed"
else
  claude plugin install "$PLUGIN" --scope user
fi
ROOT="$(claude plugin list --json | "$PYTHON" -c "
import json, sys
paths = [p['installPath'] for p in json.load(sys.stdin) if p.get('id') == '$PLUGIN' and p.get('scope') == 'user']
print(paths[0] if paths else '')")"
[ -n "$ROOT" ] && [ -f "$ROOT/scripts/voiceover.py" ] || die "installed plugin not found (claude plugin list --json)"
echo "installed at $ROOT"

say "Speech engine setup (about a minute on first run)"
CLAUDE_VOICEOVER_HOME="$DATA" "$PYTHON" "$ROOT/scripts/setup.py"
# Prime the copy of the scripts the menu bar login item runs (the hook refreshes it later).
printf '{"hook_event_name":"Install"}' | CLAUDE_PLUGIN_ROOT="$ROOT" CLAUDE_VOICEOVER_HOME="$DATA" "$ROOT/scripts/run.sh"

if [ "$MENUBAR" = 1 ]; then
  say "Menu bar app"
  CLAUDE_VOICEOVER_HOME="$DATA" "$DATA/venv/bin/python" -c "
import sys; sys.path.insert(0, '$ROOT/scripts')
import menubar_ctl; print(menubar_ctl.command('enable'))"
fi

say "Legacy setup check"
REMOVE_LEGACY="$REMOVE_LEGACY" PROJECT_DIR="$PWD" "$PYTHON" - <<'EOF'
import json, os, shutil, subprocess

home = os.path.expanduser("~")
remove = os.environ["REMOVE_LEGACY"] == "1"
files = [os.path.join(home, p) for p in (
    ".claude/hooks/claude_speak.py", ".claude/hooks/claude_speak_menubar.py",
    ".claude/hooks/claude_speak.json", ".claude/hooks/claude_speak_sessions.json",
    ".claude/hooks/claude_speak_sessions.json.lock", ".claude/commands/speak.md")]
plist = os.path.join(home, "Library/LaunchAgents/local.claude-speak-menubar.plist")
settings = [os.path.join(home, ".claude", n) for n in ("settings.json", "settings.local.json")] + \
           [os.path.join(os.environ["PROJECT_DIR"], ".claude", n) for n in ("settings.json", "settings.local.json")]

def is_legacy(hook):
    command = hook.get("command", "") if isinstance(hook, dict) else ""
    return "claude_speak" in command

def strip_legacy(data):
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return 0
    removed = 0
    for event in list(hooks):
        groups = hooks[event] if isinstance(hooks[event], list) else []
        for group in groups:
            inner = group.get("hooks", []) if isinstance(group, dict) else []
            kept = [h for h in inner if not is_legacy(h)]
            removed += len(inner) - len(kept)
            if isinstance(group, dict):
                group["hooks"] = kept
        hooks[event] = [g for g in groups if not (isinstance(g, dict) and not g.get("hooks"))]
        if not hooks[event]:
            del hooks[event]
    if not hooks:
        del data["hooks"]
    return removed

found = []
for path in files + [plist]:
    if os.path.exists(path):
        found.append(path)
        if remove:
            if path == plist:
                subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/local.claude-speak-menubar"],
                               capture_output=True)
            os.remove(path)
for path in dict.fromkeys(settings):  # de-dupe when run from $HOME
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        continue
    count = strip_legacy(data)
    if not count:
        continue
    found.append(f"{path} ({count} hook{'s' if count > 1 else ''})")
    if remove:
        shutil.copy2(path, path + ".bak-voiceover")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")

if not found:
    print("none found")
elif remove:
    print("removed legacy setup (settings backed up as *.bak-voiceover):")
    print("\n".join(f"  - {item}" for item in found))
else:
    print("LEGACY SETUP FOUND - every response would be spoken twice. Re-run with --remove-legacy:")
    print("\n".join(f"  - {item}" for item in found))
EOF

say "Done"
echo "Restart Claude Code, then control voice-over with:"
echo "  /voiceover on | off | status       (this session)"
echo "  /voiceover-menubar enable          (macOS menu bar app at login)"
