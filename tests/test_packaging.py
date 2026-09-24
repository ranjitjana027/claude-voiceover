"""Guards against shipping a plugin whose hooks point at files missing from git.

Claude Code installs the plugin from the git repo, so an untracked script means a
broken install ("run.sh: No such file or directory") even when local tests pass.
"""
import json
import os
import re
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUIRED = [
    "scripts/run.sh", "scripts/voiceover.py", "scripts/common.py", "scripts/menubar_ctl.py",
    "scripts/menubar.py", "scripts/setup.py", "scripts/install.sh", "scripts/legacy.py",
    "hooks/hooks.json", ".claude-plugin/plugin.json", ".claude-plugin/marketplace.json",
]


def tracked_files():
    try:
        out = subprocess.run(["git", "ls-files", "--stage"], cwd=ROOT, capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return {line.split("\t", 1)[1]: line.split()[0] for line in out.stdout.splitlines()}


def test_required_files_are_committed():
    missing = [path for path in REQUIRED if path not in tracked_files()]
    assert not missing, f"not tracked by git (install would be broken): {missing}"


def test_entrypoints_are_executable_in_git():
    modes = tracked_files()
    assert modes.get("scripts/run.sh") == "100755"
    assert modes.get("scripts/install.sh") == "100755"


def test_hook_commands_point_at_tracked_files():
    with open(os.path.join(ROOT, "hooks", "hooks.json"), encoding="utf-8") as f:
        hooks = json.load(f)["hooks"]
    tracked = tracked_files()
    for groups in hooks.values():
        for group in groups:
            for hook in group["hooks"]:
                for path in re.findall(r"\$\{CLAUDE_PLUGIN_ROOT\}/([\w./-]+)", hook["command"]):
                    assert path in tracked, f"hook references untracked {path}"


def test_manifest_versions_match():
    with open(os.path.join(ROOT, ".claude-plugin", "plugin.json"), encoding="utf-8") as f:
        plugin = json.load(f)
    with open(os.path.join(ROOT, ".claude-plugin", "marketplace.json"), encoding="utf-8") as f:
        listed = {p["name"]: p for p in json.load(f)["plugins"]}
    assert listed[plugin["name"]]["version"] == plugin["version"]
