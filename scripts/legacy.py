#!/usr/bin/env python3
"""Find (and optionally remove) the pre-plugin, hand-rolled version of this tool.

  legacy.py [--remove] [--project DIR]

Left in place, it speaks every response a second time. Removal is conservative:
  - only hooks whose command runs claude_speak.py / claude_speak_menubar.py are removed
  - files are moved into a timestamped backup folder, never deleted
  - every edited settings file is backed up first and rewritten atomically
  - a project's shared .claude/settings.json (usually committed) is only reported, never edited
  - so is any settings file that is a symlink, and temp/backup files never follow symlinks,
    so a cloned repo can't aim these writes at another file
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

LEGACY_HOOK = re.compile(r"claude_speak(?:_menubar)?\.py")
LEGACY_PLIST_LABEL = "local.claude-speak-menubar"
HOME = os.path.expanduser("~")
DATA_DIR = os.path.expanduser(os.environ.get("CLAUDE_VOICEOVER_HOME", "~/.claude/voiceover"))


def legacy_files():
    hooks_dir = os.path.join(HOME, ".claude", "hooks")
    files = [os.path.join(hooks_dir, name) for name in (
        "claude_speak.py", "claude_speak_menubar.py", "claude_speak.json",
        "claude_speak_sessions.json", "claude_speak_sessions.json.lock")]
    speak_md = os.path.join(HOME, ".claude", "commands", "speak.md")
    if _mentions_legacy(speak_md):  # someone else's /speak command is left alone
        files.append(speak_md)
    files.append(os.path.join(HOME, "Library", "LaunchAgents", f"{LEGACY_PLIST_LABEL}.plist"))
    return [path for path in files if os.path.exists(path)]


def _mentions_legacy(path):
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return False
    return "claude_speak" in text or "CLAUDE_SPEAK_COMMAND" in text


def settings_files(project):
    """(path, editable) pairs; the shared project settings.json and any symlink are report-only.

    A symlinked settings file (e.g. planted by a cloned repo) could point at any file of the
    user's, so it is never rewritten or backed up."""
    user = [(os.path.join(HOME, ".claude", n), True) for n in ("settings.json", "settings.local.json")]
    proj = [(os.path.join(project, ".claude", "settings.local.json"), True),
            (os.path.join(project, ".claude", "settings.json"), False)] if project else []
    seen, result = set(), []
    for path, editable in user + proj:
        real = os.path.realpath(path)
        if real not in seen and os.path.exists(path):
            seen.add(real)
            result.append((path, editable and not os.path.islink(path)))
    return result


def strip_legacy_hooks(data):
    """Remove legacy hook handlers in place; drop groups/events left empty. Returns count removed."""
    hooks = data.get("hooks") if isinstance(data, dict) else None
    if not isinstance(hooks, dict):
        return 0
    removed = 0
    for event in list(hooks):
        groups = hooks[event] if isinstance(hooks[event], list) else []
        removed_here = 0
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                continue
            kept = [h for h in group["hooks"]
                    if not (isinstance(h, dict) and LEGACY_HOOK.search(str(h.get("command", ""))))]
            removed_here += len(group["hooks"]) - len(kept)
            group["hooks"] = kept
        if removed_here:  # only tidy events we changed; leave everything else byte-for-byte
            hooks[event] = [g for g in groups if not (isinstance(g, dict) and g.get("hooks") == [])]
            if not hooks[event]:
                del hooks[event]
        removed += removed_here
    if removed and not hooks:
        del data["hooks"]
    return removed


def write_json_atomic(path, data):
    # mkstemp creates a fresh file (O_EXCL), so a planted symlink at a predictable temp
    # name can't redirect the write to some other file.
    fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".voiceover-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        shutil.copymode(path, tmp_path)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def backup(path):
    """Copy path to path.bak-voiceover without following a symlink planted at that name."""
    target = path + ".bak-voiceover"
    if os.path.isfile(target) and not os.path.islink(target):
        os.remove(target)  # our own backup from an earlier run
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:  # something other than a regular file is there: don't touch it
        fd, target = tempfile.mkstemp(dir=os.path.dirname(path), prefix=os.path.basename(target) + ".")
    with open(path, "rb") as src, os.fdopen(fd, "wb") as dst:
        shutil.copyfileobj(src, dst)
    shutil.copymode(path, target)
    return target


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--remove", action="store_true")
    parser.add_argument("--project", default=os.getcwd())
    args = parser.parse_args()

    backup_dir = os.path.join(DATA_DIR, "legacy-backup-" + time.strftime("%Y%m%d-%H%M%S"))
    found, manual = [], []

    for path in legacy_files():
        found.append(path)
        if args.remove:
            if path.endswith(".plist"):
                subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LEGACY_PLIST_LABEL}"],
                               capture_output=True)
            os.makedirs(DATA_DIR, mode=0o700, exist_ok=True)
            os.makedirs(backup_dir, mode=0o700, exist_ok=True)
            shutil.move(path, os.path.join(backup_dir, os.path.basename(path)))

    for path, editable in settings_files(args.project):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        count = strip_legacy_hooks(data)
        if not count:
            continue
        label = f"{path} ({count} hook{'s' if count > 1 else ''})"
        if not editable:
            manual.append(label)
            continue
        found.append(label)
        if args.remove:
            backup(path)
            write_json_atomic(path, data)

    if not found and not manual:
        print("none found")
        return
    if found:
        if args.remove:
            print(f"Removed legacy setup (files moved to {backup_dir}; settings backed up as *.bak-voiceover):")
        else:
            print("LEGACY SETUP FOUND - every response would be spoken twice. Re-run with --remove-legacy:")
        print("\n".join(f"  - {item}" for item in found))
    if manual:
        print("Shared project settings or symlinks (not edited automatically; remove the claude_speak hooks by hand):")
        print("\n".join(f"  - {item}" for item in manual))


if __name__ == "__main__":
    sys.exit(main())
