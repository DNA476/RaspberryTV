"""Run the actual QML with real key events; platform operations remain disabled."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QUICK_BACKEND"] = "software"
os.environ["QT_QUICK_CONTROLS_STYLE"] = "Basic"

from pathlib import Path
import tempfile
import unittest

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

    def test_keyboard_visits_all_tiles_and_enters_settings(self):
        self.assertEqual(self.view.activeFocusItem().objectName(), "tileKodi")
        for expected in ("tileYoutube", "tileMoonlight", "tileSettings"):
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
            self.assertNotEqual(item.objectName(), "headerSettings")
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


if __name__ == "__main__":
    unittest.main()
