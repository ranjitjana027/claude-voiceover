"""Shared state for claude-voiceover: paths, global config, per-session flags, speaker pid.

Standard library only: the hook must run before /voiceover-setup has installed anything.
State lives outside the plugin directory because Claude Code replaces that
directory on every plugin update.
"""
import fcntl
import json
import os
import signal
import time
from contextlib import contextmanager

DATA_DIR = os.path.expanduser(os.environ.get("CLAUDE_VOICEOVER_HOME", "~/.claude/voiceover"))
VENV_PYTHON = os.path.join(DATA_DIR, "venv", "bin", "python")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
SESSIONS_PATH = os.path.join(DATA_DIR, "sessions.json")
SPEAKER_PIDFILE = os.path.join(DATA_DIR, "speaker.pid")
MENUBAR_PIDFILE = os.path.join(DATA_DIR, "menubar.pid")
APP_DIR = os.path.join(DATA_DIR, "app")  # copy of scripts/ kept by run.sh; what the menu bar runs
LOG_PATH = os.path.join(DATA_DIR, "voiceover.log")

SESSION_TTL_SECONDS = 7 * 24 * 3600  # drop sessions that vanished without SessionEnd
DEFAULTS = {
    "enabled": True,        # default for sessions without their own override
    "rate": 185,
    "volume": 1.0,
    "voice": None,          # pyttsx3 voice id; None = system default
    "max_chars": 1500,      # 0 = no limit
    "announce_code": True,  # say "code block omitted" instead of skipping silently
}


def ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def write_json_atomic(path, value):
    ensure_data_dir()
    tmp_path = f"{path}.{os.getpid()}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(value, f, indent=2)
    os.replace(tmp_path, path)  # readers never see half a file


# ---------- global config ----------

def _valid(key, value):
    if key in ("enabled", "announce_code"):
        return isinstance(value, bool)
    if key == "voice":
        return value is None or isinstance(value, str)
    if key == "volume":
        return isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def load_config():
    config = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            stored = json.load(f)
        if isinstance(stored, dict):
            config.update({k: v for k, v in stored.items() if k in DEFAULTS and _valid(k, v)})
    except (OSError, json.JSONDecodeError):
        pass
    return config


def save_config(config):
    write_json_atomic(CONFIG_PATH, {k: config[k] for k in DEFAULTS})


# ---------- per-session flags ----------
# {session_id: {"enabled": true|false|null, "cwd": str, "last_seen": epoch}}
# enabled null = follow the global default.

def read_sessions():
    try:
        with open(SESSIONS_PATH, encoding="utf-8") as f:
            sessions = json.load(f)
        if isinstance(sessions, dict):
            return {sid: s for sid, s in sessions.items() if isinstance(s, dict)}
    except (OSError, json.JSONDecodeError):
        pass
    return {}


@contextmanager
def sessions_locked():
    """Read-modify-write the sessions file under an exclusive lock (hooks run concurrently)."""
    ensure_data_dir()
    with open(SESSIONS_PATH + ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        sessions = read_sessions()
        yield sessions
        now = time.time()
        write_json_atomic(SESSIONS_PATH, {
            sid: s for sid, s in sessions.items() if now - s.get("last_seen", 0) < SESSION_TTL_SECONDS
        })


def touch_session(sessions, session_id, cwd=None):
    entry = sessions.setdefault(session_id, {"enabled": None})
    entry["last_seen"] = time.time()
    if cwd:
        entry["cwd"] = cwd
    return entry


def session_enabled(entry, config):
    override = entry.get("enabled")
    return config["enabled"] if override is None else bool(override)


# ---------- pidfiles ----------

def read_pidfile(path):
    """Return the pidfile's JSON if its process is alive, else None."""
    try:
        with open(path, encoding="utf-8") as f:
            info = json.load(f)
        os.kill(int(info["pid"]), 0)
        return info
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def write_pidfile(path, **extra):
    write_json_atomic(path, dict(extra, pid=os.getpid()))


def release_pidfile(path):
    info = read_pidfile(path)
    if info and info["pid"] == os.getpid():
        try:
            os.remove(path)
        except OSError:
            pass


def stop_speaker(session_id=None):
    """Stop the current speaker; with session_id, only if it belongs to that session."""
    info = read_pidfile(SPEAKER_PIDFILE)
    if not info or info["pid"] == os.getpid():
        return
    if session_id is None or info.get("session_id") == session_id:
        try:
            os.kill(info["pid"], signal.SIGTERM)
        except OSError:
            pass


def log(message):
    try:
        ensure_data_dir()
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    except OSError:
        pass
