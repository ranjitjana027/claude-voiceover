# Changelog

All notable changes to this project are documented here. Versions follow [Semantic Versioning](https://semver.org/).

## Unreleased

- **Renamed the plugin to `agent-voiceover`** (was `claude-voiceover`): Claude Code now reserves plugin names
  starting with `claude-`. The plugin id is `agent-voiceover@claude-voiceover`; the marketplace keeps its name.
  To move over, re-run `scripts/install.sh` (it installs the new id, then removes the old one), or by hand:
  `claude plugin marketplace update claude-voiceover`, `claude plugin install agent-voiceover@claude-voiceover`,
  then `claude plugin uninstall claude-voiceover@claude-voiceover`, and `/voiceover-menubar enable` if you use
  the menu bar login item. Keeping both ids speaks every response twice.
  Settings, sessions and the speech engine in `~/.claude/voiceover/` are kept, and `/voiceover` is unchanged;
  the qualified form is now `/agent-voiceover:voiceover` (the old `/claude-voiceover:voiceover` still works).
- The installer refreshes an existing menu bar login item, so its private copy matches the installed version.

## 0.2.2

- Fix: on Linux with `COLUMNS` set, a long command line was cut off by `ps`, so a new prompt failed to stop
  speech still playing (`ps -ww`).
- Relay `/voiceover` replies without the "hook stopped" prefix.
- Open-source project files: contributing guide, security policy, issue templates and CI.

## 0.2.1

- Security review hardening.
- Show `/voiceover` replies as a clean message.

## 0.2.0

- Safety hardening from review.

## 0.1.1

- Release so installs from the incomplete first commit update.
- Let Claude Code install the plugin itself (`INSTALL.md`, `scripts/install.sh`).

## 0.1.0

- First release: local voice-over for Claude Code responses, per-session on/off, macOS menu bar app.
