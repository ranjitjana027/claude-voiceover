# claude-voiceover

A Claude Code plugin that reads Claude's final response aloud. It runs fully locally with
[pyttsx3](https://pypi.org/project/pyttsx3/), so there are no API keys and nothing leaves your machine.
You can turn it on or off per session, and on macOS there's a menu bar app.

## Install

**Easiest: ask Claude Code to do it.** Paste this into any session:

```text
Install the Claude Code plugin from https://github.com/ranjitjana027/claude-voiceover by following its INSTALL.md
```

Claude clones the repo, asks whether you want the menu bar app at login, and runs `scripts/install.sh`.
The script adds the marketplace, installs the plugin for your user, sets up the speech engine and checks
for leftovers. Then restart Claude Code.

**By hand, with one command:**

```bash
git clone https://github.com/ranjitjana027/claude-voiceover && sh claude-voiceover/scripts/install.sh
# add --menubar to start the menu bar app at login (not recommended on company-managed Macs)
```

Then restart Claude Code.

**By hand, inside Claude Code:**

```text
/plugin marketplace add ranjitjana027/claude-voiceover    # or a local clone: ~/Documents/claude-voiceover
/plugin install claude-voiceover@claude-voiceover
```

1. Restart Claude Code so the plugin's hooks load.
2. Run `/voiceover-setup` once. It installs the speech engine into a private virtualenv in about a minute,
   in the background. You'll hear "Voice-over is ready" when it's done.
3. That's it. Every response is now spoken. Setup is global and covers every project and session.

Requirements: Python 3.9+ (`python3`). On Linux you also need `espeak-ng`. The menu bar app is macOS only.

## Commands

The plugin's hook handles these commands directly, so they never reach the model. They take effect
instantly and use no tokens. Each also works fully qualified, for example `/claude-voiceover:voiceover off`.

| Command | What it does |
|---|---|
| `/voiceover off` / `on` / `toggle` | Voice-over for **this session** only |
| `/voiceover default` | Drop this session's override and follow the global default |
| `/voiceover status` | Show whether this session speaks, and why |
| `/voiceover global off` / `on` | Default for sessions without their own setting |
| `/voiceover-menubar start` / `stop` | Run or quit the menu bar app now (recommended) |
| `/voiceover-menubar enable` / `disable` | Start the menu bar app at every login, or stop doing that (see note below) |
| `/voiceover-menubar status` | Is it running, and does it start at login? |
| `/voiceover-setup status` | Last lines of the setup log |
| `/voiceover-setup uninstall` | Remove the login item; prints the remaining cleanup steps |

A new prompt stops speech from its own session only. Only one voice plays at a time across sessions.
Set `CLAUDE_VOICEOVER=0` in the environment to silence everything.

## Menu bar app (macOS)

🔊 on by default, 🔇 off by default, 🗣️ speaking. From the menu you can:

- turn each live session on or off
- set the default for sessions, or stop speaking
- change the voice (any installed macOS voice), rate, volume and max length
- choose whether to say "code block omitted"
- play a test voice, or open the config file

**Login item on company-managed Macs.** `enable` registers a LaunchAgent. Endpoint security tools
(SentinelOne, CrowdStrike, Jamf Protect and others) can treat a login item registered by a script as
persistence, and may kill and quarantine the processes involved. On a managed Mac, use
`/voiceover-menubar start` instead, or ask IT to allowlist it. The login item runs a copy of the scripts,
so after a plugin update, run `enable` again. `/voiceover-menubar status` tells you when that copy is out
of date. If the plugin is uninstalled, the login item removes itself the next time it starts.

Tip: the newer macOS voices (Zoe, Ava or Evan in premium, and Reed, Sandy, Flo) sound much better than
the defaults. Add them under System Settings → Accessibility → Spoken Content → System Voice → Manage Voices,
then pick one from the menu.

## What gets spoken

Only Claude's final message. Before speaking, the text is cleaned up:

- code blocks become "code block omitted" (or are skipped silently, if you choose)
- inline code keeps its text
- links keep their label, and bare URLs become "link"
- tables, headers, bullets and emphasis markers are removed; `snake_case` names are kept
- long replies are cut at 1500 characters, at a word boundary, followed by "Response truncated."
  You can change the limit.

## Files

Everything lives in `~/.claude/voiceover/`, which plugin updates don't touch:

| File | Purpose |
|---|---|
| `config.json` | Default on/off, voice, rate, volume, max length, code announcement |
| `sessions.json` | Per-session overrides; ended sessions are removed, stale ones after 7 days |
| `venv/` | Private virtualenv with pyttsx3 (+ rumps on macOS) |
| `app/` | Only with the login item: the scripts it runs, copied when you run `enable` |
| `legacy-backup-*/` | Files moved aside by `install.sh --remove-legacy` |
| `setup.log`, `voiceover.log`, `menubar.log` | Diagnostics, capped at 256 KB each; voice-over never raises errors into Claude Code |

The folder is private (`700`) because `sessions.json` lists your project paths. `--remove-legacy` also leaves a
`<settings file>.bak-voiceover` backup next to each settings file it edits.

## Safety

- **Signals only its own processes.** Before any signal is sent, the process's current command line is
  checked. A pid that has been reused by another program after voice-over's own process died is never
  touched.
- **Never slows a prompt.** A normal prompt costs about 70 ms and prints nothing. Lock waits give up after
  2 s. Before setup has run, ordinary events don't even start Python.
- **Cheap text cleaning.** Only a bounded amount of text is cleaned, with bounded patterns, so a huge
  response costs milliseconds.
- **Commands never leak.** A `/voiceover…` command has to be the entire prompt, on one line; anything
  longer goes to Claude untouched. A failed command still gets an answer instead of reaching the model.
- **Quiet elsewhere.** On Windows it's a silent no-op. Set `CLAUDE_VOICEOVER=0` to turn everything off.

## Uninstall

```text
/voiceover-setup uninstall
/plugin uninstall claude-voiceover@claude-voiceover
```

Then delete `~/.claude/voiceover/`.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install pytest
.venv/bin/python -m pytest -q
claude plugin validate .
```

Hook entrypoint: `scripts/run.sh` → `scripts/voiceover.py`, which dispatches on `hook_event_name`.

## License

MIT
