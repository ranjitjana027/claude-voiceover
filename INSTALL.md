# Installing agent-voiceover (guide for Claude Code)

You are Claude Code, and the user has asked you to install this plugin. Follow these steps in order.
Everything goes through `scripts/install.sh`, which is idempotent, so re-running it is always safe.
Don't use the `/plugin` slash commands: the CLI equivalents (`claude plugin …`) inside the script work in every
environment, including ones where `/plugin` is unavailable.

## 1. Ask one question up front

Ask the user (a single question) whether the **menu bar app should start at login**. It's macOS only; skip the
question on other platforms. Their answer decides whether you pass `--menubar`. Say in the question that this
registers a LaunchAgent, and that on a **company-managed Mac** (with endpoint security such as SentinelOne,
CrowdStrike or Jamf Protect) the recommended answer is **no**: security tools can flag a script-registered login
item as persistence. They can still run the app any time with `/voiceover-menubar start`.

## 2. Get the installer

Use an existing local clone if the user has one in their own folders (for example `~/Documents/claude-voiceover`).
Otherwise, clone it into a fresh private temp directory with this single command. It falls back to `gh` if
`git clone` fails, and prints the clone path only on success:

```bash
TMP="$(mktemp -d)" && CLONE="$TMP/claude-voiceover" &&
{ git clone --depth 1 https://github.com/ranjitjana027/claude-voiceover "$CLONE" ||
  gh repo clone ranjitjana027/claude-voiceover "$CLONE" -- --depth 1; } && echo "$CLONE" ||
  { [ -n "$TMP" ] && rm -rf "$TMP"; echo "clone failed" >&2; false; }
```

Shell variables may not survive between your commands, so use the printed path as `<clone>` from here on.

Never run the installer from a directory that already existed under `/tmp` or `$TMPDIR` (such as
`/tmp/claude-voiceover`): on a shared machine anyone can create one there. Always clone into a new `mktemp -d`.

If it prints `clone failed` (for example a network or proxy error), tell the user what the error said, and stop.

## 3. Run it

Run it **from the user's current project directory**. It checks that project's `.claude/settings*.json` for a
legacy setup. Allow up to 5 minutes; the first run installs the speech engine.

```bash
sh <clone>/scripts/install.sh [--menubar]
```

It prints its progress in sections: prerequisites, marketplace, plugin, setup, legacy check. It exits non-zero
with an `ERROR:` line when something is missing (the `claude` CLI, or Python 3.9+). Report that line to the user
exactly as printed; don't try to work around it.

## 4. Legacy setup

If the output says **LEGACY SETUP FOUND**, show the user the listed items and explain that they come from an
earlier hand-rolled version of this tool. If left in place, every response is spoken twice. Ask whether to
remove them. If the user agrees, re-run with the flag:

```bash
sh <clone>/scripts/install.sh --remove-legacy [--menubar]
```

This moves the listed files into a private `legacy-backup-<timestamp>/` folder (the output names it; by default
under `~/.claude/voiceover/`) and unloads the old login item. From the settings files it removes only the hooks
that run `claude_speak.py` or `claude_speak_menubar.py`. Each edited settings file is first backed up next to
itself as `<file>.bak-voiceover` (or `<file>.bak-voiceover.<random>` if that name is taken by something else);
the output gives each backup's path. A symlinked `~/.claude/settings*.json` is edited at its real file, and its
backup is written next to that file.

Some items need the user rather than a re-run. Tell the user about each of these sections if it appears, and
offer to help by hand only if they ask:

- **Settings not edited automatically**: these settings files still have legacy hooks, but are never edited: a
  project's shared `.claude/settings.json` (usually committed to git), a project `settings.local.json` that is a
  symlink or sits in a symlinked `.claude` directory, a file that can't be written, one that isn't valid UTF-8
  (rewriting it would destroy those bytes), or one whose rewrite failed. The user removes the `claude_speak`
  hooks by hand. Until then the old `claude_speak*.py` scripts stay in place ("Will be left in place", or "Left
  in place" after `--remove-legacy`) so those hooks keep working; once they are gone, re-running with
  `--remove-legacy` moves the scripts too.
- **Could not move**: the file is still in place (the line gives the error); the user moves or deletes it by
  hand. If the old login item is listed here, `claude_speak_menubar.py` stays too, because it still runs it.
- **Could not read**: a settings file that is broken JSON, not a regular file, over 4 MB or too deeply nested
  to check. It may still hold legacy hooks; the user should look at it.
- **Legacy check stopped early**: an unexpected error; only the listed items were handled. Report the error
  line to the user. The plugin itself is installed either way.

## 5. Finish

Tell the user:

1. **Restart Claude Code.** Plugin hooks and commands load at startup.
2. Then control voice-over with:
   - `/voiceover on|off|toggle|status`: this session only
   - `/voiceover global on|off`: default for all sessions
   - `/voiceover-menubar enable|disable|start|stop|status`: macOS menu bar app

If you cloned into a temp directory, delete it afterwards with `rm -rf "$(dirname <clone>)"`. The plugin runs
from Claude Code's plugin cache, not from the clone.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `marketplace add` fails with a network error | Check the connection or proxy, or install from a local clone with `--source <path>` |
| Setup `FAILED:` line mentions pip | Network or proxy issue; re-run the installer once it's fixed |
| No sound after restart | `/voiceover status`; then check `~/.claude/voiceover/voiceover.log` |
| Commands reach the model instead of applying instantly | Claude Code wasn't restarted after install |
| Every response is spoken twice | The old id is still installed: `claude plugin uninstall claude-voiceover@claude-voiceover` (add `--scope project` if the installer said so) |

Uninstall: `/voiceover-setup uninstall`, then `claude plugin uninstall agent-voiceover@claude-voiceover`, then
`rm -rf ~/.claude/voiceover`.
