# claude-voiceover

A Claude Code plugin that speaks Claude's final response aloud, locally with pyttsx3. See README.md for the
user-facing behaviour and CONTRIBUTING.md for contribution rules; both are authoritative.

## Commands

```bash
.venv/bin/python -m pytest -q          # all tests (create the venv first: python3 -m venv .venv && .venv/bin/pip install pytest)
.venv/bin/python -m pytest -q tests/test_voiceover.py -k <name>
claude plugin validate .               # manifest check
```

Run both before every commit. CI (`.github/workflows/tests.yml`) runs pytest on Linux and macOS, Python 3.9 and 3.13.

## Layout

- `hooks/hooks.json`: UserPromptSubmit, Stop (async) and SessionEnd all run `scripts/run.sh` → `scripts/voiceover.py`.
- `scripts/voiceover.py`: Claude Code adapter: hook payload parsing, transcript fallback, `/voiceover` commands, reply format.
- `scripts/core.py`: agent-neutral session lifecycle and controls, keyed by session; never reads a hook payload.
- `scripts/speech.py`: Markdown cleaning and pyttsx3 speaking (one speaker at a time, via `speaker.lock`).
- `scripts/common.py`: paths under `~/.claude/voiceover/`, config, locks, safe process signalling.
- `scripts/setup.py`: private venv with pinned deps. `scripts/menubar*.py`: macOS menu bar app and LaunchAgent.
- `scripts/install.sh`, `scripts/legacy.py`: installer and cleanup of the pre-plugin `claude_speak*` setup.
- `commands/*.md`: slash command definitions. `tests/`: pytest; `conftest.py` redirects all state to a tmp dir.

## Rules

- Hooks run on every prompt: keep them fast (no heavy imports on the normal path), print nothing on a normal
  prompt, never make network calls, and never raise into Claude Code; log to `voiceover.log` instead.
- Stay compatible with Python 3.9 and the standard library; runtime deps go in `scripts/setup.py`, pinned `==`.
- Only signal processes after verifying their command line (see `common.py`); never trust a pid file alone.
- Files the plugin needs at runtime must be committed, and `scripts/run.sh` / `scripts/install.sh` stay executable
  (`tests/test_packaging.py` enforces this).
- A release bumps `version` in `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json` and `VERSION` in
  `scripts/common.py` (a test checks they match) and moves the CHANGELOG "Unreleased" entries under the new version.
- Behaviour changes need tests and README updates. Keep INSTALL.md in sync with `scripts/install.sh`: it is read
  by Claude Code when a user asks it to install the plugin.
- Commit messages use Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `chore:`).
