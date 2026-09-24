import json
import os
import stat
import subprocess
import sys

import pytest

import legacy

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "legacy.py")


def settings_with_hooks():
    return {
        "permissions": {"allow": ["Bash(git:*)"]},
        "env": {"NAME": "Rañjit"},
        "hooks": {
            "Stop": [{"hooks": [
                {"type": "command", "command": "/x/python /u/.claude/hooks/claude_speak.py", "async": True},
                {"type": "command", "command": "prettier --write ."}]}],
            "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "pkill -f claude_speak.py || true"}]}],
            "PostToolUse": [{"matcher": "Write", "hooks": [{"type": "command", "command": "claude_speaker_notify.sh"}]}],
            "SessionEnd": [{"hooks": []}],
        },
    }


def test_strip_removes_only_legacy_handlers():
    data = settings_with_hooks()
    assert legacy.strip_legacy_hooks(data) == 2
    assert data["hooks"]["Stop"] == [{"hooks": [{"type": "command", "command": "prettier --write ."}]}]
    assert "UserPromptSubmit" not in data["hooks"]
    assert data["hooks"]["PostToolUse"][0]["hooks"][0]["command"] == "claude_speaker_notify.sh"  # similar name kept
    assert data["hooks"]["SessionEnd"] == [{"hooks": []}]  # untouched event left exactly as it was
    assert data["permissions"] == {"allow": ["Bash(git:*)"]}


def test_strip_without_legacy_changes_nothing():
    data = {"hooks": {}, "x": 1}
    assert legacy.strip_legacy_hooks(data) == 0 and data == {"hooks": {}, "x": 1}


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude" / "hooks").mkdir(parents=True)
    (home / ".claude" / "commands").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    return home


def run_cli(project, *args):
    return subprocess.run([sys.executable, SCRIPT, "--project", str(project), *args],
                          capture_output=True, text=True, timeout=30)


def test_cli_remove_backs_up_and_keeps_shared_settings(fake_home, tmp_path):
    (fake_home / ".claude" / "hooks" / "claude_speak.py").write_text("print()")
    (fake_home / ".claude" / "commands" / "speak.md").write_text("CLAUDE_SPEAK_COMMAND: $ARGUMENTS")
    project = tmp_path / "proj" / ".claude"
    project.mkdir(parents=True)
    (project / "settings.local.json").write_text(json.dumps(settings_with_hooks()))
    (project / "settings.json").write_text(json.dumps(settings_with_hooks()))

    report = run_cli(project.parent)
    assert "LEGACY SETUP FOUND" in report.stdout and (fake_home / ".claude" / "hooks" / "claude_speak.py").exists()

    result = run_cli(project.parent, "--remove")
    assert result.returncode == 0, result.stderr
    assert not (fake_home / ".claude" / "hooks" / "claude_speak.py").exists()
    backups = list((fake_home / ".claude" / "voiceover").glob("legacy-backup-*/claude_speak.py"))
    assert backups, "legacy file should be moved to a backup, not deleted"
    local = json.loads((project / "settings.local.json").read_text())
    assert "UserPromptSubmit" not in local["hooks"] and local["env"]["NAME"] == "Rañjit"
    assert "Rañjit" in (project / "settings.local.json").read_text()  # no \u escapes
    assert (project / "settings.local.json.bak-voiceover").exists()
    shared = json.loads((project / "settings.json").read_text())
    assert shared == settings_with_hooks(), "shared project settings must never be edited"
    assert "not edited automatically" in result.stdout


def test_unrelated_speak_command_is_left_alone(fake_home, tmp_path):
    speak = fake_home / ".claude" / "commands" / "speak.md"
    speak.write_text("My own /speak command that reads a file aloud")
    assert "none found" in run_cli(tmp_path, "--remove").stdout
    assert speak.exists()


# ---------- a cloned repo must not be able to redirect our writes ----------

def legacy_project(tmp_path):
    project = tmp_path / "proj" / ".claude"
    project.mkdir(parents=True)
    return project


def test_planted_temp_and_backup_symlinks_are_not_followed(fake_home, tmp_path):
    project = legacy_project(tmp_path)
    settings = project / "settings.local.json"
    settings.write_text(json.dumps(settings_with_hooks()))
    sentinel = tmp_path / "zshrc"
    sentinel.write_text("export KEEP=1\n")
    for suffix in (".voiceover-tmp", ".bak-voiceover"):
        os.symlink(sentinel, str(settings) + suffix)

    result = run_cli(project.parent, "--remove")
    assert result.returncode == 0, result.stderr
    assert sentinel.read_text() == "export KEEP=1\n"
    assert "UserPromptSubmit" not in json.loads(settings.read_text())["hooks"]
    assert os.path.islink(str(settings) + ".bak-voiceover"), "planted link left alone, backup written elsewhere"
    backups = [p for p in project.glob("settings.local.json.bak-voiceover.*") if not p.is_symlink()]
    assert backups and json.loads(backups[0].read_text()) == settings_with_hooks()


def test_symlinked_settings_file_is_report_only(fake_home, tmp_path):
    project = legacy_project(tmp_path)
    elsewhere = tmp_path / "someone-elses.json"
    elsewhere.write_text(json.dumps(settings_with_hooks()))
    os.symlink(elsewhere, project / "settings.local.json")

    result = run_cli(project.parent, "--remove")
    assert result.returncode == 0, result.stderr
    assert "not edited automatically" in result.stdout
    assert (project / "settings.local.json").is_symlink()
    assert json.loads(elsewhere.read_text()) == settings_with_hooks()
    assert not list(project.glob("*.bak-voiceover*"))


def test_backup_dir_is_private_and_follows_data_home(fake_home, tmp_path, monkeypatch):
    (fake_home / ".claude" / "hooks" / "claude_speak.py").write_text("print()")
    data_home = tmp_path / "data"
    monkeypatch.setenv("CLAUDE_VOICEOVER_HOME", str(data_home))
    assert run_cli(tmp_path, "--remove").returncode == 0
    (backup,) = data_home.glob("legacy-backup-*")
    assert stat.S_IMODE(os.stat(data_home).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(backup).st_mode) == 0o700
    assert (backup / "claude_speak.py").exists()
