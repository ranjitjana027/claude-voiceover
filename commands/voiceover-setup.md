---
description: One-time voice-over setup (installs the local speech engine); also status or uninstall
argument-hint: "[status | uninstall]"
disable-model-invocation: true
---
The claude-voiceover hook normally handles this command before it reaches you, so you
are only seeing it because the hook did not run. Most likely Claude Code has not been
restarted since the plugin was installed, or no `python3` (3.9+) is on this machine.

Reply briefly: setup did not start; restart Claude Code and run /voiceover-setup again,
and if it still does not start, check that `python3 --version` reports 3.9 or newer.
Do not run any tools.
