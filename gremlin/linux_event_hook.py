# -*- coding: utf-8; -*-

"""
Linux Event Hook Implementation

Uses evdev to poll input devices for keyboard and mouse events.

[KEYBOARD CAPTURE LIFECYCLE]
1. On `start()`: iterates `/dev/input/event*` looking for keyboards
   (detected by presence of Space, Enter, and Escape keys).
2. Polls `select` to wait for any keyboard events.
3. When an EV_KEY event is captured, fires registered handlers via
   `KeyEventArgs(scan_code, is_extended, is_pressed)`.

[KEY CODE MAPPING]
evdev uses its own numbering scheme. The rest of JoystickGremlin expects
Windows Virtual Key (VK) codes. Every key event translates via _EVK
(ecodes-int → VK-int) before reaching the handler chain.
"""

import logging
import select
import sys
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import gremlin.common

from PyQt5 import QtCore

import gremlin.key_codes as key_codes

logger = logging.getLogger("system")


class KeyEventArgs:
    """Event arguments matching the Windows KeyboardHook structure."""

    def __init__(self, scan_code: int, is_extended: bool, is_pressed: bool) -> None:
        self.scan_code = scan_code
        self.is_extended = is_extended
        self.is_pressed = is_pressed


class MouseEventArgs:
    """Event arguments matching the Windows MouseHook structure."""

    def __init__(self, button_id: int, is_pressed: bool, is_injected: bool = False) -> None:
        self.button_id = button_id
        self.is_pressed = is_pressed
        self.is_injected = is_injected


class KeyboardHook(QtCore.QObject):
    """Linux implementation of a keyboard hook using evdev."""

    _instance: "KeyboardHook | None" = None

    def __new__(cls) -> "KeyboardHook":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        super().__init__()
        if getattr(self, "_init_done", False):
            return
        self._running = False
        self._poll_thread: threading.Thread | None = None
        self._handlers: list[object] = []
        self._keyboards: list[object] = []  # list of evdev.InputDevice

    def register(self, handler: object) -> None:
        """Register a handler function to receive keyboard events."""
        self._handlers.append(handler)

    def start(self) -> None:
        """Start the keyboard polling thread."""
        if not self._running:
            logger.info("[LINUX_HOOK] Starting Linux keyboard poller...")
            self._running = True
            self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
            self._poll_thread.start()

    def stop(self) -> None:
        """Stop the keyboard polling thread."""
        self._running = False
        if self._poll_thread:
            self._poll_thread.join(timeout=3.0)
            self._poll_thread = None
        self._keyboards.clear()

    def _poll_loop(self) -> None:
        """Main polling loop reading from evdev keyboard devices."""
        try:
            import evdev
            from evdev import ecodes as e
        except ImportError:
            logger.error("[LINUX_HOOK] 'evdev' is required for keyboard capture on Linux.")
            return

        keyboards: list[evdev.InputDevice] = []

        # Discover keyboards
        for dev_path in evdev.list_devices():
            dev: evdev.InputDevice | None = None
            try:
                dev = evdev.InputDevice(dev_path)
                caps = dev.capabilities()
                if not caps.get(e.EV_KEY):
                    dev.close()
                    continue
                codes = caps[e.EV_KEY]
                # Accept devices with standard keyboard keys: Space(57), Enter(28), Esc(1)
                if 57 in codes and 28 in codes and 1 in codes:
                    keyboards.append(dev)
                else:
                    dev.close()
            except (PermissionError, FileNotFoundError, OSError):
                logger.warning("[LINUX_HOOK] Device %s skipped", dev_path)
            except Exception:
                logger.debug("[LINUX_HOOK] Unexpected on %s", dev_path, exc_info=True)

        if not keyboards:
            logger.warning("[LINUX_HOOK] No standard keyboard devices found.")
            return

        logger.info("[LINUX_HOOK] Polling %d keyboard device(s): %s",
                      len(keyboards), ", ".join(d.path for d in keyboards))

        while self._running:
            # Collect file descriptors for select
            fds = [d.fd for d in keyboards]
            if not fds:
                break

            try:
                # Block until any device has data
                readable, _, errord = select.select(fds, [], fds, 1.0)
            except (ValueError, OSError):
                break

            if errord:
                # Handle devices that went away
                for fd in errord:
                    keyboards = [d for d in keyboards if d.fd != fd]
                    logger.warning("[LINUX_HOOK] Keyboard device removed.")
                if not keyboards:
                    break
                continue

            for fd in readable:
                # Find the device object for this file descriptor
                dev = next((d for d in keyboards if d.fd == fd), None)
                if not dev:
                    continue

                try:
                    events = dev.read()
                    if not events:
                        continue

                    for event in events:
                        if event.type == e.EV_KEY:
                            # Only process key press/release
                            if event.value in (0, 1, 2):
                                is_down = event.value in (1, 2)
                                extended = event.code >= 224
                                vk_code = key_codes.evdev_to_vk(event.code)
                                args = KeyEventArgs(vk_code, extended, is_down)
                                for handler in self._handlers:
                                    handler(args)
                except OSError:
                    keyboards = [d for d in keyboards if d.fd != fd]
                except Exception as exc:
                    logger.warning("[LINUX_HOOK] Error reading keyboard: %s", exc)


class MouseHook(QtCore.QObject):
    """Linux implementation of a mouse hook using evdev."""

    _instance: "MouseHook | None" = None

    def __new__(cls) -> "MouseHook":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        super().__init__()
        if getattr(self, "_init_done", False):
            return
        self._running = False
        self._poll_thread: threading.Thread | None = None
        self._handlers: list[object] = []
        self._mice: list[evdev.InputDevice] = []
        self._mice_paths: set[str] = set()
        self._btn_map: dict[int, "common.MouseButton"] = {}
        self._btn_codes: set[int] = set()
        self._init_done = True

    def _build_btn_map(self) -> None:
        """Map evdev BTN_* codes to MouseButton enum members."""
        try:
            import evdev
            from evdev import ecodes as e
            import gremlin.common as common
        except ImportError:
            logger.error("[LINUX_HOOK] 'evdev' required to resolve button codes.")
            return

        # Standard button mappings
        _STD_BTNS = {
            "BTN_LEFT": common.MouseButton.Left,
            "BTN_RIGHT": common.MouseButton.Right,
            "BTN_MIDDLE": common.MouseButton.Middle,
            "BTN_SIDE": common.MouseButton.Back,
            "BTN_EXTRA": common.MouseButton.Forward,
            "BTN_FORWARD": common.MouseButton.Back,
            "BTN_BACK": common.MouseButton.Forward,
        }

        self._btn_map.clear()
        self._btn_codes.clear()
        for name, btn_class in _STD_BTNS.items():
            ec = getattr(e, name, None)
            if ec is not None:
                self._btn_map[ec] = btn_class
                self._btn_codes.add(ec)
                logger.info("[LINUX_HOOK] BtnMap[%d] -> %s", ec, btn_class)
        if self._btn_map:
            logger.info("[LINUX_HOOK] Button codes resolved: %s", self._btn_map)

    def register(self, handler: object) -> None:
        """Register a handler to receive mouse events."""
        if handler not in self._handlers:
            self._handlers.append(handler)

    def start(self) -> None:
        """Start the mouse polling thread."""
        if not self._running:
            logger.info("[LINUX_HOOK] Starting Linux mouse poller...")
            self._build_btn_map()
            if not self._btn_codes:
                logger.warning("[LINUX_HOOK] No button codes — mouse poller aborted.")
                return
            self._running = True
            self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
            self._poll_thread.start()

    def stop(self) -> None:
        """Stop the mouse polling thread."""
        self._running = False
        if self._poll_thread:
            self._poll_thread.join(timeout=3.0)
            self._poll_thread = None
        for dev in self._mice:
            try:
                dev.close()
            except OSError:
                pass
        self._mice.clear()
        self._mice_paths.clear()

    def _poll_loop(self) -> None:
        """Main polling loop reading mouse button events from evdev."""
        try:
            import evdev
            from evdev import ecodes as e
        except ImportError:
            logger.error("[LINUX_HOOK] 'evdev' is required for mouse capture.")
            return
        
        self._build_btn_map()
        if not self._btn_codes:
            return

        while self._running:
            # 1. Discover mouse devices (keep them open for polling)
            devs: list[evdev.InputDevice] = []
            dev_paths: set[str] = set()

            for dev_path in evdev.list_devices():
                dev: evdev.InputDevice | None = None
                try:
                    dev = evdev.InputDevice(dev_path)
                    caps = dev.capabilities()
                    # Must have relative motion AND button keys
                    if caps.get(e.EV_REL) and caps.get(e.EV_KEY):
                        has_btn = any(code in self._btn_codes for code in caps[e.EV_KEY])
                        if has_btn:
                            devs.append(dev)
                            dev_paths.add(dev_path)
                            #logger.info("[LINUX_HOOK] Found mouse: %s", dev_path)
                        else:
                            dev.close()
                    else:
                        dev.close()
                except (PermissionError, FileNotFoundError, OSError):
                    if dev:
                        dev.close()

            # Update tracked devices
            removed = self._mice_paths - dev_paths
            for path in removed:
                logger.info("[LINUX_HOOK] Mouse device removed: %s", path)
            self._mice_paths = dev_paths
            self._mice = devs

            if not devs:
                time.sleep(1.0)
                continue

            #logger.info("[LINUX_HOOK] Polling %d mouse device(s)", len(devs))

            # 2. Poll using 'select' to avoid blocking on a device that is idle
            fds = [d.fd for d in devs]
            try:
                readable, _, errord = select.select(fds, [], fds, 1.0)
            except (ValueError, OSError):
                break

            if errord:
                for fd in errord:
                    devs = [d for d in devs if d.fd != fd]
                    self._mice_paths.discard(d.path) # Should match by fd, logic simplified
                if not devs:
                    break
                continue

            for fd in readable:
                dev = next((d for d in devs if d.fd == fd), None)
                if not dev:
                    continue

                try:
                    events = dev.read()
                    if not events:
                        continue

                    for event in events:
                        if event.type == e.EV_SYN:
                            continue
                        
                        # Capture EV_KEY events
                        if event.type == e.EV_KEY and event.code in self._btn_codes:
                            is_pressed = event.value in (1, 2) # 1=press, 2=repeat
                            button_id = self._btn_map.get(event.code)
                            
                            if button_id is not None:
                                #print(f"\033[35m[HOOK] EV_KEY: {button_id} pressed={is_pressed}\033[0m", file=sys.stderr, flush=True)
                                args = MouseEventArgs(button_id, is_pressed, is_injected=False)
                                for handler in self._handlers:
                                    handler(args)
                except OSError:
                    devs = [d for d in devs if d.fd != fd]
                except Exception as exc:
                    logger.warning("[LINUX_HOOK] Error reading mouse: %s", exc)


def init():
    """Initialize Linux hooks."""
    _kb = KeyboardHook()
    _mouse = MouseHook()
    return _kb, _mouse
