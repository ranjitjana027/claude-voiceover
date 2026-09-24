#!/usr/bin/env python3
"""One-time setup: private virtualenv with pyttsx3 (+ rumps on macOS), default config, smoke test.

Started in the background by /voiceover-setup; progress goes to <data dir>/setup.log.
Safe to re-run: it reuses the virtualenv and only installs what's missing.
"""
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

# Exact pins: every install, fresh or re-run, gets these reviewed versions, never a newer
# (possibly compromised) release. Transitive dependencies (pyobjc on macOS) are not pinned.
PACKAGES = ["pyttsx3==2.99"] + (["rumps==0.4.0"] if sys.platform == "darwin" else [])
OLD_SETUP = [  # the pre-plugin, hand-rolled version of this tool
    "~/.claude/hooks/claude_speak.py",
    "~/.claude/hooks/claude_speak_menubar.py",
    "~/.claude/commands/speak.md",
    "~/Library/LaunchAgents/local.claude-speak-menubar.plist",
]


def step(message):
    print(f"• {message}", flush=True)


def fail(message):
    print(f"FAILED: {message}", flush=True)
    sys.exit(1)


def run(*cmd):
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        fail(f"{' '.join(cmd[:4])}...\n{(result.stderr or result.stdout).strip()[-1500:]}")
    return result


def main():
    if sys.version_info < (3, 9):
        fail(f"Python 3.9+ required, found {sys.version.split()[0]} at {sys.executable}")
    try:
        with common.file_lock("setup.lock", timeout=0):  # two pip installs into one venv corrupt it
            install()
    except common.LockUnsafe as error:
        fail(str(error))
    except common.LockTimeout:
        fail("another setup is already running; wait for it to finish")


def install():
    os.chmod(common.DATA_DIR, 0o700)  # tighten data dirs created by older versions
    if os.path.exists(common.VENV_PYTHON):
        step(f"Reusing virtualenv {os.path.dirname(os.path.dirname(common.VENV_PYTHON))}")
    else:
        step(f"Creating virtualenv with {sys.executable}")
        run(sys.executable, "-m", "venv", os.path.dirname(os.path.dirname(common.VENV_PYTHON)))

    step(f"Installing {', '.join(PACKAGES)} (this is the slow part)")
    run(common.VENV_PYTHON, "-m", "pip", "install", "--quiet", *PACKAGES)

    if sys.platform.startswith("linux") and not shutil.which("espeak-ng"):
        step("WARNING: espeak-ng not found; install it (e.g. sudo apt install espeak-ng) or nothing will play")

    if not os.path.exists(common.CONFIG_PATH):
        common.save_config(common.load_config())
        step(f"Wrote default config {common.CONFIG_PATH}")

    leftovers = [p for p in OLD_SETUP if os.path.exists(os.path.expanduser(p))]
    if leftovers:
        step("NOTE: old hand-rolled voice setup found; remove it or responses are spoken twice: "
             + ", ".join(leftovers))

    step("Smoke test")
    scripts = os.path.dirname(os.path.abspath(__file__))
    run(common.VENV_PYTHON, "-c",
        f"import sys; sys.path.insert(0, {scripts!r}); import common, voiceover;"
        "voiceover.speak('Voice-over is ready.', common.load_config())")

    print("DONE: voice-over is ready. Restart Claude Code if you haven't since installing the plugin. "
          "Then: /voiceover on|off, /voiceover-menubar enable", flush=True)


if __name__ == "__main__":
    main()
