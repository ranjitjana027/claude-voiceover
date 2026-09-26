# Security Policy

claude-voiceover runs as a Claude Code hook on every prompt, starts background processes, and on macOS can
register a login item, so security reports are taken seriously.

## Reporting a vulnerability

Please **don't open a public issue**. Report it privately through GitHub:
[Security → Report a vulnerability](https://github.com/ranjitjana027/claude-voiceover/security/advisories/new).

Include the affected version (from `.claude-plugin/plugin.json`), your OS, and steps to reproduce. You should
get a reply within a week. Once a fix is released, the advisory is published with credit to you, unless you
prefer otherwise.

## Supported versions

Only the latest release gets security fixes. Update with `claude plugin update claude-voiceover@claude-voiceover`
(or re-run `scripts/install.sh`).

## Scope

In scope: anything in this repository, for example signalling a process that isn't voice-over's own, writing
outside `~/.claude/voiceover/` or the settings files that `install.sh --remove-legacy` edits, leaking state
from the private data folder, or a prompt that makes a hook run unintended commands.

Out of scope: vulnerabilities in Claude Code itself, pyttsx3, rumps or the OS speech engines (please report
those upstream).
