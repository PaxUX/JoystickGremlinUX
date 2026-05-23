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
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""Low-level Linux vJoy interface using /dev/uinput.

This module provides the exact same Python-callable API surface as the
Windows vJoyInterface.dll, but backed by uinput virtual device nodes
instead of a native DLL.
"""

import copy
import ctypes
import enum
import fcntl
import logging
import math
import os
import struct
import threading
import time

# Define VJoyError locally to avoid circular import through gremlin.error
class VJoyError(Exception):
    """Exception raised when an error occurs within the vJoy module."""
    def __init__(self, value):
        self.value = value

from . import device_manager

# Re-export VJoyState for compatibility
class VJoyState(enum.Enum):
    """Enumeration of the possible vJoy device states (Linux)."""
    Owned = 0
    Free = 1
    Bust = 2
    Missing = 3
    Unknown = 4


# Maximum vJoy device index
MAX_VJOY_DEVICES = 16


class _LinuxVJoyInterface:
    """Linux backend for low-level vJoy device interaction via uinput.

    Each device (1-16) is individually managed. A device is "owned" when
    its corresponding /dev/uinput node has been successfully created.
    """

    # Device registry: vjoy_id -> UInputDevice
    _devices: dict[int, device_manager.UInputDevice | None] = {}
    
    # GUID Registry: vjoy_id -> GUID seed string
    # vJoy is the source of truth for virtual device GUID generation.
    # DILL consumes these seeds and creates ctypes _GUID instances locally 
    # (to avoid circular imports).
    # GUIDs are generated using SHA1 of the seed — stable regardless of
    # axis configuration changes, preventing GUID drift between DILL discovery cycles.
    _vJoy_guid_map: dict[int, str] = {}

    @classmethod
    def GetVJoyGuid(cls, vjd_id: int) -> str:
        """Return the GUID seed for a vJoy device.
        
        Generates a stable seed from the vJoy ID alone (not axis_count),
        so the GUID remains stable across discovery cycles with different configurations.
        
        Returns a seed string (e.g., "vJoy:1"). DILL converts this to a ctypes _GUID
        by hashing it. vJoyInterface does NOT import dill to avoid circular imports.
        This is the SINGLE source of truth for vJoy GUID generation.
        """
        seed = f"vJoy:{vjd_id}"
        cls._vJoy_guid_map[vjd_id] = seed
        return seed

    @classmethod
    def GetAllVjoyGuids(cls) -> dict[int, object]:
        """Get or generate all vJoy GUIDs for owned devices.
        
        Populates the registry for any unowned devices, then returns the full dict.
        """
        import copy
        # Generate GUIDs for all owned devices
        for vjd_id in range(1, 17):
            cls.GetVjoyGuid(vjd_id)
        return {k: copy.deepcopy(v) for k, v in cls._vjoy_guid_map.items()}

    # Per-device axis/button/hat capabilities: vjoy_id -> set of valid IDs
    # These are set during AcquireVJD based on device configuration
    # If a device is not in these dicts, it has no controls of that type
    _axis_cap: dict[int, set[int]] = {}
    _btn_cap: dict[int, set[int]] = {}
    _hat_cap: dict[int, set[int]] = {}

    # Axis mapping: vJoy axis constants → uinput ABS codes
    AXIS_MAP: dict[int, int] = {
        0x30: 0,   # X      → ABS_X
        0x31: 1,   # Y      → ABS_Y
        0x32: 2,   # Z      → ABS_Z
        0x33: 3,   # RX     → ABS_RX
        0x34: 5,   # RY     → ABS_RY
        0x35: 4,   # RZ     → ABS_RZ
        0x36: 30,  # SL0    → ABS_SL0
        0x37: 31,  # SL1    → ABS_SL1
    }

    # Axis min/max
    AXIS_MIN: int = -32768
    AXIS_MAX: int = 32767

    # Button range (vJoy supports up to 128)
    BUTTON_COUNT_MAX: int = 128

    # Hat count
    HAT_COUNT: int = 2

    @classmethod
    def initialize(cls) -> None:
        """Initialize the Linux vJoy interface (no-op; all lazy)."""
        pass

    @classmethod
    def GetvJoyVersion(cls) -> int:
        """Return the fake vJoy version number (matches Windows DLL)."""
        return 0x218  # 2.1.8

    @classmethod
    def vJoyEnabled(cls) -> bool:
        """Check whether uinput interface is available."""
        return os.path.exists('/dev/uinput') and os.access('/dev/uinput', os.W_OK)

    @classmethod
    def GetvJoyProductString(cls) -> str:
        return "vJoy Linux Virtual Joystick"

    @classmethod
    def GetvJoyManufacturerString(cls) -> str:
        return "Ubuntu"

    @classmethod
    def GetvJoySerialNumberString(cls) -> str:
        return f"linux-{time.strftime('%Y%m%d')}"

    # ------ Device properties ------

    @classmethod
    def GetVJDButtonNumber(cls, vjd_id: int) -> int:
        """Return the number of buttons for a specific vJoy device.
        
        Each controller gets progressively fewer buttons:
        - Controller 1: 112 buttons
        - Controller 2: 111 buttons
        - Controller 3: 110 buttons
        - etc.
        """
        buttons = 112 - (vjd_id - 1)
        return max(0, buttons) 

    @classmethod
    def GetVJDDiscPovNumber(cls, vjd_id: int) -> int:
        return 0  # We only support continuous hats

    @classmethod
    def GetVJDContPovNumber(cls, vjd_id: int) -> int:
        return cls.HAT_COUNT

    @classmethod
    def GetVJDAxisExist(cls, vjd_id: int, axis_id: int) -> int:
        """Check if a specific axis exists on a given device.
        
        :param vjd_id: vJoy device ID
        :param axis_id: vJoy axis constant (e.g. 0x30 for X)
        :return: 1 if the axis exists on this device, 0 otherwise
        """
        if vjd_id not in cls._axis_cap:
            return 0
        return 1 if axis_id in cls._axis_cap[vjd_id] else 0

    @classmethod
    def GetVJDAxisCount(cls, vjd_id: int) -> int:
        """Return the number of axes configured on this device."""
        if vjd_id not in cls._axis_cap:
            return 0
        return len(cls._axis_cap[vjd_id])

    @classmethod
    def SetVJDAxisCount(cls, vjd_id: int, axis_count: int) -> None:
        """Set/update the number of axes for a device.
        
        This is called after device creation to configure axis count.
        :param vjd_id: vJoy device ID
        :param axis_count: number of axes (0-8)
        """
        axis_count = max(0, min(axis_count, 8))
        if vjd_id in cls._axis_cap:
            if axis_count > 0:
                cls._axis_cap[vjd_id] = set(
                    list(cls.AXIS_MAP.keys())[:axis_count]
                )
            else:
                cls._axis_cap[vjd_id] = set()

    @classmethod
    def GetVJDAxisMax(cls, vjd_id: int, axis_id: int, out: "None" = None) -> bool:
        return True

    @classmethod
    def GetVJDAxisMin(cls, vjd_id: int, axis_id: int, out: "None" = None) -> bool:
        return True

    @classmethod
    def GetOwnerPid(cls, vjd_id: int) -> int:
        """Return PID of the owning process. On Linux, this is always our own PID."""
        dev = cls._devices.get(vjd_id)
        if dev is None or dev.fd is None:
            return 0
        return os.getpid()

    # ------ Device status ------

    @classmethod
    def GetVJDStatus(cls, vjd_id: int) -> int:
        dev = cls._devices.get(vjd_id)
        if dev is None or dev.fd is None:
            return VJoyState.Free.value
        return VJoyState.Owned.value

    # ------ Device management ------

    @classmethod
    def AcquireVJD(cls, vjd_id: int, axis_count: int = 8) -> bool:
        """Acquire (create) a virtual vJoy device.

        :param vjd_id: device ID (1-16)
        :param axis_count: number of axes to enable (0-8). Defaults to 8 (all axes,
            including SL0/L2 and SL1/R2 trigger axes). Matches Windows vJoy default.
        """
        if vjd_id < 1 or vjd_id > MAX_VJOY_DEVICES:
            logging.getLogger("system").error(f"Invalid vJoy device ID: {vjd_id}")
            return False

        # Clamp axis_count to valid range
        axis_count = max(0, min(axis_count, 8))

        if cls._devices.get(vjd_id) is not None:
            # Already owned by us
            return True

        try:
            dev = device_manager.UInputDevice(vjd_id)
            dev.open()
            cls._devices[vjd_id] = dev

            # Track per-device capability — only axes that were actually registered
            if axis_count > 0:
                cls._axis_cap[vjd_id] = set(
                    list(cls.AXIS_MAP.keys())[:axis_count]
                )
            else:
                cls._axis_cap[vjd_id] = set()

            # Update button capabilities based on device's actual button count
            btn_count = cls.GetVJDButtonNumber(vjd_id)
            cls._btn_cap[vjd_id] = set(range(1, btn_count + 1))

            # Hats always exist (we use continuous POV)
            cls._hat_cap[vjd_id] = set(range(1, cls.HAT_COUNT + 1))

            return True
        except PermissionError as e:
            logging.getLogger("system").error(f"Permission error acquiring vJoy device {vjd_id}: {e}")
            raise VJoyError(f"Permission denied: {e}")
        except OSError as e:
            logging.getLogger("system").error(f"OS error acquiring vJoy device {vjd_id}: {e}")
            return False

    # ------ Reconfiguration ------

    @classmethod
    def reconfigure_device(cls, vjd_id: int, axis_count: int) -> bool:
        """Reconfigure axes on a device without destroying it.

        Call this to set/update the axis count for a device that's
        already been acquired. The device will not be recreated.

        :param vjd_id: vJoy device ID
        :param axis_count: new axis count (0-8)
        :return: True if reconfiguration was successful
        """
        if vjd_id not in cls._devices or cls._devices[vjd_id] is None:
            return False

        axis_count = max(0, min(axis_count, 8))
        if axis_count > 0:
            cls._axis_cap[vjd_id] = set(
                list(cls.AXIS_MAP.keys())[:axis_count]
            )
        else:
            cls._axis_cap[vjd_id] = set()

        return True

    @classmethod
    def RelinquishVJD(cls, vjd_id: int) -> None:
        """Release (destroy) a virtual vJoy device.

        NOTE: On Linux, vJoy devices created at startup (joystick_gremlin.py)
        are persistent for the app lifetime. Calling RelinquishVJD directly
        on such devices would be incorrect — only use it when the app is
        shutting down. Guard in vjoy.py handles this.
        """
        dev = cls._devices.pop(vjd_id, None)
        if dev is not None:
            dev.close()
        # Clean up capability tracking
        cls._axis_cap.pop(vjd_id, None)
        cls._btn_cap.pop(vjd_id, None)
        cls._hat_cap.pop(vjd_id, None)

    # ------ Value setting (the write operations) ------

    _ABS_MIN = -32768
    _ABS_MAX = 32767

    @classmethod
    def SetAxis(cls, value: int, vjd_id: int, axis: int) -> bool:
        """Set an axis position. value is the raw integer (0..2*_half_range)."""
        dev = cls._devices.get(vjd_id)
        if dev is None or dev.fd is None:
            # logging.getLogger("system").debug(
            #     "vJoy.SetAxis: REJECTED (no device) vjd_id=%d axis=%d value=%d",
            #     vjd_id, axis, value
            # )
            return False

        # Check both: axis is in the static map AND on this device's capability set
        if axis not in cls.AXIS_MAP:
            # logging.getLogger("system").debug(
            #     "vJoy.SetAxis: REJECTED (axis not in map) vjd_id=%d axis=%d value=%d",
            #     vjd_id, axis, value
            # )
            return False
        if vjd_id not in cls._axis_cap or axis not in cls._axis_cap[vjd_id]:
            # logging.getLogger("system").debug(
            #     "vJoy.SetAxis: REJECTED (axis not in cap) vjd_id=%d axis=%d value=%d",
            #     vjd_id, axis, value
            # )
            return False

        abs_code = cls.AXIS_MAP[axis]
        result = dev.abs_event(abs_code, value) and dev.sync()
        # logging.getLogger("system").debug(
        #     "vJoy.SetAxis: wrote vjd_id=%d axis=%d abs_code=%d value=%d (result=%s)",
        #     vjd_id, axis, abs_code, value, result
        # )
        return result

    @classmethod
    def SetBtn(cls, is_pressed: bool, vjd_id: int, btn: int) -> bool:
        """Set a button's pressed state (1-based index → BTN + offset)."""
        dev = cls._devices.get(vjd_id)
        if dev is None or dev.fd is None:
            return False

        # Map 1-based button ID to a BTN_* code
        # vJoy buttons are split into two registered ranges:
        #   buttons 1-16  → codes 304-319 (BTN_SOUTH → BTN_TR2)
        #   buttons 17-112 → codes 336-431 (gap of 16 unregistered codes at 320-335)
        if not (1 <= btn <= cls.BUTTON_COUNT_MAX):
            return False

        if btn <= 16:
            btn_code = 304 + btn - 1
        else:
            btn_code = 336 + (btn - 17)
        return dev.key_event(btn_code, is_pressed) and dev.sync()

    @classmethod
    def SetDiscPov(cls, value: int, vjd_id: int, hat: int) -> bool:
        """Set a discrete POV hat position (not used on Linux; we use continuous)."""
        dev = cls._devices.get(vjd_id)
        if dev is None or dev.fd is None:
            return False
        # Discrete POV maps to 0/1/-1 for X/Y axes
        hat_offset = (hat - 1) * 2
        x_code = 16 + hat_offset       # HAT0X or HAT1X
        y_code = 16 + hat_offset + 1   # HAT0Y or HAT1Y

        vals = {
            -1: (0, 0),  # Neutral
            0: (0, 1),   # Up
            1: (1, 0),   # Right
            2: (0, -1),  # Down
            3: (-1, 0),  # Left
        }
        x, y = vals.get(value, (0, 0))
        return dev.abs_event(x_code, x) and dev.abs_event(y_code, y) and dev.sync()

    @classmethod
    def SetContPov(cls, value: int, vjd_id: int, hat: int) -> bool:
        """Set a continuous POV hat position (0..36000 millidegrees, or -1 = neutral)."""
        dev = cls._devices.get(vjd_id)
        if dev is None or dev.fd is None:
            return False

        # Normalize value to [-32768, 32767] range
        # Map 0..36000 → -32768..32767
        if value == -1:
            x, y = 0, 0
        else:
            # Convert to radians
            angle = (value / 36000) * 2 * 3.14159265
            x = int(32768 * -1 * math.cos(angle))
            y = int(32768 * math.sin(angle))
            # Clamp
            x = max(-32768, min(32767, x))
            y = max(-32768, min(32767, y))

        hat_offset = (hat - 1) * 2
        x_code = 16 + hat_offset
        y_code = 16 + hat_offset + 1

        return dev.abs_event(x_code, x) and dev.abs_event(y_code, y) and dev.sync()

    @classmethod
    def UpdateVJD(cls, vjd_id: int, data: object) -> bool:
        """Flush pending events (Linux sends immediately, so this is a no-op)."""
        return True

    @classmethod
    def ResetVJD(cls, vjd_id: int) -> bool:
        """Reset all controls on the device."""
        dev = cls._devices.get(vjd_id)
        if dev is None or dev.fd is None:
            return False
        dev.reset_to_neutral()
        return True

    @classmethod
    def ResetAll(cls) -> None:
        """Reset all vJoy devices."""
        for vjd in list(cls._devices.keys()):
            cls.ResetVJD(vjd)

    @classmethod
    def ResetButtons(cls, vjd_id: int) -> bool:
        """Reset only buttons on the device."""
        dev = cls._devices.get(vjd_id)
        if dev is None or dev.fd is None:
            return False
        # Release all gamepad buttons (304-340)
        for btn in range(304, 340):
            dev.key_event(btn, False)
        return dev.sync()

    @classmethod
    def ResetPovs(cls, vjd_id: int) -> bool:
        """Reset only POV hats."""
        dev = cls._devices.get(vjd_id)
        if dev is None or dev.fd is None:
            return False
        for hat in (16, 17, 18, 19):
            dev.abs_event(hat, 0)
        return dev.sync()


# ------ Export ------

VJoyInterface = _LinuxVJoyInterface

