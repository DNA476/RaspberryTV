from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QSettings

from raspberry_tv.moonlight import prepare_settings
from raspberry_tv.processes import Applications, RunningApp, Unavailable, app_command


class MoonlightTests(unittest.TestCase):
    def test_qt_command_and_missing_package(self):
        with patch("raspberry_tv.processes.shutil.which", side_effect=lambda name: name if name == "moonlight-qt" else None):
            self.assertEqual(app_command("moonlight", Path("state")), ["moonlight-qt"])
        with patch("raspberry_tv.processes.shutil.which", return_value=None):
            with self.assertRaises(Unavailable):
                app_command("moonlight", Path("state"))

    def test_preferences_preserve_pairing_and_user_quality_on_repeat_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "Moonlight.conf")
            settings = QSettings(path, QSettings.Format.IniFormat)
            settings.setValue("privatekey", "test-only-credential")
            settings.setValue("hosts/1/uuid", "test-only-host")
            settings.setValue("width", 1920)
            settings.setValue("height", 1080)
            settings.setValue("backgroundgamepad", True)
            prepare_settings(settings)
            settings.setValue("bitrate", 20000)
            prepare_settings(settings)
            saved = QSettings(path, QSettings.Format.IniFormat)
            self.assertEqual(saved.value("privatekey"), "test-only-credential")
            self.assertEqual(saved.value("hosts/1/uuid"), "test-only-host")
            self.assertEqual(saved.value("width", type=int), 1920)
            self.assertEqual(saved.value("height", type=int), 1080)
            self.assertEqual(saved.value("bitrate", type=int), 20000)
            self.assertEqual(saved.value("videocfg", type=int), 2)
            self.assertFalse(saved.value("backgroundgamepad", type=bool))
            self.assertEqual(saved.value("capturesyskeys", type=int), 0)

    def test_settings_write_failure_is_reported(self):
        settings = Mock()
        settings.status.return_value = QSettings.Status.AccessError
        with self.assertRaises(Unavailable):
            prepare_settings(settings)

    def test_moonlight_environment_does_not_leak_to_browser(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch("raspberry_tv.processes.shutil.which", side_effect=lambda name: name), \
                patch("raspberry_tv.moonlight.prepare_settings"), \
                patch("raspberry_tv.processes.subprocess.Popen") as spawn, \
                patch.dict("os.environ", {"SDL_JOYSTICK_HIDAPI": "1"}):
            apps = Applications(Path(directory))
            try:
                apps.launch("moonlight")
                moon_env = spawn.call_args.kwargs["env"]
                self.assertEqual(moon_env["SDL_JOYSTICK_HIDAPI"], "0")
                self.assertEqual(moon_env["SDL_VIDEODRIVER"], "x11")
                apps.launch("browser")
                self.assertEqual(spawn.call_args.kwargs["env"]["SDL_JOYSTICK_HIDAPI"], "1")
            finally:
                for app in apps.running.values():
                    app.log.close()

    def test_stream_window_survives_overlay_focus_and_returns_to_picker(self):
        apps = Applications(Path("state"))
        app = RunningApp(Mock(pid=100), None, 0, "0x10")
        apps.running["moonlight"] = app
        rows = "0x10 0 100 host Moonlight\n0x20 0 100 host Stream\n0x30 0 999 host Launcher"
        focus = "32"

        def command(argv, **kwargs):
            return rows if argv == ["wmctrl", "-lp"] else focus if argv == ["xdotool", "getactivewindow"] else ""

        with patch("raspberry_tv.processes.run", side_effect=command) as run, \
                patch.object(Path, "glob", return_value=[]), \
                patch("raspberry_tv.processes.x11_window_pids", return_value={}):
            apps.discover_windows()
            self.assertEqual(app.window, "0x20")
            focus = "48"
            apps.discover_windows()
            self.assertEqual(app.window, "0x20")
            apps.active = "moonlight"
            apps.resume()
            run.assert_called_with(["wmctrl", "-ia", "0x20"])
            rows = "0x10 0 100 host Moonlight"
            apps.discover_windows()
            self.assertEqual(app.window, "0x10")
