"""Run on Pi, preview safely on a development PC, or render a screenshot."""

import argparse
import hashlib
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description="Raspberry TV")
    parser.add_argument("--preview", action="store_true", help="Disable system operations")
    parser.add_argument("--windowed", action="store_true")
    parser.add_argument("--config-dir", type=Path)
    parser.add_argument("--screenshot", type=Path)
    parser.add_argument("--control", choices=["home", "menu", "power"], help="Control the running shell")
    parser.add_argument("--screen", choices=["home", "devices", "display", "network", "zapret", "system", "quick", "power"], default="home")
    args = parser.parse_args()
    preview = args.preview or sys.platform != "linux" or bool(args.screenshot)
    if not preview and (os.geteuid() == 0 or os.environ.get("XDG_SESSION_TYPE") == "wayland"):
        parser.error("Запускай оболочку обычным пользователем в сеансе Raspberry TV (X11)")
    if args.screenshot:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        os.environ["QT_QUICK_BACKEND"] = "software"
    os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")

    from PySide6.QtCore import QCoreApplication, QLocale, QObject, QEvent, QTimer, Qt, QUrl
    from PySide6.QtGui import QGuiApplication, QColor, QFont, QFontDatabase
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    from PySide6.QtQuick import QQuickView
    from .bridge import Bridge
    from .input import InputReader

    config_dir = args.config_dir or (Path.cwd() / ".run" / "preview" if preview else
                                   Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "raspberry-tv")
    socket_name = "raspberry-tv-" + hashlib.sha256(str(config_dir.resolve()).encode()).hexdigest()[:16]
    if args.control:
        control_app = QCoreApplication(sys.argv[:1])
        socket = QLocalSocket()
        socket.connectToServer(socket_name)
        if not socket.waitForConnected(1000):
            return 1
        socket.write((args.control + "\n").encode())
        socket.waitForBytesWritten(1000)
        socket.disconnectFromServer()
        return 0
    QLocale.setDefault(QLocale("ru_RU"))
    QQuickView.setDefaultAlphaBuffer(True)
    app = QGuiApplication(sys.argv[:1])
    app.setApplicationName("Raspberry TV")
    app.setOrganizationName("RaspberryTV")
    app.setQuitOnLastWindowClosed(False)
    if sys.platform == "win32":
        font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "segoeui.ttf"
        if font_path.exists():
            QFontDatabase.addApplicationFont(str(font_path))
        app.setFont(QFont("Segoe UI", 16))
    else:
        app.setFont(QFont("DejaVu Sans", 16))
    config_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(config_dir / "launcher.log", maxBytes=2_000_000, backupCount=2, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler, logging.StreamHandler()])
    bridge = Bridge(config_dir, preview)
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.UserAccessOption)
    if not args.screenshot and not server.listen(socket_name):
        existing = QLocalSocket()
        existing.connectToServer(socket_name)
        if existing.waitForConnected(500):
            existing.write(b"home\n")
            existing.waitForBytesWritten(500)
            bridge.shutdown()
            return 0
        QLocalServer.removeServer(socket_name)
        if not server.listen(socket_name):
            bridge.shutdown()
            return 1

    def accept_control():
        connection = server.nextPendingConnection()
        buffer = bytearray()
        def read():
            buffer.extend(bytes(connection.readAll()))
            if len(buffer) > 64:
                connection.abort()
            elif b"\n" in buffer:
                action = bytes(buffer).split(b"\n", 1)[0].decode("ascii", errors="ignore")
                if action in ("home", "menu", "power"):
                    bridge.action(action)
                connection.disconnectFromServer()
        connection.readyRead.connect(read)
        connection.disconnected.connect(connection.deleteLater)
        QTimer.singleShot(1000, connection, connection.abort)
        read()
    server.newConnection.connect(accept_control)
    view = QQuickView()
    view.setTitle("Raspberry TV")
    view.setColor(QColor("transparent"))
    view.setResizeMode(QQuickView.SizeRootObjectToView)
    view.rootContext().setContextProperty("backend", bridge)
    view.setSource(QUrl.fromLocalFile(str(Path(__file__).parent / "qml" / "Main.qml")))
    if view.status() == QQuickView.Error:
        bridge.shutdown()
        return 1
    view.resize(1600, 900)
    windowed = args.windowed or preview

    def surface(page):
        if page == "application":
            view.hide()
            return
        flags = Qt.Window | (Qt.WindowTitleHint | Qt.WindowSystemMenuHint | Qt.WindowCloseButtonHint if windowed else Qt.FramelessWindowHint)
        if page in ("quick", "power"):
            flags |= Qt.WindowStaysOnTopHint
        view.setFlags(flags)
        view.show() if windowed else view.showFullScreen()
        view.raise_()
        view.requestActivate()

    bridge.surface.connect(surface)
    reader = None
    if not preview:
        reader = InputReader(bridge.settings.data["controller"], bridge.controllerEvent.emit, bridge.controllerStatus.emit)
        bridge.input_reader = reader
        reader.start()
    surface("home")
    if args.screen not in ("home", "quick", "power"):
        bridge.action("section", args.screen)
    elif args.screen == "quick":
        bridge.action("menu")
    elif args.screen == "power":
        bridge.action("power")
    class CloseFilter(QObject):
        def eventFilter(self, watched, event):
            if event.type() == QEvent.Close:
                app.quit()
            return False
    close_filter = CloseFilter(view)
    view.installEventFilter(close_filter)

    if args.screenshot:
        def capture():
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            rendered = view.grabWindow()
            if rendered.isNull() or not rendered.save(str(args.screenshot)):
                app.exit(2)
            else:
                app.quit()
        QTimer.singleShot(1000, capture)
    try:
        return app.exec()
    finally:
        server.close()
        if reader:
            reader.stop()
            reader.join(timeout=3)
        bridge.shutdown()


if __name__ == "__main__":
    sys.exit(main())
