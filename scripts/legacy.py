#!/usr/bin/env python3
"""Find (and optionally remove) the pre-plugin, hand-rolled version of this tool.

  legacy.py [--remove] [--project DIR]

Left in place, it speaks every response a second time. Removal is conservative:
  - only hooks whose command runs claude_speak.py / claude_speak_menubar.py are removed
  - files are moved into a timestamped backup folder, never deleted
  - every edited settings file is copied next to itself first and rewritten atomically; the copy
    becomes <file>.bak-voiceover (replacing an earlier run's) only once the rewrite succeeded,
    so a failure never costs the previous backup; a symlinked user settings file (dotfiles) is
    edited at its real path, keeping the link
  - a project's shared .claude/settings.json (usually committed) is only reported, never edited
  - so is a project settings.local.json that is not a plain file at <project>/.claude (it, or
    .claude itself, is a symlink): a cloned repo could point it at any file of the user's
  - so is any settings file we can't write (e.g. a dotfiles link into a read-only store), and
    one that isn't valid UTF-8: Claude Code still loads it (bad bytes become U+FFFD), but
    rewriting it would destroy those bytes
  - temp and backup files are created fresh (O_EXCL/O_NOFOLLOW), never through a planted link
  - while any report-only file still runs claude_speak*.py, both scripts stay in place (the
    rest is still moved), so the remaining hooks keep working instead of failing on every event
  - a failure on one settings file or legacy file is reported and the rest carry on; the
    summary always prints and exit status stays 0, because legacy cleanup is optional and must
    not abort the installer
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
    """Copy path to a fresh <file>.bak-voiceover.<random> next to it (O_EXCL, never through a
    symlink) and return that name; removes the partial copy if copying fails."""
    fd, staged = tempfile.mkstemp(dir=os.path.dirname(path), prefix=os.path.basename(path) + ".bak-voiceover.")
    try:
        with open(path, "rb") as src, os.fdopen(fd, "wb") as dst:
            shutil.copyfileobj(src, dst)
        shutil.copymode(path, staged)
    except BaseException:
        _remove_quietly(staged)
        raise
    return staged


def promote_backup(staged, path):
    """Rename the staged copy to path.bak-voiceover and return the final name. If something
    other than a regular file holds that name (a planted link, a folder), leave it alone and
    keep the staged name."""
    target = path + ".bak-voiceover"
    if os.path.lexists(target) and not (os.path.isfile(target) and not os.path.islink(target)):
        return staged
    try:
        os.replace(staged, target)  # rename replaces a link itself, never what it points at
    except OSError:
        return staged
    return target


def _remove_quietly(path):
    try:
        os.remove(path)
    except OSError:
        pass


def writable(path):
    return os.access(path, os.W_OK) and os.access(os.path.dirname(path), os.W_OK)


def read_settings(path):
    """(data, note). note is set for a file that isn't valid UTF-8, parsed the way Claude Code
    does (bad bytes become U+FFFD) so its hooks are still seen, but not safe to rewrite.
    An empty file is data None. Raises OSError / ValueError when it can't be parsed at all."""
    with open(path, "rb") as f:
        raw = f.read()
    if not raw.strip():
        return None, None
    try:
        return json.loads(raw.decode("utf-8-sig")), None
    except UnicodeDecodeError:
        return json.loads(raw.decode("utf-8-sig", errors="replace")), "not valid UTF-8"


def remove_hooks(path, data):
    """Back up path, then rewrite it with data (already stripped). Returns the backup's path.
    On failure the new copy is removed and the error re-raised: the settings file and any
    earlier backup are untouched."""
    staged = backup(path)
    try:
        write_json_atomic(path, data)
    except BaseException:
        _remove_quietly(staged)
        raise
    return promote_backup(staged, path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--remove", action="store_true")
    parser.add_argument("--project", default=os.getcwd())
    args = parser.parse_args()

    backup_dir = os.path.join(DATA_DIR, "legacy-backup-" + time.strftime("%Y%m%d-%H%M%S"))
    found, manual, kept, unreadable, failed = [], [], [], [], []
    moved = False
    try:
        for path, editable in settings_files(args.project):
            try:
                data, note = read_settings(path)
            except (OSError, ValueError) as error:  # Claude Code can't load it either
                unreadable.append(f"{path} ({error.__class__.__name__}: {error})")
                continue
            count = strip_legacy_hooks(data)
            if not count:
                continue
            label = f"{path} ({count} hook{'s' if count > 1 else ''}"
            if editable and note:
                editable, label = False, label + f"; {note}"
            if editable and not writable(path):
                editable, label = False, label + "; not writable"
            if editable and args.remove:
                try:
                    label += f"; backup: {remove_hooks(path, data)}"
                except (OSError, ValueError) as error:  # ValueError: e.g. a lone surrogate can't be written
                    editable, label = False, label + f"; could not edit: {error}"
            (found if editable else manual).append(label + ")")

        for path in legacy_files():
            if manual and LEGACY_HOOK.fullmatch(os.path.basename(path)):
                kept.append(path)  # hooks we couldn't remove still run it; moving it would break them
                continue
            if not args.remove:
                found.append(path)
                continue
            try:
                if path.endswith(".plist"):
                    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LEGACY_PLIST_LABEL}"],
                                   capture_output=True)
                os.makedirs(DATA_DIR, mode=0o700, exist_ok=True)
                os.makedirs(backup_dir, mode=0o700, exist_ok=True)
                shutil.move(path, os.path.join(backup_dir, os.path.basename(path)))
            except OSError as error:
                failed.append(f"{path} (could not move: {error})")
                continue
            found.append(path)
            moved = True
    finally:  # even after an unexpected error, say what was already changed
        report(args.remove, backup_dir if moved else None, found, manual, kept, failed, unreadable)


def report(removing, backup_dir, found, manual, kept, failed, unreadable):
    def section(title, items):
        if items:
            print(title)
            print("\n".join(f"  - {item}" for item in items))

    if not (found or manual or kept or failed):
        print("none found")
    elif removing:
        section(f"Removed legacy setup (files moved to {backup_dir}):" if backup_dir else "Removed legacy setup:",
                found)
    else:
        section("LEGACY SETUP FOUND - every response would be spoken twice. Re-run with --remove-legacy:", found)
    section("Shared, symlinked or unwritable settings (not edited automatically; "
            "remove the claude_speak hooks by hand):", manual)
    section(f"{'Left' if removing else 'Will be left'} in place because those hooks still run them "
            "(re-run once the hooks are gone):", kept)
    section("Could not move (still in place; move or delete by hand):", failed)
    section("Could not read (Claude Code can't load these either; fix or delete them):", unreadable)


if __name__ == "__main__":
    sys.exit(main())
