---
description: macOS menu bar app for voice-over - start, stop, enable (start at login), disable, status
argument-hint: start | stop | enable | disable | status
disable-model-invocation: true
---
If an agent-voiceover message accompanies this command, the hook already applied it:
reply with that message, word for word, and nothing else. Do not run any tools.

With no such message, the hook did not run (plugin hooks disabled, CLAUDE_VOICEOVER=0,
or Claude Code not restarted since the plugin was installed).

Reply in one short sentence: nothing was changed; restart Claude Code, and if it still
happens run /voiceover-setup. Do not run any tools.
