# Changelog

All notable changes to this project are documented here. Versions follow [Semantic Versioning](https://semver.org/).

## Unreleased

- Internal: split the agent-neutral behaviour (`scripts/core.py`, `scripts/speech.py`) out of the Claude Code hook adapter, as groundwork for Codex support (#6). No behaviour change.

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
