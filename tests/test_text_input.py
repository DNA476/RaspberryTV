import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch

from raspberry_tv.config import DEFAULTS
from raspberry_tv.input import InputReader
from raspberry_tv.processes import Applications, RunningApp, TextTarget, Unavailable
from raspberry_tv.text_input import InputFailure, validate_text, server_lock


class TextDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.apps = Applications(Path("state"))
        self.app = RunningApp(Mock(poll=Mock(return_value=None)), None, 0, "0x10")
        self.apps.active = "browser"
        self.apps.running["browser"] = self.app
        self.target = TextTarget("browser", self.app, "0x10", 17)

    def test_text_uses_stdin_and_enter_is_explicit(self):
        for enter in (False, True):
            with self.subTest(enter=enter):
                worker = Mock(returncode=0, poll=Mock(return_value=0))
                with patch("raspberry_tv.processes.run"), patch("raspberry_tv.processes.subprocess.Popen", return_value=worker) as spawn:
                    self.apps.insert_text(self.target, "Test Привет !", enter)
                self.assertNotIn("Привет", repr(spawn.call_args))
                payload = json.loads(worker.communicate.call_args.args[0])
                self.assertEqual(payload, {"text": "Test Привет !", "enter": enter})

    def test_replaced_closed_or_changed_window_cannot_receive_text(self):
        for change in (lambda: self.apps.running.clear(),
                       lambda: setattr(self.app, "window", "0x20"),
                       lambda: setattr(self.app.process.poll, "return_value", 0),
                       lambda: setattr(self.apps, "active", "youtube")):
            self.setUp()
            change()
            with patch("raspberry_tv.processes.subprocess.Popen") as spawn, self.assertRaises(Unavailable):
                self.apps.insert_text(self.target, "private")
            spawn.assert_not_called()

    def test_cancel_terminates_worker_and_does_not_repeat_partial_text(self):
        worker = Mock(poll=Mock(return_value=None))
        def interrupt(*args, **kwargs):
            self.apps.text_cancelled.set()
            raise subprocess.TimeoutExpired("worker", .1)
        worker.communicate.side_effect = interrupt
        with patch("raspberry_tv.processes.run"), patch("raspberry_tv.processes.subprocess.Popen", return_value=worker), self.assertRaises(Unavailable):
            self.apps.insert_text(self.target, "private")
        self.assertEqual(worker.communicate.call_count, 1)
        worker.terminate.assert_called_once()
        worker.wait.assert_called_once()

    def test_worker_exception_cannot_expose_text_in_traceback(self):
        worker = Mock(poll=Mock(return_value=1))
        worker.communicate.side_effect = RuntimeError("private-buffer")
        with patch("raspberry_tv.processes.run"), patch("raspberry_tv.processes.subprocess.Popen", return_value=worker):
            with self.assertRaises(Unavailable) as caught:
                self.apps.insert_text(self.target, "private-buffer")
        import traceback
        self.assertNotIn("private-buffer", "".join(traceback.format_exception(caught.exception)))

    def test_control_characters_and_oversize_input_fail_before_spawn(self):
        for value in ("line\nline", "\t", "\x00", "a" * 513, "\ud800"):
            with self.subTest(value=repr(value)), self.assertRaises(InputFailure):
                validate_text(value)
        validate_text("Привет, ABC 123 !@#")

    def test_server_is_released_even_when_focus_validation_raises(self):
        display = Mock()
        with self.assertRaises(InputFailure), server_lock(display):
            raise InputFailure("focus")
        display.ungrab_server.assert_called_once()
        display.sync.assert_called_once()


class ControllerCaptureTests(unittest.TestCase):
    def setUp(self):
        self.reader = InputReader(DEFAULTS["controller"], Mock(), Mock())
        self.device = Mock(fd=10)
        self.reader.add_device(self.device)

    def test_capture_is_temporary_and_idempotent(self):
        self.assertTrue(self.reader.set_capture(True))
        self.assertTrue(self.reader.set_capture(True))
        self.device.grab.assert_called_once()
        self.assertTrue(self.reader.set_capture(False))
        self.device.ungrab.assert_called_once()
        self.assertFalse(self.reader.grabbed)

    def test_partial_capture_failure_releases_previous_devices(self):
        second = Mock(fd=20)
        second.grab.side_effect = OSError("busy")
        self.reader.add_device(second)
        self.assertFalse(self.reader.set_capture(True))
        self.device.ungrab.assert_called_once()
        self.assertFalse(self.reader.captured)

    def test_reconnected_controller_is_captured_while_editor_open(self):
        self.reader.set_capture(True)
        second = Mock(fd=20)
        self.reader.add_device(second)
        second.grab.assert_called_once()
        self.reader.set_capture(False)
        second.ungrab.assert_called_once()
