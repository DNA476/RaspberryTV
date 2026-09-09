import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from raspberry_tv.touchpad import (ABS_X, BTN_LEFT, BTN_RIGHT, BTN_TOUCH, EV_ABS, EV_KEY,
                                  EV_SYN, SYN_DROPPED, SYN_REPORT, ClickMapper, TouchpadProxy,
                                  is_dualsense_touchpad)


class TouchpadTests(unittest.TestCase):
    def click(self, mapper, x):
        return mapper.frame([(EV_KEY, BTN_LEFT, 1), (EV_ABS, ABS_X, x),
                             (EV_KEY, BTN_TOUCH, 1), (EV_SYN, SYN_REPORT, 0)])

    def test_halves_use_touch_coordinate_even_when_button_arrives_first(self):
        for x, expected in ((0, BTN_LEFT), (959, BTN_LEFT), (960, BTN_RIGHT), (1919, BTN_RIGHT)):
            with self.subTest(x=x):
                mapper = ClickMapper(0, 1919)
                self.assertEqual(self.click(mapper, x)[0], (EV_KEY, expected, 1))
                self.assertEqual(mapper.frame([(EV_KEY, BTN_LEFT, 0)]), [(EV_KEY, expected, 0)])

    def test_drag_keeps_original_button_when_crossing_middle_or_lifting_finger(self):
        for x, moved, button in ((100, 1800, BTN_LEFT), (1800, 100, BTN_RIGHT)):
            mapper = ClickMapper(0, 1919)
            self.click(mapper, x)
            mapper.frame([(EV_ABS, ABS_X, moved), (EV_KEY, BTN_TOUCH, 0)])
            self.assertEqual(mapper.frame([(EV_KEY, BTN_LEFT, 0)]), [(EV_KEY, button, 0)])
            self.assertIsNone(mapper.held)

    def test_no_finger_click_is_left_and_same_position_retains_coordinate(self):
        mapper = ClickMapper(0, 1919)
        self.click(mapper, 1800)
        mapper.frame([(EV_KEY, BTN_LEFT, 0), (EV_KEY, BTN_TOUCH, 0)])
        self.assertEqual(mapper.frame([(EV_KEY, BTN_LEFT, 1)])[0], (EV_KEY, BTN_LEFT, 1))
        mapper.frame([(EV_KEY, BTN_LEFT, 0)])
        self.assertEqual(mapper.frame([(EV_KEY, BTN_TOUCH, 1), (EV_KEY, BTN_LEFT, 1)])[-1], (EV_KEY, BTN_RIGHT, 1))

    def test_motion_multitouch_and_sync_are_not_changed(self):
        events = [(EV_ABS, 47, 1), (EV_ABS, 57, 22), (EV_ABS, 53, 1400), (EV_ABS, 54, 500),
                  (EV_ABS, ABS_X, 1000), (EV_ABS, 1, 400), (EV_KEY, BTN_TOUCH, 1),
                  (EV_KEY, 333, 1), (EV_SYN, SYN_REPORT, 0)]
        self.assertEqual(ClickMapper(0, 1919).frame(events), events)

    def test_only_physical_sony_dualsense_touchpads_match(self):
        for name, vendor, expected in (("DualSense Wireless Controller Touchpad", 0x054c, True),
                                        ("DualSense Edge Wireless Controller Touchpad", 0x054c, True),
                                        ("DualSense Wireless Controller", 0x054c, False),
                                        ("Raspberry TV DualSense Touchpad", 0x054c, False),
                                        ("DualSense Wireless Controller Touchpad", 1, False)):
            self.assertEqual(is_dualsense_touchpad(SimpleNamespace(name=name, info=SimpleNamespace(vendor=vendor))), expected)

    def proxy(self):
        proxy = object.__new__(TouchpadProxy)
        proxy.device, proxy.virtual = Mock(), Mock()
        proxy.grabbed, proxy.packet, proxy.mapper = True, [], ClickMapper(0, 1919)
        return proxy

    def test_read_buffers_complete_frames_and_preserves_one_click(self):
        proxy = self.proxy()
        event = lambda kind, code, value: SimpleNamespace(type=kind, code=code, value=value)
        proxy.device.read.return_value = [event(EV_KEY, BTN_LEFT, 1), event(EV_ABS, ABS_X, 1700)]
        proxy.read()
        proxy.virtual.write.assert_not_called()
        proxy.device.read.return_value = [event(EV_KEY, BTN_TOUCH, 1), event(EV_SYN, SYN_REPORT, 0)]
        proxy.read()
        self.assertEqual(proxy.virtual.write.call_args_list[0].args, (EV_KEY, BTN_RIGHT, 1))
        self.assertEqual(len(proxy.virtual.write.call_args_list), 4)
        self.assertEqual(proxy.packet, [])

    def test_overflow_requires_recreation_instead_of_forwarding_partial_state(self):
        proxy = self.proxy()
        proxy.device.read.return_value = [SimpleNamespace(type=EV_SYN, code=SYN_DROPPED, value=0)]
        with self.assertRaises(OSError):
            proxy.read()
        proxy.virtual.write.assert_not_called()

    def test_shutdown_releases_held_click_and_physical_device_even_if_output_fails(self):
        for fail in (False, True):
            proxy = self.proxy()
            virtual = proxy.virtual
            proxy.mapper.held = BTN_RIGHT
            if fail:
                virtual.write.side_effect = OSError('disconnected')
            proxy.close()
            virtual.write.assert_called_once_with(EV_KEY, BTN_RIGHT, 0)
            virtual.close.assert_called_once()
            proxy.device.ungrab.assert_called_once()
            proxy.device.close.assert_called_once()

    def test_initialization_failure_leaves_native_device_available(self):
        device = Mock()
        device.absinfo.return_value = SimpleNamespace(min=0, max=1919, value=0)
        device.capabilities.return_value = {EV_SYN: [0], EV_KEY: [BTN_LEFT], EV_ABS: [(ABS_X, device.absinfo())]}
        device.input_props.return_value = [0, 2]
        virtual = Mock()
        evdev = SimpleNamespace(UInput=Mock(return_value=virtual), ecodes=SimpleNamespace(INPUT_PROP_BUTTONPAD=2, BUS_VIRTUAL=6))
        with patch.dict('sys.modules', evdev=evdev), patch('raspberry_tv.touchpad.enable_natural_scrolling', side_effect=OSError):
            with self.assertRaises(OSError):
                TouchpadProxy(device, threading.Event())
        device.grab.assert_not_called()
        virtual.close.assert_called_once()
        device.close.assert_called_once()
        self.assertEqual(evdev.UInput.call_args.kwargs['input_props'], [0])
        self.assertIn(BTN_RIGHT, evdev.UInput.call_args.args[0][EV_KEY])
