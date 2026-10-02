"""Text-to-speech for voice-over: Markdown cleaning and the single speaker process.

Agent-neutral. Standard library only at import time; pyttsx3 is imported when speaking.
"""
import re

import common

# Cleaning runs on at most this much text, so a huge response can't cost more than a few ms.
CLEAN_INPUT_LIMIT = 20_000


def clean_for_speech(text, max_chars=common.DEFAULTS["max_chars"], announce_code=True):
    # Cut first: every pattern below is bounded, but there is no reason to scan a 1 MB log.
    limit = min(max_chars * 4, CLEAN_INPUT_LIMIT) if max_chars else CLEAN_INPUT_LIMIT
    text = text[:limit]
    code_placeholder = " (code block omitted) " if announce_code else " "
    text = re.sub(r"```.*?(?:```|$)", code_placeholder, text, flags=re.S)       # fenced (even unclosed)
    text = re.sub(r"`([^`\n]*)`", r"\1", text)                                   # inline code
    text = re.sub(r"\[([^\]\n]{1,300})\]\([^)\s]{1,2000}\)", r"\1", text)        # links -> label
    text = re.sub(r"https?://\S+", " link ", text)                              # bare URLs
    text = re.sub(r"^[ \t]*\|[^\n]*\|[ \t]*$", "", text, flags=re.M)            # tables
    text = re.sub(r"^[ \t]*#{1,6}[ \t]*", "", text, flags=re.M)                 # headers
    text = re.sub(r"^[ \t]*[-*+][ \t]+", "", text, flags=re.M)                  # bullets
    text = re.sub(r"(\*{1,2})(?=\S)([^*\n]{1,300}?)(?<=\S)\1", r"\2", text)     # *em* / **bold**
    text = re.sub(r"(?<!\w)(_{1,2})(?=\S)([^_\n]{1,300}?)(?<=\S)\1(?!\w)", r"\2", text)  # _em_, keeps snake_case
    text = re.sub(r"[~>]", "", text)                                            # strikethrough/quotes
    # macOS speech may treat [[...]] as engine commands ([[volm 0]], [[rate 700]]); a response
    # quoting untrusted content must not be able to mute or garble the voice.
    text = re.sub(r"\[\[[^\]\n]{0,100}\]\]", " ", text)
    text = re.sub(r"\[(?=\[)", "[ ", text)                                        # stray [[ can't open one
    text = re.sub(r"\s+", " ", text).strip()
    if max_chars and len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0] + ". Response truncated."
    return text


def speak(text, config, session_id=None):
    """Speak `text` in this process until done, after stopping whatever session is speaking.
    Never raises: a missing pyttsx3, busy speaker lock or audio failure is only logged."""
    try:
        import pyttsx3
    except ImportError:
        common.log("pyttsx3 missing; run /voiceover-setup")
        return
    try:
        common.claim_speaker(session_id)  # one voice at a time across all sessions
    except common.LockTimeout as error:
        common.log(f"not speaking: {error}")
        return
    try:
        engine = pyttsx3.init()
        engine.setProperty("rate", config["rate"])
        engine.setProperty("volume", config["volume"])
        if config["voice"]:
            engine.setProperty("voice", config["voice"])
        engine.say(text)
        engine.runAndWait()
    except Exception as error:  # a broken audio device must never surface in the coding agent
        common.log(f"speak failed: {error!r}")
    finally:
        common.release_speaker()
