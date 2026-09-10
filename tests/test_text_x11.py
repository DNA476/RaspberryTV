"""Opt-in transport checks on Xvfb, never on the television's X11 session."""
import json
import os
import shutil
import subprocess
import sys
import time
import unittest


@unittest.skipUnless(os.environ.get("RASPBERRY_TV_X11_TESTS") == "1", "isolated X11 tests are opt-in")
class X11TextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from Xlib import X, Xatom
        from Xlib.display import Display
        executable = os.environ.get("RASPBERRY_TV_XVFB") or shutil.which("Xvfb")
        if not executable:
            raise unittest.SkipTest("Xvfb is unavailable")
        cls.X, cls.Xatom = X, Xatom
        cls.old_display = os.environ.get("DISPLAY")
        cls.old_auth = os.environ.get("XAUTHORITY")
        cls.server = subprocess.Popen([executable, "-displayfd", "1", "-screen", "0", "800x600x24", "-nolisten", "tcp", "-ac"],
                                      stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        cls.addClassCleanup(cls.cleanup_server)
        number = cls.server.stdout.readline().decode().strip()
        if not number.isdigit():
            raise RuntimeError("Xvfb did not start")
        os.environ["DISPLAY"] = ":" + number
        os.environ.pop("XAUTHORITY", None)
        cls.display = Display()
        cls.addClassCleanup(cls.display.close)
        cls.desktop = cls.display.screen().root
        cls.window = cls.desktop.create_window(10, 10, 400, 200, 0, cls.display.screen().root_depth,
                                               event_mask=X.KeyPressMask | X.KeyReleaseMask)
        cls.other = cls.desktop.create_window(450, 10, 200, 200, 0, cls.display.screen().root_depth, event_mask=X.KeyPressMask)
        cls.window.map(); cls.other.map()
        cls.low = cls.display.display.info.min_keycode
        cls.high = cls.display.display.info.max_keycode

    @classmethod
    def cleanup_server(cls):
        cls.server.terminate()
        cls.server.wait(timeout=3)
        cls.server.stdout.close()
        for key, value in (("DISPLAY", cls.old_display), ("XAUTHORITY", cls.old_auth)):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def focus(self, window):
        self.desktop.change_property(self.display.intern_atom("_NET_ACTIVE_WINDOW"), self.Xatom.WINDOW, 32, [window.id])
        window.set_input_focus(self.X.RevertToParent, self.X.CurrentTime)
        self.display.sync()

    def check_delivery(self, text, *, enter=False, steal=False, cancel=False):
        self.focus(self.window)
        while self.display.pending_events():
            self.display.next_event()
        before = list(map(list, self.display.get_keyboard_mapping(self.low, self.high-self.low+1)))
        with subprocess.Popen([sys.executable, "-m", "raspberry_tv.text_input", str(self.window.id), str(self.window.id)],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as worker:
            worker.stdin.write(json.dumps(dict(text=text, enter=enter), ensure_ascii=False).encode())
            worker.stdin.close()
            received, leaked = [], []
            deadline = time.monotonic() + 15
            while worker.poll() is None or self.display.pending_events():
                if time.monotonic() > deadline:
                    worker.kill()
                    self.fail("X11 transport hung")
                while self.display.pending_events():
                    event = self.display.next_event()
                    if event.type == self.X.MappingNotify:
                        self.display.refresh_keyboard_mapping(event)
                    elif event.type == self.X.KeyPress:
                        symbol = self.display.keycode_to_keysym(event.detail, 0)
                        value = "<Enter>" if symbol == 0xff0d else chr(symbol & 0xffffff if symbol >= 0x1000000 else symbol)
                        (received if event.window.id == self.window.id else leaked).append(value)
                        if len(received) == 2:
                            if steal:
                                self.focus(self.other)
                            if cancel:
                                worker.terminate()
                time.sleep(.001)
            self.assertEqual(worker.stdout.read() + worker.stderr.read(), b"")
            self.assertEqual(leaked, [])
            self.assertFalse(any(self.display.query_keymap()), "a synthetic key stayed pressed")
            self.assertEqual(before, list(map(list, self.display.get_keyboard_mapping(self.low, self.high-self.low+1))))
            if steal or cancel:
                self.assertNotEqual(worker.returncode, 0)
                self.assertGreater(len(received), 0)
                self.assertLess(len(received), len(text))
            else:
                self.assertEqual(worker.returncode, 0)
                self.assertEqual(received, list(text) + (["<Enter>"] if enter else []))

    def test_unicode_and_punctuation(self):
        self.check_delivery("Hello, Мир! ABC абв ёЁ 123 @#?=&% ()[]{}<>/\\|\"'`~")

    def test_enter_is_sent_only_when_requested(self):
        self.check_delivery("тест", enter=True)

    def test_focus_loss_stops_before_another_window_receives_text(self):
        self.check_delivery("focus must stop delivery", steal=True)

    def test_cancellation_restores_mapping_and_releases_server(self):
        self.check_delivery("cancel while typing", cancel=True)
