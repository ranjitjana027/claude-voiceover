# Installing claude-voiceover (guide for Claude Code)

You are Claude Code, and the user has asked you to install this plugin. Follow these steps in order.
Everything goes through `scripts/install.sh`, which is idempotent, so re-running it is always safe.
Don't use the `/plugin` slash commands: the CLI equivalents (`claude plugin …`) inside the script work in every
environment, including ones where `/plugin` is unavailable.

## 1. Ask one question up front

Ask the user (a single question) whether the **menu bar app should start at login**. It's macOS only; skip the
question on other platforms. Their answer decides whether you pass `--menubar`.

## 2. Get the installer

Use an existing local clone if the user has one (for example `~/Documents/claude-voiceover`). Otherwise clone it:

```bash
git clone --depth 1 https://github.com/ranjitjana027/claude-voiceover "${TMPDIR:-/tmp}/claude-voiceover"
```

The repository may be private. If the clone fails with an auth error, try
`gh repo clone ranjitjana027/claude-voiceover "${TMPDIR:-/tmp}/claude-voiceover" -- --depth 1`. If that fails too,
tell the user they need read access to the repo, and stop.

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

This deletes the listed files, unloads the old login item, and removes only hooks whose command mentions
`claude_speak` from the settings files. Each edited settings file is backed up first as `*.bak-voiceover`.

## 5. Finish

Tell the user:

1. **Restart Claude Code.** Plugin hooks and commands load at startup.
2. Then control voice-over with:
   - `/voiceover on|off|toggle|status`: this session only
   - `/voiceover global on|off`: default for all sessions
   - `/voiceover-menubar enable|disable|start|stop|status`: macOS menu bar app

If you cloned into the temp directory, delete the clone afterwards. The plugin runs from Claude Code's
plugin cache, not from the clone.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `marketplace add` fails with an auth error | The repo is private; the user needs git access (SSH key or `gh auth login`) |
| Setup `FAILED:` line mentions pip | Network or proxy issue; re-run the installer once it's fixed |
| No sound after restart | `/voiceover status`; then check `~/.claude/voiceover/voiceover.log` |
| Commands reach the model instead of applying instantly | Claude Code wasn't restarted after install |

Uninstall: `/voiceover-setup uninstall`, then `claude plugin uninstall claude-voiceover@claude-voiceover`, then
`rm -rf ~/.claude/voiceover`.
