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


import ctypes
from ctypes import wintypes
import threading
import sys

import gremlin.common


# ==================================================================
# Linux stub for windows_event_hook
# ================================================================
# On Linux there is no user32.dll. This module stub provides no-op
# KeyboardHook and MouseHook implementations so the event_handler
# can import and use them without crashing. Physical keyboard/mouse
# capture must be implemented via evdev for Linux.

_WindowsUser32 = None  # Will hold the Windows user32 WinDLL if loaded
_windows_loaded = False  # Track whether Windows DLL was successfully loaded

try:
    # Linux doesn't have ctypes.WinDLL — attribute error
    user32 = ctypes.WinDLL("user32")
    _WindowsUser32 = user32
    _windows_loaded = True

    g_keyboard_callbacks = []
    g_mouse_callbacks = []

    # The following pages are references to the various functions used:
    #
    # SetWindowsHookEx
    #     https://msdn.microsoft.com/en-us/library/windows/desktop/ms644990(v=vs.85).aspx
    # LowLevelMouseProc
    #     https://msdn.microsoft.com/de-de/library/windows/desktop/ms644986(v=vs.85).aspx
    # MSLLHOOKSTRUCT
    #     https://msdn.microsoft.com/en-us/library/ms644970(v=vs.85).aspx
    # LowLevelKeyboardProc
    #     https://msdn.microsoft.com/en-us/library/ms644985(v=vs.85).aspx
    # KBDLLHOOKSTRUCT
    #     https://msdn.microsoft.com/en-us/library/windows/desktop/ms644967(v=vs.85).aspx

    # Signature of a hook callback function which can be used as a decorator
    HOOKPROC = ctypes.WINFUNCTYPE(
        wintypes.LPARAM,
        ctypes.c_int,
        wintypes.WPARAM,
        wintypes.LPARAM
    )

    # Function to hook into an event stream
    user32.SetWindowsHookExW.restype = wintypes.HHOOK
    user32.SetWindowsHookExW.argtypes = (
        ctypes.c_int,           # _In_ idHook
        HOOKPROC,               # _In_ lpfn
        wintypes.HINSTANCE,     # _In_ hMod
        wintypes.DWORD          # _In_ dwThreadId
    )

    # Function to call next hook in the chain
    user32.CallNextHookEx.restype = wintypes.LPARAM
    user32.CallNextHookEx.argtypes = (
        wintypes.HHOOK,         # _In_opt_ hhk
        ctypes.c_int,           # _In_     nCode
        wintypes.WPARAM,        # _In_     wParam
        wintypes.LPARAM         # _In_     lParam
    )

    # Retrieve a single message from a stream
    user32.GetMessageW.argtypes = (
        wintypes.LPMSG,         # _Out_    lpMsg
        wintypes.HWND,          # _In_opt_ hWnd
        wintypes.UINT,          # _In_     wMsgFilterMin
        wintypes.UINT           # _In_     wMsgFilterMax
    )

    # Convert message content
    user32.TranslateMessage.argtypes = (wintypes.LPMSG,)

    # Dispatch message to hooked processes
    user32.DispatchMessageW.argtypes = (wintypes.LPMSG,)

    g_keyboard_callbacks = []
    g_mouse_callbacks = []

    # Action definitions
    HC_ACTION       = 0
    WH_KEYBOARD_LL  = 13
    WH_MOUSE_LL     = 14

    WM_QUIT         = 0x0012
    WM_MOUSEMOVE    = 0x0200
    WM_LBUTTONDOWN  = 0x0201
    WM_LBUTTONUP    = 0x0202
    WM_RBUTTONDOWN  = 0x0204
    WM_RBUTTONUP    = 0x0205
    WM_MBUTTONDOWN  = 0x0207
    WM_MBUTTONUP    = 0x0208
    WM_MOUSEWHEEL   = 0x020A
    WM_XBUTTONDOWN  = 0x020B
    WM_XBUTTONUP    = 0x020C
    WM_MOUSEHWHEEL  = 0x020E


    class KBDLLHOOKSTRUCT(ctypes.Structure):

        """Data structure used with keuboard callbacks."""

        _fields_ = (
            ("vkCode",      wintypes.DWORD),
            ("scanCode",    wintypes.DWORD),
            ("flags",       wintypes.DWORD),
            ("time",        wintypes.DWORD),
            ("dwExtraInfo", wintypes.WPARAM)
        )
    LPKBDLLHOOKSTRUCT = ctypes.POINTER(KBDLLHOOKSTRUCT)


    class MSLLHOOKSTRUCT(ctypes.Structure):

        """Data structure used with mouse callbacks."""

        _fields_ = (
            ("pt",          wintypes.POINT),
            ("mouseData",   wintypes.DWORD),
            ("flags",       wintypes.DWORD),
            ("time",        wintypes.DWORD),
            ("dwExtraInfo", wintypes.WPARAM)
        )
    LPMSLLHOOKSTRUCT = ctypes.POINTER(MSLLHOOKSTRUCT)

    @HOOKPROC
    def process_keyboard_event(n_code, w_param, l_param):
        """Process a single keyboard event.

        :param n_code code detailing how to process the event
        :param w_param message type identifier
        :param l_param message content
        """
        msg = ctypes.cast(l_param, LPKBDLLHOOKSTRUCT)[0]

        # Only handle events we're supposed to, see
        # https://msdn.microsoft.com/en-us/library/windows/desktop/ms644985(v=vs.85).aspx
        if n_code >= 0 and msg.scanCode:
            # Extract data from the message
            scan_code = msg.scanCode & 0xFF
            is_extended = msg.flags is not None and bool(msg.flags & 0x0001)
            is_pressed = w_param in [0x0100, 0x0104]
            is_injected = msg.flags is not None and bool(msg.flags & 0x0010)

            # A scan code of 541 indicates AltGr being pressed. AltGr is sent
            # as a combination of RAlt + RCtrl to the system and as such
            # generates two key events, one for RAlt and one for RCtrl. The
            # RCtrl one is being modified due to RAlt being pressed.
            #
            # In this application we want the RAlt key press and ignore the
            # RCtrl key press.

            # Create the event and pass it to all all registered callbacks
            if msg.scanCode != 541:
                evt = KeyEvent(scan_code, is_extended, is_pressed, is_injected)
                for cb in g_keyboard_callbacks:
                    cb(evt)

        # Pass the event on to the next callback in the chain
        return user32.CallNextHookEx(None, n_code, w_param, l_param)


    @HOOKPROC
    def process_mouse_event(n_code, w_param, l_param):
        """Process a single mouse event.

        :param n_code code detailing how to process the event
        :param w_param message type identifier
        :param l_param message content
        """
        if n_code == HC_ACTION and w_param != WM_MOUSEMOVE:
            msg = ctypes.cast(l_param, LPMSLLHOOKSTRUCT)[0]

            # Only handle events we're supposed to, see
            # https://msdn.microsoft.com/en-us/library/windows/desktop/ms644985(v=vs.85).aspx
            button_id = None
            is_pressed = True
            if w_param in [WM_LBUTTONDOWN, WM_LBUTTONUP]:
                button_id = gremlin.common.MouseButton.Left
                is_pressed = w_param == WM_LBUTTONDOWN
            elif w_param in [WM_RBUTTONDOWN, WM_RBUTTONUP]:
                button_id = gremlin.common.MouseButton.Right
                is_pressed = w_param == WM_RBUTTONDOWN
            elif w_param in [WM_MBUTTONDOWN, WM_MBUTTONUP]:
                button_id = gremlin.common.MouseButton.Middle
                is_pressed = w_param == WM_MBUTTONDOWN
            elif w_param in [WM_XBUTTONDOWN, WM_XBUTTONUP]:
                if msg.mouseData & (0x0001 << 16):
                    button_id = gremlin.common.MouseButton.Back
                elif msg.mouseData & (0x0002 << 16):
                    button_id = gremlin.common.MouseButton.Forward
                is_pressed = w_param == WM_XBUTTONDOWN
            elif w_param == WM_MOUSEWHEEL:
                if (msg.mouseData >> 16) == 120:
                    button_id = gremlin.common.MouseButton.WheelUp
                elif (msg.mouseData >> 16) == 65416:
                    button_id = gremlin.common.MouseButton.WheelDown

            # Create the event and pass it to all all registered callbacks
            evt = MouseEvent(button_id, is_pressed, False)
            for cb in g_mouse_callbacks:
                cb(evt)

        # Pass the event on to the next callback in the chain
        return user32.CallNextHookEx(None, n_code, w_param, l_param)


    class KeyEvent:

        """Structure containing details about a key event."""

        def __init__(self, scan_code, is_extended, is_pressed, is_injected):
            """Creates a new instance with the given data.

            :param scan_code the hardware scan code of this event
            :param is_extended whether or not the scan code is an extended one
            :param is_pressed flag indicating if the key is pressed
            :param is_injected flag indicating if the event has been injected
            """
            self._scan_code = scan_code
            self._is_extended = is_extended
            self._is_pressed = is_pressed
            self._is_injected = is_injected

        def __str__(self):
            """Returns a string representation of the event.

            :return string representation of the event
            """
            return "({} {}) {}, {}".format(
                hex(self._scan_code),
                self._is_extended,
                "down" if self._is_pressed else "up",
                "injected" if self._is_injected else ""
            )

        @property
        def scan_code(self):
            return self._scan_code

        @property
        def is_extended(self):
            return self._is_extended

        @property
        def is_pressed(self):
            return self._is_pressed

        @property
        def is_injected(self):
            return self._is_injected


    class MouseEvent:

        """Structure containing information about a mouse event."""

        def __init__(self, button_id, is_pressed, is_injected):
            self._button_id = button_id
            self._is_pressed = is_pressed
            self._is_injected = is_injected
#PaxUX
        @property
        def button_id(self):
            #print(f"Wrong: {self._button_id}")
            return self._button_id

        @property
        def is_pressed(self):
            #print(f"Wrong: {self._is_pressed}")
            return self._is_pressed

        @property
        def is_injected(self):
            #print(f"Wrong: {self._is_injected}")
            return self._is_injected


    @gremlin.common.SingletonDecorator
    class KeyboardHook:

        """Hooks into the event stream and grabs keyboard related events
        and passes them on to registered callback functions.
        """

        def __init__(self):
            self._running = False
            self._listen_thread = threading.Thread(target=self._listen)

        def register(self, callback):
            """Registers a new message callback.

            :param callback the new callback to register
            """
            global g_keyboard_callbacks
            g_keyboard_callbacks.append(callback)

        def start(self):
            """Starts the hook if it is not yet running."""
            if self._running:
                return
            self._running = True
            self._listen_thread.start()

        def stop(self):
            """Stops the hook from running."""
            if self._running:
                self._running = False
                user32.PostThreadMessageW(self._listen_thread.ident, WM_QUIT, 0, 0)
                self._listen_thread.join()
                # Recreate thread so we can launch it again
                self._listen_thread = threading.Thread(target=self._listen)

        def _listen(self):
            """Configures the hook and starts listening."""
            self.hook_id = user32.SetWindowsHookExW(
                WH_KEYBOARD_LL,
                process_keyboard_event,
                None,
                0
            )

            msg = wintypes.MSG()
            while True:
                result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if not result:
                    break
                if result == -1:
                    raise ctypes.WinError(get_last_error())
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))


    @gremlin.common.SingletonDecorator
    class MouseHook:

        """Hooks into the event stream and grabs mouse related events
        and passes them on to registered callback functions.
        """

        def __init__(self):
#PaxUX
            print("Win-Mousehook")
            self._running = False
            self._listen_thread = threading.Thread(target=self._listen)

        def register(self, callback):
            """Registers a new message callback.

            :param callback the new callback to register
            """
            global g_mouse_callbacks
            g_mouse_callbacks.append(callback)

        def start(self):
            """Starts the hook if it is not yet running."""
            if self._running:
                return
            self._running = True
            self._listen_thread.start()

        def stop(self):
            """Stops the hook from running."""
            if self._running:
                self._running = False
                user32.PostThreadMessageW(self._listen_thread.ident, WM_QUIT, 0, 0)
                self._listen_thread.join()
                # Recreate thread so we can launch it again
                self._listen_thread = threading.Thread(target=self._listen)

        def _listen(self):
            """Configures the hook and starts listening."""
            self.hook_id = user32.SetWindowsHookExW(
                WH_MOUSE_LL,
                process_mouse_event,
                None,
                0
            )

            msg = wintypes.MSG()
            while True:
                result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if not result:
                    break
                if result == -1:
                    raise ctypes.WinError(get_last_error())
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))


    # Re-export HOOKPROC for callers:
    HOOKPROC = HOOKPROC
    WH_KEYBOARD_LL = WH_KEYBOARD_LL
    WH_MOUSE_LL = WH_MOUSE_LL
    WM_QUIT = WM_QUIT
    KeyEvent = KeyEvent
    MouseEvent = MouseEvent

except Exception:
    # Linux: Windows DLL not available — use evdev-backed stubs
    # Import the real Linux hook so we get an actual input grabber.
    # If evdev is not installed, fall back to no-op classes so imports don't crash.
    g_keyboard_callbacks = []
    g_mouse_callbacks = []

    _linux_hook_available = False
    try:
        from . import linux_event_hook
        _linux_hook_available = True
    except (ImportError, ModuleNotFoundError):
        _linux_hook_available = False
#PaxUX
#    print(f"Win-Linux : {_linux_hook_available}") 

    class KeyEvent:
        """Stub KeyEvent for Linux (no real keyboard data)."""
        def __init__(self, scan_code=0, is_extended=False, is_pressed=False, is_injected=False):
            self._scan_code = scan_code
            self._is_extended = is_extended
            self._is_pressed = is_pressed
            self._is_injected = is_injected

        def __str__(self):
            return "KeyEvent({}, {}, {}, {})".format(hex(self._scan_code), self._is_extended, "dn" if self._is_pressed else "up", "inj" if self._is_injected else "")

        @property
        def scan_code(self):
            return self._scan_code

        @property
        def is_extended(self):
            return self._is_extended

        @property
        def is_pressed(self):
            return self._is_pressed

        @property
        def is_injected(self):
            return self._is_injected


    # Bridge MouseEvent to the real linux_event_hook implementation.
    if _linux_hook_available:
#PaxUX        
        #print("Class MouseEvent in Win-hook")
        class MouseEvent:
            """Bridged MouseEvent wrapping linux_event_hook.MouseEventArgs."""
            def __init__(self, button_id=None, is_pressed=False, is_injected=False):
                self._evt = linux_event_hook.MouseEventArgs(button_id, is_pressed, is_injected)

#PaxUX Debug
            @property
            def button_id(self):
                #print(f"{self._evt.button_id}")
                return self._evt.button_id

            @property
            def is_pressed(self):
                #print(f"{self._evt.is_pressed}")
                return self._evt.is_pressed

            @property
            def is_injected(self):
                #print(f"{self._evt.is_injected}")
                return self._evt.is_injected
    else:
        class MouseEvent:
            """Fallback MouseEvent when evdev is not installed."""
            def __init__(self, button_id=None, is_pressed=False, is_injected=False):
                self._button_id = button_id
                self._is_pressed = is_pressed
                self._is_injected = is_injected

            @property
            def button_id(self):
                return self._button_id

            @property
            def is_pressed(self):
                return self._is_pressed

            @property
            def is_injected(self):
                return self._is_injected

    # == Linux keyboard hook using evdev ==
    # Replaces the Windows WH_KEYBOARD_LL hook.
    # Cross-distro: Ubuntu, Arch, Fedora, etc.
    # Display-server agnostic: works with X11, Wayland, and TTY.
    # Uses select.select() for reliable blocking I/O (avoids CPU-spin).

    _import_select = False
    _import_threading = False
    _evdev_imported = False
    _evdev_list_devices = None
    _EvdevInputDevice = None

    # ============================================================
    # evdev → Windows Virtual Key (VK) code table
    #
    # Linux `evdev` uses a completely different numbering scheme
    # from Windows Virtual Key codes.  The Key class in macro.py
    # expects Windows VK codes.  This table translates evdev
    # scancodes so that the rest of the app sees the right
    # values on both platforms.
    #
    # Keys are stored with numeric evdev codes (int) as keys
    # for fast lookup at event time.
    # ============================================================
    _EVDEV_TO_VK: dict[int, int] = {}

    def _build_evdev_to_vk() -> dict[int, int]:
        """Build the evdev→VK mapping using evdev.ecodes.KEY_* constants."""
        mapping: dict[int, int] = {}
        try:
            from evdev import ecodes
        except ImportError:
            return mapping

        # Helper: resolve NAME → evdev integer code → store VK
        def _map(evdev_name: str, vk_code: int) -> None:
            code = getattr(ecodes, evdev_name, None)
            if code is not None:
                mapping[code] = vk_code

        # ── Function keys ─────────────────────────────────
        _map('KEY_ESC',           0x1B)
        for i, f in enumerate(range(59, 68), start=1):
            _map(f'KEY_F{i}', 0x70 + i - 1)
        for i, f in enumerate(range(68, 71), start=12):
            pass  # F13-F24 handled below
        _map('KEY_F13', 0x7C); _map('KEY_F14', 0x7D); _map('KEY_F15', 0x7E)
        _map('KEY_F16', 0x7F); _map('KEY_F17', 0x80); _map('KEY_F18', 0x81)
        _map('KEY_F19', 0x82); _map('KEY_F20', 0x83); _map('KEY_F21', 0x84)
        _map('KEY_F22', 0x85); _map('KEY_F23', 0x86); _map('KEY_F24', 0x87)

        # ── Modifier keys ────────────────────────────────
        _map('LEFTCTRL',    0xA2); _map('RIGHTCTRL',   0xA3)
        _map('LEFTSHIFT',   0xA0); _map('RIGHTSHIFT',  0xA1)
        _map('LEFTALT',     0xA4); _map('RIGHTALT',    0xA5)
        _map('LEFTMETA',    0x5B); _map('RIGHTMETA',   0x5C)
        _map('MENU',        0x5D); _map('CAPSLOCK',    0x14)
        _map('NUMLOCK',     0x90); _map('SCROLLLOCK',  0x91)

        # ── Navigation / Arrows ───────────────────────────
        _map('HOME',         0x24); _map('END',         0x23)
        _map('PAGEUP',       0x21); _map('PAGEDOWN',    0x22)
        _map('UP',           0x26); _map('LEFT',        0x25)
        _map('DOWN',         0x28); _map('RIGHT',       0x27)
        _map('INSERT',       0x2D); _map('DELETE',      0x2E)

        # ── Editing / Control ────────────────────────────
        _map('ENTER',        0x0D); _map('ESCAPE',      0x1B)
        _map('BACKSPACE',    0x08); _map('TAB',         0x09)
        _map('PAUSE',        0x13); _map('PRINT',       0x2C)
        _map('SLEEP',        0x5F)

        # ── Letter keys (A=0x41 … Z=0x5A) ──────────────
        for i, lc in enumerate('abcdefghijklmnopqrstuvwxyz'):
            _map(f'KEY_{lc.upper()}', 0x41 + i)

        # ── Top-number row (VK 0x30‑0x39) ──────────────
        _map('KEY_1', 0x31); _map('KEY_2', 0x32)
        _map('KEY_3', 0x33); _map('KEY_4', 0x34)
        _map('KEY_5', 0x35); _map('KEY_6', 0x36)
        _map('KEY_7', 0x37); _map('KEY_8', 0x38)
        _map('KEY_9', 0x39); _map('KEY_0', 0x30)

        # ── Punctuation (QWERTY) ────────────────────────
        _map('GRAVE',        0xC0)  # ` / ~  (KEY_102ND may vary)
        _map('MINUS',        0xBD)  # - / _
        _map('EQUAL',        0xBB)  # = / +
        _map('LEFTBRACE',    0xDB)  # [ / {
        _map('RIGHTBRACE',   0xDD)  # ] / }
        _map('BACKSLASH',    0xDC)  # \ |
        _map('SEMICOLON',    0xBA)  # ; :
        _map('APOSTROPHE',   0xDE)  # ' "
        _map('COMMA',        0xBC)  # , <
        _map('DOT',          0xBE)  # . >
        _map('SLASH',        0xBF)  # / ?

        # ── Numpad ──────────────────────────────────────
        _map('KP0',          0x60); _map('KP1',         0x61)
        _map('KP2',          0x62); _map('KP3',         0x63)
        _map('KP4',          0x64); _map('KP5',         0x65)
        _map('KP6',          0x66); _map('KP7',         0x67)
        _map('KP8',          0x68); _map('KP9',         0x69)
        _map('KPDOT',        0x6E); _map('KPASTERISK',  0x6A)
        _map('KPPLUS',       0x6B); _map('KPMINUS',     0x6D)
        _map('KPSLASH',      0x6F); _map('KPENTER',     0x0D)

        # ── Mouse buttons ────────────────────────────────
        _map('BTN_LEFT',     0x01); _map('BTN_RIGHT',   0x02)
        _map('BTN_MIDDLE',   0x04); _map('BTN_SIDE',    0x05)
        _map('BTN_EXTRA',    0x06)

        return mapping

    _EVDEV_TO_VK = _build_evdev_to_vk()

    try:
        import evdev
        from evdev import InputDevice as _EvdevInputDevice
        from evdev import ecodes
        _evdev_imported = True
        _evdev_list_devices = evdev.list_devices
        # Define _select and _threading for use throughout this block
        global _select
        import select as _select
        global _threading
        import threading as _threading
        _import_select = True
        _import_threading = True
    except ImportError:
        pass

    class KeyboardHook:
        """Captures keyboard events via evdev on Linux.
        
        Replacement for Windows WH_KEYBOARD_LL hook.
        Works across X11, Wayland, and TTY environments.
        Uses select.select() for reliable blocking I/O to avoid CPU-spin.
        """
        _instance = None

        def __new__(cls):
            if cls._instance is None:
                cls._instance = object.__new__(cls)
            return cls._instance

        def __init__(self):
            if not hasattr(self, '_running'):  # Only init once
                self._running = False
                self.hook_id = None
                self._listen_thread = None
                self._callbacks = []
                self._keyboard_devices = []

        def register(self, callback):
            """Registers a new callback to receive key events."""
            if callback not in self._callbacks:
                self._callbacks.append(callback)

        def start(self):
            """Starts capturing keyboard events."""
            if self._running:
                return

            if not _evdev_imported:
                return

            # Detect keyboard devices
            devices = []
            for dev_path in _evdev_list_devices():
                try:
                    dev = _EvdevInputDevice(dev_path)
                    caps = dev.capabilities()
                    key_codes = caps.get(1, [])  # EV_KEY
                    abs_codes = caps.get(3, [])  # EV_ABS

                    # Check for keyboard characteristics
                    has_keyboard = any(1 <= k <= 240 for k in key_codes)
                    has_gamepad_btns = any(304 <= k <= 369 for k in key_codes)
                    has_mouse_btns = any(k in [272, 273, 274] for k in key_codes)

                    # Exclude gamepad axes
                    gamepad_axes = {0, 1, 2, 3, 4, 16, 17}
                    has_gamepad_axes = bool(gamepad_axes & set(abs_codes))

                    if has_keyboard and not has_gamepad_btns and not has_gamepad_axes and not has_mouse_btns:
                        devices.append(dev)
                except (PermissionError, OSError):
                    continue

            if not devices:
                self._running = False
                return

            self._keyboard_devices = devices
            self._running = True
            self._listen_thread = _threading.Thread(target=self._listen, daemon=True)
            self._listen_thread.start()

        def stop(self):
            """Stops the hook."""
            self._running = False
            if self._listen_thread:
                self._listen_thread.join(timeout=3.0)
                self._listen_thread = None
            self._keyboard_devices.clear()

        def _listen(self):
            """Background thread: waits for keyboard events using select.
            
            Uses select.select() for reliable blocking I/O regardless of
            evdev version, avoiding CPU-spin when no input is present.
            """
            # Build list of device file descriptors
            fds = [dev.fd for dev in self._keyboard_devices]

            while self._running and fds:
                # Block until any device has data to read (timeout=0.5 for responsive stop)
                try:
                    ready, _, _ = _select.select(fds, [], fds, 0.5)
                except (ValueError, OSError):
                    # FD became invalid (device disconnected)
                    ready = []

                if not ready:
                    # Timeout or stale FD - refresh device list periodically
                    if not self._running:
                        break
                    # Re-check devices (may have been reconnected)
                    new_devices = []
                    for dev_path in _evdev_list_devices():
                        try:
                            dev = _EvdevInputDevice(dev_path)
                            caps = dev.capabilities()
                            key_codes = caps.get(1, [])
                            abs_codes = caps.get(3, [])
                            has_keyboard = any(1 <= k <= 240 for k in key_codes)
                            has_gamepad_btns = any(304 <= k <= 369 for k in key_codes)
                            has_gamepad_axes = any(a in [0, 1, 2, 3, 4, 16, 17] for a in abs_codes)
                            if has_keyboard and not has_gamepad_btns and not has_gamepad_axes:
                                new_devices.append(dev)
                        except (PermissionError, OSError):
                            continue
                    new_fds = [d.fd for d in new_devices]
                    if new_fds and new_fds != fds:
                        # Devices changed
                        self._keyboard_devices = new_devices
                        fds = new_fds
                    continue

                # Process ready devices
                for fd in ready:
                    try:
                        dev = next((d for d in self._keyboard_devices if d.fd == fd), None)
                        if not dev:
                            continue
                        for event in dev.read():
                            if event.type == ecodes.EV_KEY and event.value in (0, 1):
                                raw_code = event.code
                                if raw_code > 0:
                                    # Convert Linux evdev code → Windows VK code
                                    vk_code = _EVDEV_TO_VK.get(raw_code, raw_code)
                                    evt = KeyEvent(
                                        scan_code=vk_code,
                                        is_extended=vk_code > 127,
                                        is_pressed=event.value == 1,
                                    )
                                    for callback in self._callbacks:
                                        callback(evt)
                    except (OSError, IOError):
                        continue

    # Bridge MouseHook to the real linux_event_hook implementation.
    if _linux_hook_available:
        # Alias the real evdev-backed MouseHook so callers that use
        # gremlin.windows_event_hook.MouseHook get the working implementation.
#PaxUX
        #print("Windows_event_hook: Link Mouse")
        MouseHook = linux_event_hook.MouseHook
        #print("all good")
    else:
        print("Error: evdev is not installed")
        # class MouseHook:
        #     """Fallback stub MouseHook when evdev is not installed."""
        #     _instance = None

        #     def __new__(cls):
        #         if cls._instance is None:
        #             cls._instance = object.__new__(cls)
        #         return cls._instance

        #     def __init__(self):
        #         if not hasattr(self, '_running'):
        #             self._running = False
        #             self.hook_id = None
        #             self._listen_thread = None

        #     def register(self, callback):
        #         """Registers a new callback (no-op on Linux fallback)."""
        #         pass

        #     def start(self):
        #         """Starts the hook (no-op on Linux fallback)."""
        #         self._running = True

        #     def stop(self):
        #         """Stops the hook (no-op on Linux fallback)."""
        #         self._running = False
