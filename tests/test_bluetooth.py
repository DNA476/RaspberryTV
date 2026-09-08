import subprocess
import sys
import unittest
from unittest.mock import patch

from raspberry_tv.bluetooth import pair_with_agent
from raspberry_tv.linux import LinuxSystem
from raspberry_tv.processes import Unavailable

ADDRESS = "50:EE:32:EC:F7:F6"
PAIRED = {"Paired": "yes", "Bonded": "yes", "Connected": "yes", "Icon": "input-gaming"}


class BluetoothTests(unittest.TestCase):
    def test_trusted_device_still_needs_persistent_pairing(self):
        with patch("raspberry_tv.linux.device_info", side_effect=[{"Trusted": "yes"}, PAIRED, PAIRED]), \
                patch("raspberry_tv.linux.pair_with_agent") as pair, \
                patch("raspberry_tv.linux.run", return_value="Connection successful"), \
                patch("raspberry_tv.linux.input_ready", return_value=True):
            LinuxSystem().bluetooth("pair", ADDRESS)
            pair.assert_called_once_with(ADDRESS)

    def test_success_message_without_bond_does_not_count_as_pairing(self):
        with patch("raspberry_tv.linux.device_info", side_effect=[{}, {"Paired": "yes", "Bonded": "no"}]), \
                patch("raspberry_tv.linux.pair_with_agent"), \
                patch("raspberry_tv.linux.run") as command:
            with self.assertRaisesRegex(Unavailable, "не сохранил"):
                LinuxSystem().bluetooth("pair", ADDRESS)
            command.assert_not_called()

    def test_reconnect_keeps_existing_bond(self):
        with patch("raspberry_tv.linux.device_info", return_value=PAIRED), \
                patch("raspberry_tv.linux.pair_with_agent") as pair, \
                patch("raspberry_tv.linux.run", return_value="Connection successful"), \
                patch("raspberry_tv.linux.input_ready", return_value=True):
            LinuxSystem().bluetooth("connect", ADDRESS)
            pair.assert_not_called()

    def test_connected_without_input_is_reported_as_not_ready(self):
        with patch("raspberry_tv.linux.device_info", return_value=PAIRED), \
                patch("raspberry_tv.linux.run", return_value="Connection successful"), \
                patch("raspberry_tv.linux.input_ready", return_value=False), \
                patch("raspberry_tv.linux.time.monotonic", side_effect=[0, 0, 9]), \
                patch("raspberry_tv.linux.time.sleep"):
            with self.assertRaisesRegex(Unavailable, "не готово"):
                LinuxSystem().bluetooth("connect", ADDRESS)

    def test_temporary_pairing_is_renewed_for_this_address_only(self):
        with patch("raspberry_tv.linux.device_info", side_effect=[{"Paired": "yes", "Bonded": "no"}, PAIRED, PAIRED]), \
                patch("raspberry_tv.linux.pair_with_agent"), \
                patch("raspberry_tv.linux.run", return_value="Connection successful") as command, \
                patch("raspberry_tv.linux.input_ready", return_value=True):
            LinuxSystem().bluetooth("pair", ADDRESS)
            self.assertEqual(command.call_args_list[0].args[0], ["bluetoothctl", "remove", ADDRESS])

    def test_malformed_address_is_rejected_before_running_commands(self):
        with patch("raspberry_tv.linux.run") as command:
            with self.assertRaises(ValueError):
                LinuxSystem().bluetooth("pair", ":" * 17)
            command.assert_not_called()


@unittest.skipUnless(sys.platform == "linux", "Linux pipes are polled with select")
class AgentProcessTests(unittest.TestCase):
    def test_agent_stays_alive_until_pairing_completes(self):
        original = subprocess.Popen
        program = ('import sys; print("Agent registered", flush=True); '
                   'assert sys.stdin.readline().strip() == "pair ' + ADDRESS + '"; '
                   'print("Pairing successful", flush=True); assert sys.stdin.readline().strip() == "quit"')
        def start(argv, **kwargs):
            self.assertEqual(argv, ["bluetoothctl", "--agent", "NoInputNoOutput"])
            return original([sys.executable, "-u", "-c", program], **kwargs)
        with patch("raspberry_tv.bluetooth.subprocess.Popen", side_effect=start):
            pair_with_agent(ADDRESS)

    def test_failed_pairing_is_not_reported_as_success(self):
        original = subprocess.Popen
        program = ('import sys; print("Agent registered", flush=True); sys.stdin.readline(); '
                   'print("Failed to pair: org.bluez.Error.AuthenticationFailed", flush=True); sys.stdin.readline()')
        with patch("raspberry_tv.bluetooth.subprocess.Popen", side_effect=lambda argv, **kw: original([sys.executable, "-u", "-c", program], **kw)):
            with self.assertRaises(Unavailable):
                pair_with_agent(ADDRESS)
