import io
import json
import os
import time

import pytest

import common
import voiceover


@pytest.fixture
def spoken(monkeypatch):
    calls = []
    monkeypatch.setattr(voiceover, "speak", lambda text, config, session_id=None: calls.append((text, session_id)))
    return calls


def run_hook(monkeypatch, capsys, payload):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    voiceover.main()
    out = capsys.readouterr().out.strip()
    reply = json.loads(out) if out else None
    if reply and "hookSpecificOutput" in reply:  # command reply: expose the relayed message
        context = reply["hookSpecificOutput"]["additionalContext"]
        assert context.startswith(voiceover.REPLY_PREFIX)
        reply["reason"] = context[len(voiceover.REPLY_PREFIX):]
    return reply


def prompt(session_id, text):
    return {"hook_event_name": "UserPromptSubmit", "session_id": session_id, "cwd": "/w/repo", "prompt": text}


def stop(session_id, message):
    return {"hook_event_name": "Stop", "session_id": session_id, "last_assistant_message": message}


# ---------- text cleaning ----------

@pytest.mark.parametrize("raw, expected", [
    ("Build passed.", "Build passed."),
    ("## Title\n\n- **Bold** item", "Title Bold item"),
    ("Call `rate_limit` in user_service", "Call rate_limit in user_service"),
    ("See [docs](https://go.dev) or https://x.io/y", "See docs or link"),
    ("| a | b |\n|---|---|\nafter", "after"),
    ("before ```py\nx = 1\n``` after", "before (code block omitted) after"),
    ("*one* and _two_", "one and two"),
])
def test_clean_for_speech(raw, expected):
    assert voiceover.clean_for_speech(raw) == expected


def test_clean_for_speech_can_skip_code_silently():
    assert voiceover.clean_for_speech("a ```x``` b", announce_code=False) == "a b"


def test_clean_for_speech_truncates_at_word_boundary():
    text = voiceover.clean_for_speech("word " * 100, max_chars=22)
    assert text == "word word word word. Response truncated."


def test_clean_for_speech_no_limit():
    assert len(voiceover.clean_for_speech("word " * 1000, max_chars=0)) == 4999


# ---------- command parsing ----------

@pytest.mark.parametrize("text, kind, args", [
    ("/voiceover off", None, "off"),
    ("/claude-voiceover:voiceover on", None, "on"),
    ("/voiceover", None, None),
    ("  /voiceover-menubar enable  ", "menubar", "enable"),
    ("/claude-voiceover:voiceover-setup status", "setup", "status"),
])
def test_command_regex_matches(text, kind, args):
    match = voiceover.match_command(text)
    assert match and match.group(1) == kind and match.group(2) == args


@pytest.mark.parametrize("text", [
    "please fix /voiceover off", "/voiceovers", "voiceover off", "/voice off",
    "/voiceover off\nalso refactor the auth module",  # regression: extra lines were swallowed
])
def test_command_regex_ignores_other_prompts(text):
    assert voiceover.match_command(text) is None


# ---------- per-session behaviour ----------

def test_session_off_mutes_only_that_session(monkeypatch, capsys, spoken):
    reply = run_hook(monkeypatch, capsys, prompt("A", "/voiceover off"))
    assert reply["reason"] == "Voice-over is OFF for this session."
    run_hook(monkeypatch, capsys, stop("A", "muted"))
    run_hook(monkeypatch, capsys, stop("B", "B talks"))
    assert spoken == [("B talks", "B")]


def test_toggle_default_and_status(monkeypatch, capsys, spoken):
    assert "ON" in run_hook(monkeypatch, capsys, prompt("A", "/voiceover status"))["reason"]
    assert "OFF" in run_hook(monkeypatch, capsys, prompt("A", "/voiceover toggle"))["reason"]
    reply = run_hook(monkeypatch, capsys, prompt("A", "/voiceover default"))["reason"]
    assert reply == "Voice-over is ON for this session (following the global default)."


def test_global_default_applies_to_sessions_without_override(monkeypatch, capsys, spoken):
    run_hook(monkeypatch, capsys, prompt("A", "/voiceover on"))
    assert run_hook(monkeypatch, capsys, prompt("A", "/voiceover global off"))["reason"] == \
        "Voice-over default for sessions is OFF."
    run_hook(monkeypatch, capsys, stop("A", "A overrides"))
    run_hook(monkeypatch, capsys, stop("B", "B follows default"))
    assert spoken == [("A overrides", "A")]


def test_unknown_option_is_rejected(monkeypatch, capsys):
    assert "Unknown option 'loud'" in run_hook(monkeypatch, capsys, prompt("A", "/voiceover loud"))["reason"]


def test_normal_prompt_is_not_blocked_and_registers_session(monkeypatch, capsys):
    assert run_hook(monkeypatch, capsys, prompt("A", "hello")) is None
    assert common.read_sessions()["A"]["cwd"] == "/w/repo"


def test_commands_before_setup_point_to_setup(monkeypatch, capsys):
    os.remove(common.VENV_PYTHON)
    for text in ("/voiceover off", "/voiceover-menubar start"):
        assert run_hook(monkeypatch, capsys, prompt("A", text))["reason"] == voiceover.SETUP_HINT


def test_stop_hook_active_is_silent(monkeypatch, capsys, spoken):
    run_hook(monkeypatch, capsys, dict(stop("A", "again"), stop_hook_active=True))
    assert spoken == []


def test_stop_falls_back_to_transcript(monkeypatch, capsys, spoken, tmp_path):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("\n".join(json.dumps(e) for e in [
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "old"}]}},
        {"type": "user", "message": {"role": "user", "content": "hi"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "latest"}]}},
        "not json {",
    ]))
    run_hook(monkeypatch, capsys, {"hook_event_name": "Stop", "session_id": "A", "transcript_path": str(transcript)})
    assert spoken == [("latest", "A")]


def test_session_end_forgets_session(monkeypatch, capsys):
    run_hook(monkeypatch, capsys, prompt("A", "/voiceover off"))
    run_hook(monkeypatch, capsys, {"hook_event_name": "SessionEnd", "session_id": "A"})
    assert "A" not in common.read_sessions()


def test_stale_sessions_are_pruned():
    with common.sessions_locked() as sessions:
        sessions["old"] = {"enabled": False, "last_seen": time.time() - common.SESSION_TTL_SECONDS - 1}
        sessions["new"] = {"enabled": True, "last_seen": time.time()}
    assert list(common.read_sessions()) == ["new"]


# ---------- robustness ----------

@pytest.mark.parametrize("stdin", ["not json", "[]", "null", json.dumps({"hook_event_name": "Nope"})])
def test_malformed_input_is_ignored(monkeypatch, capsys, stdin):
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    voiceover.main()
    assert capsys.readouterr().out == ""


def test_handler_errors_are_logged_not_raised(monkeypatch, capsys):
    def boom(_):
        raise RuntimeError("audio device gone")
    monkeypatch.setitem(voiceover.HANDLERS, "Stop", boom)
    run_hook(monkeypatch, capsys, stop("A", "x"))
    assert "audio device gone" in open(common.LOG_PATH).read()


def test_bad_config_values_fall_back_to_defaults():
    common.ensure_data_dir()
    with open(common.CONFIG_PATH, "w") as f:
        json.dump({"rate": "fast", "volume": 3, "enabled": "yes", "voice": 7, "max_chars": -1}, f)
    assert common.load_config() == common.DEFAULTS


def test_env_kill_switch(monkeypatch, capsys, spoken):
    monkeypatch.setenv("CLAUDE_VOICEOVER", "0")
    assert run_hook(monkeypatch, capsys, prompt("A", "/voiceover off")) is None
    run_hook(monkeypatch, capsys, stop("A", "x"))
    assert spoken == []


def test_command_reply_is_relayed_without_hook_prefix(monkeypatch, capsys, spoken):
    reply = run_hook(monkeypatch, capsys, prompt("A", "/claude-voiceover:voiceover on"))
    # no continue/decision: those make Claude Code prefix "Operation stopped by hook:"
    assert not {"continue", "stopReason", "decision"} & reply.keys()
    assert reply["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert reply["reason"] == "Voice-over is ON for this session."
