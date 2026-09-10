"""Run the actual QML with real key events; platform operations remain disabled."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QUICK_BACKEND"] = "software"
os.environ["QT_QUICK_CONTROLS_STYLE"] = "Basic"

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QObject, QUrl, Qt
from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication
from PySide6.QtQuick import QQuickView
from PySide6.QtTest import QTest

from raspberry_tv.bridge import Bridge


class InterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])
        font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/segoeui.ttf"
        if font_path.exists():
            QFontDatabase.addApplicationFont(str(font_path))
            cls.app.setFont(QFont("Segoe UI", 16))

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.bridge = Bridge(Path(self.directory.name), preview=True)
        self.messages = []
        self.bridge.notice.connect(lambda title, message: self.messages.append((title, message)))
        self.view = QQuickView()
        self.view.setResizeMode(QQuickView.SizeRootObjectToView)
        self.view.rootContext().setContextProperty("backend", self.bridge)
        self.view.setSource(QUrl.fromLocalFile(str(Path(__file__).resolve().parents[1] / "raspberry_tv/qml/Main.qml")))
        self.view.resize(1600, 900)
        self.view.show()
        QTest.qWait(60)
        self.root = self.view.rootObject()
        self.assertEqual(self.view.status(), QQuickView.Ready)

    def tearDown(self):
        self.view.setSource(QUrl())
        self.view.close()
        self.bridge.shutdown()
        self.directory.cleanup()

    def visual_item(self, name, parent=None):
        parent = parent or self.root
        if parent.objectName() == name:
            return parent
        for child in parent.childItems():
            found = self.visual_item(name, child)
            if found is not None:
                return found
        return None

    def test_keyboard_visits_all_tiles_and_enters_settings(self):
        self.assertEqual(self.view.activeFocusItem().objectName(), "tileKodi")
        for expected in ("tileYoutube", "tileMoonlight", "tileBrowser", "tileSettings"):
            QTest.keyClick(self.view, Qt.Key_Right)
            QTest.qWait(170)
            self.assertEqual(self.view.activeFocusItem().objectName(), expected)
        QTest.keyClick(self.view, Qt.Key_Return)
        self.assertEqual(self.bridge.state["page"], "settings")

    def test_preview_cannot_start_system_applications(self):
        QTest.keyClick(self.view, Qt.Key_Return)
        self.assertEqual(self.bridge.state["page"], "home")
        self.assertFalse(self.bridge.apps.running)
        self.assertIn("Raspberry Pi", self.messages[-1][1])

    def test_domain_editor_validation_and_persistence(self):
        self.bridge.action("section", "zapret")
        self.bridge.action("domains")
        QTest.qWait(40)
        self.assertEqual(self.bridge.state["editor"]["action"], "domains")
        self.bridge.action("editor_save", "file:///tmp/private")
        self.assertTrue(self.bridge.state["editor"])
        self.bridge.action("editor_save", "example.org\nYouTube.com")
        self.assertFalse(self.bridge.state["editor"])
        from raspberry_tv.config import Settings
        self.assertEqual(Settings(Path(self.directory.name)).data["zapret"]["domains"], ["example.org", "youtube.com"])

    def test_quick_menu_cancel_returns_to_previous_surface(self):
        self.bridge.action("section", "system")
        self.bridge.action("menu")
        self.assertEqual(self.bridge.state["page"], "quick")
        self.bridge.action("back")
        self.assertEqual(self.bridge.state["page"], "settings")

    def test_gamepad_focus_is_trapped_inside_modal(self):
        self.bridge.action("domains")
        QTest.qWait(30)
        for _ in range(6):
            self.bridge.controllerEvent.emit("right")
            QTest.qWait(5)
            item = self.view.activeFocusItem()
            self.assertNotEqual(item.objectName(), "tileYoutube")
            self.assertNotEqual(item.objectName(), "headerPower")
        self.bridge.controllerEvent.emit("back")
        self.assertFalse(self.bridge.state["editor"])

    def test_gamepad_reaches_offscreen_settings_rows(self):
        self.bridge.action("section", "zapret")
        QTest.qWait(60)
        # Focus the first enabled row then use the same navigation path as evdev.
        list_view = self.root.findChild(QObject, "settingsList")
        delegates = [item for item in list_view.childItems()[0].childItems() if item.property("tvListIndex") is not None]
        first = min(delegates, key=lambda item: item.property("tvListIndex"))
        first.forceActiveFocus()
        for _ in range(6):
            self.bridge.controllerEvent.emit("down")
            QTest.qWait(30)
        self.assertEqual(self.view.activeFocusItem().property("tvListIndex"), 6)

    def test_status_refresh_keeps_focused_network_action(self):
        self.bridge.action("section", "zapret")
        QTest.qWait(40)
        view = self.root.findChild(QObject, "settingsList")
        delegates = [item for item in view.childItems()[0].childItems() if item.property("tvListIndex") is not None]
        first = min(delegates, key=lambda item: item.property("tvListIndex"))
        first.forceActiveFocus()
        self.bridge._status({"zapret_status": "Работает"})
        QTest.qWait(40)
        self.assertEqual(self.view.activeFocusItem().property("tvListIndex"), 0)

    def test_preview_network_actions_remain_isolated(self):
        for action in ("zapret_apply", "zapret_toggle", "zapret_tune", "zapret_rollback", "zapret_confirm"):
            self.bridge.action(action)
            self.assertFalse(self.bridge.state["confirm"])
            self.assertIn("Raspberry Pi", self.messages[-1][1])

    def test_power_button_opens_menu_and_cancel_restores_header_focus(self):
        header = self.root.findChild(QObject, "headerPower")
        header.forceActiveFocus()
        self.bridge.controllerEvent.emit("accept")
        QTest.qWait(30)
        self.assertEqual(self.bridge.state["page"], "power")
        sleep = self.visual_item("quick_suspend")
        self.assertFalse(sleep.property("enabled"))
        for _ in range(2):
            self.bridge.controllerEvent.emit("down")
            QTest.qWait(10)
        self.assertEqual(self.view.activeFocusItem().objectName(), "quick_back")
        self.bridge.controllerEvent.emit("accept")
        QTest.qWait(30)
        self.assertEqual(self.bridge.state["page"], "home")
        self.assertEqual(self.view.activeFocusItem().objectName(), "headerPower")

    def test_power_confirmation_cancel_restores_trigger_and_preview_is_safe(self):
        self.bridge.action("power")
        QTest.qWait(30)
        self.bridge.controllerEvent.emit("accept")
        QTest.qWait(30)
        self.assertEqual(self.bridge.state["confirm"]["action"], "poweroff")
        self.bridge.controllerEvent.emit("back")
        QTest.qWait(30)
        self.assertEqual(self.view.activeFocusItem().objectName(), "quick_poweroff")
        with patch.object(self.bridge, "_job") as job:
            for action in ("poweroff", "reboot"):
                self.bridge.action(action)
                self.bridge.action("confirmed")
                self.assertIn("Raspberry Pi", self.messages[-1][1])
            job.assert_not_called()

    def test_switching_quick_and_power_menus_keeps_original_return_page(self):
        self.bridge.action("section", "system")
        for action in ("menu", "power", "menu", "back"):
            self.bridge.action(action)
        self.assertEqual(self.bridge.state["page"], "settings")
        self.assertEqual(self.bridge.state["section"], "system")

    def test_browser_home_page_editor_validates_and_persists(self):
        self.bridge.action("section", "system")
        self.bridge.action("browser_home")
        self.bridge.action("editor_save", "javascript:alert(1)")
        self.assertTrue(self.bridge.state["editor"])
        self.bridge.action("editor_save", "example.org")
        from raspberry_tv.config import Settings
        self.assertEqual(Settings(Path(self.directory.name)).data["browser"]["start_url"], "https://example.org/")
        self.assertFalse(self.bridge.state["editor"])

    def test_browser_preview_cannot_launch_type_or_send_history_commands(self):
        with patch.object(self.bridge.apps, "launch") as launch, patch.object(self.bridge.apps, "browser_action") as control:
            self.bridge.action("launch", "browser")
            self.bridge._update(activeApp="browser")
            self.bridge.action("menu")
            self.bridge.action("browser_address")
            self.bridge.action("editor_save", "example.org")
            for action in ("browser_back", "browser_forward", "browser_reload"):
                self.bridge.action(action)
            launch.assert_not_called()
            control.assert_not_called()
        self.assertTrue(self.bridge.state["editor"])

    def test_browser_failure_restores_address_editor_for_retry(self):
        from raspberry_tv.processes import Unavailable
        self.bridge._update(activeApp="browser")
        self.bridge.action("menu")
        self.bridge.action("browser_address")
        with patch.object(self.bridge, "_need_pi"), patch.object(self.bridge.apps, "browser_action", side_effect=Unavailable("Окно закрылось")), \
                patch("raspberry_tv.bridge.LOG.exception"):
            self.bridge.action("editor_save", "example.org")
            self.assertEqual(self.bridge.state["page"], "application")
            for _ in range(50):
                if not self.bridge.pending:
                    break
                QTest.qWait(10)
        self.assertFalse(self.bridge.pending)
        self.assertEqual(self.bridge.state["page"], "quick")
        self.assertTrue(self.bridge.state["editor"])
        self.assertEqual(self.messages[-1][1], "Окно закрылось")

    def test_all_five_tiles_fit_small_screen_at_every_focus_position(self):
        self.view.resize(1280, 720)
        QTest.qWait(170)
        tiles = [self.root.findChild(QObject, name) for name in ("tileKodi", "tileYoutube", "tileMoonlight", "tileBrowser", "tileSettings")]
        for tile in tiles:
            tile.forceActiveFocus()
            QTest.qWait(170)
            for candidate in tiles:
                left = candidate.mapToScene(candidate.boundingRect().topLeft()).x()
                right = candidate.mapToScene(candidate.boundingRect().topRight()).x()
                self.assertGreater(left, 30)
                self.assertLess(right, 1250)

    def test_browser_menu_fits_small_screen_and_reaches_cancel(self):
        self.view.resize(1280, 720)
        self.bridge._update(activeApp="browser")
        self.bridge.action("menu")
        QTest.qWait(30)
        for expected in ("browser_address", "browser_back", "browser_forward", "browser_reload",
                         "minimize", "close", "section", "back"):
            self.bridge.controllerEvent.emit("down")
            QTest.qWait(10)
            item = self.view.activeFocusItem()
            self.assertEqual(item.objectName(), "quick_" + expected)
            self.assertGreaterEqual(item.mapToScene(item.boundingRect().topLeft()).y(), 0)
            self.assertLessEqual(item.mapToScene(item.boundingRect().bottomRight()).y(), 720)

    def test_browser_address_keyboard_accepts_gamepad_keys_and_cancel_returns_to_menu(self):
        self.bridge._update(activeApp="browser")
        self.bridge.action("menu")
        QTest.qWait(30)
        self.bridge.controllerEvent.emit("down")
        self.bridge.controllerEvent.emit("accept")
        QTest.qWait(30)
        field = self.root.findChild(QObject, "editorText")
        self.assertEqual(self.view.activeFocusItem(), field)
        self.bridge.controllerEvent.emit("down")
        QTest.qWait(10)
        letter = self.view.activeFocusItem().property("text")
        self.assertEqual(len(letter), 1)
        self.bridge.controllerEvent.emit("accept")
        self.assertEqual(field.property("text"), letter)
        self.bridge.controllerEvent.emit("back")
        QTest.qWait(30)
        self.assertEqual(field.property("text"), "")
        self.assertEqual(self.view.activeFocusItem().objectName(), "quick_browser_address")


if __name__ == "__main__":
    unittest.main()
