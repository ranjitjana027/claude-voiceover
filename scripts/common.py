"""Shared state for agent-voiceover: paths, global config, per-session flags, process tracking.

Standard library only: the hook must run before /voiceover-setup has installed anything.
State lives outside the plugin directory because Claude Code replaces that
directory on every plugin update.
"""
import errno
import json
import os
import signal
import subprocess
import tempfile
import time
from contextlib import contextmanager

try:
    import fcntl
except ImportError:  # Windows: unsupported; voiceover.main() exits before anything needs it
    fcntl = None

VERSION = "0.3.0"
DATA_DIR = os.path.expanduser(os.environ.get("CLAUDE_VOICEOVER_HOME", "~/.claude/voiceover"))
VENV_PYTHON = os.path.join(DATA_DIR, "venv", "bin", "python")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
SESSIONS_PATH = os.path.join(DATA_DIR, "sessions.json")
SPEAKER_PIDFILE = os.path.join(DATA_DIR, "speaker.pid")
MENUBAR_PIDFILE = os.path.join(DATA_DIR, "menubar.pid")
APP_DIR = os.path.join(DATA_DIR, "app")  # copy of scripts/ the login item runs; written only by enable
LOG_PATH = os.path.join(DATA_DIR, "voiceover.log")
LOG_MAX_BYTES = 256 * 1024
LOCK_TIMEOUT_SECONDS = 2  # never let a stuck lock holder stall the user's prompt

# Command-line fragments that identify our own processes. A pid is only ever signalled
# if its current command line still contains all of them, so a pid reused by an
# unrelated program after our process died is never touched.
SPEAKER_MARKERS = ("python", "voiceover")
MENUBAR_MARKERS = ("python", "menubar.py")

SESSION_TTL_SECONDS = 7 * 24 * 3600  # drop sessions that vanished without SessionEnd
DEFAULTS = {
    "enabled": True,        # default for sessions without their own override
    "rate": 185,
    "volume": 1.0,
    "voice": None,          # pyttsx3 voice id; None = system default
    "max_chars": 1500,      # 0 = no limit
    "announce_code": True,  # say "code block omitted" instead of skipping silently
}


class LockTimeout(Exception):
    pass


class LockUnsafe(LockTimeout):
    """Raised when the lock path is a symlink. A LockTimeout subclass, so callers that fall back
    on a busy lock fall back here too; the message says how to fix it."""


def ensure_data_dir():
    os.makedirs(DATA_DIR, mode=0o700, exist_ok=True)  # sessions.json lists project paths


def write_json_atomic(path, value):
    ensure_data_dir()
    # mkstemp: a fresh 0600 file (O_EXCL), never a symlink someone left at a guessable name;
    # after the replace the file at path is 0600 (sessions.json lists project paths)
    fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, indent=2)
        os.replace(tmp_path, path)  # readers never see half a file
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


@contextmanager
def file_lock(name, timeout=None):
    """Exclusive advisory lock on <data dir>/<name>; raises LockTimeout instead of hanging,
    or LockUnsafe if the lock path is a symlink (never opened through it)."""
    ensure_data_dir()
    path = os.path.join(DATA_DIR, name)
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise LockUnsafe(f"{path} is a symlink; delete it and try again") from None
        raise
    with os.fdopen(fd, "r+") as lock:
        deadline = time.monotonic() + (LOCK_TIMEOUT_SECONDS if timeout is None else timeout)
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise LockTimeout(name)
                time.sleep(0.02)
        yield


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
    with file_lock("sessions.lock"):
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


# ---------- process tracking ----------

def process_command(pid):
    try:
        result = subprocess.run(["ps", "-ww", "-o", "command=", "-p", str(pid)],
                                capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip()


def read_pidfile(path, markers):
    """Return the pidfile's JSON only if that pid is alive AND is still our process."""
    try:
        with open(path, encoding="utf-8") as f:
            info = json.load(f)
        pid = int(info["pid"])
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None
    if pid <= 1:  # 0 / -1 would signal whole process groups
        return None
    command = process_command(pid).lower()  # framework builds show ".../Python"
    if not command or not all(marker in command for marker in markers):
        return None  # dead, or the pid now belongs to an unrelated program
    info["pid"] = pid
    return info


def write_pidfile(path, **extra):
    write_json_atomic(path, dict(extra, pid=os.getpid()))


def release_pidfile(path):
    try:
        with open(path, encoding="utf-8") as f:
            mine = json.load(f).get("pid") == os.getpid()
        if mine:
            os.remove(path)
    except (OSError, ValueError, AttributeError, json.JSONDecodeError):
        pass


def terminate(info):
    try:
        os.kill(info["pid"], signal.SIGTERM)
    except OSError:
        pass


def current_speaker():
    """The live speaker's pidfile info, or None. Clears a stale pidfile (speech killed
    mid-sentence never removes its own), so later calls don't pay for a `ps` each time."""
    if not os.path.exists(SPEAKER_PIDFILE):
        return None
    info = read_pidfile(SPEAKER_PIDFILE, SPEAKER_MARKERS)
    if info is None:
        try:
            with file_lock("speaker.lock", timeout=0):  # a new speaker writes under this lock
                if read_pidfile(SPEAKER_PIDFILE, SPEAKER_MARKERS) is None:
                    os.remove(SPEAKER_PIDFILE)
        except (LockTimeout, OSError):
            pass
    return info


def stop_speaker(session_id=None):
    """Stop the current speaker; with session_id, only if it belongs to that session."""
    info = current_speaker()
    if not info or info["pid"] == os.getpid():
        return
    if session_id is None or info.get("session_id") == session_id:
        terminate(info)


def speaker_lock_problem():
    """Why speaking is impossible right now (a symlinked speaker.lock), or None."""
    path = os.path.join(DATA_DIR, "speaker.lock")
    return f"{path} is a symlink; delete it and try again" if os.path.islink(path) else None


def claim_speaker(session_id):
    """Become the only speaker: stop the current one and record ourselves, atomically."""
    with file_lock("speaker.lock"):
        stop_speaker()
        write_pidfile(SPEAKER_PIDFILE, session_id=session_id)


def release_speaker():
    try:
        with file_lock("speaker.lock"):
            release_pidfile(SPEAKER_PIDFILE)
    except LockTimeout:
        pass  # a new speaker is taking over; it overwrites the pidfile anyway


def log(message):
    try:
        ensure_data_dir()
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > LOG_MAX_BYTES:
            os.replace(LOG_PATH, LOG_PATH + ".1")
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    except OSError:
        pass
