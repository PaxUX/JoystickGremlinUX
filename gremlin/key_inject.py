# -*- coding: utf-8; -*-

# vkey_keyboard: inject keys via /dev/uinput on Linux.
# Works on X11, Wayland, and TTY environments.
# Maps Windows Virtual Key codes → evdev KEY_* codes → uinput EV_KEY events.
# Uses dual backend support: evdev.UInput (preferred) with raw ioctl fallback.

import contextlib
import fcntl
import logging
import os
import struct
import threading
import time

import gremlin.key_codes as key_codes

logger = logging.getLogger("system")


# Thread lock for singleton keyboard
_keyboard_lock = threading.Lock()


# ecodes reverse-lookup: ecodes code → human-readable name (for evdev_name())
# Built once at load time; if ecodes isn't available it falls back gracefully.
_evdev_name: dict[int, str] = {}
_evdev_available = False
try:
    from evdev import ecodes as _ecodes
    _evdev_available = True
    _evdev_name = {
        code: name for name, code in vars(_ecodes).items()
        if isinstance(code, int) and name.isupper()
    }
except ImportError:
    pass


# ========== VK → evdev KEY mapping (DEPRECATED — now uses key_codes) ===
# Original _AT_KEY_NAMES table preserved for reference only.
# All VK→evdev lookups now delegate to gremlin.key_codes module.
# _AT_KEY_NAMES: dict[int, str] = { ... }  # DEPRECATED

# ========== Logging / name helpers ===


def _vk_to_name(vk: int) -> str:
    """Get human-readable name for a VK code for logging."""
    # Use our local ecodes lookup for the evdev name, then display name from key_codes
    ev_code = key_codes.vk_to_evdev(vk)
    if ev_code is not None and _evdev_available and ev_code in _evdev_name:
        return _evdev_name[ev_code]
    return key_codes.vk_display_name(vk)


def evdev_name(ev_code: int) -> str:
    """Lookup evdev code → human-readable name for logging."""
    if _evdev_available and ev_code in _evdev_name:
        return _evdev_name[ev_code]
    return f"KEY_0x{ev_code:04X}"


def vk_to_evdev(vk: int) -> int | None:
    """Convert Windows VK code to evdev KEY_* raw code.

    Delegates to gremlin.key_codes.vk_to_evdev().

    Returns the evdev code if the VK is mapped, or **None** if unmapped.
    Unmapped VK codes **never** silently pass through — that would produce
    a wrong key on screen.
    """
    return key_codes.vk_to_evdev(vk)


def vk_exists(vk: int) -> bool:
    """Check whether a VK code has a valid evdev mapping."""
    return key_codes.vk_to_evdev(vk) is not None


# ========== Dual-Backend uinput Injection ==========

# ioctl helpers for raw backend
def _ioctl_nr(base: int, nr: int, size: int) -> int:
    """Pack _IOW ioctl number."""
    return ((size & 0x1FFF) << 16) | (base << 8) | nr

UI_IOC_BASE = ord('U')
UI_DEV_SETUP = _ioctl_nr(UI_IOC_BASE, 3, 92)
UI_DEV_CREATE = _ioctl_nr(UI_IOC_BASE, 1, 0)
UI_SET_KEYBIT = _ioctl_nr(UI_IOC_BASE, 10, 4)

# -- DEPRECATED — DO NOT USE --
# struct_input_event = struct.Struct('<LLHHi')  # timeval(tv_sec,tv_usec) + type + code + value
#
# NOTE FOR FUTURE REVIEW:
# The above line has a critical ABI bug. On Linux, C's struct timeval has
# two 'long' fields (tv_sec, tv_usec), each 8 bytes on 64-bit systems.
# Python's struct 'L' is ONLY 4 bytes. So '<LLHHi' packs to 16 bytes,
# but the kernel's struct input_event expects 24 bytes (16 for time +
# 2 for type + 2 for code + 4 for value). When we write 16 bytes to
# /dev/uinput, the kernel reads the remaining 8 bytes as garbage,
# corrupting the type/code/value fields and causing wrong keys to type.
# The fix is to use 'QQ' (8 bytes each) instead of 'LL' (4 bytes each).
# See: https://docs.kernel.org/userspace-api/input/uinput.html
#
# FIXED version deployed below:

# See: https://docs.kernel.org/userspace-api/input/uinput.html
struct_input_event = struct.Struct('<QQHHi')  # tv_sec(8B)+tv_usec(8B)+type(2B)+code(2B)+value(4B)


class _RawUinputBackend:
    """Raw uinput backend using ctypes/ioctl.

    Works on all Linux distros without requiring the 'evdev' Python package.
    Only handles EV_KEY events for keyboard injection.
    """

    UINPUT_PATH = "/dev/uinput"
    EV_KEY = 1
    EV_SYN = 0
    SYN_REPORT = 0
    MAX_KEYS = 1024  # Max number of key bits to advertise

    def __init__(self, ev_codes: set[int], name: str = 'JoystickGremlin Virtual Keyboard') -> None:
        self.fd: int | None = None
        self._ev_codes: set[int] = ev_codes
        self._name = name

    def _try_open(self) -> bool:
        """Open /dev/uinput and create a virtual keyboard device."""
        if self.fd is not None:
            return True
        if not os.path.exists(self.UINPUT_PATH):
            logger.error("[RAW_UINPUT] %s not found", self.UINPUT_PATH)
            return False
        if not os.access(self.UINPUT_PATH, os.W_OK):
            logger.error("[RAW_UINPUT] No write permission to %s", self.UINPUT_PATH)
            return False
        try:
            self.fd = os.open(self.UINPUT_PATH, os.O_WRONLY | os.O_NONBLOCK)
        except PermissionError:
            logger.error("[RAW_UINPUT] Permission denied opening %s", self.UINPUT_PATH)
            return False
        except OSError as e:
            logger.error("[RAW_UINPUT] Cannot open %s: %s", self.UINPUT_PATH, e)
            return False
        try:
            self._setup_device()
            self._create_device()
            return True
        except Exception as e:
            logger.error("[RAW_UINPUT] Device setup failed: %s: %s", type(e).__name__, e)
            self._close()
            return False

    def _close(self) -> None:
        """Close the uinput file descriptor."""
        if self.fd is not None:
            with contextlib.suppress(OSError):
                os.close(self.fd)
            self.fd = None

    def _setup_device(self) -> None:
        """Configure the uinput device with our key codes."""
        # Set key bits for each key code
        ev_codes = sorted(e for e in self._ev_codes if 0 <= e <= 255)  # Only KEY_0..KEY_255
        for ev_code in ev_codes:
            key_bit = struct.pack('I', ev_code)
            fcntl.ioctl(self.fd, UI_SET_KEYBIT, key_bit)

        # Build uinput_setup (92 bytes: id(8) + name(80) + ff_effects_max(4))
        name_raw = self._name.encode('utf-8')[:80].ljust(80, b'\x00')
        setup = struct.pack('HHI4s',  # id.vendor(2), id.product(2), id.version(2), id.bustype aligned + padding
                           0,       # bus (0 = unused in this position)
                           0,       # vendor
                           0,       # product
                           name_raw,
        )
        # Correct format: struct uinput_setup is [10 bytes reserved] + name[80] + ff_effects_max[4]
        setup = b'\x00' * 8 + name_raw + struct.pack('I', 0)
        fcntl.ioctl(self.fd, UI_DEV_SETUP, setup)

    def _create_device(self) -> None:
        """Activate the uinput device."""
        fcntl.ioctl(self.fd, UI_DEV_CREATE)

    def write_key(self, ev_code: int, pressed: bool) -> bool:
        """Write a single EV_KEY event via raw ioctl."""
        if self.fd is None:
            return False
        event = struct_input_event.pack(0, 0, self.EV_KEY, ev_code, 1 if pressed else 0)
        syn = struct_input_event.pack(0, 0, self.EV_SYN, self.SYN_REPORT, 0)
        try:
            os.write(self.fd, event)
            os.write(self.fd, syn)
            return True
        except OSError as e:
            logger.error("[RAW_UINPUT] Write failed: %s", e)
            return False

    def __del__(self) -> None:
        self._close()


def _collect_ev_codes() -> set[int]:
    """Collect all unique evdev KEY codes from the canonical key_codes module."""
    # Delegates to key_codes.validate() which checks both directions.
    # The actual set of injectable codes is the domain of key_codes.vk_to_evdev().
    if key_codes._vk_to_evdev is None:
        return set()
    return set(key_codes._vk_to_evdev.values())


class VKeyKeyboard:
    """Inject keyboard keypresses via /dev/uinput on Linux.

    Uses dual backend support:
    1. evdev.UInput (preferred, cleaner API) — falls through if missing
    2. Raw ioctl syscalls via fcntl — works without evdev package
    Tries the primary backend first and falls back gracefully.
    """

    DEFAULT_NAME = 'JoystickGremlin Virtual Keyboard'

    def __init__(self, name: str = DEFAULT_NAME) -> None:
        self._ui: object | None = None  # evdev.UInput or None
        self._fd: int | None = None     # raw uinput fd
        self._initialized = False
        self._error_count = 0
        self._backend = 'none'
        self._backend_name = name
        self._ev_codes: set[int] = _collect_ev_codes()

    def _try_init_evdev(self) -> bool:
        """Try to init with evdev.UInput (preferred)."""
        try:
            from evdev import UInput as EvdevUInput
            from evdev import ecodes

            key_codes = sorted(self._ev_codes)
            self._ui = EvdevUInput(
                {ecodes.EV_KEY: key_codes},
                name=self._backend_name,
                vendor=0x1234, product=0x5678, version=1, bustype=3,  # BUS_USB
            )
            self._backend = 'evdev'
            self._initialized = True
            self._error_count = 0
            logger.debug('[KEY_INJECT] Virtual keyboard created via evdev.UInput')
            return True
        except ImportError:
            logger.debug('[KEY_INJECT] evdev not available — falling back to raw uinput')
            return False
        except Exception as e:
            logger.error('[KEY_INJECT] evdev init failed: %s: %s', type(e).__name__, e)
            return False

    def _try_init_raw(self) -> bool:
        """Try to init with raw ioctl backend (fallback)."""
        try:
            backend = _RawUinputBackend(self._ev_codes, self._backend_name)
            if not backend._try_open():
                return False
            self._fd = backend.fd
            self._backend = 'raw'
            self._initialized = True
            self._error_count = 0
            logger.debug('[KEY_INJECT] Virtual keyboard created via raw ioctl')
            return True
        except Exception as e:
            logger.error('[KEY_INJECT] raw uinit failed: %s: %s', type(e).__name__, e)
            return False

    def _try_init(self) -> bool:
        """Try to initialize the virtual keyboard, trying evdev then raw fallback."""
        if self._initialized:
            return True
        if self._try_init_evdev():
            return True
        if self._try_init_raw():
            return True
        logger.error('[KEY_INJECT] All uinput backends failed')
        return False

    def _ensure_init(self) -> bool:
        """Ensure the keyboard is initialized, returning False on failure."""
        if not self._initialized:
            return self._try_init()
        return True

    @staticmethod
    def _close(fd_obj: object) -> None:
        """Close a file descriptor-like object."""
        if fd_obj is not None:
            with contextlib.suppress(Exception):
                fd_obj.close()

    def inject_key_down(self, vk_code: int) -> bool:
        """Press a key by Windows VK code."""
        if not self._ensure_init():
            return False

        ev_code = vk_to_evdev(vk_code)
        if ev_code is None:
            if __debug__:
                logger.debug(
                    "[KEY_INJECT] CANCELLED DOWN: VK 0x%02X (%s) — unmapped",
                    vk_code, _vk_to_name(vk_code),
                )
            return False

        if __debug__:
            key_name = _vk_to_name(vk_code)
            logger.debug(
                "[KEY_INJECT] KEY DOWN: VK 0x%02X → evdev %d (%s)",
                vk_code, ev_code, key_name,
            )
        else:
            key_name = key_codes.vk_display_name(vk_code)

        if self._backend == 'evdev':
            from evdev import ecodes
            try:
                self._ui.write(ecodes.EV_KEY, ev_code, 1)  # type: ignore
                self._ui.syn()  # type: ignore
                self._error_count = 0
                logger.debug("[KEY_INJECT] DOWN OK: VK 0x%02X (%s) via evdev", vk_code, key_name)
                return True
            except Exception as e:
                # Fall through to raw
                if __debug__:
                    logger.debug("[KEY_INJECT] evdev write failed, switching to raw: %s", e)
                self._backend = 'none'
                self._ui = None
                self._initialized = False
                if not self._try_init_raw():
                    return False

        if self._backend == 'raw':
            import ctypes
            backend_fd = self._fd
            if backend_fd is None:
                logger.error("[KEY_INJECT] raw backend fd is None")
                return False
            event = struct_input_event.pack(0, 0, 1, ev_code, 1)
            syn = struct_input_event.pack(0, 0, 0, 0, 0)
            try:
                os.write(backend_fd, event)
                os.write(backend_fd, syn)
                self._error_count = 0
                logger.debug("[KEY_INJECT] DOWN OK: VK 0x%02X (%s) via raw", vk_code, key_name)
                return True
            except OSError as e:
                logger.error("[KEY_INJECT] DOWN FAIL: %s: %s", type(e).__name__, e)
                return False

        logger.error("[KEY_INJECT] No usable backend active")
        return False

    def inject_key_up(self, vk_code: int) -> bool:
        """Release a key by Windows VK code."""
        if not self._ensure_init():
            return False

        ev_code = vk_to_evdev(vk_code)
        if ev_code is None:
            if __debug__:
                logger.debug(
                    "[KEY_INJECT] CANCELLED UP: VK 0x%02X (%s) — unmapped",
                    vk_code, _vk_to_name(vk_code),
                )
            return False

        if __debug__:
            key_name = _vk_to_name(vk_code)
            logger.debug(
                "[KEY_INJECT] KEY UP: VK 0x%02X → evdev %d (%s)",
                vk_code, ev_code, key_name,
            )
        else:
            key_name = key_codes.vk_display_name(vk_code)

        if self._backend == 'evdev':
            from evdev import ecodes
            try:
                self._ui.write(ecodes.EV_KEY, ev_code, 0)  # type: ignore
                self._ui.syn()  # type: ignore
                self._error_count = 0
                logger.debug("[KEY_INJECT] UP OK: VK 0x%02X (%s) via evdev", vk_code, key_name)
                return True
            except Exception as e:
                if __debug__:
                    logger.debug("[KEY_INJECT] evdev write failed, switching to raw: %s", e)
                self._backend = 'none'
                self._ui = None
                self._initialized = False
                if not self._try_init_raw():
                    return False

        if self._backend == 'raw':
            backend_fd = self._fd
            if backend_fd is None:
                logger.error("[KEY_INJECT] raw backend fd is None")
                return False
            event = struct_input_event.pack(0, 0, 1, ev_code, 0)
            syn = struct_input_event.pack(0, 0, 0, 0, 0)
            try:
                os.write(backend_fd, event)
                os.write(backend_fd, syn)
                logger.debug("[KEY_INJECT] UP OK: VK 0x%02X (%s) via raw", vk_code, key_name)
                return True
            except OSError as e:
                logger.error("[KEY_INJECT] UP FAIL: %s: %s", type(e).__name__, e)
                return False

        logger.error("[KEY_INJECT] No usable backend active")
        return False

    def inject_key_press(self, vk_code: int) -> None:
        """Press and release a key."""
        if __debug__:
            key_name = _vk_to_name(vk_code)
            logger.debug("[KEY_INJECT] KEY PRESS: VK 0x%02X (%s)", vk_code, key_name)
        else:
            key_name = key_codes.vk_display_name(vk_code)
        if self.inject_key_down(vk_code):
            # [LATE-3] Removed time.sleep(0.01) to reduce macro/keypress latency.
            # Original (unoptimized): time.sleep(0.01)
            self.inject_key_up(vk_code)
        else:
            if __debug__:
                logger.debug(
                    "[KEY_INJECT] KEY PRESS FAILED: VK 0x%02X (%s) — key-down failed",
                    vk_code, key_name,
                )

    def inject_keys(self, vk_codes: list[int], pressed: bool) -> bool:
        """Press or release multiple keys simultaneously."""
        if not self._ensure_init():
            return False

        # Filter out unmapped VK codes
        mapped: list[tuple[int, int]] = []  # (vk, ev_code)
        for vk in vk_codes:
            ev = vk_to_evdev(vk)
            if ev is not None:
                mapped.append((vk, ev))

        if not mapped:
            if __debug__:
                logger.debug(
                    "[KEY_INJECT] No VK codes in %s have valid evdev mappings",
                    [f"0x{vk:02X}" for vk in vk_codes]
                )
            return False

        if __debug__:
            names = ', '.join(_vk_to_name(vk) for vk, _ in mapped)
            logger.debug("[KEY_INJECT] KEY %s: [%s]", 'PRESS' if pressed else 'RELEASE', names)
        else:
            names = ""

        state = 1 if pressed else 0

        if self._backend == 'evdev':
            from evdev import ecodes
            try:
                for _, ev_code in mapped:
                    self._ui.write(ecodes.EV_KEY, ev_code, state)  # type: ignore
                self._ui.syn()  # type: ignore
                if __debug__:
                    logger.debug("[KEY_INJECT] %s OK: [%s] via evdev", 'PRESS' if pressed else 'RELEASE', names)
                return True
            except Exception:
                self._backend = 'none'
                self._ui = None
                self._initialized = False
                if not self._try_init_raw():
                    return False

        if self._backend == 'raw':
            backend_fd = self._fd
            if backend_fd is None:
                logger.error("[KEY_INJECT] raw backend fd is None")
                return False
            for vk, ev_code in mapped:
                event = struct_input_event.pack(0, 0, 1, ev_code, state)
                os.write(backend_fd, event)
            syn = struct_input_event.pack(0, 0, 0, 0, 0)
            os.write(backend_fd, syn)
            if __debug__:
                logger.debug("[KEY_INJECT] %s OK: [%s] via raw", 'PRESS' if pressed else 'RELEASE', names)
            return True

        logger.error("[KEY_INJECT] No usable backend active")
        return False

    def close(self) -> None:
        """Close the virtual keyboard device."""
        if self._backend == 'evdev':
            self._close(self._ui)
            self._ui = None
        elif self._backend == 'raw':
            self._close(self._fd)
            self._fd = None
        self._initialized = False
        self._backend = 'none'

    def __del__(self) -> None:
        self.close()


# ========== Global keyboard injector ==========

_keyboard_injector: VKeyKeyboard | None = None
_lock = threading.Lock()


def is_uinput_available() -> bool:
    """Check if /dev/uinput is available and can be opened for writing."""
    return (
        os.path.exists('/dev/uinput')
        and os.access('/dev/uinput', os.W_OK)
    )


def inject_key_down(vk_code: int) -> bool:
    """Global function to inject a key press."""
    global _keyboard_injector

    # Early check: if VK code is unmapped, abort before attempting init
    if vk_to_evdev(vk_code) is None:
        return False

    if __debug__:
        key_name = _vk_to_name(vk_code)
        logger.debug("[KEY_INJECT] → inject_key_down: VK 0x%02X (%s)", vk_code, key_name)
    else:
        key_name = key_codes.vk_display_name(vk_code)

    with _lock:
        if _keyboard_injector is None:
            _keyboard_injector = VKeyKeyboard()
        if not _keyboard_injector._initialized:
            if not _keyboard_injector._try_init():
                if __debug__:
                    logger.debug(
                        "[KEY_INJECT] → inject_key_down: uinput not available — skipping",
                    )
                return False
        result = _keyboard_injector.inject_key_down(vk_code)
        if result and __debug__:
            logger.debug(
                "[KEY_INJECT] ← inject_key_down: VK 0x%02X (%s) SUCCESS", vk_code, key_name
            )
        elif not result and __debug__:
            logger.debug(
                "[KEY_INJECT] ← inject_key_down: VK 0x%02X (%s) FAILED", vk_code, key_name
            )
        return result


def inject_key_up(vk_code: int) -> bool:
    """Global function to inject a key release."""
    global _keyboard_injector

    # Early check: if VK code is unmapped, abort before attempting init
    if vk_to_evdev(vk_code) is None:
        return False

    if __debug__:
        key_name = _vk_to_name(vk_code)
        logger.debug("[KEY_INJECT] → inject_key_up: VK 0x%02X (%s)", vk_code, key_name)
    else:
        key_name = key_codes.vk_display_name(vk_code)

    with _lock:
        if _keyboard_injector is None:
            _keyboard_injector = VKeyKeyboard()
        if not _keyboard_injector._initialized:
            if not _keyboard_injector._try_init():
                if __debug__:
                    logger.debug(
                        "[KEY_INJECT] → inject_key_up: uinput not available — skipping",
                    )
                return False
        result = _keyboard_injector.inject_key_up(vk_code)
        if result and __debug__:
            logger.debug(
                "[KEY_INJECT] ← inject_key_up: VK 0x%02X (%s) SUCCESS", vk_code, key_name
            )
        elif not result and __debug__:
            logger.debug(
                "[KEY_INJECT] ← inject_key_up: VK 0x%02X (%s) FAILED", vk_code, key_name
            )
        return result


def inject_key(vk_code: int) -> None:
    """Press and release a single key."""
    if __debug__:
        key_name = _vk_to_name(vk_code)
        logger.debug("[KEY_INJECT] → inject_key (press+release): VK 0x%02X (%s)", vk_code, key_name)
    else:
        key_name = key_codes.vk_display_name(vk_code)
    if inject_key_down(vk_code):
        # [LATE-3] Removed time.sleep(0.01) to reduce macro/keypress latency.
        # Original (unoptimized): time.sleep(0.01)
        inject_key_up(vk_code)
    else:
        logger.error(
            "[KEY_INJECT] ← inject_key: VK 0x%02X (%s) — key-down failed, aborting",
            vk_code, key_name,
        )


# ========== Key name lookup utilities ==========

def key_from_name(name: str) -> int | None:
    """Look up VK code by key name (case insensitive).

    Delegates to key_codes.vk_display_name() and key_codes._evdev_name()
    for the reverse-lookup.
    """
    name = name.lower().strip()

    # First: try key_codes._VK_NAMES (VK code → display name)
    for vk, display_name in key_codes._VK_NAMES.items():
        if display_name.lower() == name:
            return vk

    # Second: try ecodes evdev names (e.g. KEY_PRINT → PRINTSCREEN)
    if _evdev_available:
        for evdev_name_str, code in _evdev_name.items():
            if evdev_name_str.lower() == name:
                # Find the VK that maps to this evdev code
                for vk, ev_code in key_codes._vk_to_evdev.items():
                    if ev_code == code:
                        return vk

    return None
