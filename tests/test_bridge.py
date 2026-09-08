"""Exercise asynchronous service dispatch without changing real network rules."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from PySide6.QtGui import QGuiApplication
from raspberry_tv.bridge import Bridge


class ServiceDispatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def test_zapret_actions_reach_client_after_draft_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge = Bridge(Path(directory), preview=True)
            errors = []
            bridge.notice.connect(lambda title, message: errors.append(message) if title == "Не получилось" else None)
            try:
                bridge.action("profile")
                bridge.action("tcp")
                with patch.object(bridge, "_need_pi"), \
                        patch("raspberry_tv.bridge.zapret.request", return_value={"enabled": False}) as request, \
                        patch("raspberry_tv.bridge.zapret.status", return_value={"zapret": {"enabled": False}}):
                    for action, operation, confirmation in (
                        ("zapret_apply", "apply", True), ("zapret_toggle", "enable", True),
                        ("zapret_tune", "tune", True), ("zapret_confirm", "confirm", False),
                        ("zapret_rollback", "rollback", False), ("zapret_cancel", "cancel", False),
                    ):
                        with self.subTest(action=action):
                            request.reset_mock()
                            bridge.action(action)
                            if confirmation:
                                bridge.action("confirmed")
                            deadline = time.monotonic() + 3
                            while bridge.pending and time.monotonic() < deadline:
                                self.app.processEvents()
                                time.sleep(.005)
                            self.assertFalse(bridge.pending)
                            self.assertEqual(errors, [])
                            self.assertEqual(request.call_count, 1)
                            self.assertEqual(request.call_args.args, (operation,))
                            if confirmation:
                                self.assertEqual(request.call_args.kwargs["config"], bridge.settings.data["zapret"])
            finally:
                bridge.shutdown()
