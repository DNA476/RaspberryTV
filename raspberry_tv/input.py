"""Global hotkeys via evdev; modal overlays temporarily capture controller input."""

import select
import threading
import time


class InputReader(threading.Thread):
    def __init__(self, mappings: dict, emit, report):
        super().__init__(name="controller", daemon=True)
        self.mappings = mappings
        self.emit = emit
        self.report = report
        self.stop_event = threading.Event()
        self.devices = {}
        self.devices_lock = threading.RLock()
        self.captured = False
        self.grabbed = set()
        self.home_down = None
        self.long_sent = False
        self.directions = {}

    def run(self):
        try:
            import evdev
        except ImportError:
            self.report("Для геймпада нужен компонент evdev")
            return
        next_scan = 0
        try:
            while not self.stop_event.is_set():
                now = time.monotonic()
                if now >= next_scan:
                    next_scan = now + 2
                    known = {d.path for d in self.devices.values()}
                    for path in evdev.list_devices():
                        if path in known:
                            continue
                        try:
                            device = evdev.InputDevice(path)
                            keys = device.capabilities().get(evdev.ecodes.EV_KEY, [])
                            if evdev.ecodes.BTN_GAMEPAD not in keys:
                                device.close()
                                continue
                            if self.add_device(device):
                                self.report(device.name)
                        except OSError:
                            continue
                ready, _, _ = select.select(list(self.devices), [], [], 0.02) if self.devices else ([], [], [])
                if not self.devices:
                    self.stop_event.wait(0.1)
                for fd in ready:
                    try:
                        device = self.devices[fd]
                        for event in device.read():
                            if event.type == evdev.ecodes.EV_KEY:
                                self.button(event.code, event.value, now)
                            elif event.type == evdev.ecodes.EV_ABS:
                                code = event.code
                                if code in (evdev.ecodes.ABS_HAT0X, evdev.ecodes.ABS_HAT0Y):
                                    self.direction((fd, code), event.value, code == evdev.ecodes.ABS_HAT0X, now)
                                elif code in (evdev.ecodes.ABS_X, evdev.ecodes.ABS_Y):
                                    info = device.absinfo(code)
                                    center = (info.min + info.max) / 2
                                    radius = max(1, (info.max - info.min) / 2)
                                    value = (event.value - center) / radius
                                    self.direction((fd, code), (1 if value > 0 else -1) if abs(value) > 0.55 else 0,
                                                   code == evdev.ecodes.ABS_X, now)
                    except OSError:
                        with self.devices_lock:
                            device = self.devices.pop(fd)
                            self.grabbed.discard(fd)
                            device.close()
                        self.directions = {k: v for k, v in self.directions.items() if k[0] != fd}
                        self.home_down = None
                        self.report("Контроллер отключён")
                self.tick(now)
        finally:
            self.set_capture(False)
            with self.devices_lock:
                for device in self.devices.values():
                    device.close()

    def add_device(self, device):
        with self.devices_lock:
            if self.captured:
                try:
                    device.grab()
                except OSError:
                    device.close()
                    self.report("Перехват геймпада недоступен")
                    return False
                self.grabbed.add(device.fd)
            self.devices[device.fd] = device
            return True

    def set_capture(self, enabled):
        """Temporarily keep modal navigation away from native application input."""
        with self.devices_lock:
            try:
                if enabled:
                    for fd, device in self.devices.items():
                        if fd not in self.grabbed:
                            device.grab()
                            self.grabbed.add(fd)
                    self.captured = True
                    return True
            except OSError:
                pass
            for fd in self.grabbed:
                try:
                    self.devices[fd].ungrab()
                except OSError:
                    pass
            self.grabbed.clear()
            self.captured = False
            self.directions.clear()
            return not enabled

    def button(self, code: int, value: int, now: float):
        if code == self.mappings["home"]:
            if value == 1:
                self.home_down, self.long_sent = now, False
            elif value == 0:
                if self.home_down is not None and not self.long_sent:
                    self.emit("home")
                self.home_down = None
        elif value == 1:
            for action in ("menu", "accept", "back"):
                if code == self.mappings[action]:
                    self.emit(action)
                    break

    def direction(self, key, value: int, horizontal: bool, now: float):
        if value == 0:
            self.directions.pop(key, None)
            return
        action = ("right" if value > 0 else "left") if horizontal else ("down" if value > 0 else "up")
        if key not in self.directions or self.directions[key][0] != action:
            self.emit(action)
            self.directions[key] = (action, now + 0.35)

    def tick(self, now: float):
        if self.home_down is not None and not self.long_sent and now - self.home_down >= 1.2:
            self.emit("power")
            self.long_sent = True
        for key, (action, due) in list(self.directions.items()):
            if now >= due:
                self.emit(action)
                self.directions[key] = (action, now + 0.14)

    def stop(self):
        self.stop_event.set()
