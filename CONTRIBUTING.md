# Contributing to agent-voiceover

Thanks for helping out! Bug reports, fixes, docs improvements and ideas are all welcome.

## Reporting a bug

Open an [issue](https://github.com/ranjitjana027/claude-voiceover/issues/new/choose) with:

- your OS and version, Python version (`python3 --version`) and Claude Code version (`claude --version`)
- what you did, what you expected, and what happened
- the output of `/voiceover status` and the last lines of `~/.claude/voiceover/voiceover.log`
  (and `setup.log` or `menubar.log` if relevant). These logs can contain project paths; trim anything private.

Security issues go through [SECURITY.md](SECURITY.md), not public issues.

## Development setup

```bash
git clone https://github.com/ranjitjana027/claude-voiceover && cd claude-voiceover
python3 -m venv .venv && .venv/bin/pip install pytest
.venv/bin/python -m pytest -q
claude plugin validate .
```

To run your working copy inside Claude Code:

```bash
claude plugin marketplace add ./
claude plugin install agent-voiceover@claude-voiceover
```

Restart Claude Code after each change to hooks or commands. The runtime state lives in `~/.claude/voiceover/`;
the tests never touch it.

## Pull requests

- Keep each PR focused on one change, and add or update tests in `tests/` for behaviour changes.
- Make sure `pytest` and `claude plugin validate .` pass. CI runs the tests on Linux and macOS.
- Maintainers can add the `claude-review` label to a PR from a branch in this repo to get an automated Claude Code
  review (PRs from forks are skipped).
- Hooks run on every prompt, so keep them fast and quiet: no output on a normal prompt, no new network calls,
  and never raise errors into Claude Code (log them instead).
- New Python dependencies must be pinned (`name==version`) in `scripts/setup.py`.
- New files that the plugin needs at runtime must be committed; `tests/test_packaging.py` checks this.
- For a release, bump `version` in `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json`, and
  `VERSION` in `scripts/common.py` (a test checks they match), and add an entry to [CHANGELOG.md](CHANGELOG.md).
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`…).

By contributing, you agree that your contributions are licensed under the [MIT License](LICENSE).
