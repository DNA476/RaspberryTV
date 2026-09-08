"""Exercise asynchronous service dispatch without changing real network rules."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from PySide6.QtGui import QGuiApplication
from raspberry_tv.bridge import Bridge
from raspberry_tv.processes import RunningApp, TextTarget, Unavailable


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


class KeyboardStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.bridge = Bridge(Path(self.directory.name), preview=True)
        self.bridge.input_reader = Mock(set_capture=Mock(return_value=True))
        app = RunningApp(Mock(poll=Mock(return_value=None)), None, 0, "0x10")
        self.target = TextTarget("browser", app, "0x10", 17)
        self.bridge.apps.running["browser"] = app
        self.bridge.apps.active = "browser"
        self.bridge._update(activeApp="browser")
        self.bridge._page("application")
        self.messages = []
        self.bridge.notice.connect(lambda *args: self.messages.append(args))

    def tearDown(self):
        self.bridge.shutdown()
        self.directory.cleanup()

    def wait(self):
        deadline = time.monotonic() + 3
        while self.bridge.pending and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertFalse(self.bridge.pending)

    def open_keyboard(self):
        with patch.object(self.bridge.apps, "text_target", return_value=self.target):
            self.bridge.action("menu")
            self.wait()
        with patch.object(self.bridge, "_need_pi"):
            self.bridge.action("keyboard")
        self.assertEqual(self.bridge.state["editor"]["action"], "external_text")

    def test_preview_never_sends_text(self):
        self.open_keyboard()
        with patch.object(self.bridge.apps, "insert_text") as send:
            self.bridge.action("editor_save", "private")
            send.assert_not_called()

    def test_send_clears_editor_and_releases_controller_without_persisting_text(self):
        for action, enter in (("editor_save", False), ("editor_send_enter", True)):
            with self.subTest(action=action):
                self.open_keyboard()
                with patch.object(self.bridge, "_need_pi"), patch.object(self.bridge.apps, "insert_text") as send:
                    self.bridge.action(action, "private-buffer")
                    self.wait()
                send.assert_called_once_with(self.target, "private-buffer", enter)
                self.assertEqual(self.bridge.state["editor"], {})
                self.assertIsNone(self.bridge._text_target)
                self.bridge.input_reader.set_capture.assert_called_with(False)
                self.assertNotIn("private-buffer", repr(self.bridge.state))
                self.assertFalse((Path(self.directory.name) / "settings.json").exists())

    def test_send_error_clears_buffer_and_requires_new_target(self):
        self.open_keyboard()
        with patch.object(self.bridge, "_need_pi"), patch.object(self.bridge.apps, "insert_text", side_effect=Unavailable("Ввод остановлен")), patch("raspberry_tv.bridge.LOG.exception"):
            self.bridge.action("editor_save", "private-buffer")
            self.wait()
        self.assertEqual(self.bridge.state["page"], "quick")
        self.assertEqual(self.bridge.state["editor"], {})
        self.assertFalse(self.bridge.state["keyboardAvailable"])

    def test_home_cancels_active_delivery_and_late_callback_cannot_restore_editor(self):
        self.open_keyboard()
        def sending(*args):
            self.bridge.apps.text_cancelled.wait(2)
        with patch.object(self.bridge, "_need_pi"), patch.object(self.bridge.apps, "insert_text", side_effect=sending):
            self.bridge.action("editor_save", "private-buffer")
            self.bridge.action("home")
            self.wait()
        self.assertTrue(self.bridge.apps.text_cancelled.is_set())
        self.assertEqual(self.bridge.state["page"], "home")
        self.assertEqual(self.bridge.state["editor"], {})
        self.bridge.input_reader.set_capture.assert_called_with(False)

    def test_disconnect_clears_editor_and_releases_capture(self):
        self.open_keyboard()
        self.bridge._controller_status("Контроллер отключён")
        self.assertEqual(self.bridge.state["editor"], {})
        self.assertIsNone(self.bridge._text_target)
        self.bridge.input_reader.set_capture.assert_called_with(False)
