"""Remap DualSense clicks; libinput still handles pointer motion and two-finger scroll."""

import logging
import select
import threading
import time

LOG = logging.getLogger(__name__)
NAME = "Raspberry TV DualSense Touchpad"
# Linux input ABI constants; keeping the packet mapper independent of evdev allows Windows tests.
EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
SYN_REPORT, SYN_DROPPED = 0, 3
BTN_LEFT, BTN_RIGHT, BTN_TOUCH = 272, 273, 330
ABS_X = 0


class ClickMapper:
    def __init__(self, minimum, maximum):
        self.midpoint = (minimum + maximum + 1) / 2
        self.x = None
        self.touching = False
        self.held = None

    def frame(self, events):
        # HID can report the button before coordinates in the same SYN_REPORT frame.
        for kind, code, value in events:
            if (kind, code) == (EV_ABS, ABS_X):
                self.x = value
            elif (kind, code) == (EV_KEY, BTN_TOUCH):
                self.touching = bool(value)
        result = []
        for kind, code, value in events:
            if (kind, code) == (EV_KEY, BTN_LEFT):
                if value == 1 and self.held is None:
                    self.held = BTN_RIGHT if self.touching and self.x is not None and self.x >= self.midpoint else BTN_LEFT
                code = self.held or BTN_LEFT
                if value == 0:
                    self.held = None
            result.append((kind, code, value))
        return result


def is_dualsense_touchpad(device):
    return device.info.vendor == 0x054c and device.name.startswith("DualSense") and device.name.endswith("Touchpad")


def enable_natural_scrolling(node):
    """Configure only our virtual device in the existing X11 session (also after hotplug)."""
    from Xlib import X, Xatom
    from Xlib.display import Display
    from Xlib.ext import xinput

    display = Display()
    try:
        node_atom = display.intern_atom("Device Node")
        scroll_atom = display.intern_atom("libinput Natural Scrolling Enabled")
        for device in display.xinput_query_device(xinput.AllDevices).devices:
            if device.name != NAME:
                continue
            prop = display.xinput_get_device_property(device.deviceid, node_atom, X.AnyPropertyType, 0, 256)
            if not prop.value or bytes(prop.value[1]).rstrip(b"\0").decode() != node:
                continue
            display.xinput_change_device_property(device.deviceid, scroll_atom, Xatom.INTEGER,
                                                  X.PropModeReplace, (8, [1]))
            display.sync()
            enabled = display.xinput_get_device_property(device.deviceid, scroll_atom, Xatom.INTEGER, 0, 1)
            return bool(enabled.value and list(enabled.value[1]) == [1])
        return False
    finally:
        display.close()


class TouchpadProxy:
    def __init__(self, device, stop):
        import evdev

        self.device = device
        self.virtual = None
        self.grabbed = False
        self.packet = []
        axis = device.absinfo(ABS_X)
        self.mapper = ClickMapper(axis.min, axis.max)
        self.mapper.x = axis.value
        try:
            capabilities = device.capabilities()
            capabilities.pop(EV_SYN, None)
            capabilities[EV_KEY] = sorted(set(capabilities[EV_KEY]) | {BTN_RIGHT})
            # Two real buttons, not a clickpad: don't let libinput reinterpret our chosen click.
            properties = [prop for prop in device.input_props() if prop != evdev.ecodes.INPUT_PROP_BUTTONPAD]
            self.virtual = evdev.UInput(capabilities, name=NAME, vendor=device.info.vendor,
                                        product=device.info.product, version=device.info.version,
                                        bustype=evdev.ecodes.BUS_VIRTUAL, input_props=properties)
            deadline = time.monotonic() + 3
            while not enable_natural_scrolling(self.virtual.device.path):
                if stop.wait(.05) or time.monotonic() >= deadline:
                    raise OSError("Virtual touchpad did not appear in X11")
            device.grab()
            self.grabbed = True
            # Drain old reports; start from an idle device so no held touches/buttons are lost.
            try:
                while list(device.read()):
                    if stop.is_set() or time.monotonic() >= deadline:
                        raise OSError("Touchpad did not become idle")
            except BlockingIOError:
                pass
            if stop.is_set() or any(key in device.active_keys() for key in (BTN_TOUCH, BTN_LEFT)):
                raise OSError("Wait for fingers and button to be released before taking over")
            self.mapper.x = device.absinfo(ABS_X).value
        except BaseException:
            self.close()
            raise

    def read(self):
        for event in self.device.read():
            if (event.type, event.code) == (EV_SYN, SYN_DROPPED):
                raise OSError("Touchpad input overflow; recreate the proxy from idle state")
            self.packet.append((event.type, event.code, event.value))
            if (event.type, event.code) == (EV_SYN, SYN_REPORT):
                for kind, code, value in self.mapper.frame(self.packet):
                    self.virtual.write(kind, code, value)
                self.packet.clear()

    def close(self):
        try:
            if self.virtual:
                try:
                    if self.mapper.held:
                        self.virtual.write(EV_KEY, self.mapper.held, 0)
                        self.virtual.syn()
                finally:
                    self.virtual.close()
                    self.virtual = None
        except OSError:
            # A disconnected uinput device must not prevent the physical grab from releasing.
            LOG.debug("Virtual touchpad already disconnected", exc_info=True)
        finally:
            try:
                if self.grabbed:
                    try:
                        self.device.ungrab()
                    except OSError:
                        pass
                    self.grabbed = False
            finally:
                self.device.close()


class TouchpadReader(threading.Thread):
    def __init__(self):
        super().__init__(name="dualsense-touchpad", daemon=True)
        self.stopped = threading.Event()

    def run(self):
        import evdev

        devices = {}
        warned = set()
        next_scan = 0
        try:
            while not self.stopped.is_set():
                if time.monotonic() >= next_scan:
                    next_scan = time.monotonic() + 2
                    paths = set(evdev.list_devices())
                    warned.intersection_update(paths)
                    known = {proxy.device.path for proxy in devices.values()}
                    for path in paths - known:
                        device = None
                        try:
                            device = evdev.InputDevice(path)
                            if not is_dualsense_touchpad(device):
                                device.close()
                                continue
                            proxy = TouchpadProxy(device, self.stopped)
                            devices[device.fd] = proxy
                            warned.discard(path)
                            LOG.info("DualSense touchpad ready: split click and natural scroll")
                        except Exception:
                            if device:
                                device.close()
                            if path not in warned:
                                warned.add(path)
                                LOG.warning("Touchpad remapping unavailable; native input kept", exc_info=True)
                ready, _, _ = select.select(list(devices), [], [], .05) if devices else ([], [], [])
                if not devices:
                    self.stopped.wait(.1)
                for fd in ready:
                    try:
                        devices[fd].read()
                    except OSError:
                        devices.pop(fd).close()
        finally:
            for proxy in devices.values():
                proxy.close()

    def stop(self):
        self.stopped.set()
