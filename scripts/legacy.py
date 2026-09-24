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
    rest is still moved), so the remaining hooks keep working instead of failing on every event;
    likewise claude_speak_menubar.py stays if the old login item (handled first) can't be moved
  - a settings file that isn't a regular file, is over 4 MB or is too deeply nested to parse is
    listed as unreadable, never read in full: a cloned repo could point it at /dev/zero
  - a failure on one settings file or legacy file is reported and the rest carry on; even an
    unexpected error is reported (with what was already done) and exit status stays 0, because
    legacy cleanup is optional and must not abort the installer. Only Ctrl-C exits non-zero.
"""
import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import traceback

LEGACY_HOOK = re.compile(r"claude_speak(?:_menubar)?\.py")
LEGACY_PLIST_LABEL = "local.claude-speak-menubar"
HOME = os.path.expanduser("~")
DATA_DIR = os.path.expanduser(os.environ.get("CLAUDE_VOICEOVER_HOME", "~/.claude/voiceover"))
MAX_SETTINGS_BYTES = 4 << 20


def legacy_files():
    """Existing legacy files. The login item comes first: if it can't be moved, the script it
    runs (claude_speak_menubar.py) must stay."""
    files = [os.path.join(HOME, "Library", "LaunchAgents", f"{LEGACY_PLIST_LABEL}.plist")]
    hooks_dir = os.path.join(HOME, ".claude", "hooks")
    files += [os.path.join(hooks_dir, name) for name in (
        "claude_speak.py", "claude_speak_menubar.py", "claude_speak.json",
        "claude_speak_sessions.json", "claude_speak_sessions.json.lock")]
    speak_md = os.path.join(HOME, ".claude", "commands", "speak.md")
    if _mentions_legacy(speak_md):  # someone else's /speak command is left alone
        files.append(speak_md)
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
    other than a regular file holds that name (a planted link, a folder), or the rename fails,
    leave that name alone and keep the staged one (<file>.bak-voiceover.<random>)."""
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
    """(data, note). A leading BOM is ignored. note is set for a file that isn't valid UTF-8,
    parsed the way Claude Code does (bad bytes become U+FFFD) so its hooks are still seen, but
    not safe to rewrite. An empty file is data None. Raises OSError, ValueError (not a regular
    file, over MAX_SETTINGS_BYTES, bad JSON) or RecursionError (nested too deeply to parse)."""
    # O_NONBLOCK: opening a planted FIFO must not hang; the fstat below then rejects it
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as f:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("not a regular file")
        raw = f.read(MAX_SETTINGS_BYTES + 1)
    if len(raw) > MAX_SETTINGS_BYTES:
        raise ValueError(f"larger than {MAX_SETTINGS_BYTES >> 20} MB")
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
    moved = completed = False
    crash = None
    try:
        for path, editable in settings_files(args.project):
            try:
                data, note = read_settings(path)
            except (OSError, ValueError, RecursionError) as error:
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
                except (OSError, ValueError, RecursionError) as error:  # e.g. a lone surrogate can't be written
                    editable, label = False, label + f"; could not edit: {error}"
            (found if editable else manual).append(label + ")")

        login_item_stuck = False
        for path in legacy_files():
            name = os.path.basename(path)
            if manual and LEGACY_HOOK.fullmatch(name):
                kept.append(path)  # hooks we couldn't remove still run it; moving it would break them
                continue
            if login_item_stuck and name == "claude_speak_menubar.py":
                kept.append(f"{path} (the old login item still runs it)")
                continue
            if not args.remove:
                found.append(path)
                continue
            destination = os.path.join(backup_dir, name)
            try:
                if path.endswith(".plist"):
                    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LEGACY_PLIST_LABEL}"],
                                   capture_output=True)
                os.makedirs(DATA_DIR, mode=0o700, exist_ok=True)
                os.makedirs(backup_dir, mode=0o700, exist_ok=True)
                shutil.move(path, destination)
            except OSError as error:
                # a cross-device move copies before it deletes, so a copy may be left behind
                copy = f"; a copy is in {destination}" if os.path.lexists(destination) else ""
                failed.append(f"{path} (could not move: {error}{copy})")
                login_item_stuck = login_item_stuck or path.endswith(".plist")
                continue
            found.append(path)
            moved = True
        completed = True
    except Exception as error:  # optional cleanup: report it, but never abort the installer
        crash = f"{error.__class__.__name__}: {error}"
        traceback.print_exc()
    finally:  # even after an unexpected error or Ctrl-C, say what was already changed
        try:
            report(args.remove, backup_dir if moved else None, found, manual, kept, failed, unreadable,
                   completed, crash)
        except OSError:  # e.g. stdout is a closed pipe; the changes themselves are done
            _silence_stdout()


def _silence_stdout():
    """Point stdout at /dev/null so the exit-time flush can't fail again on a closed pipe."""
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        os.close(devnull)
    except (OSError, ValueError):
        pass


def report(removing, backup_dir, found, manual, kept, failed, unreadable, completed=True, crash=None):
    def section(title, items):
        if items:
            print(title)
            print("\n".join(f"  - {item}" for item in items))

    if not completed:
        done = "only what is listed below was done" if removing else "the list below may be incomplete"
        print(f"Legacy check stopped early ({crash or 'interrupted'}); {done}.")
    elif not (found or manual or kept or failed or unreadable):
        print("no legacy setup found")
    if removing:
        section(f"Removed legacy setup (files moved to {backup_dir}):" if backup_dir else "Removed legacy setup:",
                found)
    else:
        section("LEGACY SETUP FOUND - every response would be spoken twice. Re-run with --remove-legacy:", found)
    section("Settings not edited automatically (shared, symlinked, unwritable, not UTF-8 or failed to edit; "
            "remove the claude_speak hooks by hand):", manual)
    section(f"{'Left' if removing else 'Will be left'} in place because hooks or the old login item still run "
            "them (re-run once those are gone):", kept)
    section("Could not move (still in place; move or delete by hand):", failed)
    section("Could not read (check these by hand for claude_speak hooks):", unreadable)


if __name__ == "__main__":
    sys.exit(main())
