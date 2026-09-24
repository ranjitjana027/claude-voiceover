#!/usr/bin/env python3
"""
claude-voiceover hook entrypoint (Stop, UserPromptSubmit, SessionEnd).

  Stop              speak Claude's final response if voice-over is on for this session
  UserPromptSubmit  stop this session's speech; handle the plugin's slash commands
  SessionEnd        forget the session

Slash commands are intercepted here and blocked, so they never reach the model:
they apply instantly and cost no tokens.

  /voiceover [on|off|toggle|default|status]   this session
  /voiceover global [on|off]                  default for sessions without an override
  /voiceover-menubar [start|stop|enable|disable|status]
  /voiceover-setup [status|uninstall]

Standard library only at import time; pyttsx3 is imported when speaking.
"""
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402
import menubar_ctl  # noqa: E402

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
# Plugin commands can be typed bare (/voiceover) or qualified (/claude-voiceover:voiceover).
COMMAND = re.compile(r"^\s*/(?:claude-voiceover:)?voiceover(?:-(menubar|setup))?(?:\s+(.*?))?\s*$", re.S | re.I)
SETUP_HINT = "Voice-over isn't set up yet. Run /voiceover-setup once (about a minute), then restart Claude Code."


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


def clean_for_speech(text, max_chars=common.DEFAULTS["max_chars"], announce_code=True):
    code_placeholder = " (code block omitted) " if announce_code else " "
    text = re.sub(r"```.*?```", code_placeholder, text, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)                      # inline code
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)          # links -> label
    text = re.sub(r"https?://\S+", " link ", text)                # bare URLs
    text = re.sub(r"^\s*\|.*\|\s*$", "", text, flags=re.M)        # tables
    text = re.sub(r"^\s*#{1,6}\s*", "", text, flags=re.M)         # headers
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.M)          # bullets
    text = re.sub(r"(\*{1,2})(\S.*?\S|\S)\1", r"\2", text)                  # *em* / **bold**
    text = re.sub(r"(?<!\w)(_{1,2})(\S.*?\S|\S)\1(?!\w)", r"\2", text)      # _em_, keeps snake_case
    text = re.sub(r"[~>]", "", text)                              # strikethrough/quotes
    text = re.sub(r"\s+", " ", text).strip()
    if max_chars and len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0] + ". Response truncated."
    return text


def speak(text, config, session_id=None):
    try:
        import pyttsx3
    except ImportError:
        common.log("pyttsx3 missing; run /voiceover-setup")
        return
    common.stop_speaker()  # one voice at a time across all sessions
    common.write_pidfile(common.SPEAKER_PIDFILE, session_id=session_id)
    try:
        engine = pyttsx3.init()
        engine.setProperty("rate", config["rate"])
        engine.setProperty("volume", config["volume"])
        if config["voice"]:
            engine.setProperty("voice", config["voice"])
        engine.say(text)
        engine.runAndWait()
    except Exception as error:  # a broken audio device must never surface in Claude Code
        common.log(f"speak failed: {error!r}")
    finally:
        common.release_pidfile(common.SPEAKER_PIDFILE)


# ---------- commands ----------

def voiceover_command(args, session_id, config):
    words = args.lower().split()
    if words[:1] == ["global"]:
        if words[1:2] in (["on"], ["off"]):
            config["enabled"] = words[1] == "on"
            common.save_config(config)
        return f"Voice-over default for sessions is {'ON' if config['enabled'] else 'OFF'}."

    action = words[0] if words else "status"
    if action not in ("on", "off", "toggle", "default", "status"):
        return f"Unknown option '{action}'. Use: on, off, toggle, default, status, global on|off."
    with common.sessions_locked() as sessions:
        entry = common.touch_session(sessions, session_id)
        if action in ("on", "off"):
            entry["enabled"] = action == "on"
        elif action == "toggle":
            entry["enabled"] = not common.session_enabled(entry, config)
        elif action == "default":
            entry["enabled"] = None
        on = common.session_enabled(entry, config)
        source = "this session's setting" if entry["enabled"] is not None else "global default"
    if not on:
        common.stop_speaker(session_id)
    return f"Voice-over is {'ON' if on else 'OFF'} for this session ({source})."


def setup_command(args):
    action = (args.split() or ["install"])[0].lower()
    if action == "status":
        return setup_status()
    if action == "uninstall":
        menubar_ctl.disable()
        return ("Menu bar login item removed. To finish: /plugin uninstall claude-voiceover, "
                f"then delete {common.DATA_DIR}")
    if action != "install":
        return "Unknown option. Use: /voiceover-setup, /voiceover-setup status, /voiceover-setup uninstall."
    # pip install takes ~1 min, longer than UserPromptSubmit may block, so run detached.
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
    return voiceover_command(args, data.get("session_id") or "unknown", common.load_config())


# ---------- hook events ----------

def on_stop(data):
    if data.get("stop_hook_active"):  # avoid re-speaking on forced continuations
        return
    config = common.load_config()
    session_id = data.get("session_id")
    if session_id:
        with common.sessions_locked() as sessions:
            entry = common.touch_session(sessions, session_id, data.get("cwd"))
            enabled = common.session_enabled(entry, config)
    else:
        enabled = config["enabled"]
    if not enabled:
        return

    text = data.get("last_assistant_message") or \
        last_message_from_transcript(data.get("transcript_path", ""))
    text = clean_for_speech(text or "", config["max_chars"], config["announce_code"])
    if text:
        speak(text, config, session_id)


def on_user_prompt(data):
    session_id = data.get("session_id")
    common.stop_speaker(session_id)  # a new prompt interrupts this session's speech only
    match = COMMAND.match(data.get("prompt") or "")
    if match:
        print(json.dumps({"decision": "block", "reason": handle_command(match, data)}))
    elif session_id:
        with common.sessions_locked() as sessions:
            common.touch_session(sessions, session_id, data.get("cwd"))


def on_session_end(data):
    session_id = data.get("session_id")
    if session_id:
        common.stop_speaker(session_id)
        with common.sessions_locked() as sessions:
            sessions.pop(session_id, None)


HANDLERS = {"Stop": on_stop, "UserPromptSubmit": on_user_prompt, "SessionEnd": on_session_end}


def main():
    if os.environ.get("CLAUDE_VOICEOVER", "1") == "0":
        return
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
