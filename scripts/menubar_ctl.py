"""Start/stop the menu bar app and manage its macOS login item (a LaunchAgent)."""
import os
import signal
import subprocess
import sys
import time

import common

LABEL = "local.claude-voiceover.menubar"
PLIST_PATH = os.path.expanduser(f"~/Library/LaunchAgents/{LABEL}.plist")
MENUBAR_LOG = os.path.join(common.DATA_DIR, "menubar.log")
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
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def agent_loaded():
    return _launchctl("print", f"{_domain()}/{LABEL}").returncode == 0


def running_pid():
    info = common.read_pidfile(common.MENUBAR_PIDFILE)
    return info["pid"] if info else None


def _menubar_script():
    return os.path.join(common.APP_DIR, "menubar.py")


def _wait_for(predicate, seconds=5):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.2)
    return predicate()


def start():
    if running_pid():
        return "Menu bar app is already running (look for 🔊 in the menu bar)."
    if agent_loaded():
        _launchctl("kickstart", f"{_domain()}/{LABEL}")
    else:
        with open(MENUBAR_LOG, "a") as log_file:
            subprocess.Popen([common.VENV_PYTHON, _menubar_script()],
                             stdout=log_file, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             start_new_session=True, env=dict(os.environ, CLAUDE_VOICEOVER_HOME=common.DATA_DIR))
    if _wait_for(running_pid):
        return "Menu bar app started (🔊 in the menu bar)."
    return f"Menu bar app did not start; see {MENUBAR_LOG}"


def stop():
    if agent_loaded():
        # SIGTERM alone would make launchd restart it (KeepAlive on crash), so unload instead.
        _launchctl("bootout", f"{_domain()}/{LABEL}")
    pid = running_pid()
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    _wait_for(lambda: not running_pid())
    tail = " It will start again at login; use disable to stop that." if os.path.exists(PLIST_PATH) else ""
    return "Menu bar app stopped." + tail


def enable():
    stop()
    os.makedirs(os.path.dirname(PLIST_PATH), exist_ok=True)
    with open(PLIST_PATH, "w", encoding="utf-8") as f:
        f.write(_plist())
    result = _launchctl("bootstrap", _domain(), PLIST_PATH)
    if result.returncode != 0:
        return f"Could not register login item: {result.stderr.strip()}"
    if _wait_for(running_pid):
        return "Menu bar app enabled: running now and will start at every login."
    return f"Login item registered, but the app did not start; see {MENUBAR_LOG}"


def disable():
    stop()
    try:
        os.remove(PLIST_PATH)
    except FileNotFoundError:
        pass
    return "Menu bar app stopped and removed from login items."


def status():
    pid = running_pid()
    login = "starts at login" if os.path.exists(PLIST_PATH) else "does not start at login"
    return f"Menu bar app is {'running (pid ' + str(pid) + ')' if pid else 'not running'}; {login}."


def _plist():
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{_xml_escape(common.VENV_PYTHON)}</string>
        <string>{_xml_escape(_menubar_script())}</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>CLAUDE_VOICEOVER_HOME</key>
        <string>{_xml_escape(common.DATA_DIR)}</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <!-- Restart after a crash, but stay closed when quit from the menu (clean exit). -->
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>ThrottleInterval</key>
    <integer>30</integer>
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
