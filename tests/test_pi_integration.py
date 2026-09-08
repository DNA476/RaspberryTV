from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from raspberry_tv.kodi import prepare_cec
from raspberry_tv.linux import LinuxSystem


class KodiSettingsTests(unittest.TestCase):
    def test_keep_tv_on_preserves_other_settings_and_original_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cec_CEC_Adapter.xml"
            original = '<settings><setting id="enabled" value="1"/><setting id="standby_devices" value="36037"/></settings>'
            path.write_text(original, encoding="utf-8")
            prepare_cec(Path(directory))
            first = path.read_bytes()
            settings = {s.get("id"): s.get("value") for s in ET.parse(path).getroot()}
            self.assertEqual(settings["enabled"], "1")
            self.assertEqual(settings["standby_devices"], "231")
            self.assertEqual(settings["send_inactive_source"], "0")
            prepare_cec(Path(directory))
            self.assertEqual(path.read_bytes(), first)
            self.assertEqual(path.with_suffix(".xml.before-raspberry-tv").read_text(), original)

    def test_invalid_kodi_settings_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cec_CEC_Adapter.xml"
            path.write_text("<broken", encoding="utf-8")
            with self.assertRaises(ValueError):
                prepare_cec(Path(directory))
            self.assertEqual(path.read_text(), "<broken")


class HdmiReconnectTests(unittest.TestCase):
    def test_reconnected_tv_is_enabled_when_no_output_is_active(self):
        before = "HDMI-2 connected (normal)\n   1280x720 50.00+"
        after = "HDMI-2 connected primary 1280x720+0+0 (normal)\n   1280x720 50.00*+"
        with patch("raspberry_tv.linux.run", side_effect=[before, "", after]) as command:
            outputs = LinuxSystem().display_outputs()
        self.assertEqual(outputs[0]["current"], "1280x720")
        self.assertEqual(command.call_args_list[1].args[0], ["xrandr", "--output", "HDMI-2", "--auto", "--primary"])

    def test_active_display_and_disconnected_tv_are_not_reconfigured(self):
        for output in ("HDMI-2 disconnected", "HDMI-2 connected 1280x720+0+0 (normal)\n   1280x720 50.00*+"):
            with self.subTest(output=output), patch("raspberry_tv.linux.run", return_value=output) as command:
                LinuxSystem().display_outputs()
                self.assertEqual(command.call_count, 1)
