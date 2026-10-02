"""The agent-neutral core, driven directly with session keys (no hook payloads)."""
import os

import pytest

import common
import core
import speech


@pytest.fixture
def spoken(monkeypatch):
    calls = []
    monkeypatch.setattr(speech, "speak", lambda text, config, session_id=None: calls.append((text, session_id)))
    return calls


def test_completion_is_cleaned_and_spoken_with_its_session_key(spoken):
    core.response_completed("k", "**Done**: see `x.py`", "/w/repo")
    assert spoken == [("Done: see x.py", "k")]
    assert common.read_sessions()["k"]["cwd"] == "/w/repo"


@pytest.mark.parametrize("text", [None, "", "   ", "```\ncode only\n```"])
def test_nothing_to_say_is_not_spoken(spoken, text):
    common.save_config(dict(common.DEFAULTS, announce_code=False))
    core.response_completed("k", text)
    assert spoken == []


def test_sessions_are_isolated_even_when_native_ids_collide(spoken):
    core.set_session("abc", "off")
    core.response_completed("abc", "claude session")
    core.response_completed("codex:abc", "codex session")
    assert spoken == [("codex session", "codex:abc")]


def test_missing_key_follows_the_global_default(spoken):
    core.set_global(False)
    core.response_completed(None, "nobody")
    core.set_global(True)
    core.response_completed(None, "everybody")
    assert spoken == [("everybody", None)]
    assert common.read_sessions() == {}


def test_set_session_reports_status():
    assert core.set_session("k") == core.SessionStatus(on=True, following_default=True, speaker_problem=None)
    assert core.set_session("k", "toggle") == core.SessionStatus(False, False, None)
    assert core.set_session("k", "default").following_default


def test_set_session_rejects_unknown_actions():
    with pytest.raises(ValueError):
        core.set_session("k", "loud")


def test_set_global_without_a_value_only_reads():
    assert core.set_global() is True
    assert core.set_global(False) is False
    assert core.set_global() is False


def test_session_ended_forgets_only_that_session():
    core.register("a", "/w/a")
    core.register("b", "/w/b")
    core.session_ended("a")
    assert list(common.read_sessions()) == ["b"]


@pytest.fixture
def stopped(monkeypatch):
    calls = []
    monkeypatch.setattr(common, "stop_speaker", lambda session_id=None: calls.append(session_id))
    return calls


def busy_sessions_lock(monkeypatch):
    def busy():
        raise common.LockTimeout("sessions.lock")
    monkeypatch.setattr(common, "sessions_locked", busy)


def test_busy_sessions_lock_falls_back_to_the_global_default(spoken, monkeypatch):
    core.set_session("k", "off")
    busy_sessions_lock(monkeypatch)
    core.response_completed("k", "spoken anyway")
    assert spoken == [("spoken anyway", "k")]
    assert "using the global default" in open(common.LOG_PATH).read()


def test_text_callable_is_only_called_when_the_session_speaks(spoken):
    calls = []

    def text():
        calls.append(1)
        return "lazy"
    core.set_session("muted", "off")
    core.response_completed("muted", text)
    assert calls == []
    core.response_completed("loud", text)
    assert calls == [1] and spoken == [("lazy", "loud")]


@pytest.mark.parametrize("action, stops", [("off", True), ("toggle", True), ("on", False), ("status", False)])
def test_set_session_stops_its_own_speech_only_when_turned_off(stopped, action, stops):
    core.set_session("k", action)
    assert stopped == (["k"] if stops else [])


@pytest.mark.parametrize("key", ["codex:abc", None])
def test_cancel_speech_passes_the_key_through(stopped, key):
    core.cancel_speech(key)
    assert stopped == [key]


def test_speaker_problem_is_reported_only_when_on(tmp_path):
    os.symlink(tmp_path / "elsewhere", tmp_path / "speaker.lock")
    assert "is a symlink" in core.set_session("k", "status").speaker_problem
    assert core.set_session("k", "off").speaker_problem is None


@pytest.mark.parametrize("error", [common.LockTimeout("sessions.lock"), common.LockUnsafe("sessions.lock is a symlink")])
def test_register_never_raises_on_lock_problems(monkeypatch, error):
    def broken():
        raise error
    monkeypatch.setattr(common, "sessions_locked", broken)
    core.register("k")
    logged = os.path.exists(common.LOG_PATH) and "bookkeeping skipped" in open(common.LOG_PATH).read()
    assert logged == isinstance(error, common.LockUnsafe)


def test_session_ended_lets_lock_errors_through(monkeypatch):
    busy_sessions_lock(monkeypatch)
    with pytest.raises(common.LockTimeout):
        core.session_ended("k")
