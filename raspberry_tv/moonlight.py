"""Moonlight Qt 6.1 preferences for the Pi 5 X11 session."""

from PySide6.QtCore import QSettings

from .processes import Unavailable


def prepare_settings(settings=None):
    settings = settings if settings is not None else QSettings("Moonlight Game Streaming Project", "Moonlight")
    # Use Moonlight's own serializer: the same file contains pairing credentials.
    # Fill missing preferences only, so later quality/language choices survive.
    defaults = {
        "width": 1280, "height": 720, "fps": 60, "bitrate": 10000,
        "videocfg": 2, "videodec": 0, "hdr": False, "yuv444": False,
        "windowmode": 1, "uidisplaymode": 2,
    }
    for key, value in defaults.items():
        if not settings.contains(key):
            settings.setValue(key, value)
    # Required for launcher menus: no background gamepad input or system-key grab.
    settings.setValue("backgroundgamepad", False)
    settings.setValue("capturesyskeys", 0)
    settings.sync()
    if settings.status() != QSettings.Status.NoError:
        raise Unavailable("Не удалось сохранить настройки Moonlight")
