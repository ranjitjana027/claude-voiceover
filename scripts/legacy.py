#!/usr/bin/env python3
"""Find (and optionally remove) the pre-plugin, hand-rolled version of this tool.

  legacy.py [--remove] [--project DIR]

Left in place, it speaks every response a second time. Removal is conservative:
  - only hooks whose command runs claude_speak.py / claude_speak_menubar.py are removed
  - files are moved into a timestamped backup folder, never deleted
  - every edited settings file is backed up next to itself first (replacing a backup from an
    earlier run) and rewritten atomically; a symlinked user settings file (dotfiles) is edited
    at its real path, keeping the link
  - a project's shared .claude/settings.json (usually committed) is only reported, never edited
  - so is a project settings.local.json that is not a plain file at <project>/.claude (it, or
    .claude itself, is a symlink): a cloned repo could point it at any file of the user's
  - so is any settings file we can't write (e.g. a dotfiles link into a read-only store)
  - temp and backup files are created fresh (O_EXCL/O_NOFOLLOW), never through a planted link
  - while any report-only file still runs claude_speak*.py, both scripts stay in place (the
    rest is still moved), so the remaining hooks keep working instead of failing on every event
  - a failure on one settings file is reported and the rest carry on; exit status stays 0,
    because legacy cleanup is optional and must not abort the installer
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
    """(path, editable) pairs. Editable entries give the resolved real path (the file that is
    read and rewritten); report-only entries keep the path as given, for display.

    User settings are the user's own, so a symlink there (dotfiles) is followed. A project's
    settings.local.json is editable only if its real path is exactly
    <realpath(project)>/.claude/settings.local.json; the shared settings.json is always report-only."""
    user = [(os.path.join(HOME, ".claude", n), True) for n in ("settings.json", "settings.local.json")]
    proj = []
    if project:
        claude_dir = os.path.join(os.path.realpath(project), ".claude")
        local = os.path.join(project, ".claude", "settings.local.json")
        proj = [(local, os.path.realpath(local) == os.path.join(claude_dir, "settings.local.json")),
                (os.path.join(project, ".claude", "settings.json"), False)]
    seen, result = set(), []
    for path, editable in user + proj:
        real = os.path.realpath(path)
        if real not in seen and os.path.exists(path):
            seen.add(real)
            result.append((real if editable else path, editable))
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
        if removed_here:  # only tidy events we changed; other events keep their structure
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
    """Copy path to path.bak-voiceover, or path.bak-voiceover.<random> if something other than
    a regular file holds that name; never writes through a symlink. Returns the backup path."""
    target = path + ".bak-voiceover"
    if os.path.isfile(target) and not os.path.islink(target):
        os.remove(target)  # normally our backup from an earlier run; replace it
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:  # something other than a regular file is there: don't touch it
        fd, target = tempfile.mkstemp(dir=os.path.dirname(path), prefix=os.path.basename(target) + ".")
    with open(path, "rb") as src, os.fdopen(fd, "wb") as dst:
        shutil.copyfileobj(src, dst)
    shutil.copymode(path, target)
    return target


def writable(path):
    return os.access(path, os.W_OK) and os.access(os.path.dirname(path), os.W_OK)


def remove_hooks(path, data):
    """Back up path, then rewrite it with data. Returns the backup path; on failure removes
    the new backup and re-raises, so the file is either fully updated or untouched."""
    saved = backup(path)
    try:
        write_json_atomic(path, data)
    except BaseException:
        try:
            os.remove(saved)
        except OSError:
            pass
        raise
    return saved


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--remove", action="store_true")
    parser.add_argument("--project", default=os.getcwd())
    args = parser.parse_args()

    backup_dir = os.path.join(DATA_DIR, "legacy-backup-" + time.strftime("%Y%m%d-%H%M%S"))
    found, manual, kept, unreadable, moved = [], [], [], [], False

    for path, editable in settings_files(args.project):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as error:  # ValueError: bad JSON or bad UTF-8
            unreadable.append(f"{path} ({error.__class__.__name__}: {error})")
            continue
        count = strip_legacy_hooks(data)
        if not count:
            continue
        label = f"{path} ({count} hook{'s' if count > 1 else ''}"
        if editable and not writable(path):
            editable, label = False, label + "; not writable"
        if editable and args.remove:
            try:
                label += f"; backup: {remove_hooks(path, data)}"
            except OSError as error:
                editable, label = False, label + f"; could not edit: {error}"
        (found if editable else manual).append(label + ")")

    for path in legacy_files():
        if manual and LEGACY_HOOK.fullmatch(os.path.basename(path)):
            kept.append(path)  # hooks we couldn't remove still run it; moving it would break them
            continue
        found.append(path)
        if args.remove:
            if path.endswith(".plist"):
                subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LEGACY_PLIST_LABEL}"],
                               capture_output=True)
            os.makedirs(DATA_DIR, mode=0o700, exist_ok=True)
            os.makedirs(backup_dir, mode=0o700, exist_ok=True)
            shutil.move(path, os.path.join(backup_dir, os.path.basename(path)))
            moved = True

    if not (found or manual or unreadable):
        print("none found")
        return
    if found:
        if args.remove:
            print(f"Removed legacy setup (files moved to {backup_dir}):" if moved else "Removed legacy setup:")
        else:
            print("LEGACY SETUP FOUND - every response would be spoken twice. Re-run with --remove-legacy:")
        print("\n".join(f"  - {item}" for item in found))
    if manual:
        print("Shared, symlinked or unwritable settings (not edited automatically; "
              "remove the claude_speak hooks by hand):")
        print("\n".join(f"  - {item}" for item in manual))
    if kept:
        print(f"{'Left' if args.remove else 'Will be left'} in place because those hooks still run them "
              "(re-run once the hooks are gone):")
        print("\n".join(f"  - {item}" for item in kept))
    if unreadable:
        print("Could not read (skipped; check these by hand for claude_speak hooks):")
        print("\n".join(f"  - {item}" for item in unreadable))


if __name__ == "__main__":
    sys.exit(main())
