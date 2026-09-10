from pathlib import Path
from types import SimpleNamespace
import unittest
import subprocess
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

    def test_address_opens_in_existing_profile_without_synthetic_text_input(self):
        request = Mock()
        request.wait.return_value = 0
        with patch("raspberry_tv.processes.run", return_value="16") as command, \
                patch("raspberry_tv.processes.shutil.which", return_value="/usr/bin/chromium"), \
                patch("raspberry_tv.processes.subprocess.Popen", return_value=request) as spawn:
            self.apps.browser_action("address", "example.org/watch?q=test")
        argv = spawn.call_args.args[0]
        self.assertEqual(argv[-1], "https://example.org/watch?q=test")
        self.assertNotIn("--start-fullscreen", argv)
        self.assertIn(f"--user-data-dir={Path('state') / 'browsers' / 'browser'}", argv)
        self.assertFalse(any(call.args[0][1] in ("key", "type") for call in command.call_args_list))
        self.assertIs(self.apps.running["browser"].process, self.process)

    def test_focus_loss_cancels_browser_action(self):
        with patch("raspberry_tv.processes.run", return_value="99") as command, \
                patch("raspberry_tv.processes.subprocess.Popen") as spawn:
            with self.assertRaises(Unavailable):
                self.apps.browser_action("address", "example.org")
        spawn.assert_not_called()
        self.assertFalse(any(call.args[0][1] == "key" for call in command.call_args_list))

    def test_timed_out_url_request_is_terminated_without_logging_address(self):
        request = Mock()
        request.wait.side_effect = subprocess.TimeoutExpired(["chromium", "https://example.org/?private=value"], 5)
        with patch("raspberry_tv.processes.run", return_value="16"), \
                patch("raspberry_tv.processes.shutil.which", return_value="/usr/bin/chromium"), \
                patch("raspberry_tv.processes.subprocess.Popen", return_value=request), \
                patch.object(self.apps, "_terminate") as terminate:
            with self.assertRaises(Unavailable) as caught:
                self.apps.browser_action("address", "example.org/?private=value")
        self.assertIs(terminate.call_args.args[0].process, request)
        self.assertNotIn("private", str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)

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
        for action, key in (("back", "alt+Left"), ("forward", "alt+Right"), ("reload", "F5")):
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
