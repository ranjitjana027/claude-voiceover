"""Regression tests for the 0.2.0 safety review: wrong-process kills, CPU blow-ups,
swallowed prompts, silent command failures, lock stalls, platform and permission issues."""
import io
import json
import os
import signal
import stat
import subprocess
import sys
import time

import pytest

import common
import menubar_ctl
import voiceover

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")


def run_hook(monkeypatch, capsys, payload):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    voiceover.main()
    out = capsys.readouterr().out.strip()
    return json.loads(out) if out else None


@pytest.fixture
def bystander():
    """An unrelated long-running process whose pid may end up in a stale pidfile."""
    proc = subprocess.Popen(["sleep", "30"])
    yield proc
    proc.kill()
    proc.wait()


# ---------- never signal a process that isn't ours ----------

@pytest.mark.parametrize("session_filter", [None, "A"])
def test_stale_speaker_pidfile_never_kills_unrelated_process(bystander, session_filter):
    common.write_json_atomic(common.SPEAKER_PIDFILE, {"pid": bystander.pid, "session_id": "A"})
    common.stop_speaker(session_filter)
    time.sleep(0.2)
    assert bystander.poll() is None, "unrelated process was signalled"


def test_stale_speaker_pidfile_is_cleared_once_detected(bystander):
    common.write_json_atomic(common.SPEAKER_PIDFILE, {"pid": bystander.pid, "session_id": "A"})
    assert common.current_speaker() is None
    assert not os.path.exists(common.SPEAKER_PIDFILE), "stale pidfile should be removed"
    assert bystander.poll() is None


def test_new_speaker_claim_does_not_kill_unrelated_process(bystander):
    common.write_json_atomic(common.SPEAKER_PIDFILE, {"pid": bystander.pid, "session_id": "A"})
    common.claim_speaker("B")
    time.sleep(0.2)
    assert bystander.poll() is None
    assert json.load(open(common.SPEAKER_PIDFILE))["pid"] == os.getpid()


def test_stale_menubar_pidfile_is_not_running_and_not_stopped(bystander, monkeypatch):
    common.write_json_atomic(common.MENUBAR_PIDFILE, {"pid": bystander.pid})
    monkeypatch.setattr(menubar_ctl, "agent_loaded", lambda: False)
    assert menubar_ctl.running() is None
    menubar_ctl.stop()
    time.sleep(0.2)
    assert bystander.poll() is None


@pytest.mark.parametrize("pid", [0, -1, 1, "x", None])
def test_dangerous_or_bogus_pids_are_rejected(pid):
    common.write_json_atomic(common.SPEAKER_PIDFILE, {"pid": pid})
    assert common.read_pidfile(common.SPEAKER_PIDFILE, common.SPEAKER_MARKERS) is None


def test_real_speaker_is_recognised_and_stopped():
    speaker = subprocess.Popen([sys.executable, "-c", "import time, sys; sys.argv=['voiceover']; time.sleep(30)",
                                "voiceover"])
    try:
        common.write_json_atomic(common.SPEAKER_PIDFILE, {"pid": speaker.pid, "session_id": "A"})
        common.stop_speaker("B")  # other session: left alone
        time.sleep(0.2)
        assert speaker.poll() is None
        common.stop_speaker("A")
        assert speaker.wait(timeout=5) == -signal.SIGTERM
    finally:
        speaker.kill()


# ---------- CPU: huge responses clean quickly ----------

@pytest.mark.parametrize("name, text", [
    ("asterisks", "*a " * 70000), ("underscores", "_a " * 70000), ("brackets", "[x](" * 50000),
    ("mixed", "**x _y `z` [a](b) " * 10000), ("blank lines", "\n" * 200000 + "x"),
    ("unclosed fences", "```" + "a" * 200000),
])
@pytest.mark.parametrize("max_chars", [1500, 0])
def test_clean_for_speech_is_fast_on_pathological_input(name, text, max_chars):
    start = time.perf_counter()
    voiceover.clean_for_speech(text, max_chars=max_chars)
    assert time.perf_counter() - start < 0.5, f"{name} took too long"


# ---------- commands always answer ----------

def test_command_failure_still_blocks_with_message(monkeypatch, capsys):
    def boom(_):
        raise RuntimeError("launchctl exploded")
    monkeypatch.setattr(menubar_ctl, "command", boom)
    reply = run_hook(monkeypatch, capsys, {"hook_event_name": "UserPromptSubmit", "session_id": "A",
                                           "prompt": "/voiceover-menubar start"})
    assert reply["decision"] == "block" and "launchctl exploded" in reply["reason"]


def test_setup_refuses_to_run_twice(monkeypatch, capsys):
    with common.file_lock("setup.lock"):
        reply = run_hook(monkeypatch, capsys, {"hook_event_name": "UserPromptSubmit", "session_id": "A",
                                               "prompt": "/voiceover-setup"})
    assert reply["reason"].startswith("Setup is already running")


def test_uninstall_on_linux_does_not_call_launchctl(monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(menubar_ctl, "disable", lambda: pytest.fail("launchctl path used on Linux"))
    reply = run_hook(monkeypatch, capsys, {"hook_event_name": "UserPromptSubmit", "session_id": "A",
                                           "prompt": "/voiceover-setup uninstall"})
    assert "No login item on this platform" in reply["reason"]


# ---------- locks never stall the user's prompt ----------

def test_busy_sessions_lock_does_not_block_normal_prompt(monkeypatch, capsys):
    monkeypatch.setattr(common, "LOCK_TIMEOUT_SECONDS", 0.2)
    lock_holder = subprocess.Popen([sys.executable, "-c",
        f"import fcntl, time; f = open({os.path.join(common.DATA_DIR, 'sessions.lock')!r}, 'w');"
        "fcntl.flock(f, fcntl.LOCK_EX); print('locked', flush=True); time.sleep(30)"],
        stdout=subprocess.PIPE, text=True)
    try:
        assert lock_holder.stdout.readline().strip() == "locked"
        start = time.perf_counter()
        with pytest.raises(common.LockTimeout):
            with common.file_lock("sessions.lock", timeout=0.2):
                pass
        assert run_hook(monkeypatch, capsys, {"hook_event_name": "UserPromptSubmit", "session_id": "A",
                                              "prompt": "hello"}) is None
        assert time.perf_counter() - start < 2
    finally:
        lock_holder.kill()
        lock_holder.wait()


# ---------- platform, permissions, logs ----------

def test_windows_is_a_silent_no_op(monkeypatch, capsys):
    monkeypatch.setattr(common, "fcntl", None)
    assert run_hook(monkeypatch, capsys, {"hook_event_name": "UserPromptSubmit", "session_id": "A",
                                          "prompt": "/voiceover off"}) is None


def test_data_dir_is_private(tmp_path, monkeypatch):
    target = tmp_path / "fresh"
    monkeypatch.setattr(common, "DATA_DIR", str(target))
    common.ensure_data_dir()
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o700


def test_log_is_capped(monkeypatch):
    monkeypatch.setattr(common, "LOG_MAX_BYTES", 1000)
    for _ in range(100):
        common.log("x" * 100)
    assert os.path.getsize(common.LOG_PATH) <= 1000 + 200


def test_login_item_has_no_crash_restart_loop():
    plist = menubar_ctl._plist()
    assert "KeepAlive" not in plist and "<key>RunAtLoad</key>" in plist


# ---------- run.sh before setup ----------

def run_launcher(tmp_path, payload):
    env = dict(os.environ, CLAUDE_VOICEOVER_HOME=str(tmp_path / "home"), CLAUDE_PLUGIN_ROOT=os.path.dirname(SCRIPTS))
    return subprocess.run(["sh", os.path.join(SCRIPTS, "run.sh")], input=json.dumps(payload),
                          capture_output=True, text=True, env=env, timeout=10)


def test_launcher_before_setup_ignores_ordinary_events(tmp_path):
    for payload in ({"hook_event_name": "Stop", "session_id": "A", "last_assistant_message": "hi"},
                    {"hook_event_name": "UserPromptSubmit", "session_id": "A", "prompt": "hello"}):
        result = run_launcher(tmp_path, payload)
        assert result.returncode == 0 and result.stdout == "" and result.stderr == ""
    assert not (tmp_path / "home").exists(), "nothing should be written before setup"


def test_launcher_before_setup_answers_voiceover_commands(tmp_path):
    result = run_launcher(tmp_path, {"hook_event_name": "UserPromptSubmit", "session_id": "A",
                                     "prompt": "/voiceover off"})
    assert json.loads(result.stdout)["reason"] == voiceover.SETUP_HINT


def test_launcher_kill_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_VOICEOVER", "0")
    result = run_launcher(tmp_path, {"hook_event_name": "UserPromptSubmit", "session_id": "A",
                                     "prompt": "/voiceover off"})
    assert result.stdout == ""


# ---------- 0.2.1 security review ----------

@pytest.mark.parametrize("text, spoken", [
    ("hi [[volm 0]] there", "hi there"),
    ("a [[rate 700]]b", "a b"),
    ("open [[inpt PHON", "open [ [inpt PHON"),
    ("x [[[ y", "x [ [ [ y"),
    ("[[[[volm 0]]]]", "]]"),
    ("list[[1,2],[3]]", "list[ [1,2],[3]]"),  # nested list: one space, sounds the same
])
def test_speech_engine_commands_are_stripped(text, spoken):
    assert voiceover.clean_for_speech(text) == spoken


@pytest.mark.parametrize("text", ["a[0][1]", "m[i][j] = 1", "see [1] and [2]"])
def test_ordinary_brackets_are_spoken_unchanged(text):
    assert voiceover.clean_for_speech(text) == text


def test_state_files_are_private_and_temp_names_are_not_followed(tmp_path):
    sentinel = tmp_path / "sentinel"
    sentinel.write_text("keep")
    os.symlink(sentinel, f"{common.CONFIG_PATH}.{os.getpid()}.tmp")  # the pre-0.2.1 temp name
    common.save_config(common.load_config())
    assert sentinel.read_text() == "keep"
    assert stat.S_IMODE(os.stat(common.CONFIG_PATH).st_mode) == 0o600
    assert json.load(open(common.CONFIG_PATH))["rate"] == common.DEFAULTS["rate"]
    assert not list(tmp_path.glob(".*.tmp")), "no temp file left behind"


def test_lock_file_symlink_is_not_followed(tmp_path):
    sentinel = tmp_path / "sentinel"
    sentinel.write_text("keep")
    os.symlink(sentinel, os.path.join(common.DATA_DIR, "sessions.lock"))
    with pytest.raises(common.LockUnsafe, match="is a symlink; delete it"):
        with common.file_lock("sessions.lock"):
            pass
    assert sentinel.read_text() == "keep"


def test_symlinked_sessions_lock_still_speaks_with_global_default(tmp_path, monkeypatch, capsys):
    spoken = []
    monkeypatch.setattr(voiceover, "speak", lambda text, config, session_id=None: spoken.append(text))
    os.symlink(tmp_path / "nowhere", os.path.join(common.DATA_DIR, "sessions.lock"))
    run_hook(monkeypatch, capsys, {"hook_event_name": "Stop", "session_id": "A", "last_assistant_message": "hello"})
    assert spoken == ["hello"]
    assert "is a symlink" in open(common.LOG_PATH).read()


def test_symlinked_setup_lock_gives_an_actionable_reply(tmp_path, monkeypatch, capsys):
    os.symlink(tmp_path / "nowhere", os.path.join(common.DATA_DIR, "setup.lock"))
    reply = run_hook(monkeypatch, capsys, {"hook_event_name": "UserPromptSubmit", "session_id": "A",
                                           "prompt": "/voiceover-setup"})
    assert reply["stopReason"].startswith("Setup can't start:") and "delete it" in reply["stopReason"]


def test_lock_file_is_private():
    with common.file_lock("x.lock"):
        pass
    assert stat.S_IMODE(os.stat(os.path.join(common.DATA_DIR, "x.lock")).st_mode) == 0o600
