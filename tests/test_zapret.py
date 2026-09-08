from copy import deepcopy
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from raspberry_tv.config import DEFAULTS
from raspberry_tv.zapret_service import Manager, firewall_rules, scoped_arguments, validated_config, probe


class FakeEngine:
    def __init__(self):
        self.up = False
        self.fail = False
        self.config = None

    def start(self, config):
        if self.fail:
            self.fail = False
            raise ValueError("rejected strategy")
        self.up, self.config = True, deepcopy(config)

    def stop(self):
        self.up = False

    def running(self):
        return self.up


class ZapretTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.engine = FakeEngine()
        self.catalog = {"general.bat": Path("general.bat"), "general_alt.bat": Path("general_alt.bat")}
        self.manager = self.make_manager()
        self.config = {**deepcopy(DEFAULTS["zapret"]), "interface": "wlan0"}

    def make_manager(self):
        return Manager(self.directory, self.engine, self.catalog, lambda: ["wlan0"])

    def test_trial_survives_gui_loss_and_times_out(self):
        self.manager.handle({"action": "apply", "config": self.config})
        self.assertTrue(self.engine.running())
        self.manager.deadline = time.monotonic() - 1
        self.manager.tick()
        self.assertFalse(self.engine.running())
        self.assertFalse(self.manager.status()["pending"])

    def test_reboot_rolls_back_unconfirmed_change(self):
        self.manager.handle({"action": "apply", "config": self.config})
        restarted = self.make_manager()
        self.assertFalse(restarted.status()["enabled"])
        self.assertFalse(self.engine.running())

    def test_confirmed_settings_survive_restart_and_support_rollback(self):
        self.manager.handle({"action": "apply", "config": self.config})
        self.manager.handle({"action": "confirm"})
        restarted = self.make_manager()
        self.assertTrue(self.engine.running())
        restarted.handle({"action": "rollback"})
        self.assertFalse(self.engine.running())

    def test_failed_start_restores_confirmed_strategy(self):
        self.manager.handle({"action": "apply", "config": self.config})
        self.manager.handle({"action": "confirm"})
        self.engine.fail = True
        with self.assertRaises(ValueError):
            self.manager.handle({"action": "apply", "config": {**self.config, "strategy": "general_alt.bat"}})
        self.assertTrue(self.engine.running())
        self.assertEqual(self.engine.config["strategy"], "general.bat")

    def test_injection_and_unknown_strategies_rejected_before_apply(self):
        for change in ({"strategy": "../evil.bat"}, {"strategy": "evil.bat"},
                       {"interface": 'wlan0";flush'}, {"interface": "eth0"},
                       {"domains": ["https://x.com/$(id)"]}, {"domains": []}, {"backend": "iptables"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validated_config({**self.config, **change}, self.catalog, ["wlan0"])

    def test_private_destinations_are_excluded_without_excluding_pi_source(self):
        rules = firewall_rules("80,443", "443,50000-50100", "wlan0")
        self.assertIn("post ip daddr", rules)
        self.assertNotIn("post ip saddr", rules)
        self.assertIn("pre ip saddr", rules)
        self.assertIn("192.168.0.0/16", rules)
        self.assertIn("fc00::/7", rules)
        self.assertIn("22, 47984", rules)
        self.assertIn("queue num 221 bypass", rules)
        self.assertNotIn("flush", rules)
        for ports in ("443; flush ruleset", "0", "65536", "300-100", "443,,80"):
            with self.subTest(ports=ports), self.assertRaises(ValueError):
                firewall_rules(ports, "443", "wlan0")

    def test_gui_scope_replaces_all_upstream_positive_scopes(self):
        args = scoped_arguments([
            "--filter-tcp=443 --hostlist=lists/general --hostlist-domains=discord.media --dpi-desync=fake --new",
            "--filter-udp=443 --ipset=lists/all --ipset-exclude=lists/exclude --dpi-desync-repeats=6",
        ], "/domains.txt")
        self.assertNotIn("--hostlist=lists/general", args)
        self.assertNotIn("--hostlist-domains=discord.media", args)
        self.assertNotIn("--ipset=lists/all", args)
        self.assertIn("--ipset-exclude=lists/exclude", args)
        self.assertIn("--dpi-desync=fake", args)
        self.assertEqual(args.count("--hostlist=/domains.txt"), 2)
        self.assertEqual(args.count("--new"), 1)

    def test_probe_never_requests_private_addresses(self):
        with patch("subprocess.run") as run:
            run.return_value.stdout = "192.168.0.1 STREAM example.com\n"
            run.return_value.returncode = 0
            self.assertFalse(probe("example.com", "wlan0")["ok"])
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0][0], "getent")

    def test_tuning_restores_previous_state_and_preserves_results(self):
        with patch("raspberry_tv.zapret_service.probe", return_value={"ok": True}):
            self.manager.handle({"action": "tune", "config": self.config})
            deadline = time.monotonic() + 3
            while self.manager.tuning and time.monotonic() < deadline:
                time.sleep(0.01)
        self.assertFalse(self.manager.tuning)
        self.assertFalse(self.engine.running())
        self.assertEqual(len(self.manager.results), 2)
        self.assertEqual(self.manager.results[0]["passed"], 3)
        self.assertFalse(self.manager.status()["pending"])


if __name__ == "__main__":
    unittest.main()
