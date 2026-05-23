# -*- coding: utf-8; -*-

# Copyright (C) 2015 - 2019 Lionel Ott
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import functools
import inspect
import logging
import time
import sys
from threading import Timer

from PyQt5 import QtCore

import dill
from dill.dill_backend import _ABS_TO_DINPUT_AXIS, _LinuxDILL

# Linux-only build uses the evdev stub for input hooking (preserving the
# windows_event_hook name for compatibility with existing EventListener code).
from . import linux_event_hook as windows_event_hook

from . import common, config, error, joystick_handling, macro, util

logger = logging.getLogger("system")


class Event:

    """Represents a single event captured by the system."""

    def __init__(
            self,
            event_type,
            identifier,
            device_guid,
            value=None,
            is_pressed=None,
            raw_value=None
    ):
        self.event_type = event_type
        self.identifier = identifier
        self.device_guid = device_guid
        self.is_pressed = is_pressed
        self.value = value
        self.raw_value = raw_value
        # Aliases used by action plugins for uniform event access
        self.input_type = event_type
        self.input_index = identifier
        self.button_id = identifier
        # Aliases used by action plugins for uniform event access
        self.input_type = event_type
        self.input_index = identifier
        self.button_id = identifier
        # Aliases used by action plugins for uniform event access
        self.input_type = event_type
        self.input_index = identifier
        self.button_id = identifier

    def clone(self):
        return Event(
            self.event_type,
            self.identifier,
            self.device_guid,
            self.value,
            self.is_pressed,
            self.raw_value
        )

    def __eq__(self, other):
        if not isinstance(other, Event):
            return NotImplemented
        return (
            self.event_type == other.event_type
            and self.identifier == other.identifier
            and self.device_guid == other.device_guid
        )

    def __ne__(self, other):
        return not (self == other)

    def __hash__(self):
        if self.event_type == common.InputType.Keyboard:
            return hash((
                self.device_guid,
                self.event_type.value,
                self.identifier,
                1 if isinstance(self.identifier, tuple) and len(self.identifier) > 1 and bool(self.identifier[1]) else 0
            ))
        elif self.event_type == common.InputType.JoystickButton:
            # Button down/up events MUST find the same callbacks — use the same
            # key hash as Mouse to share the existing bucket strategy
            return hash((
                self.device_guid,
                self.event_type.value,
                self.identifier,
                0
            ))
        elif self.event_type == common.InputType.Mouse:
            return hash((
                self.device_guid,
                self.event_type.value,
                self.identifier,
                0
            ))
        else:
            return hash((
                self.device_guid,
                self.event_type.value,
                self.identifier,
                0
            ))

    @staticmethod
    def from_key(key):
        from . import macro
        assert isinstance(key, macro.Key), f"Expected macro.Key, got {type(key)}"
        return Event(
            event_type=common.InputType.Keyboard,
            identifier=(key.scan_code, key.is_extended),
            device_guid=dill.GUID_Keyboard
        )


@common.SingletonDecorator
class EventListener(QtCore.QObject):

    """Listens for keyboard and joystick events."""

    joystick_event = QtCore.pyqtSignal(object)
    keyboard_event = QtCore.pyqtSignal(object)
    mouse_event = QtCore.pyqtSignal(object)
    virtual_event = QtCore.pyqtSignal(object)
    device_change_event = QtCore.pyqtSignal()

    def __init__(self):
        QtCore.QObject.__init__(self)
        # stub: on Linux keyboard/mouse hooks are no-ops
        self.keyboard_hook = windows_event_hook.KeyboardHook()
        self.keyboard_hook.register(self._keyboard_handler)
#JW
        self.mouse_hook = windows_event_hook.MouseHook()
        self.mouse_hook.register(self._mouse_handler)
#JW


        self._calibrations = {}
        self._device_update_timer = None
        self._running = True
        self._keyboard_state = {}

#JW
        self.mouse_hook.start()
#JW
        self.keyboard_hook.start()

        # On Linux: dill.DILL is a stub and won't provide joystick events
        if hasattr(dill.DILL, 'init'):
            # IMPORTANT: Set callback BEFORE init() — DILL's polling thread
            # starts immediately inside init() and will fire events to any
            # registered callback.  If the callback is None (the default),
            # early events are silently discarded, which is why triggers
            # were not reaching the UI.
            dill.DILL.set_input_event_callback(self._joystick_event_handler)
            dill.DILL.set_device_change_callback(self._joystick_device_handler)
            dill.DILL.init()  # starts polling thread (callback already registered)

        threading__target = functools.partial(self._run)
        import threading as _th
        _th.Thread(target=self._run).start()

    def terminate(self):
        self._running = False
        self.keyboard_hook.stop()

    def reload_calibrations(self):
        cfg = config.Configuration()
        for key in self._calibrations:
            limits = cfg.get_calibration(key[0], key[1])
            self._calibrations[key] = \
                util.create_calibration_function(
                    limits[0],
                    limits[1],
                    limits[2]
                )

    def _run(self):
        while self._running:
            time.sleep(0.008)

    def _joystick_event_handler(self, data):
        event_obj = dill.InputEvent(data)
        if event_obj.input_type == dill.InputType.Axis:
            self.joystick_event.emit(Event(
                event_type=common.InputType.JoystickAxis,
                device_guid=event_obj.device_guid,
                identifier=event_obj.input_index,
                value=self._apply_calibration(event_obj),
                raw_value=event_obj.value
            ))
        elif event_obj.input_type == dill.InputType.Button:
            self.joystick_event.emit(Event(
                event_type=common.InputType.JoystickButton,
                device_guid=event_obj.device_guid,
                identifier=event_obj.input_index,
                is_pressed=event_obj.value == 1
            ))
        elif event_obj.input_type == dill.InputType.Hat:
            direction = util.dill_hat_lookup.get(event_obj.value, (0, 0))
            self.joystick_event.emit(Event(
                event_type=common.InputType.JoystickHat,
                device_guid=event_obj.device_guid,
                identifier=event_obj.input_index,
                value=direction
            ))

    def _joystick_device_handler(self, data, action):
        if self._device_update_timer is not None:
            self._device_update_timer.cancel()
        # [LATE-6] Reduced to 10ms (was 200ms) for rapid hot-plug detection.
        # Original: Timer(0.2, self._run_device_list_update)
        self._device_update_timer = Timer(0.01, self._run_device_list_update)
        self._device_update_timer.start()

    def _run_device_list_update(self):
        joystick_handling.joystick_devices_initialization()
        self._init_joysticks()
        self.device_change_event.emit()

    def _keyboard_handler(self, event):
        key_id = (event.scan_code, event.is_extended)
        is_pressed = event.is_pressed
        is_repeat = self._keyboard_state.get(key_id, False) and is_pressed
        if not is_repeat:
            self._keyboard_state[key_id] = is_pressed
            self.keyboard_event.emit(Event(
                event_type=common.InputType.Keyboard,
                device_guid=dill.GUID_Keyboard,
                identifier=key_id,
                is_pressed=is_pressed,
            ))
        return True

    def _mouse_handler(self, event):
        import sys
        #print(f"\033[33m[CH2] _mouse_handler: injected={event.is_injected}, button_id={event.button_id}({type(event.button_id).__name__}), is_pressed={event.is_pressed}\033[0m", file=sys.stderr, flush=True)
        if not event.is_injected:
            evt = Event(
                event_type=common.InputType.Mouse,
                device_guid=dill.GUID_Keyboard,
                identifier=event.button_id,
                is_pressed=event.is_pressed,
            )
            #print(f"\033[33m[CH2] creating Event: identifier={evt.identifier} type={type(evt.identifier).__name__}\033[0m", file=sys.stderr, flush=True)
            self.mouse_event.emit(evt)
            #print(f"\033[33m[CH2] mouse_event.emit() called\033[0m", file=sys.stderr, flush=True)
        else:
            logger.warning("[CH2][gremlin/event_handler.py:_mouse_handler] SKIPPED (is_injected=True)")
            #print(f"\033[33m[CH2] SKIPPED (is_injected=True)\033[0m", file=sys.stderr, flush=True)
        return True

    def _apply_calibration(self, event):
        key = (event.device_guid, event.input_index)
        if key in self._calibrations:
            return self._calibrations[key](event.value)
        else:
            # Unipolar axes (triggers) already have their values scaled to
            # 0..32767 by the event dispatcher, so we must use that range
            # for slider_calibration, NOT the raw hardware range.
            if event.input_index in (3, 6, 7, 8, 9, 10, 11, 12):
                return util.slider_calibration(event.value, 0, 32767)
            return util.axis_calibration(event.value, -32768, 0, 32767)

    def _init_joysticks(self):
        for dev_info in joystick_handling.joystick_devices():
            self._load_calibrations(dev_info)

    def _load_calibrations(self, device_info):
        cfg = config.Configuration()
        device_ranges = _LinuxDILL._axis_abs_ranges.get(device_info.device_guid, {})
        # Build reverse lookup: dinput axis index → (abs_min, abs_max)
        dinput_to_range: dict[int, tuple[int, int]] = {}
        for abs_code, (abs_min, abs_max) in device_ranges.items():
            di_idx = _ABS_TO_DINPUT_AXIS.get(abs_code)
            if di_idx is not None:
                dinput_to_range[di_idx] = (abs_min, abs_max)
        for entry in device_info.axis_map:
            abs_min, abs_max = dinput_to_range.get(entry.axis_index, (-32768, 32767))
            if abs_max - abs_min < 32768:
                # Unipolar axis (trigger): use slider_calibration defaults.
                # The event dispatcher already scales triggers to 0..32767,
                # so the calibration max must use 32767, NOT abs_max
                # (which is the raw hardware range, e.g. 1023 for 10-bit).
                limits = (0, 0, 32767)
            else:
                # Bipolar axis: use config defaults
                limits = cfg.get_calibration(
                    device_info.device_guid,
                    entry.axis_index
                )
            self._calibrations[(device_info.device_guid, entry.axis_index)] = \
                util.create_calibration_function(
                    limits[0],
                    limits[1],
                    limits[2]
                )


@common.SingletonDecorator
class EventHandler(QtCore.QObject):

    """Listens to the inputs from multiple different input devices."""

    mode_changed = QtCore.pyqtSignal(str)
    is_active = QtCore.pyqtSignal(bool)

    def __init__(self):
        QtCore.QObject.__init__(self)
        self.process_callbacks = True
        self.plugins = {}
        self.callbacks = {}
        self._event_lookup = {}
        self._active_mode = None
        self._previous_mode = None

    @property
    def active_mode(self):
        return self._active_mode

    @property
    def previous_mode(self):
        return self._previous_mode

    def add_plugin(self, plugin):
        if plugin.keyword not in self.plugins:
            self.plugins[plugin.keyword] = plugin

    def add_callback(self, device_guid, mode, event, callback, permanent=False):
        if device_guid not in self.callbacks:
            self.callbacks[device_guid] = {}
        if mode not in self.callbacks[device_guid]:
            self.callbacks[device_guid][mode] = {}
        if event not in self.callbacks[device_guid][mode]:
            self.callbacks[device_guid][mode][event] = []
        self.callbacks[device_guid][mode][event].append((
            self._install_plugins(callback),
            permanent
        ))

    def build_event_lookup(self, inheritance_tree):
        for parent, children in inheritance_tree.items():
            for device_guid in self.callbacks:
                if parent in self.callbacks[device_guid]:
                    device_cb = self.callbacks[device_guid]
                    parent_cb = device_cb[parent]
                    for child in children:
                        if child not in device_cb:
                            device_cb[child] = {}
                        for event, callbacks in parent_cb.items():
                            if event not in device_cb[child]:
                                device_cb[child][event] = callbacks
            self.build_event_lookup(children)

    def change_mode(self, new_mode):
        mode_exists = False
        for device in self.callbacks.values():
            if new_mode in device:
                mode_exists = True
        if not mode_exists:
            logging.getLogger("system").error(
                "The mode \"{}\" does not exist or has no"
                " associated callbacks".format(new_mode)
            )
        if mode_exists:
            if self._active_mode != new_mode:
                self._previous_mode = self._active_mode
            cfg = config.Configuration()
            cfg.set_last_mode(cfg.last_profile, new_mode)
            self._active_mode = new_mode
            self.mode_changed.emit(self._active_mode)

    def resume(self):
        self.process_callbacks = True
        self.is_active.emit(self.process_callbacks)

    def pause(self):
        self.process_callbacks = False
        self.is_active.emit(self.process_callbacks)

    def toggle_active(self):
        self.process_callbacks = not self.process_callbacks
        self.is_active.emit(self.process_callbacks)

    def clear(self):
        self.callbacks = {}

    @QtCore.pyqtSlot(object)
    def process_event(self, event):
        matching = self._matching_callbacks(event)
        logger.info(
            "[INPUT_TRACE] EventHandler.process_event: type=%s id=%s guid=%s mode=%s matching_cbs=%d process_callbacks=%s",
            event.event_type,
            event.identifier,
            str(event.device_guid),
            self._active_mode,
            len(matching),
            self.process_callbacks,
        )
        for cb in matching:
            logger.info("[INPUT_TRACE] → calling callback for event type=%s id=%s",
                       event.event_type, event.identifier)
            try:
                cb(event)
                logger.info("[INPUT_TRACE] ← callback returned successfully")
            except error.VJoyError as e:
                util.display_error(str(e))
                logging.getLogger("system").exception(
                    "VJoy related error: {}".format(e)
                )
                self.pause()

    def _matching_callbacks(self, event):
        callback_list = []
        if event.device_guid in self.callbacks:
            callback_list = self.callbacks[event.device_guid].get(
                self._active_mode, {}
            ).get(event, [])
        if not self.process_callbacks:
            return [c[0] for c in callback_list if c[1]]
        else:
            return [c[0] for c in callback_list]

    def _install_plugins(self, callback):
        signature = inspect.signature(callback).parameters
        for keyword, plugin in self.plugins.items():
            if keyword in signature:
                callback = plugin.install(callback, functools.partial)
        return callback
