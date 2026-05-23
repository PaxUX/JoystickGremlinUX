# -*- coding: utf-8; -*-

# Copyright (C) 2015 - 2019 Lionel Ott
# Copyright (C) 2026 JoystickGremlin Linux Migration
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY OR FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""Linux virtual joystick device manager using /dev/uinput.

Masquerades virtual devices as Xbox 360 controllers for Steam/Wine/Proton
compatibility.

CRITICAL: ioctl numbers MUST use capital 'U' (85) as UINPUT_IOCTL_BASE.
Lowercase 'u' (117) is WRONG — this was the original cause of all [Errno 22]
Invalid argument errors throughout this file.

Modern kernel (≥6.x) requires UI_DEV_SETUP + UI_ABS_SETUP ioctls instead of
the legacy write() method which was removed.

Verified kernel values (compiled with gcc from <linux/uinput.h>):
  UI_SET_EVBIT  = 0x40045564
  UI_SET_KEYBIT = 0x40045565
  UI_SET_ABSBIT = 0x40045567
  UI_DEV_SETUP   (struct uinput_setup, 92 bytes)
  UI_ABS_SETUP   (struct uinput_abs_setup, 28 bytes)
  UI_DEV_CREATE = 0x00005501
"""

import ctypes
import fcntl
import logging
import os
import struct
import threading
import time
from types import TracebackType

# Define VJoyError locally to avoid circular import through gremlin.error
class VJoyError(Exception):
    """Exception raised when an error occurs within the vJoy module."""
    def __init__(self, value):
        self.value = value


# === libc helper (needed for large-struct ioctls) ----------

libc = ctypes.CDLL(None, use_errno=True)
libc.ioctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_void_p]
libc.ioctl.restype = ctypes.c_int

# === ioctl numbers (from <linux/uinput.h>, UINPUT_IOCTL_BASE = 'U' = 85) --

_UI = ord('U')  # 85, NOT ord('u') = 117


def _IOW_UI(nr: int, sz: int) -> int:
    return ((1 << 30) | (_UI << 8) | (nr << 0) | (sz << 16))


def _IO_UI(nr: int) -> int:
    return (_UI << 8) | nr


UI_SET_EVBIT     = _IOW_UI(100, 4)   # 0x40045564
UI_SET_KEYBIT    = _IOW_UI(101, 4)   # 0x40045565
UI_SET_ABSBIT    = _IOW_UI(103, 4)   # 0x40045567  (103 NOT 102!)
UI_DEV_SETUP     = _IOW_UI(3, 92)    # struct uinput_setup (92 bytes: id(8) + name(80) + ff_effects_max(4))
UI_ABS_SETUP     = _IOW_UI(4, 28)    # struct uinput_abs_setup (28 bytes)
UI_DEV_CREATE                    = _IO_UI(1)                    # 0x00005501
UI_DEV_DESTROY                   = _IO_UI(2)                   # 0x00005502
UI_INPUT_PROP_PERSISTENT         = (1 << 2)                    # bit 2


# === Event types ------

EV_SYN = 0
EV_KEY = 1
EV_ABS = 3

# === UInput event struct format (24 bytes on 64-bit Linux) ------
# struct input_event:
#   struct timeval  time;   // tv_sec(8) + tv_usec(8) = 16 bytes
#   __u16           type;   // 2 bytes
#   __u16           code;   // 2 bytes
#   __s32           value;  // 4 bytes
#   // TOTAL = 24 bytes
_INPUT_EVENT_FMT = "<qQHHi"
_INPUT_EVENT_SIZE = 24  # struct.calcsize(_INPUT_EVENT_FMT)


# === Device identity ---
# vJoy devices present as a generic uinput virtual controller.
# No masquerade — games and SDL2 map via axis/button capabilities, not VID/PID.

BUS_USB      = 3
_DEVICE_NAME = "vJoy Linux"

# === vJoy identity — used for device recognition in DILL ---
# DILL identifies vJoy devices strictly by our native VID/PID.
VENDOR_VJOY  = 0x1234
PRODUCT_VJOY = 0xBEAD

# === Recognized vJoy device signatures (Linux + Windows) ===
# DILL's DeviceSummary.is_virtual() checks against this set.
_VJOY_IDS = {(VENDOR_VJOY, PRODUCT_VJOY)}


# === Button codes -------

BTN_SOUTH  = 304
BTN_EAST   = 305
BTN_NORTH  = 307
BTN_WEST   = 308
BTN_TL     = 310
BTN_TR     = 311
BTN_THUMBL = 316
BTN_THUMBR = 317
BTN_SELECT = 314
BTN_START  = 315
BTN_TL2    = 318
BTN_TR2    = 319


# === Axis mapping -------

_AXIS_MAP: dict[int, int] = {
    0x30: 0,   # X  → ABS_X
    0x31: 1,   # Y  → ABS_Y
    0x32: 2,   # Z  → ABS_Z
    0x33: 3,   # RX → ABS_RX
    0x34: 5,   # RY → ABS_RY
    0x35: 4,   # RZ → ABS_RZ
    0x36: 30,  # SL0
    0x37: 31,  # SL1
}


# === Axis range -------

_ABS_MIN   = -32768
_ABS_MAX   = 32767
_ABS_FUZZ  = 16
_ABS_FLAT  = 8


# === Kernel structs (ctypes definitions for ioctl calls) =======---

class _input_id(ctypes.Structure):
    _fields_ = [
        ('bustype',  ctypes.c_uint16),
        ('vendor',   ctypes.c_uint16),
        ('product',  ctypes.c_uint16),
        ('version',  ctypes.c_uint16),
    ]
    _pack_ = 1


class _uinput_setup(ctypes.Structure):
    """struct uinput_setup from <linux/uinput.h> (92 bytes).
    
    struct input_id          (8 bytes)
    char name[UINPUT_MAX_NAME_SIZE]  (80 bytes, UINPUT_MAX_NAME_SIZE=80)
    __u32 ff_effects_max           (4 bytes)
    Total: 92 bytes
    """
    UINPUT_MAX_NAME_SIZE = 80
    
    _fields_ = [
        ('id',             _input_id),
        ('name',           ctypes.c_char * UINPUT_MAX_NAME_SIZE),  # 80 bytes
        ('ff_effects_max', ctypes.c_uint32),
    ]
    _pack_ = 1


class _uinput_abs_setup(ctypes.Structure):
    """struct uinput_abs_setup from <linux/uinput.h> (28 bytes).
    
    kernel source (include/uapi/linux/uinput.h):
        struct uinput_abs_setup {
            __u16 code;          // absolute code (ABS_X, ABS_Y, etc.)
            __u16 padding;       // MUST be 0!
            struct input_absinfo absinfo;
        };
    """
    _fields_ = [
        ('code',     ctypes.c_uint16),  # ABS_X(0x30), ABS_Y(0x31), etc.
        ('padding',  ctypes.c_uint16),  # MUST be 0 (not 'filler'!)
        ('absinfo',  ctypes.c_int * 6),  # value, min, max, fuzz, flat, resolution
    ]
    _pack_ = 1
    # _fields_ = [
    #     ('code',     ctypes.c_uint16),
    #     ('filler',   ctypes.c_uint16),
    #     ('absinfo',  ctypes.c_int * 6),  # value, min, max, fuzz, flat, resolution
    # ]
    # _pack_ = 1


# === Input-event formatting --------

def _fmt_ev(etype: int, code: int, value: int) -> bytes:
    r"""Pack an `input_event` struct (24 bytes: tv_sec, tv_usec, type, code, value).

    On 64-bit Linux, `struct input_event` from <linux/input.h> is:
        struct timeval  time;   // tv_sec(8) + tv_usec(8) = 16 bytes
        __u16           type;   // 2 bytes
        __u16           code;   // 2 bytes
        __s32           value;  // 4 bytes
        // TOTAL = 24 bytes

    Packed format '<qQHHi': q(8) + Q(8) + H(2) + H(2) + i(4) = 24 bytes.

    >>> OLD (20 bytes — rejected by uinput):
    return struct.pack('<iIiHHi', ts_sec, ts_usec, 0, etype, code, value)

    The old pack format truncated 64-bit timestamps to 32-bit, causing the
    kernel to drop all events with -EINVAL (errno 22). Fixed by using 'q' for
    tv_sec (64-bit signed) and 'Q' for tv_usec (64-bit unsigned) timestamps.
    """
    t = time.time()
    ts_sec = int(t)
    ts_usec = int((t - ts_sec) * 1_000_000)
    return struct.pack('<qQHHi', ts_sec, ts_usec, etype, code, value)


# === UInputDevice --------

class UInputDevice:
    """Virtual joystick device emitter via /dev/uinput.

    Optimized for throughput with a buffered event model:
    - write_event() / key_event() / abs_event() queue events in _event_buffer
    - flush_buffer() appends EV_SYN and sends one os.write()
    This aligns with the Windows vJoy DLL's UpdateVJD() semantics where
    all changes within a frame are flushed atomically.
    """

    def __init__(self, device_id: int) -> None:
        self.device_id = device_id
        self.fd: int | None = None
        self.name = f"{_DEVICE_NAME} (#{device_id})"
        # Buffered event data — cleared after each flush.
        # EV_SYN is appended by flush_buffer().
        self._event_buffer: bytearray = bytearray()
        # Whether to buffer events (set by UInputDevice.open())
        self._buffered: bool = False

    #-- open / create -

    def open(self) -> bool:
        uinput_path = '/dev/uinput'
        if not os.path.exists(uinput_path):
            raise PermissionError(f'{uinput_path} not found. sudo modprobe uinput')
        if not os.access(uinput_path, os.W_OK):
            raise PermissionError(
                f'{uinput_path} not writable. '
                'sudo usermod -aG input $USER && newgrp input'
            )

        try:
            self.fd = os.open(uinput_path, os.O_WRONLY | os.O_NONBLOCK)
        except PermissionError as exc:
            raise PermissionError(
                f"Cannot open {uinput_path} — in input group?"
            ) from exc
        except OSError as exc:
            raise PermissionError(f"Cannot open {uinput_path}: {exc}")

        try:
            # === Zero: Verify kernel ABI (ENOTTY → ancient kernel) ===
            self._verify_uinput_support(self.fd)
            # === Step 1: Register capability bits ===
            for code in (EV_KEY, EV_ABS):
                if fcntl.ioctl(self.fd, UI_SET_EVBIT, code) != 0:
                    raise OSError(22, "UI_SET_EVBIT failed")
            for btn in range(BTN_SOUTH, BTN_TR2 + 1):
                fcntl.ioctl(self.fd, UI_SET_KEYBIT, btn)
            # Register additional buttons dynamically based on controller ID
            # Controller #1 gets 112 total, #2 gets 111, etc.
            # First 16 buttons come from the BTN_SOUTH→BTN_TR2 range above
            btn_count = max(0, 112 - (self.device_id - 1))
            extra_btns = max(0, btn_count - 16)
            for btn in range(BTN_SOUTH + 32, BTN_SOUTH + 32 + extra_btns):
                fcntl.ioctl(self.fd, UI_SET_KEYBIT, btn)
            # Register actual kernel absolute axis capability bits (values from _AXIS_MAP)
            for abs_code in _AXIS_MAP.values():  # Must use values (kernel ABS_* codes), NOT keys (vJoy DLL constants)
                fcntl.ioctl(self.fd, UI_SET_ABSBIT, abs_code)
            for hat_id in (0, 1):
                fcntl.ioctl(self.fd, UI_SET_ABSBIT, 16 + hat_id * 2)
                fcntl.ioctl(self.fd, UI_SET_ABSBIT, 17 + hat_id * 2)

            # === Step 2: Configure absolute axes (MUST be before UI_DEV_CREATE) ===
            # UI_ABS_SETUP must be called before UI_DEV_CREATE — the kernel bakes
            # axis configuration into the device at creation time. After CREATE,
            # further UI_ABS_SETUP calls fail with EINVAL.
            # struct uinput_abs_setup: code(2) + padding(2) + absinfo(24) = 28 bytes
            for code in _AXIS_MAP.values():  # values are kernel ABS_* codes (0, 1, 2, 3, 5, 4, 30, 31)
                abs_setup_s = _uinput_abs_setup()
                abs_setup_s.code     = code
                abs_setup_s.padding  = 0  # MUST be exactly 0!
                abs_setup_s.absinfo[0] = 0        # value
                abs_setup_s.absinfo[1] = _ABS_MIN # minimum
                abs_setup_s.absinfo[2] = _ABS_MAX # maximum
                abs_setup_s.absinfo[3] = _ABS_FUZZ  # fuzz
                abs_setup_s.absinfo[4] = _ABS_FLAT  # flat
                abs_setup_s.absinfo[5] = 0        # resolution
                if libc.ioctl(self.fd, UI_ABS_SETUP, ctypes.pointer(abs_setup_s)) < 0:
                    errno_val = ctypes.get_errno()
                    raise OSError(errno_val, f"UI_ABS_SETUP({code}) failed: {os.strerror(errno_val)}")

            # === Step 3: Modern device setup (kernel ≥6.x) ===
            # struct uinput_setup: id(8) + name(80) + ff_effects_max(4) = 92 bytes
            setup = _uinput_setup()
            setup.id.bustype  = BUS_USB
            setup.id.vendor   = 0x0000  # Native vJoy VID — no masquerade
            setup.id.product  = 0x0000  # Native vJoy PID — no masquerade
            setup.id.version  = 1
            setup.name = self.name.encode("utf-8").ljust(80, b"\x00")
            setup.ff_effects_max = 0
            if libc.ioctl(self.fd, UI_DEV_SETUP, ctypes.pointer(setup)) < 0:
                errno_val = ctypes.get_errno()
                raise OSError(errno_val, f"UI_DEV_SETUP failed: {os.strerror(errno_val)}")

            # === Step 4: Create the device ===
            if fcntl.ioctl(self.fd, UI_DEV_CREATE) < 0:
                errno_val = ctypes.get_errno()
                raise OSError(errno_val, "UI_DEV_CREATE failed")

            logging.getLogger("system").info(
                f"vJoy device #{self.device_id} created on uinput"
            )

            # Enable buffered event model — see write_event()
            self._buffered = True
            return True

        except OSError as exc:
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None
            raise VJoyError(f"Failed to create uinput device: {exc}")

    #-- kernel / device support verification --

    def _verify_uinput_support(self, fd: int) -> bool:
        """Validate that the kernel supports our uinput ABI.

        Called immediately after ``os.open('/dev/uinput')`` to catch
        ENOTTY (kernel < 4.5) or other ABI mismatches *before* we
        proceed to ``UI_DEV_SETUP`` + ``UI_ABSS_SETUP``.

        Returns ``True`` on success; raises ``RuntimeError`` otherwise.
        """
        # Probe UI_DEV_SETUP — if the kernel doesn't understand the
        # ioctl number it returns ENOTTY (88).
        setup = _uinput_setup()
        setup.id.bustype  = BUS_USB
        setup.id.vendor   = 0x0000  # Bare device ID for probe — vendor/product not used during probing
        setup.id.product  = 0x0000
        setup.id.version  = 1
        setup.ff_effects_max = 0
        rc_setup = libc.ioctl(fd, UI_DEV_SETUP, ctypes.pointer(setup))
        if rc_setup < 0:
            errno_val = ctypes.get_errno()
            if errno_val == 88:  # ENOTTY
                raise RuntimeError(
                    "Kernel uinput API unsupported — UI_DEV_SETUP rejected "
                    "(ENOTTY).  Kernel < 4.5 is not supported.  "
                    "Minimum kernel version required: 4.5"
                )

        # Probe UI_ABS_SETUP
        abs_setup = _uinput_abs_setup()
        abs_setup.code = 0
        abs_setup.filler = 0
        rc_abs = libc.ioctl(fd, UI_ABS_SETUP, ctypes.pointer(abs_setup))
        if rc_abs < 0:
            errno_val = ctypes.get_errno()
            if errno_val == 88:  # ENOTTY
                raise RuntimeError(
                    "Kernel uinput API unsupported — UI_ABS_SETUP rejected "
                    "(ENOTTY).  Kernel < 4.5 is not supported.  "
                    "Minimum kernel version required: 4.5"
                )

        return True

    #-- close -

    def close(self) -> None:
        if self.fd is not None:
            try:
                fcntl.ioctl(self.fd, UI_DEV_DESTROY)
            except OSError:
                pass
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None

    def __enter__(self) -> "UInputDevice":
        self.open()
        return self

    def __exit__(self, exc_t, exc_v, exc_tb) -> None:
        self.close()

    def __del__(self) -> None:
        if self.fd is not None:
            self.close()

    #-- event emission -

    def _fmt_event(self, etype: int, code: int, value: int) -> bytes:
        r"""Pack an `input_event` struct (24 bytes).

        On 64-bit Linux, `struct input_event` from <linux/input.h> is:
            struct timeval  time;   // tv_sec(8) + tv_usec(8) = 16 bytes
            __u16           type;   // 2 bytes
            __u16           code;   // 2 bytes
            __s32           value;  // 4 bytes
            // TOTAL = 24 bytes

        Packed format '<qQHHi': q(8) + Q(8) + H(2) + H(2) + i(4) = 24 bytes.

        >>> OLD (20 bytes — rejected by uinput):
        return struct.pack('<iIiHHi', ts_sec, ts_usec, 0, etype, code, value)

        The old pack format truncated 64-bit timestamps to 32-bit, causing the
        kernel to drop all events with -EINVAL (errno 22) — axes, buttons, and
        hats all silently failed until this was corrected.
        """
        t = time.time()
        ts_sec = int(t)
        ts_usec = int((t - ts_sec) * 1_000_000)
        return struct.pack('<qQHHi', ts_sec, ts_usec, etype, code, value)

    def write_event(self, etype: int, code: int, value: int) -> bool:
        """Queue an event in the write buffer.

        Instead of calling os.write() per-event (which triggers a full
        user→kernel context switch for each 24-byte packet), events are
        batched in an in-memory bytearray and flushed via flush_buffer().
        A single os.write() sends the entire frame in one syscall.

        Each event includes a 64-bit timestamp matching
        struct.input_event's timeval — uinput requires this or the kernel
        will interpret the fields incorrectly.
        """
        if self.fd is None:
            return False
        try:
            self._event_buffer.extend(
                struct.pack(_INPUT_EVENT_FMT, 
                           int(time.time()), 
                           int((time.time() % 1) * 1_000_000), 
                           etype, code, value)
            )
            return True
        except Exception:
            self._event_buffer.clear()
            return False

    def flush_buffer(self) -> bool:
        """Flush all buffered events to the kernel in a single os.write().

        Appends an EV_SYN event to mark the frame boundary, then writes
        everything as one contiguous buffer — matching the Windows vJoy
        UpdateVJD() semantic of atomic batch flush.
        """
        if not self._event_buffer:
            return True

        if self.fd is None:
            return False

        # Append EV_SYN frame boundary marker
        #self._event_buffer.extend(struct.pack("<IHi", EV_SYN, 0, 0))
        #JW AI fix
        t = time.time()
        ts_sec = int(t)
        ts_usec = int((t - ts_sec) * 1_000_000)
        self._event_buffer.extend(struct.pack('<qQHHi', int(time.time()), ts_usec, EV_SYN, 0, 0))
        #print("While you see this the Controllor available in jstest-gsk")
        
        try:
            n = os.write(self.fd, self._event_buffer)
            self._event_buffer.clear()
            return n > 0
        except OSError:
            self._event_buffer.clear()
            return False

    def sync(self) -> bool:
        """Legacy sync — now a pass-through to flush_buffer().

        Kept for API compat with existing callers. This is the primary
        trigger for buffered event flushes.
        """
        return self.flush_buffer()

    def abs_event(self, code: int, value: int) -> bool:
        return self.write_event(EV_ABS, code, value)

    def key_event(self, code: int, pressed: bool) -> bool:
        return self.write_event(EV_KEY, code, 1 if pressed else 0)

    def release_event(self, code: int) -> bool:
        return self.write_event(EV_KEY, code, 0)

    #-- reset to neutral -

    def reset_to_neutral(self) -> None:
        for _v, abs_c in _AXIS_MAP.items():
            self.abs_event(abs_c, 0)
        for hat_id in (0, 1):
            self.abs_event(16 + hat_id * 2, 0)
            self.abs_event(17 + hat_id * 2, 0)
        for btn in range(BTN_SOUTH, BTN_SOUTH + 256):
            self.release_event(btn)
        self.sync()

    def release_axis(self) -> None:
        self.sync()

    def press_button(self, btn: int) -> None:
        if 1 <= btn <= 128:
            self.write_event(EV_KEY, 304 + btn - 1, 1)

    def release_button(self, btn: int) -> None:
        if 1 <= btn <= 128:
            self.release_event(304 + btn - 1)

    def press_event(self, code: int) -> None:
        self.write_event(EV_KEY, code, 1)

    def get_sysname(self) -> str:
        if self.fd is None:
            return ""
        buf = ctypes.create_string_buffer(256)
        nr = ((1 << 30) | (int(_UI) << 8) | (44 << 0) | (256 << 16))
        rc = libc.ioctl(self.fd, nr, buf)
        if rc < 0:
            return ""
        return buf.value.decode("utf-8", errors="replace").strip("\x00")
