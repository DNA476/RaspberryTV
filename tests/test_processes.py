from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from raspberry_tv.processes import Applications, RunningApp, Unavailable


class BrowserTests(unittest.TestCase):
    def setUp(self):
        self.apps = Applications(Path("state"))
        self.process = Mock(pid=123)
        self.process.poll.return_value = None
        self.apps.running["browser"] = RunningApp(self.process, None, 0, "0x10")
        self.apps.active = "browser"

    def test_reopening_live_browser_preserves_process_and_session(self):
        with patch("raspberry_tv.processes.subprocess.Popen") as spawn, patch.object(self.apps, "resume") as resume:
            self.apps.launch("browser", "https://example.org/")
        spawn.assert_not_called()
        resume.assert_called_once()
        self.assertIs(self.apps.running["browser"].process, self.process)

    def test_address_is_typed_only_after_focusing_owned_window(self):
        calls = []
        def run(argv, **kwargs):
            calls.append((argv, kwargs))
            return "16" if argv[1] == "getactivewindow" else ""
        with patch("raspberry_tv.processes.run", side_effect=run):
            self.apps.browser_action("address", "example.org/watch?q=test")
        self.assertEqual(calls[0][0], ["xdotool", "windowactivate", "--sync", "0x10"])
        typed = [(argv, kwargs) for argv, kwargs in calls if argv[1] == "type"]
        self.assertEqual(len(typed), 1)
        self.assertEqual(typed[0][1]["input_text"], "https://example.org/watch?q=test")
        self.assertNotIn("https://example.org/watch?q=test", typed[0][0])
        self.assertEqual(calls[-1][0][-1], "Return")

    def test_focus_loss_before_typing_stops_address_and_submit(self):
        def run(argv, **kwargs):
            if argv[1] == "getactivewindow":
                return next(focus)
            return ""
        focus = iter(["16", "99"])
        with patch("raspberry_tv.processes.run", side_effect=run) as command:
            with self.assertRaises(Unavailable):
                self.apps.browser_action("address", "example.org")
        self.assertFalse(any(call.args[0][1] == "type" or call.args[0][-1] == "Return" for call in command.call_args_list))

    def test_browser_actions_refuse_unowned_or_exited_applications(self):
        with patch("raspberry_tv.processes.run") as command:
            self.apps.active = "kodi"
            with self.assertRaises(Unavailable):
                self.apps.browser_action("back")
            self.apps.active = "browser"
            self.process.poll.return_value = 0
            with self.assertRaises(Unavailable):
                self.apps.browser_action("reload")
        command.assert_not_called()

    def test_history_keys_use_browser_shortcuts(self):
        for action, key in (("back", "alt+Left"), ("forward", "alt+Right"), ("reload", "ctrl+r")):
            with self.subTest(action=action), patch("raspberry_tv.processes.run", return_value="16") as command:
                self.apps.browser_action(action)
                command.assert_called_with(["xdotool", "key", "--clearmodifiers", key])


class WindowOwnershipTests(unittest.TestCase):
    def test_kodi_without_pid_is_identified_by_xorg_and_child_process(self):
        apps = Applications(Path("state"))
        apps.running["kodi"] = RunningApp(SimpleNamespace(pid=100), None, 0)
        child_stat = Mock()
        child_stat.parent.name = "101"
        child_stat.read_text.return_value = "101 (kodi.bin) S 100 100 100"
        rows = "0x01 0 0 host Another Kodi\n0x02 0 0 host Kodi"
        with patch("raspberry_tv.processes.run", return_value=rows) as command, \
                patch.object(Path, "glob", return_value=[child_stat]), \
                patch("raspberry_tv.processes.x11_window_pids", return_value={"0x01": 999, "0x02": 101}):
            apps.discover_windows()
        self.assertEqual(apps.running["kodi"].window, "0x02")
        command.assert_called_with(["wmctrl", "-ir", "0x02", "-b", "add,fullscreen"], check=False)

    def test_unidentified_window_is_never_claimed_by_its_title(self):
        apps = Applications(Path("state"))
        apps.running["kodi"] = RunningApp(SimpleNamespace(pid=100), None, 0)
        with patch("raspberry_tv.processes.run", return_value="0x01 0 0 host Kodi") as command, \
                patch.object(Path, "glob", return_value=[]), \
                patch("raspberry_tv.processes.x11_window_pids", return_value={}):
            apps.discover_windows()
        self.assertEqual(apps.running["kodi"].window, "")
        self.assertEqual(command.call_count, 1)
