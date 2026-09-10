"""Short-lived XTEST worker. Text travels over stdin, never argv or the clipboard."""

from contextlib import closing, contextmanager
import json
import signal
import sys
import time

MAX_TEXT = 512


class InputFailure(RuntimeError):
    pass


def validate_text(text):
    if not isinstance(text, str) or len(text) > MAX_TEXT or any(not c.isprintable() for c in text):
        raise InputFailure("Введите до 512 символов без переносов строки")


def inside(window, ancestor):
    for _ in range(32):
        if getattr(window, "id", 0) == ancestor:
            return True
        if not getattr(window, "id", 0):
            break
        window = window.query_tree().parent
    return False


def active(display, window):
    from Xlib import Xatom
    prop = display.screen().root.get_full_property(display.intern_atom("_NET_ACTIVE_WINDOW"), Xatom.WINDOW)
    return bool(prop is not None and len(prop.value) and int(prop.value[0]) == window)


def capture_focus(window):
    from Xlib.display import Display
    with closing(Display()) as display:
        focus = display.get_input_focus().focus
        if not active(display, window) or not inside(focus, window):
            raise InputFailure("Сначала выбери поле в приложении, затем открой Options")
        return focus.id


@contextmanager
def server_lock(display):
    display.grab_server()
    try:
        yield
    finally:
        display.ungrab_server()
        display.sync()


def type_text(window, focus_id, text, enter=False):
    """Check the pinned X11 focus and inject each key in one server critical section."""
    from Xlib import X
    from Xlib.display import Display
    from Xlib.ext import xtest

    validate_text(text)
    with closing(Display()) as display:
        if not display.has_extension("XTEST"):
            raise InputFailure("XTEST недоступен")
        focus = display.create_resource_object("window", focus_id)
        deadline = time.monotonic() + 2
        while not active(display, window):
            if time.monotonic() >= deadline:
                raise InputFailure("Окно не получило фокус")
            time.sleep(.02)
        with server_lock(display):
            if not active(display, window) or not inside(focus, window):
                raise InputFailure("Целевое окно изменилось")
            focus.set_input_focus(X.RevertToParent, X.CurrentTime)
        low, high = display.display.info.min_keycode, display.display.info.max_keycode
        mapping = display.get_keyboard_mapping(low, high - low + 1)
        spare = next((low + i for i, row in enumerate(mapping) if not any(row)), None)
        if spare is None:
            raise InputFailure("Нет свободной клавиши X11")
        original = list(mapping[spare - low])
        # Map every level alike: input must not depend on the desktop's RU/EN group.
        symbols = [ord(c) if ord(c) <= 255 else 0x01000000 | ord(c) for c in text]
        if enter:
            symbols.append(0xff0d)
        try:
            for symbol in symbols:
                display.change_keyboard_mapping(spare, [[symbol] * len(original)])
                display.sync()
                # Let clients refresh their mapping before the key event arrives.
                time.sleep(.02)
                with server_lock(display):
                    if not active(display, window) or getattr(display.get_input_focus().focus, "id", 0) != focus_id:
                        raise InputFailure("Ввод остановлен: окно потеряло фокус")
                    if any(display.query_keymap()):
                        raise InputFailure("Отпусти клавиши физической клавиатуры")
                    try:
                        xtest.fake_input(display, X.KeyPress, spare)
                    finally:
                        xtest.fake_input(display, X.KeyRelease, spare)
                        display.sync()
                time.sleep(.02)
        finally:
            display.change_keyboard_mapping(spare, [original])
            display.sync()


def main():
    def interrupted(*_):
        raise InputFailure("Cancelled")
    signal.signal(signal.SIGTERM, interrupted)
    try:
        request = json.loads(sys.stdin.buffer.read(8193).decode("utf-8"))
        type_text(int(sys.argv[1]), int(sys.argv[2]), request["text"], bool(request.get("enter")))
        return 0
    except Exception:
        # This worker deliberately prints neither text nor exception details.
        return 1


if __name__ == "__main__":
    sys.exit(main())
