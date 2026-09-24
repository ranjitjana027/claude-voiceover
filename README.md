# claude-voiceover

A Claude Code plugin that reads Claude's final response aloud. It runs fully locally with
[pyttsx3](https://pypi.org/project/pyttsx3/), so there are no API keys and nothing leaves your machine.
You can turn it on or off per session, and on macOS there's a menu bar app.

## Install

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
| `/voiceover-menubar start` / `stop` | Run or quit the menu bar app now |
| `/voiceover-menubar enable` / `disable` | Start the menu bar app at every login, or stop doing that |
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
| `app/` | Copy of the scripts the menu bar login item runs; refreshed after plugin updates |
| `setup.log`, `voiceover.log`, `menubar.log` | Diagnostics; voice-over never raises errors into Claude Code |

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
