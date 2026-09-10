#!/usr/bin/env python3
"""Verify uinput/XInput wiring using the connected pad, without clicking or moving the pointer."""
import os
import threading

import evdev
from raspberry_tv.touchpad import TouchpadProxy, is_dualsense_touchpad

if os.geteuid() == 0:
    raise SystemExit("Run as the user of the X11 TV session")
if not os.access('/dev/uinput', os.W_OK):
    raise SystemExit("The TV session user cannot write /dev/uinput")
checked = 0
for path in evdev.list_devices():
    device = evdev.InputDevice(path)
    if not is_dualsense_touchpad(device):
        device.close()
        continue
    proxy = None
    try:
        proxy = TouchpadProxy(device, threading.Event())
        print("PASS: physical DualSense -> uinput -> XInput natural scrolling; no input generated")
        checked += 1
    finally:
        if proxy:
            proxy.close()
        else:
            device.close()
if not checked:
    raise SystemExit("Connect DualSense and leave its touchpad released, then repeat")
