from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from raspberry_tv.config import DEFAULTS, Settings, normalize_domains, normalize_url, validate
from raspberry_tv.input import InputReader
from raspberry_tv.linux import parse_outputs, split_nmcli
from raspberry_tv.processes import app_command, Unavailable


class SettingsTests(unittest.TestCase):
    def test_save_reload_and_previous_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            settings = Settings(path)
            settings.save(scale="large")
            settings.save(cec=False)
            self.assertEqual(Settings(path).data["scale"], "large")
            self.assertTrue(json.loads(settings.backup.read_text(encoding="utf-8"))["cec"])

    def test_corruption_recovers_last_valid_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(Path(directory))
            settings.save(scale="large")
            settings.save(scale="small")
            settings.path.write_text("{broken", encoding="utf-8")
            restored = Settings(Path(directory))
            self.assertEqual(restored.data["scale"], "large")
            self.assertIn("резервной", restored.notice)

    def test_invalid_save_does_not_destroy_previous_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(Path(directory))
            settings.save(scale="large")
            with self.assertRaises(ValueError):
                settings.save(scale="invalid")
            self.assertEqual(Settings(Path(directory)).data["scale"], "large")

    def test_failed_atomic_replace_keeps_previous_main_file(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(Path(directory))
            settings.save(scale="large")
            import os
            original = os.replace
            def replace(source, target):
                if target == settings.path:
                    raise OSError("disk full")
                return original(source, target)
            with patch("raspberry_tv.config.os.replace", side_effect=replace):
                with self.assertRaises(OSError):
                    settings.save(scale="small")
            self.assertEqual(Settings(Path(directory)).data["scale"], "large")
            self.assertEqual(list(Path(directory).glob(".settings-*")), [])

    def test_independent_default_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Settings(Path(directory) / "one")
            first.data["zapret"]["domains"].append("example.org")
            second = Settings(Path(directory) / "two")
            self.assertNotIn("example.org", second.data["zapret"]["domains"])

    def test_duplicate_controller_buttons_rejected(self):
        data = deepcopy(DEFAULTS)
        data["controller"]["menu"] = data["controller"]["home"]
        with self.assertRaises(ValueError):
            validate(data)

    def test_old_settings_gain_browser_default_without_losing_network_config(self):
        data = deepcopy(DEFAULTS)
        del data["browser"]
        data["zapret"]["interface"] = "wlan0"
        data["scale"] = "large"
        restored = validate(data)
        self.assertEqual(restored["browser"], DEFAULTS["browser"])
        self.assertEqual(restored["zapret"], data["zapret"])
        self.assertEqual(restored["scale"], "large")


class ValidationTests(unittest.TestCase):
    def test_browser_addresses_support_https_idn_and_local_networks(self):
        for value, expected in {
            "Example.org/path?q=tv#video": "https://example.org/path?q=tv#video",
            " пример.рф ": "https://xn--e1afmkfd.xn--p1ai/",
            "http://localhost:8080": "http://localhost:8080/",
            "raspberrypi.local:8080/info": "https://raspberrypi.local:8080/info",
            "http://[::1]:8080/": "http://[::1]:8080/",
            "example.org/я?q=ю": "https://example.org/%D1%8F?q=%D1%8E",
        }.items():
            with self.subTest(value=value):
                self.assertEqual(normalize_url(value), expected)

    def test_browser_rejects_non_web_schemes_credentials_and_invalid_hosts(self):
        for value in ("", "javascript:alert(1)", "data:text/html,hello", "file:///etc/passwd", "chrome://settings",
                      "--no-sandbox", "https://user:secret@example.org", "https:///missing", "https://bad_name.org",
                      "https://example.org:invalid", "example.org\nfile:///etc/passwd", "example.org\\@other.org", "https://[bad]/"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_url(value)

    def test_browser_profile_is_separate_and_start_page_is_validated(self):
        with patch("raspberry_tv.processes.shutil.which", return_value="/usr/bin/chromium"):
            browser = app_command("browser", Path("state"), "example.org")
            youtube = app_command("youtube", Path("state"))
            with self.assertRaises(ValueError):
                app_command("browser", Path("state"), "file:///etc/passwd")
        self.assertEqual(browser[-1], "https://example.org/")
        self.assertIn("--start-fullscreen", browser)
        self.assertNotIn("--kiosk", browser)
        self.assertNotIn("--no-sandbox", browser)
        profile = lambda argv: next(x for x in argv if x.startswith("--user-data-dir="))
        self.assertNotEqual(profile(browser), profile(youtube))

    def test_domains_normalize_deduplicate_and_support_idn(self):
        self.assertEqual(normalize_domains("YouTube.com\nyoutube.com,пример.рф;ytimg.com."),
                         ["youtube.com", "xn--e1afmkfd.xn--p1ai", "ytimg.com"])

    def test_domains_reject_paths_ips_and_shell_syntax(self):
        for value in ("https://example.org/", "127.0.0.1", "localhost", "*.example.org", "bad_name.org", "$(id).org"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_domains(value)

    def test_youtube_command_uses_kiosk_private_profile_and_browser_sandbox(self):
        with patch("raspberry_tv.processes.shutil.which", return_value="/usr/bin/chromium"):
            command = app_command("youtube", Path("state"))
        self.assertEqual(command[-1], "https://www.youtube.com/")
        self.assertNotIn("--no-sandbox", command)
        self.assertIn("--kiosk", command)
        self.assertTrue(any("browsers" in x and "youtube" in x for x in command if x.startswith("--user-data-dir=")))

    def test_kodi_launches_native_application(self):
        with patch("raspberry_tv.processes.shutil.which", return_value="/usr/bin/kodi"):
            self.assertEqual(app_command("kodi", Path("state")), ["kodi", "--standalone"])

    def test_unknown_application_is_rejected(self):
        with self.assertRaises(Unavailable):
            app_command("unconfigured-app", Path("state"))


class LinuxParsingTests(unittest.TestCase):
    def test_nmcli_escaped_ssid(self):
        self.assertEqual(split_nmcli(r"Living\:Room\\TV:78:WPA2:wlan0"), ["Living:Room\\TV", "78", "WPA2", "wlan0"])

    def test_xrandr_outputs_and_current_refresh_rate(self):
        result = parse_outputs("""Screen 0: minimum 320 x 200
HDMI-1 connected primary 1920x1080+0+0 (normal left inverted right x axis y axis)
   1920x1080    60.00*+  50.00   59.94
   1280x720     60.00
HDMI-2 disconnected (normal left inverted right x axis y axis)
""")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["current"], "1920x1080")
        self.assertEqual(result[0]["rate"], "60.00")
        self.assertEqual(len(result[0]["modes"]), 4)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.reader = InputReader(DEFAULTS["controller"], self.events.append, lambda x: None)

    def test_short_home_press(self):
        self.reader.button(316, 1, 1.0)
        self.reader.button(316, 0, 1.2)
        self.assertEqual(self.events, ["home"])

    def test_face_shortcuts_trigger_once_on_press(self):
        for code in (307, 308):
            for value in (1, 2, 0):
                self.reader.button(code, value, 1.0)
        self.assertEqual(self.events, ["fullscreen", "escape"])

    def test_older_controller_settings_gain_shortcuts(self):
        from raspberry_tv.config import validate
        old = {"home": 316, "menu": 315, "accept": 304, "back": 305}
        settings = validate({"controller": old})
        self.assertEqual(settings["controller"], DEFAULTS["controller"])
        with self.assertRaises(ValueError):
            validate({"controller": {**old, "escape": 304}})

    def test_long_home_press_does_not_also_trigger_home_on_release(self):
        self.reader.button(316, 1, 1.0)
        self.reader.tick(2.3)
        self.reader.tick(3.0)
        self.reader.button(316, 0, 3.1)
        self.assertEqual(self.events, ["power"])

    def test_direction_repeat_stops_when_stick_is_released(self):
        self.reader.direction((1, 0), 1, True, 0)
        self.reader.tick(0.2)
        self.reader.tick(0.4)
        self.reader.direction((1, 0), 0, True, 0.5)
        self.reader.tick(1.0)
        self.assertEqual(self.events, ["right", "right"])


if __name__ == "__main__":
    unittest.main()
