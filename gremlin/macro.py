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

import collections
import ctypes
from ctypes import wintypes
import functools
import logging
import os
import sys
import time
import threading

from threading import Event, Lock, Thread
from xml.etree import ElementTree

from . import common
import gremlin.key_codes as key_codes
from gremlin.common import InputType

logger = logging.getLogger("system")
# win32api/win32con stub for Linux
# ==========================================================================
try:
    import win32con
    import win32api
except ImportError:
    # Linux: provide minimal win32con/win32api stubs for UI/build-time
    if sys.platform.startswith("win"):
        raise  # Re-raise on Windows if pywin32 is actually missing

    class _win32con_stub:
        """Minimal win32con stub providing virtual key codes needed at import
        time only. The actual keybd_event calls are stubbed by _win32api_stub."""
        VK_BACK = 0x08
        VK_TAB = 0x09
        VK_BACKPACK_BUTTON = 0x7E
        VK_F1 = 0x70
        VK_F2 = 0x71
        VK_F3 = 0x72
        VK_F4 = 0x73
        VK_F5 = 0x74
        VK_F6 = 0x75
        VK_F7 = 0x76
        VK_F8 = 0x77
        VK_F9 = 0x78
        VK_F10 = 0x79
        VK_F11 = 0x7A
        VK_F12 = 0x7B
        VK_ESCAPE = 0x1B
        VK_SPACE = 0x20
        VK_PRIOR = 0x21
        VK_NEXT = 0x22
        VK_END = 0x23
        VK_HOME = 0x24
        VK_LEFT = 0x25
        VK_UP = 0x26
        VK_RIGHT = 0x27
        VK_DOWN = 0x28
        VK_INSERT = 0x2D
        VK_DELETE = 0x2E
        VK_LWIN = 0x5B
        VK_RWIN = 0x5C
        VK_LSHIFT = 0xA0
        VK_RSHIFT = 0xA1
        VK_LCONTROL = 0xA2
        VK_RCONTROL = 0xA3
        VK_LMENU = 0xA4
        VK_RMENU = 0xA5
        VK_LBUTTON = 0x01
        VK_RBUTTON = 0x02
        VK_MBUTTON = 0x04
        VK_XBUTTON1 = 0x05
        VK_XBUTTON2 = 0x06
        VK_NUMPAD0 = 0x60
        VK_NUMPAD1 = 0x61
        VK_NUMPAD2 = 0x62
        VK_NUMPAD3 = 0x63
        VK_NUMPAD4 = 0x64
        VK_NUMPAD5 = 0x65
        VK_NUMPAD6 = 0x66
        VK_NUMPAD7 = 0x67
        VK_NUMPAD8 = 0x68
        VK_NUMPAD9 = 0x69
        VK_MULTIPLY = 0x6A
        VK_ADD = 0x6B
        VK_SEPARATOR = 0x6C
        VK_SUBTRACT = 0x6D
        VK_DECIMAL = 0x6E
        VK_DIVIDE = 0x6F
        VK_NUMLOCK = 0x90
        VK_CAPITAL = 0x14
        VK_SHIFT = 0x10
        VK_CONTROL = 0x11
        VK_MENU = 0x12
        VK_PAUSE = 0x13
        VK_RETURN = 0x0D
        VK_CONVERT = 0x18
        VK_NONCONVERT = 0x19
        VK_ACCEPT = 0x1E
        VK_MODECHANGE = 0x1F
        VK_0 = 0x30
        VK_1 = 0x31
        VK_2 = 0x32
        VK_3 = 0x33
        VK_4 = 0x34
        VK_5 = 0x35
        VK_6 = 0x36
        VK_7 = 0x37
        VK_8 = 0x38
        VK_9 = 0x39
        VK_A = 0x41
        VK_B = 0x42
        VK_C = 0x43
        VK_D = 0x44
        VK_E = 0x45
        VK_F = 0x46
        VK_G = 0x47
        VK_H = 0x48
        VK_I = 0x49
        VK_J = 0x4A
        VK_K = 0x4B
        VK_L = 0x4C
        VK_M = 0x4D
        VK_N = 0x4E
        VK_O = 0x4F
        VK_P = 0x50
        VK_Q = 0x51
        VK_R = 0x52
        VK_S = 0x53
        VK_T = 0x54
        VK_U = 0x55
        VK_V = 0x56
        VK_W = 0x57
        VK_X = 0x58
        VK_Y = 0x59
        VK_Z = 0x5A
        VK_PRINT = 0x2C
        VK_SCROLL = 0x46
        VK_APPS = 0x5D

        KEYEVENTF_KEYUP = 0x02
        KEYEVENTF_EXTENDEDKEY = 0x01
        KEYEVENTF_UNICODE = 0x04

    win32con = _win32con_stub()

    class _win32api_stub:
        """Stub for win32api.keybd_event and related functions.
        On Linux these are no-ops because we have no raw HID injection.
        
        NOTE: This is where keys SHOULD be injected on Linux.
        Currently a no-op. Logging shows what key should be activated.
        """
        @staticmethod
        def keybd_event(vKey, bScan, dwFlags, dwExtraInfo):
            if dwFlags & win32con.KEYEVENTF_KEYUP:
                logger.info("[KEY_INJECT] (stub) KEY UP: VK 0x%02X", vKey)
            else:
                logger.info("[KEY_INJECT] (stub) KEY DOWN: VK 0x%02X", vKey)
            # TODO: wire to key_inject.on Linux to actually inject
        @staticmethod
        def vk_keymapping(vk_code):
            """Stub for win32api.vk_keymapping — returns (vk_code, scan_code, extended_flag).
            On Windows this does keyboard layout translation; on Linux we return (VK, VK, 0)."""
            return (vk_code, vk_code, 0)
    win32api = _win32api_stub()


# ==========================================================================
# Linux stub: macro Python API classes (key mapping, macro management,
# action types — no actual key sending on Linux, display/UI names only)
# Original Windows versions are preserved in comments below.
# ==========================================================================

# ---------------------------------------------------------------------------
# Stub: Key — single virtual key descriptor (name + scan code)
# ---------------------------------------------------------------------------
class Key:
    """Descriptor for a single virtual key (scan code / virtual key code).

    On Linux this is a pure data object — it does NOT send any keystrokes.
    It exists only so the UI can display key names and the code_runner
    can create `Macro` objects that *look* valid at the Python level.
    """

    _key_names: dict[int, str] = {
        0x01: "MouseLeft", 0x02: "MouseRight", 0x04: "MouseMiddle",
        0x05: "MouseX1", 0x06: "MouseX2",
        0x08: "Back",     0x09: "Tab",         0x0D: "Enter",
        0x10: "Shift",    0x11: "Ctrl",        0x12: "Alt",
        0x13: "Pause",    0x14: "CapsLock",    0x18: "Convert",
        0x19: "NonConvert", 0x1E: "Accept",    0x1F: "ModeChange",
        0x20: "Space",    0x21: "PageUp",      0x22: "PageDown",
        0x23: "End",      0x24: "Home",        0x25: "Left",
        0x26: "Up",       0x27: "Right",       0x28: "Down",
        0x2C: "Print",    0x2D: "Insert",      0x2E: "Delete",
        0x2F: "Help",     0x5B: "LeftWin",     0x5C: "RightWin",
        0x5D: "Apps",     0x60: "Numpad0",     0x61: "Numpad1",
        0x62: "Numpad2",  0x63: "Numpad3",     0x64: "Numpad4",
        0x65: "Numpad5",  0x66: "Numpad6",     0x67: "Numpad7",
        0x68: "Numpad8",  0x69: "Numpad9",     0x6A: "Multiply",
        0x6B: "Add",      0x6C: "Separator",   0x6D: "Subtract",
        0x6E: "Decimal",  0x6F: "Divide",      0x70: "F1",
        0x71: "F2",       0x72: "F3",          0x73: "F4",
        0x74: "F5",       0x75: "F6",          0x76: "F7",
        0x77: "F8",       0x78: "F9",          0x79: "F10",
        0x7A: "F11",      0x7B: "F12",         0x7C: "F13",
        0x7D: "F14",      0x7E: "F15",         0x7F: "F16",
        0x80: "F17",      0x81: "F18",         0x82: "F19",
        0x83: "F20",      0x84: "F21",         0x85: "F22",
        0x86: "F23",      0x87: "F24",
        0x90: "NumLock",  0x91: "ScrollLock",
        0xA0: "LeftShift", 0xA1: "RightShift",
        0xA2: "LeftCtrl",  0xA3: "RightCtrl",
        0xA4: "LeftAlt",   0xA5: "RightAlt",
        0xB5: "Mute",     0xB6: "VolumeDown",  0xB7: "VolumeUp",
        0xB8: "Pause",    0xBA: "Semicolon",   0xBB: "Equals",
        0xBC: "Comma",    0xBD: "Minus",       0xBE: "Period",
        0xBF: "Slash",    0xC0: "Accent",      0xDB: "OpenBracket",
        0xDC: "BackSlash", 0xDD: "CloseBracket", 0xDE: "Quote",
    }

    _all_keys: dict[str, "Key"] = {}  # cache by name for key_from_name

    def __init__(self, scan_code: int, is_extended: bool = False) -> None:
        self._scan_code = scan_code
        self._is_extended = is_extended
        self._vk_code: int = scan_code  # on stub, VK == scan_code
        self._name: str = self._key_names.get(scan_code, f"Key({scan_code:#x})")

    @property
    def name(self) -> str:
        """Display name, e.g. 'Z', 'Enter', 'LeftShift'."""
        return self._name

    @property
    def scan_code(self) -> int:
        """Raw scan code."""
        return self._scan_code

    @property
    def is_extended(self) -> bool:
        """Whether this key is an extended key (numpad div, arrows, etc.)."""
        return self._is_extended

    @property
    def vk_code(self) -> int:
        """Virtual key code."""
        return self._vk_code

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Key):
            return self._scan_code == other._scan_code
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._scan_code)

    def __repr__(self) -> str:
        return f"Key({self.name!r})"


# ---------------------------------------------------------------------------
# Stub: Keys — named collection of key constants (Keys.Z, Keys.ESC, …)
# ---------------------------------------------------------------------------
class _KeysMeta(type):
    """Metaclass that lazily creates Key instances for named keys."""

    _mapping: dict[str, int] = {
        "LEFT": 0x25, "UP": 0x26, "RIGHT": 0x27, "DOWN": 0x28,
        "PRIOR": 0x21, "NEXT": 0x22, "END": 0x23, "HOME": 0x24,
        "SELECT": 0x29, "OPEN": 0x35, "PROPS": 0x36, "BACK": 0x08,
        "PRINT": 0x2C, "INS": 0x2D, "DEL": 0x2E, "HELP": 0x2F,
        "NUM0": 0x60, "NUM1": 0x61, "NUM2": 0x62, "NUM3": 0x63,
        "NUM4": 0x64, "NUM5": 0x65, "NUM6": 0x66, "NUM7": 0x67,
        "NUM8": 0x68, "NUM9": 0x69, "NUM_MUL": 0x6A, "NUM_ADD": 0x6B,
        "NUM_SEP": 0x6C, "NUM_SUB": 0x6D, "NUM_DEC": 0x6E, "NUM_DIV": 0x6F,
        "NUMLOCK": 0x90, "SCROLL": 0x91, "CAPS": 0x14,
        "LWIN": 0x5B, "RWIN": 0x5C, "APPS": 0x5D,
        "BACKPACK": 0x7E, "F15": 0x7E, "FOOD": 0x7F,
        "MODEPAIRING": 0x80,
    }

    def __getattr__(cls, name: str) -> Key:
        """Create a Key for the named attribute on first access."""
        if name.startswith("_"):
            raise AttributeError(name)
        sc: int | None = cls._mapping.get(name)
        if sc is None:
            # Fall through to win32con VK_ constants
            sc = getattr(win32con, f"VK_{name}", None)
        if sc is None:
            raise AttributeError(f"No such key: {name}")
        if sc not in _KeyClass._all_keys:
            # Auto-detect extended keys (arrows, numpad div, etc.)
            keys = sc
            k = _KeyClass(sc, False)
            k._name = name
            k._vk_code = sc
            _KeyClass._all_keys[name] = k
        return _KeyClass._all_keys[name].scan_code  # type: ignore[no-any-return]


_KeyClass = Key  # Save original class before Key is overwritten by singleton
Key = _KeysMeta("Keys", (), {})


# Also populate from win32con VK_ constants where not already mapped
_base_key_names = {
    "VK_BACK": "Back", "VK_TAB": "Tab", "VK_RETURN": "Enter",
    "VK_SHIFT": "Shift", "VK_CONTROL": "Ctrl", "VK_MENU": "Alt",
    "VK_PAUSE": "Pause", "VK_CAPITAL": "CapsLock", "VK_ESCAPE": "Esc",
    "VK_SPACE": "Space", "VK_PRIOR": "PageUp", "VK_NEXT": "PageDown",
    "VK_END": "End", "VK_HOME": "Home", "VK_LEFT": "Left",
    "VK_UP": "Up", "VK_RIGHT": "Right", "VK_DOWN": "Down",
    "VK_INSERT": "Insert", "VK_DELETE": "Delete", "VK_HELP": "Help",
    "VK_LWIN": "LeftWin", "VK_RWIN": "RightWin",
    "VK_LSHIFT": "LeftShift", "VK_RSHIFT": "RightShift",
    "VK_LCONTROL": "LeftCtrl", "VK_RCONTROL": "RightCtrl",
    "VK_LMENU": "LeftAlt", "VK_RMENU": "RightAlt",
    "VK_NUMPAD0": "Numpad0", "VK_NUMPAD1": "Numpad1", "VK_NUMPAD2": "Numpad2",
    "VK_NUMPAD3": "Numpad3", "VK_NUMPAD4": "Numpad4", "VK_NUMPAD5": "Numpad5",
    "VK_NUMPAD6": "Numpad6", "VK_NUMPAD7": "Numpad7", "VK_NUMPAD8": "Numpad8",
    "VK_NUMPAD9": "Numpad9", "VK_MULTIPLY": "NumMultiply",
    "VK_ADD": "NumAdd", "VK_SEPARATOR": "NumSeparator",
    "VK_SUBTRACT": "NumSubtract", "VK_DECIMAL": "NumDecimal",
    "VK_DIVIDE": "NumDivide", "VK_NUMLOCK": "NumLock",
    "VK_F1": "F1", "VK_F2": "F2", "VK_F3": "F3", "VK_F4": "F4",
    "VK_F5": "F5", "VK_F6": "F6", "VK_F7": "F7", "VK_F8": "F8",
    "VK_F9": "F9", "VK_F10": "F10", "VK_F11": "F11", "VK_F12": "F12",
    "VK_LBUTTON": "MouseLeft", "VK_RBUTTON": "MouseRight",
    "VK_MBUTTON": "MouseMiddle", "VK_XBUTTON1": "MouseX1",
    "VK_XBUTTON2": "MouseX2",
}
for vk_name, display_name in _base_key_names.items():
    sc = getattr(win32con, vk_name, None)
    if sc and sc not in _KeyClass._key_names:
        _KeyClass._key_names[sc] = display_name

# ─── A-Z letter keys and 0-9 number keys ───
# These were missing from _key_names entirely, so key_from_code() would
# always fall through to f"Key({scan_code:#x})" for common keys.
for i, ch in enumerate('abcdefghijklmnopqrstuvwxyz'):
    if (0x41 + i) not in _KeyClass._key_names:
        _KeyClass._key_names[0x41 + i] = ch.upper()
for i in range(10):
    vk_code = 0x30 + i
    if vk_code not in _KeyClass._key_names:
        _KeyClass._key_names[vk_code] = str(i)


def key_from_code(scan_code: int, is_extended: bool = False) -> Key:
    """Create a Key from a scan code and optional extended flag."""
    name = _KeyClass._key_names.get(scan_code, f"Key({scan_code:#x})")
    if name in _KeyClass._all_keys:
        return _KeyClass._all_keys[name]
    k = _KeyClass(scan_code, is_extended)
    k._name = name
    _KeyClass._all_keys[name] = k
    return k


def key_from_name(name: str) -> Key | None:
    """Create a Key from a display name (case-insensitive).

    Returns None if the name is not found.
    """
    if name is None:
        return None
    lower = name.lower()

    # Canonical lookup: iterate all known key names (fast, reliable)
    for vk_code, disp_name in _KeyClass._key_names.items():
        if disp_name.lower() == lower:
            if vk_code in _KeyClass._all_keys:
                return _KeyClass._all_keys[disp_name]
            return _KeyClass(vk_code)

    # Fallback: direct lookup in metaclass Keys mapping (e.g. "Z", "F1")
    if name.upper() in _KeysMeta._mapping:
        sc = _KeysMeta._mapping[name.upper()]
        return _KeyClass(sc)

    return None


def key_to_string(key: Key | None) -> str:
    """Convert a Key to its display string name."""
    if key is None:
        return ""
    return key.name


# ---------------------------------------------------------------------------
# Stub: Macro — container for a sequence of actions
# ---------------------------------------------------------------------------
class _MacroAction:
    """Internal representation of a single macro action entry."""

    def __init__(
        self,
        action_type: str,
        key: Key | None = None,
        is_pressed: bool = True,
        is_extended: bool = False,
        vjoy_id: int = 0,
        vjoy_axis: int = 0,
        value: float = 0.0,
        duration: float = 0.0,
        joystick_id: int = 0,
        joystick_axis: int = 0,
        is_pressed_joy: bool = True,
        is_vjoy_button: bool = False,
        mb_value: int = 0,
        dx: int = 0,
        dy: int = 0,
    ) -> None:
        self.action_type = action_type
        self.key = key
        self.is_pressed = is_pressed
        self.is_extended = is_extended
        self.vjoy_id = vjoy_id
        self.vjoy_axis = vjoy_axis
        self.value = value
        self.duration = duration
        self.joystick_id = joystick_id
        self.joystick_axis = joystick_axis
        self.is_pressed_joy = is_pressed_joy
        self.is_vjoy_button = is_vjoy_button
        self.mb_value = mb_value  # For MouseButtonAction button identifier
        self.dx = dx
        self.dy = dy


class Macro:
    """A sequence of actions to execute (keys pressed, pauses, vJoy, …)."""

    def __init__(self) -> None:
        self._actions: list[_MacroAction] = []
        self.exclusive: bool = False       # For use by MacroFunctor
        self.repeat: object | None = None  # CountRepeat | HoldRepeat | ToggleRepeat | None

    def add_action(self, action: object, index: int = -1) -> None:
        """Add an action to the internal action list.

        On Linux this translates action-plugin action types (KeyAction,
        JoystickAction, MouseButtonAction, MouseMotionAction, PauseAction,
        VJoyAction) into _MacroAction entries that the executor can process.
        """
        if isinstance(action, KeyAction):
            self._actions.append(_MacroAction(
                action_type="key",
                key=action.key,
                is_pressed=action.is_pressed,
            ))
        elif isinstance(action, JoystickAction):
            self._actions.append(_MacroAction(
                action_type="joystick",
                joystick_id=action.device_guid,
                joystick_axis=action.input_id,
                value=action.value,
            ))
        elif isinstance(action, MouseButtonAction):
            self._actions.append(_MacroAction(
                action_type="mouse_button",
                mb_value=action.button,
                is_pressed=action.is_pressed,
            ))
        elif isinstance(action, MouseMotionAction):
            self._actions.append(_MacroAction(
                action_type="mouse_motion",
                dx=action.dx,
                dy=action.dy,
            ))
        elif isinstance(action, PauseAction):
            self._actions.append(_MacroAction(
                action_type="pause",
                duration=action.duration,
            ))
        elif isinstance(action, VJoyAction):
            from gremlin.common import InputType as _InputType
            self._actions.append(_MacroAction(
                action_type="vjoy",
                vjoy_id=action.vjoy_id,
                vjoy_axis=action.input_id,
                is_vjoy_button=(action.input_type == _InputType.JoystickButton),
                value=action.value,
            ))
        else:
            logger.warning(
                "[MACRO] Unknown action type: %s — skipping", type(action).__name__
            )

    def press(self, key: Key) -> None:
        """Add a key-press action."""
        self._actions.append(_MacroAction("key", key=key, is_pressed=True))

    def release(self, key: Key) -> None:
        """Add a key-release action."""
        self._actions.append(_MacroAction("key", key=key, is_pressed=False))

    def tap(self, key: Key) -> None:
        """Add a key-tap (press then release)."""
        self.press(key)
        self.release(key)

    def wait(self, duration: float) -> None:
        """Add a pause/action wait."""
        self._actions.append(_MacroAction("pause", duration=duration))

    def mouse(self, x: int, y: int) -> None:
        """Add a mouse motion action."""
        self._actions.append(_MacroAction("mouse_motion", duration=0))

    @property
    def is_valid(self) -> bool:
        return len(self._actions) > 0

    @property
    def duration(self) -> float:
        return sum(a.duration for a in self._actions)


# ---------------------------------------------------------------------------
# Stub: MacroManager — singleton managing macro queue / execution thread
# ---------------------------------------------------------------------------
class _MacroThread(Thread):
    """Background thread that executes queued macros (no-op on Linux)."""

    def run(self) -> None:
        pass


class MacroManager(metaclass=common.SingletonMetaclass):
    """Singleton that manages macro execution lifecycle.

    On Linux this manages a queue of macro actions and executes them
    by injecting keys via /dev/uinput (virtual keyboard).

    Singleton pattern: uses `metaclass=common.SingletonMetaclass`
    (project-standard) instead of manual ``__new__``.
    """

    _lock: Lock = Lock()
    _queue: collections.deque[Macro] = collections.deque()
    _exec_thread: Thread | None = None
    _stop_event: Event | None = None
    _has_work: Event | None = None

    def __init__(self) -> None:
        self._default_delay = 100
        self._stop_event = Event()
        self._has_work = Event()

    @property
    def default_delay(self) -> int:
        return self._default_delay

    @default_delay.setter
    def default_delay(self, value: int) -> None:
        self._default_delay = value

    def start(self) -> None:
        """Start the macro execution thread (called once at app init)."""
        # Reset stop event — it may be set from a prior stop() call
        if self._stop_event is not None:
            was_set = self._stop_event.is_set()
            self._stop_event.clear()
            logger.info("[MACRO_MGR] start(): _stop_event was_set=%s, after_clear=%s (id=%s)",
                        was_set, self._stop_event.is_set(), id(self._stop_event))
        else:
            logger.warning("[MACRO_MGR] start(): _stop_event is None!")
        if self._exec_thread is None or not self._exec_thread.is_alive():
            logger.info("[MACRO_MGR] Starting macro executor thread")
            self._exec_thread = threading.Thread(target=self._run_loop_wrapper, daemon=True, name="MacroExecutor")
            self._exec_thread.start()
        else:
            logger.debug("[MACRO_MGR] executor thread already running, no need to start")

    def stop(self) -> None:
        """Stop the macro execution thread."""
        self._stop_event.set()
        self._has_work.set()
        if self._exec_thread:
            self._exec_thread.join(timeout=1.0)
            self._exec_thread = None
        logger.info("[MACRO_MGR] Macro executor stopped")

    def terminate_macro(self, macro: Macro) -> None:
        """Terminate a macro associated with a HoldRepeat by removing it
        from the queue and stopping its re-queueing."""
        with self._lock:
            self._queue = collections.deque(m for m in self._queue if m is not macro)
            if not self._queue:
                self._has_work.set()
                self._has_work.clear()
        logger.info("[MACRO_MGR] terminated macro (HoldRepeat end callback)")

    def queue_macro(self, macro: Macro) -> None:
        """Queue a macro for immediate execution."""
        logger.info("[MACRO_MGR] queue_macro: action_count=%d", len(macro._actions))
        # Log each action in the macro for debugging
        for i, act in enumerate(macro._actions):
            if act.action_type == "key" and act.key is not None:
                vk = act.key._vk_code if hasattr(act.key, '_vk_code') else act.key.scan_code
                logger.info("[MACRO_MGR]   action[%d]: key VK=0x%02X pressed=%s",
                            i, vk, act.is_pressed)
            elif act.action_type == "vjoy":
                btn_str = "btn" if act.is_vjoy_button else "axis"
                logger.info("[MACRO_MGR]   action[%d]: %s device=%d %s=%d value=%s is_vjoy_button=%s",
                            i, "BUTTON" if act.is_vjoy_button else "AXIS",
                            act.vjoy_id, btn_str, act.vjoy_axis, act.value, act.is_vjoy_button)
            elif act.action_type == "mouse_button":
                logger.info("[MACRO_MGR]   action[%d]: mouse_button button=%s pressed=%s",
                            i, act.mb_value, act.is_pressed)
            elif act.action_type == "pause":
                logger.info("[MACRO_MGR]   action[%d]: pause duration=%.2fs", i, act.duration)
            else:
                logger.info("[MACRO_MGR]   action[%d]: type=%s", i, act.action_type)

        with self._lock:
            self._queue.append(macro)
        self._has_work.set()
        logger.info("[MACRO_MGR] queue_macro: macro queued, _has_work set, thread started=%s",
                     self._exec_thread is not None and self._exec_thread.is_alive())
        
        # If executor thread isn't running yet (e.g., app just started),
        # make sure it's started
        self.start()

    def _run_loop_wrapper(self) -> None:
        """Wrapper for _run_loop that catches and logs any crash."""
        logger.info("[MACRO_MGR] _run_loop_wrapper STARTING, _has_work=%s (id=%s), _stop_Event=%s (id=%s)",
                     self._has_work, id(self._has_work), self._stop_event, id(self._stop_event))
        try:
            logger.info("[MACRO_MGR] _run_loop_wrapper entering _run_loop")
            self._run_loop()
            logger.info("[MACRO_MGR] _run_loop exited normally")
        except Exception:
            logger.critical("[MACRO_MGR] _run_loop crashed — see traceback below")
            import traceback
            logger.critical(traceback.format_exc())
            logger.critical("[MACRO_MGR] giving up, not restarting")
            return

    def _run_loop(self) -> None:
        """Background loop: dequeue macros and execute them."""
        logger.info("[MACRO_MGR] Executor thread started")
        while not self._stop_event.is_set():
            # Wait for work to be available
            if not self._has_work.wait(timeout=0.1):
                continue  # timeout, no work
            
            logger.info("[MACRO_MGR] _has_work flagged — polling lock...")
            
            macro = None
            with self._lock:
                if self._queue:
                    macro = self._queue.popleft()
                    logger.info("[MACRO_MGR] dequeued macro %s (%d actions)",
                                id(macro), len(macro._actions))
                else:
                    logger.info("[MACRO_MGR] queue empty despite _has_work — ignoring")
                if not self._queue:
                    self._has_work.clear()
            
            if macro is None:
                continue
                
            logger.info("[MACRO_MGR] executing macro with %d actions", len(macro._actions))
            try:
                self._execute_macro(macro)
                logger.info("[MACRO_MGR] macro execution completed successfully")
            except Exception:
                logger.exception("[MACRO_MGR] macro execution raised exception")

    def _execute_macro(self, macro: Macro) -> None:
        """Execute all actions in a macro sequentially."""
        logger.info("[MACRO] Executing macro with %d actions, type=%s",
                    len(macro._actions), type(self))
        for i, action in enumerate(macro._actions):
            logger.info("[MACRO] action[%d/%d] type=%s vjoy_id=%d vjoy_axis=%d value=%s is_vjoy_button=%s is_pressed=%s mb_value=%s",
                        i, len(macro._actions), action.action_type,
                        action.vjoy_id, action.vjoy_axis, action.value,
                        action.is_vjoy_button if hasattr(action, 'is_vjoy_button') else 'N/A',
                        action.is_pressed if hasattr(action, 'is_pressed') else 'N/A',
                        action.mb_value if hasattr(action, 'mb_value') else 'N/A')
            try:
                if action.action_type == "key" and action.key is not None:
                    logger.info("[MACRO] action[%d/%d]: executing key", i, len(macro._actions))
                    self._execute_key(action)
                elif action.action_type == "vjoy":
                    logger.info("[MACRO] action[%d/%d]: executing vjoy button=%s", i, len(macro._actions), hasattr(action, 'is_vjoy_button') and action.is_vjoy_button)
                    self._execute_vjoy(action)
                    logger.info("[MACRO] action[%d/%d]: vjoy execution completed", i, len(macro._actions))
                elif action.action_type == "joystick":
                    logger.info("[MACRO] action[%d/%d]: executing joystick", i, len(macro._actions))
                    self._execute_joystick(action)
                elif action.action_type == "mouse_button":
                    logger.info("[MACRO] action[%d/%d]: executing mouse_button", i, len(macro._actions))
                    self._execute_mouse_button(action)
                elif action.action_type == "mouse_motion":
                    logger.info("[MACRO] action[%d/%d]: executing mouse_motion dx=%d dy=%d", i, len(macro._actions), action.dx, action.dy)
                    self._execute_mouse_motion(action)
                elif action.action_type == "pause":
                    logger.info("[MACRO] action[%d] type=wait, sleeping %.2fs", i, action.duration)
                    time.sleep(action.duration)  # duration is in decimal seconds
                else:
                    logger.warning("[MACRO] action[%d/%d] type=%s UNRECOGNIZED — no executor exists",
                                   i, len(macro._actions), action.action_type)
                # Small delay between actions
                if i < len(macro._actions) - 1 and self._default_delay > 0:
                    time.sleep(self._default_delay / 1000.0)
            except Exception:
                logger.exception("[MACRO] action[%d] raised exception, skipping rest", i)

    def _execute_key(self, action: _MacroAction) -> None:
        """Execute a single key action via /dev/uinput."""
        logger.info("[KEY] _execute_key called: action_type=%s key=%s pressed=%s",
                    action.action_type, action.key, action.is_pressed)
        if action.key is None:
            logger.info("[KEY] skipping — key is None")
            return
            
        key_obj = action.key
        scan_code = key_obj.scan_code if hasattr(key_obj, 'scan_code') else key_obj
        logger.info("[KEY] key_obj type=%s scan_code=%s", type(key_obj), scan_code)
        
        # Convert to VK code for injection
        if hasattr(key_obj, '_vk_code'):
            vk_code = key_obj._vk_code
            logger.info("[KEY] extracted vk_code via _vk_code: 0x%02X", vk_code)
        else:
            vk_code = scan_code  # use scan code as VK for key_inject
            logger.info("[KEY] using scan_code as vk_code: 0x%02X", vk_code)
        
        # Use key_inject.vk_to_evdev() as the authoritative source
        # vk_to_evdev now returns None for unmapped VK codes (critical fix)
        logger.info("[KEY] calling vk_to_evdev(0x%02X)...", vk_code)
        from gremlin import key_inject
        ev_code = key_inject.vk_to_evdev(vk_code)
        logger.info("[KEY] vk_to_evdev returned: %s", ev_code)
        
        # If vk doesn't have a valid evdev mapping, log and skip
        if ev_code is None:
            logger.warning("[KEY] vk_to_evdev(0x%02X) → None, no evdev mapping for this VK code. "
                           "Key cannot be injected.", vk_code)
            return
        
        pressed = action.is_pressed
        logger.info("[KEY] ready to inject: VK=0x%02X → evdev=%d pressed=%s", vk_code, ev_code, pressed)
        
        # Inject via key_inject module if available
        try:
            if pressed:
                logger.info("[KEY] calling inject_key_down(0x%02X)...", vk_code)
                key_inject.inject_key_down(vk_code)
                logger.info("[KEY] inject_key_down returned OK — no error logged")
            else:
                logger.info("[KEY] calling inject_key_up(0x%02X)...", vk_code)
                key_inject.inject_key_up(vk_code)
                logger.info("[KEY] inject_key_up returned OK — no error logged")
        except Exception:
            logger.exception("[KEY] injection raised exception")

    def _execute_vjoy(self, action: _MacroAction) -> None:
        """Execute a vJoy action (button, axis, or hat) on a virtual joystick device."""
        logger.info("[VJOY] _execute_vjoy called: vjoy_id=%d axis=%s value=%s",
                    action.vjoy_id, action.vjoy_axis, action.value)
        if action.vjoy_id <= 0:
            logger.info("[VJOY] skipping: vjoy_id=%d <= 0", action.vjoy_id)
            return
        
        try:
            from vjoy_linux.vjoy_interface import VJoyInterface
        except ImportError:
            logger.warning("[VJOY] vJoyInterface not available on this system")
            return
        
        dev = VJoyInterface._devices.get(action.vjoy_id)
        if dev is None or dev.fd is None:
            logger.warning("[VJOY] vJoy device %d not found or fd=None", action.vjoy_id)
            return
        
        logger.info("[VJOY] device %d found (fd=%s), determining action type...", action.vjoy_id, dev.fd)
        if hasattr(action, 'is_vjoy_button') and action.is_vjoy_button:
            # Button press: value should be True/False
            logger.info("[VJOY] button action detected")
            raw_val = action.value
            if isinstance(raw_val, str):
                is_pressed = raw_val.lower() in ("true", "1", "yes", "on", "pressed")
            else:
                is_pressed = bool(raw_val)
            try:
                result = VJoyInterface.SetBtn(is_pressed, action.vjoy_id, action.vjoy_axis)
                state_str = "pressed" if is_pressed else "released"
                logger.info("[VJOY] SetBtn → device=%d axis=%d %s result=%s",
                            action.vjoy_id, action.vjoy_axis, state_str, result)
            except Exception:
                logger.exception("[VJOY] SetBtn failed")
        else:
            # Axis or POV hat: value should be numeric (float or int)
            logger.info("[VJOY] axis/hat action detected")
            try:
                value = float(action.value) if action.value is not None else 0.0
            except (ValueError, TypeError):
                logger.warning("[VJOY] invalid value '%s' for vJoy device %d",
                               action.value, action.vjoy_id)
                return
            
            # Determine if this is an axis or POV hat based on vjoy_axis range
            if action.vjoy_axis < 10:
                logger.info("[VJOY] classified as axis (vjoy_axis=%d)", action.vjoy_axis)
                if 0.0 <= value <= 1.0:
                    abs_value = int(32767 * value)
                elif -1.0 <= value < 0.0:
                    abs_value = int(32767 * -1 * value)
                else:
                    abs_value = int(32767 * max(-1.0, min(1.0, value)))
                
                try:
                    result = VJoyInterface.SetAxis(abs_value, action.vjoy_id, action.vjoy_axis)
                    logger.info("[VJOY] SetAxis → device=%d axis=%d value=%d result=%s",
                                action.vjoy_id, action.vjoy_axis, abs_value, result)
                except Exception:
                    logger.exception("[VJOY] SetAxis failed")
            else:
                logger.info("[VJOY] classified as POV hat (vjoy_axis=%d)", action.vjoy_axis)
                pov_value = int(value) if not isinstance(value, int) else value
                try:
                    result = VJoyInterface.SetContPov(pov_value, action.vjoy_id, action.vjoy_axis)
                    logger.info("[VJOY] SetContPov → device=%d hat=%d value=%d result=%s",
                                action.vjoy_id, action.vjoy_axis, pov_value, result)
                except Exception:
                    logger.exception("[VJOY] SetContPov failed")

    def _execute_mouse_button(self, action: _MacroAction) -> None:
        """Execute a mouse button action via uinput/XTest mouse injection."""
        logger.info("[MOUSE] _execute_mouse_button called: mb_value=%s pressed=%s",
                    action.mb_value, action.is_pressed)
        from gremlin.common import MouseButton
        button = action.mb_value if action.mb_value else MouseButton.Left
        logger.info("[MOUSE] button resolved: raw=%s type=%s", action.mb_value, type(button))
        if not isinstance(button, MouseButton):
            logger.info("[MOUSE] attempting int coercion...")
            try:
                button = MouseButton(int(button))
                logger.info("[MOUSE] coercion OK: %s", button)
            except (ValueError, TypeError):
                logger.error("[MOUSE] cannot coerce mb_value=%s to MouseButton — aborting",
                             action.mb_value)
                return

        logger.info("[MOUSE] action=%s button=%s",
                    "press" if action.is_pressed else "release", button)
        if action.is_pressed:
            try:
                from gremlin_linux.mouse_inject import mouse_press as _mouse_press
                logger.info("[MOUSE] calling mouse_press...")
                _mouse_press(button)
                logger.info("[MOUSE] mouse_press OK — no error logged")
            except Exception:
                logger.exception("[MOUSE] mouse_press injection failed")
        else:
            try:
                from gremlin_linux.mouse_inject import mouse_release as _mouse_release
                logger.info("[MOUSE] calling mouse_release...")
                _mouse_release(button)
                logger.info("[MOUSE] mouse_release OK — no error logged")
            except Exception:
                logger.exception("[MOUSE] mouse_release injection failed")

    def _execute_mouse_motion(self, action: _MacroAction) -> None:
        """Execute a mouse motion (move to screen position dx, dy).

        Uses xdotool (matching the working map_to_mouse XY motion at line 469).
        """
        logger.info("[MOUSE] _execute_mouse_motion called: dx=%d dy=%d",
                    action.dx, action.dy)
        try:
            import subprocess
            result = subprocess.run(
                ["xdotool", "mousemove", str(action.dx), str(action.dy)],
                timeout=5,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if result.returncode == 0:
                logger.info("[MOUSE] mouse_motion → xdotool (%d, %d) OK",
                            action.dx, action.dy)
            else:
                logger.warning("[MOUSE] xdotool exited with code %d", result.returncode)
        except FileNotFoundError:
            logger.warning("[MOUSE] xdotool not found — mouse motion skipped")
        except subprocess.TimeoutExpired:
            logger.warning("[MOUSE] xdotool timed out for (%d, %d)", action.dx, action.dy)
        except Exception:
            logger.exception("[MOUSE] mouse_motion injection failed")

    def _execute_joystick(self, action: _MacroAction) -> None:
        """Execute a joystick action: bridge a physical joystick event into vJoy output.

        When a physical joystick button/axis event is captured (e.g. by a physical
        joystick plugin), this translates it into the equivalent vJoy output on
        the configured virtual device.
        """
        guid = str(action.joystick_id) if action.joystick_id is not None else "None"
        axis = int(action.joystick_axis) if action.joystick_axis else 0
        logger.info(
            "[JOYSTICK] _execute_joystick called: device_guid=%s axis=%d value=%s",
            guid, axis, action.value
        )
        logger.info(
            "[MACRO] Joystick action: device_guid=%s axis=%d value=%s (no-op — vJoy output on Linux not yet implemented)",
            guid, axis, action.value
        )


# ---------------------------------------------------------------------------
# Stub: Action types used by macro plugins
# These must match what action_plugins/macro/__init__.py expects:
#   JoystickAction, KeyAction, MouseButtonAction, MouseMotionAction,
#   PauseAction, VJoyAction
# ---------------------------------------------------------------------------
class JoystickAction:
    """Represents a joystick axis/button action."""

    def __init__(
        self,
        device_guid: int,
        input_type: object,
        input_id: int,
        value: str = "",
        axis_type: str = "absolute",
    ) -> None:
        self.device_guid = device_guid
        self.input_type = input_type  # InputType.JoystickAxis, JoystickButton, JoystickHat
        self.input_id = input_id
        self.value = value  # For axis: numeric string; for button: "True"/"False"; for hat: direction string
        self.axis_type = axis_type  # "absolute" or "relative" (for vJoy compatibility)


class KeyAction:
    """Represents a keyboard key action."""

    def __init__(self, key: Key, is_pressed: bool = True) -> None:
        self.key = key
        self.is_pressed = is_pressed


class MouseButtonAction:
    """Represents a mouse button press/release."""

    def __init__(
        self,
        button: int,
        is_pressed: bool = True
    ) -> None:
        self.button = button
        self.is_pressed = is_pressed


class MouseMotionAction:
    """Represents mouse motion (delta X, Y)."""

    def __init__(self, dx: int, dy: int) -> None:
        self.dx = dx
        self.dy = dy


class PauseAction:
    """Represents a pause / wait duration."""

    def __init__(self, duration: float) -> None:
        self.duration = duration


class VJoyAction:
    """Represents a vJoy axis/button/hat action."""

    def __init__(
        self,
        vjoy_id: int,
        input_type: object,
        input_id: int,
        value: str | bool,
        axis_type: str = "absolute",
    ) -> None:
        self.vjoy_id = vjoy_id
        self.input_type = input_type  # InputType.JoystickAxis, JoystickButton, JoystickHat
        self.input_id = input_id
        self.value = value  # For axis: numeric string; for button: "True"/"False"; for hat: direction string
        self.axis_type = axis_type  # "absolute" or "relative" (for vJoy compatibility)


# ===== Repeat types used by macro plugin for auto-repeating a macro =====
# These are pure data objects — they store repeat configuration
# and handle XML serialization for profile persistence.

class CountRepeat:
    """Repeat a macro a specific number of times."""

    def __init__(self, count: int = 1, delay: float = 0.1) -> None:
        self.count: int = count
        self.delay: float = delay

    def to_xml(self) -> "ElementTree.Element":
        """Serialize to XML element."""
        node = ElementTree.Element("repeat")
        node.set("type", "count")
        count_el = ElementTree.SubElement(node, "count")
        count_el.set("value", str(self.count))
        delay_el = ElementTree.SubElement(node, "delay")
        delay_el.set("value", str(self.delay))
        return node

    @classmethod
    def from_xml(cls, node: "ElementTree.Element") -> "CountRepeat":
        """Deserialize from XML element."""
        count = int(node.find("count").get("value"))  # type: ignore[union-attr]
        delay = float(node.find("delay").get("value"))  # type: ignore[union-attr]
        return cls(count=count, delay=delay)


class ToggleRepeat:
    """Toggle repeat on/off; macro repeats until toggle pressed again."""

    def __init__(self, delay: float = 0.1) -> None:
        self.delay: float = delay

    def to_xml(self) -> "ElementTree.Element":
        """Serialize to XML element."""
        node = ElementTree.Element("repeat")
        node.set("type", "toggle")
        delay_el = ElementTree.SubElement(node, "delay")
        delay_el.set("value", str(self.delay))
        return node

    @classmethod
    def from_xml(cls, node: "ElementTree.Element") -> "ToggleRepeat":
        """Deserialize from XML element."""
        delay = float(node.find("delay").get("value"))  # type: ignore[union-attr]
        return cls(delay=delay)


class HoldRepeat:
    """Hold to repeat; macro repeats while button is held."""

    def __init__(self, delay: float = 0.1) -> None:
        self.delay: float = delay

    def to_xml(self) -> "ElementTree.Element":
        """Serialize to XML element."""
        node = ElementTree.Element("repeat")
        node.set("type", "hold")
        delay_el = ElementTree.SubElement(node, "delay")
        delay_el.set("value", str(self.delay))
        return node

    @classmethod
    def from_xml(cls, node: "ElementTree.Element") -> "HoldRepeat":
        """Deserialize from XML element."""
        delay = float(node.find("delay").get("value"))  # type: ignore[union-attr]
        return cls(delay=delay)
