"""Agent-neutral voice-over behaviour, called by the per-agent hook adapters.

Adapters (e.g. voiceover.py for Claude Code) parse their own hook payloads, own their
command syntax and output format, and call these with a session key and plain text. Nothing
here reads a hook payload or a transcript.

Errors: register and response_completed never raise on a busy or unsafe sessions lock (they
log or fall back). session_ended and set_session let LockTimeout/LockUnsafe and OSError
through; set_global takes no lock and can only raise OSError when saving the config. The
adapter decides how to report them.

Standard library only at import time.
"""
from collections import namedtuple

import common
import speech

SESSION_ACTIONS = ("on", "off", "toggle", "default", "status")

# on: will this session speak; following_default: no per-session override;
# speaker_problem: why nothing can be spoken right now (only checked when on), or None.
SessionStatus = namedtuple("SessionStatus", "on following_default speaker_problem")


# ---------- lifecycle ----------

def cancel_speech(key):
    """Stop the current speech if it belongs to session `key`. key=None stops whichever
    session is speaking, so pass None only on purpose."""
    common.stop_speaker(key)


def register(key, cwd=None):
    """Record that session `key` is active. Bookkeeping only: waits at most
    LOCK_TIMEOUT_SECONDS for the sessions lock, then skips (logged if the lock is unsafe)."""
    try:
        with common.sessions_locked() as sessions:
            common.touch_session(sessions, key, cwd)
    except common.LockUnsafe as error:
        common.log(f"session bookkeeping skipped: {error}")
    except common.LockTimeout:
        pass


def response_completed(key, text, cwd=None):
    """Speak `text` (raw Markdown str) if voice-over is on for session `key`, and mark the
    session active. The global default decides when key is None or the sessions lock is busy.

    `text` may instead be a zero-argument callable returning it, called only when the
    session will speak, so an adapter's expensive fallback (reading a transcript) is skipped
    for muted sessions.
    """
    config = common.load_config()
    enabled = config["enabled"]
    if key:
        try:
            with common.sessions_locked() as sessions:
                enabled = common.session_enabled(common.touch_session(sessions, key, cwd), config)
        except common.LockTimeout as error:
            common.log(f"sessions lock unavailable ({error}); using the global default")
    if not enabled:
        return
    if callable(text):
        text = text()
    text = speech.clean_for_speech(text or "", config["max_chars"], config["announce_code"])
    if text:
        speech.speak(text, config, key)


def session_ended(key):
    """Stop this session's speech and forget it. Raises if the sessions lock stays busy."""
    common.stop_speaker(key)
    with common.sessions_locked() as sessions:
        sessions.pop(key, None)


# ---------- controls ----------

def set_session(key, action="status"):
    """Apply one of SESSION_ACTIONS to session `key` and return its SessionStatus."""
    if action not in SESSION_ACTIONS:
        raise ValueError(f"unknown session action {action!r}")
    config = common.load_config()
    with common.sessions_locked() as sessions:
        entry = common.touch_session(sessions, key)
        if action in ("on", "off"):
            entry["enabled"] = action == "on"
        elif action == "toggle":
            entry["enabled"] = not common.session_enabled(entry, config)
        elif action == "default":
            entry["enabled"] = None
        on = common.session_enabled(entry, config)
        following_default = entry["enabled"] is None
    if not on:
        common.stop_speaker(key)
    return SessionStatus(on, following_default, common.speaker_lock_problem() if on else None)


def set_global(on=None):
    """Set the default for sessions without an override (None: leave it); return the default."""
    config = common.load_config()
    if on is not None:
        config["enabled"] = on
        common.save_config(config)
    return config["enabled"]
