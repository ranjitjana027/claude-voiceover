"""scripts/install.sh against a stand-in `claude` CLI that records its calls."""
import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEW, OLD = "agent-voiceover@claude-voiceover", "claude-voiceover@claude-voiceover"

FAKE_CLAUDE = '''#!{python}
import json, os, sys
state_path, log_path = os.environ["FAKE_STATE"], os.environ["FAKE_LOG"]
state = json.load(open(state_path))
args = sys.argv[1:]
with open(log_path, "a") as log:
    log.write(" ".join(args) + "\\n")
if args[:3] == ["plugin", "marketplace", "list"]:
    print(json.dumps([{{"name": "claude-voiceover"}}]))
elif args[:2] == ["plugin", "list"]:
    lists_path = state_path + ".lists"  # FAKE_LIST_FAILS_AFTER=n: the first n reads succeed
    reads = int(open(lists_path).read()) + 1 if os.path.exists(lists_path) else 1
    open(lists_path, "w").write(str(reads))
    if reads > int(os.environ.get("FAKE_LIST_FAILS_AFTER", "1000000")):
        sys.exit(1)
    print(json.dumps(state))
elif args[:2] == ["plugin", "install"]:
    state.append({{"id": args[2], "scope": "user", "installPath": os.environ["FAKE_ROOT"]}})
elif args[:2] == ["plugin", "uninstall"]:
    if os.environ.get("FAKE_UNINSTALL_FAILS"):
        sys.exit(1)
    scope = args[args.index("--scope") + 1]
    state = [p for p in state if not (p["id"] == args[2] and p["scope"] == scope)]
json.dump(state, open(state_path, "w"))
'''


@pytest.fixture
def installer(tmp_path):
    if sys.platform not in ("darwin", "linux"):
        pytest.skip("installer supports macOS and Linux only")
    if not shutil.which("python3"):
        pytest.skip("the installer needs a python3 on PATH")
    bin_dir, plugin_root = tmp_path / "bin", tmp_path / "plugin"
    (plugin_root / "scripts").mkdir(parents=True)
    for script in ("voiceover.py", "setup.py", "legacy.py"):
        (plugin_root / "scripts" / script).write_text("")  # setup and legacy checks: no-ops
    bin_dir.mkdir()
    claude = bin_dir / "claude"
    claude.write_text(FAKE_CLAUDE.format(python=sys.executable))
    claude.chmod(0o755)
    state, log = tmp_path / "state.json", tmp_path / "calls.log"

    def run(installed, **fake):
        state.write_text(json.dumps(installed))
        log.write_text("")
        if os.path.exists(f"{state}.lists"):
            os.remove(f"{state}.lists")
        env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}", HOME=str(tmp_path),
                   CLAUDE_VOICEOVER_HOME=str(tmp_path / "data"), FAKE_STATE=str(state),
                   FAKE_LOG=str(log), FAKE_ROOT=str(plugin_root), **fake)
        result = subprocess.run(["sh", os.path.join(ROOT, "scripts", "install.sh")], env=env,
                                capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
        return [p["id"] + ":" + p["scope"] for p in json.loads(state.read_text())], \
            log.read_text().splitlines(), result.stdout

    return run


def test_fresh_install_uses_the_new_id(installer):
    installed, calls, _ = installer([])
    assert installed == [f"{NEW}:user"]
    assert not any("uninstall" in call for call in calls)


def test_old_id_is_replaced_after_the_new_one_is_installed(installer):
    installed, calls, _ = installer([{"id": OLD, "scope": "user", "installPath": "/old"}])
    assert installed == [f"{NEW}:user"]
    install = calls.index(f"plugin install {NEW} --scope user")
    assert calls.index(f"plugin uninstall {OLD} --scope user") > install


def test_old_id_in_a_project_is_reported_not_removed(installer):
    installed, calls, out = installer([{"id": OLD, "scope": "project", "installPath": "/old"}])
    assert sorted(installed) == sorted([f"{NEW}:user", f"{OLD}:project"])
    assert not any("uninstall" in call for call in calls)
    assert f"claude plugin uninstall {OLD} --scope project" in out


def test_rerun_with_both_ids_updates_the_new_one_and_removes_the_old(installer, tmp_path):
    installed, calls, _ = installer([{"id": NEW, "scope": "user", "installPath": str(tmp_path / "plugin")},
                                     {"id": OLD, "scope": "user", "installPath": "/old"}])
    assert installed == [f"{NEW}:user"]
    assert f"plugin update {NEW}" in calls and not any(c.startswith("plugin install") for c in calls)


def test_failed_old_id_removal_warns_and_still_finishes(installer):
    installed, _, out = installer([{"id": OLD, "scope": "user", "installPath": "/old"}], FAKE_UNINSTALL_FAILS="1")
    assert sorted(installed) == sorted([f"{NEW}:user", f"{OLD}:user"])
    assert f"WARNING: could not remove it; run: claude plugin uninstall {OLD}" in out
    assert "==> Done" in out


def test_unreadable_plugin_list_warns_about_the_old_id(installer):
    # reads 1-2 locate the new plugin; the third, looking for the old id, fails
    _, _, out = installer([], FAKE_LIST_FAILS_AFTER="2")
    assert f"WARNING: could not check for {OLD}" in out
    assert "==> Done" in out


@pytest.mark.parametrize("registered", [True, False])
def test_existing_login_item_is_refreshed_and_a_failure_only_warns(installer, tmp_path, registered):
    plist = tmp_path / "Library" / "LaunchAgents" / "local.claude-voiceover.menubar.plist"
    plist.parent.mkdir(parents=True)
    plist.write_text("")
    venv_python = tmp_path / "data" / "venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    calls = tmp_path / "menubar-calls"
    # stand-in for the venv python: records the refresh and reports success or failure
    venv_python.write_text(f"#!/bin/sh\necho refresh >> '{calls}'\nexit {0 if registered else 1}\n")
    venv_python.chmod(0o755)
    _, _, out = installer([])
    assert calls.read_text() == "refresh\n"
    assert ("WARNING: the menu bar login item is not registered" in out) is not registered
    assert "==> Done" in out
