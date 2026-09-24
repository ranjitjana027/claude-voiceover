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
    assert not (fake_home / ".claude" / "commands" / "speak.md").exists()
    backups = list((fake_home / ".claude" / "voiceover").glob("legacy-backup-*/speak.md"))
    assert backups, "legacy file should be moved to a backup, not deleted"
    # the shared settings.json still runs claude_speak.py, so moving it would break those hooks
    assert (fake_home / ".claude" / "hooks" / "claude_speak.py").exists()
    assert "Left in place" in result.stdout
    assert f"backup: {project.resolve()}/settings.local.json.bak-voiceover)" in result.stdout
    assert "settings backed up as" not in result.stdout
    assert "files moved to" in result.stdout, "speak.md was moved, so the folder is named"
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
    (backup,) = project.glob("settings.local.json.bak-voiceover.*")
    assert json.loads(backup.read_text()) == settings_with_hooks()
    assert f"backup: {backup.resolve()}" in result.stdout, "summary names the real backup"
    assert not list(project.glob(".voiceover-*")), "no temp file left behind"


@pytest.mark.parametrize("linked", ["file", "dir"])
def test_symlinked_project_settings_are_report_only(fake_home, tmp_path, linked):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "settings.local.json").write_text(json.dumps(settings_with_hooks()))
    if linked == "dir":  # the repo commits .claude itself as a symlink
        (tmp_path / "proj").mkdir()
        os.symlink(elsewhere, tmp_path / "proj" / ".claude")
        project = tmp_path / "proj" / ".claude"
    else:
        project = legacy_project(tmp_path)
        os.symlink(elsewhere / "settings.local.json", project / "settings.local.json")
    (fake_home / ".claude" / "hooks" / "claude_speak.py").write_text("print()")

    result = run_cli(project.parent, "--remove")
    assert result.returncode == 0, result.stderr
    assert "not edited automatically" in result.stdout
    assert json.loads((elsewhere / "settings.local.json").read_text()) == settings_with_hooks()
    assert not list(elsewhere.glob("*.bak-voiceover*"))
    assert (fake_home / ".claude" / "hooks" / "claude_speak.py").exists(), "its hooks still need it"


def test_backup_dir_is_private_and_follows_data_home(fake_home, tmp_path, monkeypatch):
    (fake_home / ".claude" / "hooks" / "claude_speak.py").write_text("print()")
    data_home = tmp_path / "data"
    monkeypatch.setenv("CLAUDE_VOICEOVER_HOME", str(data_home))
    assert run_cli(tmp_path, "--remove").returncode == 0
    (backup,) = data_home.glob("legacy-backup-*")
    assert stat.S_IMODE(os.stat(data_home).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(backup).st_mode) == 0o700
    assert (backup / "claude_speak.py").exists()


def test_symlinked_user_settings_are_edited_at_the_real_file(fake_home, tmp_path):
    dotfiles = tmp_path / "dotfiles"
    dotfiles.mkdir()
    real = dotfiles / "settings.json"
    real.write_text(json.dumps(settings_with_hooks()))
    real.chmod(0o644)
    link = fake_home / ".claude" / "settings.json"
    os.symlink(real, link)
    (fake_home / ".claude" / "hooks" / "claude_speak.py").write_text("print()")

    result = run_cli(tmp_path, "--remove")
    assert result.returncode == 0, result.stderr
    assert link.is_symlink(), "the dotfiles link is kept"
    assert "UserPromptSubmit" not in json.loads(real.read_text())["hooks"]
    assert stat.S_IMODE(os.stat(real).st_mode) == 0o644, "mode kept"
    assert json.loads((dotfiles / "settings.json.bak-voiceover").read_text()) == settings_with_hooks()
    assert not (fake_home / ".claude" / "hooks" / "claude_speak.py").exists(), "no hook needs it any more"


def test_backup_replaces_earlier_backup_and_skips_directory(tmp_path):
    folder = tmp_path / "proj"
    folder.mkdir()
    settings = folder / "settings.local.json"
    settings.write_text('{"v": "new"}')
    earlier = folder / "settings.local.json.bak-voiceover"
    earlier.write_text("old")
    assert legacy.remove_hooks(str(settings), {"v": "stripped"}) == str(earlier)
    assert json.loads(earlier.read_text()) == {"v": "new"}

    earlier.unlink()
    earlier.mkdir()
    settings.write_text('{"v": "again"}')
    target = legacy.remove_hooks(str(settings), {"v": "stripped"})
    assert target != str(earlier) and json.loads(open(target).read()) == {"v": "again"}
    assert earlier.is_dir() and not list(earlier.iterdir())


def test_failed_rewrite_keeps_the_earlier_backup(tmp_path, monkeypatch):
    folder = tmp_path / "proj"
    folder.mkdir()
    settings = folder / "settings.local.json"
    settings.write_text('{"v": "current"}')
    earlier = folder / "settings.local.json.bak-voiceover"
    earlier.write_text("old")
    with pytest.raises(ValueError):  # a lone surrogate can't be encoded as UTF-8
        legacy.remove_hooks(str(settings), {"v": "\ud800"})
    assert earlier.read_text() == "old" and settings.read_text() == '{"v": "current"}'
    assert sorted(p.name for p in folder.iterdir()) == ["settings.local.json", "settings.local.json.bak-voiceover"]


def test_failed_backup_copy_leaves_no_partial_file(tmp_path, monkeypatch):
    folder = tmp_path / "proj"
    folder.mkdir()
    settings = folder / "settings.local.json"
    settings.write_text('{"v": 1}')

    def disk_full(src, dst):
        dst.write(b"{")
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(legacy.shutil, "copyfileobj", disk_full)
    with pytest.raises(OSError):
        legacy.remove_hooks(str(settings), {})
    assert [p.name for p in folder.iterdir()] == ["settings.local.json"]


def test_failed_rewrite_leaves_settings_and_no_temp_file(tmp_path):
    folder = tmp_path / "proj"
    folder.mkdir()
    settings = folder / "settings.local.json"
    settings.write_text('{"a": 1}')
    with pytest.raises(TypeError):
        legacy.write_json_atomic(str(settings), {"x": object()})
    assert settings.read_text() == '{"a": 1}'
    assert [p.name for p in folder.iterdir()] == ["settings.local.json"]


def test_report_mode_says_scripts_will_be_left(fake_home, tmp_path):
    (fake_home / ".claude" / "hooks" / "claude_speak.py").write_text("print()")
    project = legacy_project(tmp_path)
    (project / "settings.json").write_text(json.dumps(settings_with_hooks()))
    out = run_cli(project.parent).stdout
    script = str(fake_home / ".claude" / "hooks" / "claude_speak.py")
    assert "Will be left in place" in out
    assert out.index(script) > out.index("Will be left in place"), "listed as kept, not as to-be-moved"


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write anywhere")
def test_unwritable_user_settings_are_reported_not_crashed(fake_home, tmp_path):
    store = tmp_path / "store"  # e.g. a Nix home-manager link into a read-only store
    store.mkdir()
    (store / "settings.json").write_text(json.dumps(settings_with_hooks()))
    os.symlink(store / "settings.json", fake_home / ".claude" / "settings.json")
    (fake_home / ".claude" / "hooks" / "claude_speak.py").write_text("print()")
    store.chmod(0o555)
    (store / "settings.json").chmod(0o444)
    try:
        for args in ((), ("--remove",)):
            result = run_cli(tmp_path, *args)
            assert result.returncode == 0 and "Traceback" not in result.stderr, result.stderr
            assert "not writable" in result.stdout and "not edited automatically" in result.stdout
        assert json.loads((store / "settings.json").read_text()) == settings_with_hooks()
        assert sorted(p.name for p in store.iterdir()) == ["settings.json"]
        assert (fake_home / ".claude" / "hooks" / "claude_speak.py").exists(), "its hooks still need it"
    finally:
        store.chmod(0o755)


def test_failed_rewrite_removes_its_backup_and_is_reported(fake_home, tmp_path, monkeypatch, capsys):
    project = legacy_project(tmp_path)
    settings = project / "settings.local.json"
    settings.write_text(json.dumps(settings_with_hooks()))

    def fail(path, data):
        raise PermissionError(13, "Operation not permitted", path)
    monkeypatch.setattr(legacy, "write_json_atomic", fail)
    monkeypatch.setattr(sys, "argv", ["legacy.py", "--remove", "--project", str(project.parent)])
    monkeypatch.setattr(legacy, "HOME", str(fake_home))
    monkeypatch.setattr(legacy, "DATA_DIR", str(tmp_path / "data"))
    legacy.main()
    assert json.loads(settings.read_text()) == settings_with_hooks()
    assert sorted(p.name for p in project.iterdir()) == ["settings.local.json"], "backup removed"
    assert "could not edit" in capsys.readouterr().out


@pytest.mark.parametrize("kind", ["bad json", "directory"])
def test_unreadable_settings_are_listed_not_crashed(fake_home, tmp_path, kind):
    path = fake_home / ".claude" / "settings.json"
    path.mkdir() if kind == "directory" else path.write_text('{"hooks": ')
    result = run_cli(tmp_path, "--remove")
    assert result.returncode == 0 and "Traceback" not in result.stderr, result.stderr
    assert "none found" in result.stdout and "Could not read" in result.stdout


def test_empty_settings_file_is_not_reported(fake_home, tmp_path):
    (fake_home / ".claude" / "settings.local.json").write_text("\n")
    assert run_cli(tmp_path).stdout == "none found\n"


def test_non_utf8_settings_keep_scripts_and_bytes(fake_home, tmp_path):
    # "Rañjit" as a Latin-1 byte: invalid UTF-8, which Claude Code (Node) tolerates
    raw = json.dumps(settings_with_hooks(), ensure_ascii=False).encode("latin-1")
    assert b"\xf1" in raw
    path = fake_home / ".claude" / "settings.json"
    path.write_bytes(raw)
    (fake_home / ".claude" / "hooks" / "claude_speak.py").write_text("print()")
    result = run_cli(tmp_path, "--remove")
    assert result.returncode == 0, result.stderr
    assert path.read_bytes() == raw, "never rewritten: that would destroy the Latin-1 byte"
    assert "not valid UTF-8" in result.stdout and "Left in place" in result.stdout
    assert (fake_home / ".claude" / "hooks" / "claude_speak.py").exists()


def test_failed_move_is_reported_and_summary_still_prints(fake_home, tmp_path, monkeypatch):
    (fake_home / ".claude" / "settings.json").write_text(json.dumps(settings_with_hooks()))
    (fake_home / ".claude" / "commands" / "speak.md").write_text("CLAUDE_SPEAK_COMMAND")
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("")  # CLAUDE_VOICEOVER_HOME is a file, so the backup folder can't be made
    monkeypatch.setenv("CLAUDE_VOICEOVER_HOME", str(blocker))
    result = run_cli(tmp_path, "--remove")
    assert result.returncode == 0 and "Traceback" not in result.stderr, result.stderr
    assert "Could not move" in result.stdout and "speak.md" in result.stdout
    assert "backup:" in result.stdout, "the settings edit that already happened is still reported"
    assert (fake_home / ".claude" / "commands" / "speak.md").exists()


def test_one_failing_settings_file_does_not_stop_the_next(fake_home, tmp_path):
    bad = settings_with_hooks()
    bad["env"]["NAME"] = "\ud800"  # loads fine, but can't be written back as UTF-8
    (fake_home / ".claude" / "settings.json").write_text(json.dumps(bad))
    project = legacy_project(tmp_path)
    (project / "settings.local.json").write_text(json.dumps(settings_with_hooks()))
    result = run_cli(project.parent, "--remove")
    assert result.returncode == 0, result.stderr
    assert "could not edit" in result.stdout
    assert "UserPromptSubmit" not in json.loads((project / "settings.local.json").read_text())["hooks"]
    assert (project / "settings.local.json.bak-voiceover").exists()
    assert not (fake_home / ".claude" / "settings.json.bak-voiceover").exists()


def test_project_through_symlinked_path_is_still_editable(fake_home, tmp_path):
    project = legacy_project(tmp_path)
    (project / "settings.local.json").write_text(json.dumps(settings_with_hooks()))
    alias = tmp_path / "alias"
    os.symlink(project.parent, alias)
    result = run_cli(alias, "--remove")
    assert result.returncode == 0, result.stderr
    assert "UserPromptSubmit" not in json.loads((project / "settings.local.json").read_text())["hooks"]
    assert (project / "settings.local.json.bak-voiceover").exists()


def test_settings_only_removal_names_no_backup_folder(fake_home, tmp_path):
    (fake_home / ".claude" / "settings.json").write_text(json.dumps(settings_with_hooks()))
    out = run_cli(tmp_path, "--remove").stdout
    assert "Removed legacy setup:" in out and "files moved to" not in out
