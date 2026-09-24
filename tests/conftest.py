import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import common  # noqa: E402


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    """Point every state file at a throwaway dir so tests never touch ~/.claude/voiceover."""
    monkeypatch.setattr(common, "DATA_DIR", str(tmp_path))
    for name, filename in [("CONFIG_PATH", "config.json"), ("SESSIONS_PATH", "sessions.json"),
                           ("SPEAKER_PIDFILE", "speaker.pid"), ("MENUBAR_PIDFILE", "menubar.pid"),
                           ("LOG_PATH", "voiceover.log")]:
        monkeypatch.setattr(common, name, str(tmp_path / filename))
    venv_python = tmp_path / "venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.touch()  # "setup done" unless a test removes it
    monkeypatch.setattr(common, "VENV_PYTHON", str(venv_python))
    return tmp_path
