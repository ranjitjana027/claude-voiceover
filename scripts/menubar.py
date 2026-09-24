#!/usr/bin/env python3
"""
claude-voiceover menu bar app (macOS). Start it with /voiceover-menubar start|enable.

Global settings go to config.json and per-session on/off to sessions.json in the
data dir; the hook re-reads both on each turn, so changes apply from the next response.
"""
import atexit
import json
import os
import subprocess
import sys
from collections import defaultdict

import AppKit
import rumps

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
RATES = [150, 170, 185, 200, 220, 250, 300]
VOLUMES = [0.25, 0.5, 0.75, 1.0]
MAX_LENGTHS = [500, 1000, 1500, 3000, 0]
TEST_MESSAGE = (
    "## Voice test\n\nThis is how **Claude** will sound. "
    "```py\nprint('hidden')\n``` That was a code block."
)
ICON_ON, ICON_OFF, ICON_SPEAKING = "🔊", "🔇", "🗣️"


def installed_voices():
    """Return [(voice_id, name, locale)] using the same ids pyttsx3 uses on macOS."""
    voices = []
    for voice_id in AppKit.NSSpeechSynthesizer.availableVoices():
        attrs = AppKit.NSSpeechSynthesizer.attributesForVoice_(voice_id)
        voices.append((
            str(voice_id),
            str(attrs.get("VoiceName", voice_id)),
            str(attrs.get("VoiceLocaleIdentifier", "unknown")),
        ))
    return sorted(voices, key=lambda v: v[1].lower())


def is_speaking():
    return common.read_pidfile(common.SPEAKER_PIDFILE) is not None


def sessions_signature():
    try:
        return os.stat(common.SESSIONS_PATH).st_mtime_ns
    except OSError:
        return None


class VoiceoverMenuBar(rumps.App):
    def __init__(self):
        super().__init__("Claude Voice-over", title=ICON_ON, quit_button="Quit")
        self.config = common.load_config()
        self.test_procs = []
        self.sessions_shown = None  # (file mtime, default) the Sessions menu was built from

        self.sessions_menu = rumps.MenuItem("Sessions")
        self.enabled_item = rumps.MenuItem("On by default", callback=self.toggle_enabled)
        self.stop_item = rumps.MenuItem("Stop speaking", callback=self.stop_speaking)
        self.code_item = rumps.MenuItem("Announce code blocks", callback=self.toggle_code)
        self.rate_menu = self._choice_menu(
            "Rate", RATES, "rate", lambda r: f"{r} wpm" + (" (default)" if r == common.DEFAULTS["rate"] else ""))
        self.volume_menu = self._choice_menu("Volume", VOLUMES, "volume", lambda v: f"{int(v * 100)}%")
        self.length_menu = self._choice_menu(
            "Max length", MAX_LENGTHS, "max_chars", lambda n: f"{n} chars" if n else "No limit")
        self.voice_menu = self._voice_menu()

        self.menu = [
            self.sessions_menu,
            self.enabled_item,
            self.stop_item,
            None,
            self.voice_menu,
            self.rate_menu,
            self.volume_menu,
            self.length_menu,
            self.code_item,
            None,
            rumps.MenuItem("Test voice", callback=self.test_voice),
            rumps.MenuItem("Open config file", callback=self.open_config),
            None,
        ]
        self.refresh()

    # ---------- menu construction ----------

    def _choice_menu(self, title, values, key, label):
        menu = rumps.MenuItem(title)
        for value in values:
            item = rumps.MenuItem(label(value), callback=self._setter(key, value))
            item.choice_value = value
            menu.add(item)
        menu.config_key = key
        return menu

    def _voice_menu(self):
        menu = rumps.MenuItem("Voice")
        default = rumps.MenuItem("System default", callback=self._setter("voice", None))
        default.choice_value = None
        menu.add(default)
        menu.add(None)

        by_language = defaultdict(list)
        for voice_id, name, locale in installed_voices():
            by_language[locale.split("_")[0]].append((voice_id, name, locale))
        seen_titles = set()

        def voice_item(voice_id, name, locale):
            # rumps keys menu items by title, so duplicates (e.g. compact vs premium) need a suffix
            title = f"{name}  ({locale})"
            if title in seen_titles:
                quality = next((p for p in voice_id.split(".") if p in ("compact", "enhanced", "premium")), voice_id)
                title = f"{name}  ({locale}, {quality})"
            seen_titles.add(title)
            item = rumps.MenuItem(title, callback=self._setter("voice", voice_id))
            item.choice_value = voice_id
            return item

        for voice in by_language.pop("en", []):
            menu.add(voice_item(*voice))
        if by_language:
            menu.add(None)
            others = rumps.MenuItem("Other languages")
            for language in sorted(by_language):
                submenu = rumps.MenuItem(language)
                for voice in by_language[language]:
                    submenu.add(voice_item(*voice))
                others.add(submenu)
            menu.add(others)
        menu.config_key = "voice"
        return menu

    def rebuild_sessions_menu(self, force=False):
        shown = (sessions_signature(), self.config["enabled"])
        if shown == self.sessions_shown and not force:
            return
        self.sessions_shown = shown
        if len(self.sessions_menu):  # rumps only creates the native submenu on first add
            self.sessions_menu.clear()

        sessions = sorted(common.read_sessions().items(),
                          key=lambda kv: kv[1].get("last_seen", 0), reverse=True)
        if not sessions:
            self.sessions_menu.add(rumps.MenuItem("No sessions yet (send a prompt first)"))
            return
        for session_id, entry in sessions:
            project = os.path.basename(entry.get("cwd", "")) or "unknown"
            note = "" if entry.get("enabled") is not None else "  · default"
            item = rumps.MenuItem(f"{project}  ·  {session_id[:8]}{note}",
                                  callback=self._session_toggler(session_id))
            item.state = common.session_enabled(entry, self.config)
            self.sessions_menu.add(item)
        self.sessions_menu.add(None)
        self.sessions_menu.add(rumps.MenuItem("Reset all to default", callback=self.reset_sessions))
        self.sessions_menu.add(rumps.MenuItem("Forget all sessions", callback=self.forget_sessions))

    # ---------- state ----------

    def _setter(self, key, value):
        def callback(_):
            self.config[key] = value
            self.save()
        return callback

    def save(self):
        common.save_config(self.config)
        self.refresh()

    def refresh(self):
        self.enabled_item.state = self.config["enabled"]
        self.code_item.state = self.config["announce_code"]
        for menu in (self.rate_menu, self.volume_menu, self.length_menu, self.voice_menu):
            self._mark_choice(menu, self.config[menu.config_key])
        self.rebuild_sessions_menu()
        self.update_title(None)

    def _mark_choice(self, menu, current):
        for item in menu.values():
            if hasattr(item, "choice_value"):
                item.state = item.choice_value == current
            elif isinstance(item, rumps.MenuItem) and len(item):
                self._mark_choice(item, current)  # nested language submenus

    @rumps.timer(1)
    def update_title(self, _):
        # Reap finished test runs; a zombie child would still look alive to is_speaking().
        self.test_procs = [p for p in self.test_procs if p.poll() is None]
        # /voiceover global on|off edits config.json behind our back; pick that up.
        stored = common.load_config()
        if stored != self.config:
            self.config = stored
            self.refresh()
            return
        self.rebuild_sessions_menu()  # cheap: only rebuilds when the sessions file changed
        if is_speaking():
            self.title = ICON_SPEAKING
        else:
            self.title = ICON_ON if self.config["enabled"] else ICON_OFF

    # ---------- actions ----------

    def _session_toggler(self, session_id):
        def callback(_):
            with common.sessions_locked() as sessions:
                entry = sessions.get(session_id)
                if entry is None:
                    return
                entry["enabled"] = not common.session_enabled(entry, self.config)
                turned_off = not entry["enabled"]
            if turned_off:
                common.stop_speaker(session_id)
            self.rebuild_sessions_menu(force=True)
        return callback

    def reset_sessions(self, _):
        with common.sessions_locked() as sessions:
            for entry in sessions.values():
                entry["enabled"] = None
        self.rebuild_sessions_menu(force=True)

    def forget_sessions(self, _):
        with common.sessions_locked() as sessions:
            sessions.clear()
        self.rebuild_sessions_menu(force=True)

    def toggle_enabled(self, _):
        self.config["enabled"] = not self.config["enabled"]
        self.save()

    def toggle_code(self, _):
        self.config["announce_code"] = not self.config["announce_code"]
        self.save()

    def stop_speaking(self, _):
        common.stop_speaker()
        for proc in self.test_procs:
            proc.terminate()  # covers a test run that hasn't written the pidfile yet
            proc.wait()
        self.test_procs = []
        self.update_title(None)

    def test_voice(self, _):
        # Uses the hook's own cleaning + speaking code with the current settings,
        # ignoring on/off so you can preview while muted.
        proc = subprocess.Popen(
            [sys.executable, "-c",
             "import json, sys, voiceover as v;"
             "cfg = json.load(sys.stdin);"
             "v.speak(v.clean_for_speech(cfg.pop('message'), cfg['max_chars'], cfg['announce_code']), cfg)"],
            stdin=subprocess.PIPE, cwd=SCRIPTS_DIR,
        )
        proc.stdin.write(json.dumps(dict(self.config, message=TEST_MESSAGE)).encode())
        proc.stdin.close()
        self.test_procs.append(proc)

    def open_config(self, _):
        if not os.path.exists(common.CONFIG_PATH):
            common.save_config(self.config)
        subprocess.Popen(["open", "-t", common.CONFIG_PATH])


def hide_dock_icon():
    AppKit.NSBundle.mainBundle().infoDictionary()["LSUIElement"] = "1"


if __name__ == "__main__":
    if common.read_pidfile(common.MENUBAR_PIDFILE):
        sys.exit(0)  # already running
    common.write_pidfile(common.MENUBAR_PIDFILE)
    atexit.register(common.release_pidfile, common.MENUBAR_PIDFILE)
    hide_dock_icon()
    VoiceoverMenuBar().run()
