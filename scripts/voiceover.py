#!/usr/bin/env python3
"""
claude-voiceover hook entrypoint for Claude Code (Stop, UserPromptSubmit, SessionEnd).

  Stop              speak Claude's final response if voice-over is on for this session
  UserPromptSubmit  stop this session's speech; handle the plugin's slash commands
  SessionEnd        forget the session

Slash commands are applied here, before the model runs; Claude only repeats the
result (see command_reply). A command must be the whole prompt, on one line;
anything longer goes to Claude untouched.

  /voiceover [on|off|toggle|default|status]   this session
  /voiceover global [on|off]                  default for sessions without an override
  /voiceover-menubar [start|stop|enable|disable|status]
  /voiceover-setup [status|uninstall]

This is the Claude Code adapter: it parses Claude's hook payloads and owns the slash
commands and reply format; the agent-neutral behaviour lives in core.py and speech.py.

Standard library only at import time; pyttsx3 is imported when speaking.
"""
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402
import core  # noqa: E402
import menubar_ctl  # noqa: E402
# Re-exported for setup.py, menubar.py's voice test and older tests. core calls speech.speak,
# so patch speech.speak (not voiceover.speak) to intercept hook speech.
from speech import clean_for_speech, speak  # noqa: E402,F401

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
# Plugin commands can be typed bare (/voiceover) or qualified (/claude-voiceover:voiceover).
COMMAND = re.compile(r"/(?:claude-voiceover:)?voiceover(?:-(menubar|setup))?(?:[ \t]+([^\n]*?))?[ \t]*", re.I)
REPLY_PREFIX = ("The claude-voiceover hook already handled this command. Reply with exactly the "
                "message below, word for word, and nothing else. Do not run any tools.\n\n")
SETUP_HINT = "Voice-over isn't set up yet. Run /voiceover-setup once (about a minute), then restart Claude Code."


def match_command(prompt):
    return COMMAND.fullmatch((prompt or "").strip())


# ---------- text ----------

def last_message_from_transcript(path):
    """Fallback for older Claude Code versions without last_assistant_message."""
    text = ""
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(entry, dict):
                    continue
                msg = entry.get("message") or {}
                if entry.get("type") == "assistant" and msg.get("role") == "assistant":
                    parts = [c.get("text", "") for c in msg.get("content", [])
                             if isinstance(c, dict) and c.get("type") == "text"]
                    if any(parts):
                        text = "\n".join(parts)
    except OSError:
        pass
    return text


# ---------- commands ----------

def voiceover_command(args, session_id):
    words = args.lower().split()
    if words[:1] == ["global"]:
        on = core.set_global({"on": True, "off": False}.get(words[1]) if len(words) > 1 else None)
        return f"Voice-over default for sessions is {'ON' if on else 'OFF'}."

    action = words[0] if words else "status"
    if action not in core.SESSION_ACTIONS:
        return f"Unknown option '{action}'. Use: on, off, toggle, default, status, global on|off."
    status = core.set_session(session_id, action)
    note = " (following the global default)" if status.following_default else ""
    problem = status.speaker_problem
    warning = f" Nothing will be spoken until this is fixed: {problem}" if problem else ""
    return f"Voice-over is {'ON' if status.on else 'OFF'} for this session{note}.{warning}"


def setup_command(args):
    action = (args.split() or ["install"])[0].lower()
    if action == "status":
        return setup_status()
    if action == "uninstall":
        removed = menubar_ctl.disable() if sys.platform == "darwin" else "No login item on this platform."
        return (f"{removed} To finish: /plugin uninstall claude-voiceover, then delete {common.DATA_DIR}")
    if action != "install":
        return "Unknown option. Use: /voiceover-setup, /voiceover-setup status, /voiceover-setup uninstall."
    try:
        with common.file_lock("setup.lock", timeout=0):
            pass
    except common.LockUnsafe as error:
        return f"Setup can't start: {error}"
    except common.LockTimeout:
        return "Setup is already running. Check progress with /voiceover-setup status."
    # pip install takes ~1 min, longer than UserPromptSubmit may block, so run detached;
    # setup.py takes setup.lock itself for its whole run.
    common.ensure_data_dir()
    with open(os.path.join(common.DATA_DIR, "setup.log"), "w") as log_file:
        subprocess.Popen([sys.executable, os.path.join(SCRIPTS_DIR, "setup.py")],
                         stdout=log_file, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         start_new_session=True)
    return ("Setting up voice-over in the background (about a minute). You'll hear "
            "\"Voice-over is ready\" when done. Check progress with /voiceover-setup status.")


def setup_status():
    try:
        with open(os.path.join(common.DATA_DIR, "setup.log"), encoding="utf-8") as f:
            lines = [line.rstrip() for line in f if line.strip()]
    except OSError:
        return "Setup has not been run. Run /voiceover-setup."
    return "Setup log (last lines):\n" + "\n".join(lines[-8:])


def handle_command(match, data):
    kind, args = (match.group(1) or "").lower(), match.group(2) or ""
    if kind == "setup":
        return setup_command(args)
    if not os.path.exists(common.VENV_PYTHON):
        return SETUP_HINT
    if kind == "menubar":
        return menubar_ctl.command(args)
    return voiceover_command(args, data.get("session_id") or "unknown")


def command_reply(message):
    """Hook output that lets Claude relay `message` as its whole reply.

    The command is already applied; the model only echoes the result. Ending the turn
    from the hook instead (continue: false, or decision "block") makes Claude Code
    prefix the message with "Operation stopped by hook:" / "...blocked by hook:".
    """
    return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                   "additionalContext": REPLY_PREFIX + message}}


# ---------- hook events ----------

def on_stop(data):
    if data.get("stop_hook_active"):  # avoid re-speaking on forced continuations
        return
    text = data.get("last_assistant_message") or \
        (lambda: last_message_from_transcript(data.get("transcript_path", "")))  # read only if speaking
    core.response_completed(data.get("session_id"), text, data.get("cwd"))


def on_user_prompt(data):
    session_id = data.get("session_id")
    core.cancel_speech(session_id)  # this session's speech only (any session's if the payload has no id)
    match = match_command(data.get("prompt"))
    if match:
        try:
            reason = handle_command(match, data)
        except Exception as error:  # still answer, so Claude has a result to relay
            common.log(f"command failed: {error!r}")
            reason = f"Voice-over command failed: {error}. Details in {common.LOG_PATH}"
        print(json.dumps(command_reply(reason)))
    elif session_id:
        core.register(session_id, data.get("cwd"))


def on_session_end(data):
    session_id = data.get("session_id")
    if session_id:
        core.session_ended(session_id)


HANDLERS = {"Stop": on_stop, "UserPromptSubmit": on_user_prompt, "SessionEnd": on_session_end}


def main():
    if os.environ.get("CLAUDE_VOICEOVER", "1") == "0" or common.fcntl is None:
        return  # disabled, or Windows (unsupported): stay silent rather than error on every event
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        return
    if not isinstance(data, dict):
        return
    handler = HANDLERS.get(data.get("hook_event_name", ""))
    if handler:
        try:
            handler(data)
        except Exception as error:  # never break the user's session over voice-over
            common.log(f"{data.get('hook_event_name')} failed: {error!r}")


if __name__ == "__main__":
    main()
