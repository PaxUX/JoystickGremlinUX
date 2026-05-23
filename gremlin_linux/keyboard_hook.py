# -*- coding: utf-8; -*-

# Copyright (C) 2025 JoystickGremlin Linux Port
#
# Linux keyboard input capture using evdev.
# Replaces the Windows WH_KEYBOARD_LL hook.
#
# Cross-distro compatible: works on Ubuntu, Arch, Fedora, etc.
# Display-server agnostic: X11, Wayland, or TTY.
# Lightweight: pure evdev binding, no X server dependency.

import evdev
from evdev import InputDevice
import threading
import logging

logger = logging.getLogger("system")


def _build_evdev_to_vk() -> dict[int, int]:
    """Build the evdev→VK code mapping using evdev.ecodes.KEY_* constants.

    Linux `evdev` uses a completely different keycode numbering scheme from
    Windows Virtual Key codes.  This table translates raw evdev codes so that
    the rest of JoystickGremlin (which expects Windows VK codes) works
    correctly on both platforms.
    """
    mapping: dict[int, int] = {}
    try:
        from evdev import ecodes
    except ImportError:
        return mapping

    def _map(evdev_const: str, vk_code: int) -> None:
        code = getattr(ecodes, evdev_const, None)
        if code is not None:
            mapping[code] = vk_code

    # ── F-keys ──
    _map('KEY_F1', 0x70);   _map('KEY_F2', 0x71);   _map('KEY_F3', 0x72)
    _map('KEY_F4', 0x73);   _map('KEY_F5', 0x74);   _map('KEY_F6', 0x75)
    _map('KEY_F7', 0x76);   _map('KEY_F8', 0x77);   _map('KEY_F9', 0x78)
    _map('KEY_F10', 0x79);  _map('KEY_F11', 0x7A);  _map('KEY_F12', 0x7B)
    _map('KEY_F13', 0x7C);  _map('KEY_F14', 0x7D);  _map('KEY_F15', 0x7E)
    _map('KEY_F16', 0x7F);  _map('KEY_F17', 0x80);  _map('KEY_F18', 0x81)
    _map('KEY_F19', 0x82);  _map('KEY_F20', 0x83);  _map('KEY_F21', 0x84)
    _map('KEY_F22', 0x85);  _map('KEY_F23', 0x86);  _map('KEY_F24', 0x87)
    _map('KEY_ESC', 0x1B)

    # ── Modifiers ──
    _map('KEY_LEFTSHIFT', 0xA0);  _map('KEY_RIGHTSHIFT', 0xA1)
    _map('KEY_LEFTCTRL', 0xA2);   _map('KEY_RIGHTCTRL', 0xA3)
    _map('KEY_LEFTALT', 0xA4);    _map('KEY_RIGHTALT', 0xA5)
    _map('KEY_LEFTMETA', 0x5B);   _map('KEY_RIGHTMETA', 0x5C)
    _map('KEY_MENU', 0x5D);       _map('KEY_CAPSLOCK', 0x14)
    _map('KEY_NUMLOCK', 0x90);    _map('KEY_SCROLLLOCK', 0x91)

    # ── Navigation ──
    _map('KEY_HOME', 0x24);  _map('KEY_END', 0x23);
    _map('KEY_PAGEUP', 0x21); _map('KEY_PAGEDOWN', 0x22);
    _map('KEY_UP', 0x26);    _map('KEY_DOWN', 0x28);
    _map('KEY_LEFT', 0x25);  _map('KEY_RIGHT', 0x27);
    _map('KEY_INSERT', 0x2D);  _map('KEY_DELETE', 0x2E)

    # ── Editing ──
    _map('KEY_ENTER', 0x0D);  _map('KEY_TAB', 0x09)
    _map('KEY_BACKSPACE', 0x08); _map('KEY_PAUSE', 0x13)
    _map('KEY_PRINT', 0x2C)

    # ── Letters — A-Z = 0x41-0x5A ──
    for i, ch in enumerate('abcdefghijklmnopqrstuvwxyz'):
        _map(f'KEY_{ch.upper()}', 0x41 + i)

    # ── Number row 1-0 — VK 0x31-0x30 ──
    # evdev KEY_1 = 2 … KEY_0 = 11
    _map('KEY_1', 0x31); _map('KEY_2', 0x32); _map('KEY_3', 0x33)
    _map('KEY_4', 0x34); _map('KEY_5', 0x35); _map('KEY_6', 0x36)
    _map('KEY_7', 0x37); _map('KEY_8', 0x38); _map('KEY_9', 0x39)
    _map('KEY_0', 0x30)

    # ── Punctuation (QWERTY) ──
    _map('KEY_GRAVE', 0xC0)   # ` / ~
    _map('KEY_MINUS', 0xBD)   # - / _
    _map('KEY_EQUAL', 0xBB)   # = / +
    _map('KEY_LEFTBRACE', 0xDB)  # [ / {
    _map('KEY_RIGHTBRACE', 0xDD) # ] / }
    _map('KEY_BACKSLASH', 0xDC)  # \ |
    _map('KEY_SEMICOLON', 0xBA)  # ; :
    _map('KEY_APOSTROPHE', 0xDE) # ' "
    _map('KEY_COMMA', 0xBC)   # , <
    _map('KEY_DOT', 0xBE)     # . >
    _map('KEY_SLASH', 0xBF)   # / ?

    # ── Numpad ──
    _map('KEY_KP0', 0x60);  _map('KEY_KP1', 0x61);  _map('KEY_KP2', 0x62)
    _map('KEY_KP3', 0x63);  _map('KEY_KP4', 0x64);  _map('KEY_KP5', 0x65)
    _map('KEY_KP6', 0x66);  _map('KEY_KP7', 0x67);  _map('KEY_KP8', 0x68)
    _map('KEY_KP9', 0x69);  _map('KEY_KPDOT', 0x6E)
    _map('KEY_KPASTERISK', 0x6A); _map('KEY_KPPLUS', 0x6B)
    _map('KEY_KPMINUS', 0x6D);  _map('KEY_KPSLASH', 0x6F)
    _map('KEY_KPENTER', 0x0D)

    # ── Misc ──
    _map('KEY_SPACE', 0x20)

    return mapping


_EVDEV_TO_VK: dict[int, int] | None = None


def _evdev_to_vk(raw_code: int) -> int:
    """Translate a raw Linux evdev keycode to a Windows Virtual Key code.

    If the code is unmapped it is returned unchanged as a safe fallback.
    """
    global _EVDEV_TO_VK
    if _EVDEV_TO_VK is None:
        _EVDEV_TO_VK = _build_evdev_to_vk()
    return _EVDEV_TO_VK.get(raw_code, raw_code)


class KeyEvent:
    """Linux KeyEvent struct matching windows_event_hook interface.

    Wayland note: on native Wayland sessions, the compositor/focused client
    handles key repeat. The kernel delivers value=2 (repeat) events to
    all evdev readers including our hook. The original code dropped these
    entirely, which caused sustained-press logic in macros (HoldRepeat) to
    never fire repeat ticks on Wayland.
    """

    def __init__(
        self,
        scan_code: int,
        is_extended: bool,
        is_pressed: bool,
        is_injected: bool = False,
        is_repeat: bool = False,
    ) -> None:
        self._scan_code = scan_code
        self._is_extended = is_extended
        self._is_pressed = is_pressed
        self._is_injected = is_injected
        self._is_repeat = is_repeat

    @property
    def scan_code(self) -> int:
        return self._scan_code

    @property
    def is_extended(self) -> bool:
        return self._is_extended

    @property
    def is_pressed(self) -> bool:
        return self._is_pressed

    @property
    def is_injected(self) -> bool:
        return self._is_injected

    @property
    def is_repeat(self) -> bool:
        """Whether this event is a kernel-generated repeat (value==2).

        On X11: always False (X server handles repeat).
        On Wayland: True for auto-repeated keys, False for raw press/release.
        """
        return self._is_repeat

    def __str__(self) -> str:
        return (
            f"KbdEvent(scan={hex(self._scan_code)}"
            f", ext={self._is_extended}"
            f", press={self._is_pressed}"
            f", inj={self._is_injected}"
            f", rpt={self._is_repeat})"
        )


def _classify_keyboard_devices() -> list[InputDevice]:
    """Detect keyboard devices from /dev/input/event*.

    A device is considered a keyboard if it has EV_KEY capability and
    contains standard keyboard keys (A-Z, ESC, SPACE, CAPSLOCK, TAB)
    but does NOT have gamepad-style buttons (BTN_SOUTH etc) or mouse buttons (BTN_*_LEFT).
    """
    keyboards: list[InputDevice] = []

    for dev_path in evdev.list_devices():
        try:
            dev = InputDevice(dev_path)
        except (PermissionError, OSError):
            continue

        caps = dev.capabilities()
        key_codes = caps.get(1, [])  # EV_KEY
        abs_codes = caps.get(3, [])  # EV_ABS

        # Check for keyboard characteristics
        # Standard Linux key codes: 0-255 for most keys
        has_keyboard = any(0 < k < 250 for k in key_codes)
        has_gamepad_btns = any(304 <= k <= 369 for k in key_codes)
        has_mouse_btns = any(k in [272, 273, 274] for k in key_codes)

        # Check for gamepad axes (ABS_X, ABS_Y, etc.)
        gamepad_axes = {0, 1, 2, 3, 4, 16, 17}  # ABS_X/Y/Z/RX/RY /HAT0/1
        has_gamepad_axes = bool(gamepad_axes & set(abs_codes))

        if has_keyboard and not has_gamepad_btns and not has_gamepad_axes and not has_mouse_btns:
            keyboards.append(dev)
            logger.debug(
                "[KB_HOOK] Detected keyboard: %s at %s",
                dev.name,
                dev_path,
            )

    return keyboards


class KeyboardHook:
    """Captures keyboard events via evdev on Linux.

    Replacement for Windows WH_KEYBOARD_LL hook.
    Works across X11, Wayland, and TTY environments.
    """

    def __init__(self) -> None:
        self._callbacks: list[callable] = []
        self._keyboard_devices: list[InputDevice] = []
        self._running = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def register(self, callback: callable) -> None:
        """Register a callback to receive keyboard events.

        Args:
            callback: Function that receives a KeyEvent on each key press/release.
        """
        with self._lock:
            if callback not in self._callbacks:
                self._callbacks.append(callback)

    def start(self) -> None:
        """Start capturing keyboard events."""
        if self._running:
            return

        self._keyboard_devices = _classify_keyboard_devices()
        if not self._keyboard_devices:
            logger.warning("[KB_HOOK] No keyboard devices found on /dev/input.")
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name="kb-hook",
        )
        self._thread.start()
        logger.info(
            "[KB_HOOK] Started. Capturing from %d keyboard device(s).",
            len(self._keyboard_devices),
        )

    def stop(self) -> None:
        """Stop capturing keyboard events."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        self._keyboard_devices.clear()

    def _emit(self, event: KeyEvent) -> None:
        """Fire event to all registered callbacks."""
        with self._lock:
            callbacks = list(self._callbacks)
        for cb in callbacks:
            try:
                cb(event)
            except Exception:
                logger.exception("[KB_HOOK] Callback raised during event delivery.")

    def _poll_loop(self) -> None:
        """Background thread: read events from all keyboard devices."""
        while self._running:
            devices = [dev for dev in self._keyboard_devices if dev.fd >= 0]
            if not devices:
                break

            for dev in devices:
                try:
                    for event in dev.read():
                        # event.type == ecodes.EV_KEY (1)
                        if event.type == 1:
                            # evdev event.value: 0=release, 1=press, 2=repeat
                            # On X11: X server handles key repeat → X hook only gets press/release.
                            # On Wayland: compositor/focused-client handles repeat → kernel sends
                            # event.value=2 to all readers. We include repeat events so any
                            # code that needs sustained-press (e.g. HoldRepeat in macros) works.
                            # Filter: only release (0) and any non-repeat (1 or 2)
                            if event.value in (0, 1, 2):
                                raw_code = int(event.code)
                                # Translate Linux evdev code → Windows VK code
                                # so the rest of the app sees correct key names
                                sc = _evdev_to_vk(raw_code)
                                is_extended = sc > 127

                                # On X11: repeat events (value==2) are debounced by the X server
                                # before reaching the hook, so this branch never fires on X11.
                                # On Wayland: repeat events ARE delivered to all evdev readers.
                                # We treat repeat as "still pressed" for downstream logic that
                                # checks hold state.
                                is_repeat = (event.value == 2)
                                is_down = (event.value in (1, 2))

                                kb_event = KeyEvent(
                                    scan_code=sc,
                                    is_extended=is_extended,
                                    is_pressed=is_down,
                                    is_injected=False,       # evdev never marks injected keys
                                    is_repeat=is_repeat,      # repeat vs raw press/release
                                )
                                self._emit(kb_event)
                except (OSError, IOError) as e:
                    # Device was removed or disconnected
                    logger.warning(
                        "[KB_HOOK] Device disconnected: %s - %s",
                        dev.path if hasattr(dev, 'path') else dev.fd,
                        e,
                    )
                    continue
                except Exception:
                    logger.exception(
                        "[KB_HOOK] Error reading from device: %s",
                        dev.path if hasattr(dev, 'path') else dev.fd,
                    )


# Global singleton for compatibility with windows_event_hook API
_g_keyboard_hook: KeyboardHook | None = None
g_keyboard_callbacks: list[callable] = []


def get_keyboard_hook() -> KeyboardHook:
    """Get the global KeyboardHook singleton."""
    global _g_keyboard_hook
    if _g_keyboard_hook is None:
        _g_keyboard_hook = KeyboardHook()
    return _g_keyboard_hook
