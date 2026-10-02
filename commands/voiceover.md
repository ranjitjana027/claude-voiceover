---
description: Voice-over for this session - on, off, toggle, default, status, or global on|off
argument-hint: on | off | toggle | default | status | global on|off
disable-model-invocation: true
---
If an agent-voiceover message accompanies this command, the hook already applied it:
reply with that message, word for word, and nothing else. Do not run any tools.

With no such message, the hook did not run (plugin hooks disabled, CLAUDE_VOICEOVER=0,
or Claude Code not restarted since the plugin was installed).

Reply in one short sentence: the voice-over setting was not changed; restart Claude Code,
and if it still happens run /voiceover-setup. Do not run any tools.
