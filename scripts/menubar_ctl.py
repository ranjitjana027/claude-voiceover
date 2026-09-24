"""Start/stop the menu bar app and manage its optional macOS login item (a LaunchAgent).

The login item is opt-in (/voiceover-menubar enable). Enterprise endpoint security
(SentinelOne, Jamf Protect, ...) can treat a script-registered LaunchAgent as
persistence, so start/stop without a login item is the recommended mode on managed Macs.
"""
import glob
import os
import re
import shutil
import subprocess
import sys
import time

import common

LABEL = "local.claude-voiceover.menubar"
PLIST_PATH = os.path.expanduser(f"~/Library/LaunchAgents/{LABEL}.plist")
MENUBAR_LOG = os.path.join(common.DATA_DIR, "menubar.log")
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
USAGE = "Use: /voiceover-menubar start | stop | enable (start at login) | disable | status"


def command(args):
    if sys.platform != "darwin":
        return "The menu bar app is macOS only. Use /voiceover on|off to control voice-over."
    action = (args.split() or ["status"])[0].lower()
    actions = {"start": start, "stop": stop, "enable": enable, "disable": disable, "status": status}
    return actions[action]() if action in actions else f"Unknown option '{action}'. {USAGE}"


def _domain():
    return f"gui/{os.getuid()}"


def _launchctl(*args):
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, timeout=10)


def agent_loaded():
    return _launchctl("print", f"{_domain()}/{LABEL}").returncode == 0


def running():
    return common.read_pidfile(common.MENUBAR_PIDFILE, common.MENUBAR_MARKERS)


def _wait_for(predicate, seconds=5):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.2)
    return predicate()


def _trim_log():
    try:
        if os.path.getsize(MENUBAR_LOG) > common.LOG_MAX_BYTES:
            os.replace(MENUBAR_LOG, MENUBAR_LOG + ".1")
    except OSError:
        pass


def start():
    if running():
        return "Menu bar app is already running (look for 🔊 in the menu bar)."
    if agent_loaded():
        _launchctl("kickstart", f"{_domain()}/{LABEL}")
    else:
        common.ensure_data_dir()
        _trim_log()
        with open(MENUBAR_LOG, "a") as log_file:
            subprocess.Popen([common.VENV_PYTHON, os.path.join(SCRIPTS_DIR, "menubar.py")],
                             stdout=log_file, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             start_new_session=True, env=dict(os.environ, CLAUDE_VOICEOVER_HOME=common.DATA_DIR))
    if _wait_for(running):
        return "Menu bar app started (🔊 in the menu bar)."
    return f"Menu bar app did not start; see {MENUBAR_LOG}"


def stop():
    if agent_loaded():
        _launchctl("bootout", f"{_domain()}/{LABEL}")
    info = running()
    if info:
        common.terminate(info)
    _wait_for(lambda: not running())
    tail = " It will start again at login; use disable to stop that." if os.path.exists(PLIST_PATH) else ""
    return "Menu bar app stopped." + tail


def enable():
    stop()
    # launchd agents can't read TCC-protected folders such as ~/Documents (where a local
    # clone may live), so the login item runs a private copy, refreshed only right here.
    shutil.rmtree(common.APP_DIR, ignore_errors=True)
    os.makedirs(common.APP_DIR, mode=0o700)
    for path in glob.glob(os.path.join(SCRIPTS_DIR, "*.py")):
        shutil.copy2(path, common.APP_DIR)
    os.makedirs(os.path.dirname(PLIST_PATH), exist_ok=True)
    with open(PLIST_PATH, "w", encoding="utf-8") as f:
        f.write(_plist())
    result = _launchctl("bootstrap", _domain(), PLIST_PATH)
    if result.returncode != 0:
        os.remove(PLIST_PATH)  # don't leave a login item behind that we reported as failed
        return f"Could not register login item ({result.stderr.strip()}); nothing was installed."
    if _wait_for(running):
        return ("Menu bar app enabled: running now and will start at every login. "
                "After plugin updates, run /voiceover-menubar enable again to refresh it.")
    return f"Login item registered, but the app did not start; see {MENUBAR_LOG}"


def disable():
    stop()
    try:
        os.remove(PLIST_PATH)
    except FileNotFoundError:
        pass
    shutil.rmtree(common.APP_DIR, ignore_errors=True)
    return "Menu bar app stopped and removed from login items."


def login_copy_version():
    try:
        with open(os.path.join(common.APP_DIR, "common.py"), encoding="utf-8") as f:
            found = re.search(r'^VERSION = "([^"]+)"', f.read(), re.M)
        return found.group(1) if found else None
    except OSError:
        return None


def status():
    info = running()
    state = f"running (pid {info['pid']})" if info else "not running"
    if not os.path.exists(PLIST_PATH):
        return f"Menu bar app is {state}; does not start at login."
    copy_version = login_copy_version()
    stale = "" if copy_version == common.VERSION else \
        f" The login copy is version {copy_version or 'unknown'}; run /voiceover-menubar enable to update it."
    return f"Menu bar app is {state}; starts at login.{stale}"


def _plist():
    # No KeepAlive: a crash stays down instead of restarting every 30s and filling the log.
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{_xml_escape(common.VENV_PYTHON)}</string>
        <string>{_xml_escape(os.path.join(common.APP_DIR, "menubar.py"))}</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>CLAUDE_VOICEOVER_HOME</key>
        <string>{_xml_escape(common.DATA_DIR)}</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>ProcessType</key>
    <string>Interactive</string>
    <key>StandardOutPath</key>
    <string>{_xml_escape(MENUBAR_LOG)}</string>
    <key>StandardErrorPath</key>
    <string>{_xml_escape(MENUBAR_LOG)}</string>
</dict>
</plist>
"""


def _xml_escape(value):
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
