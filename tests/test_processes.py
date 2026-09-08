from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from raspberry_tv.processes import Applications, RunningApp


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
