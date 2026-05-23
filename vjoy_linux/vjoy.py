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

"""High-level vJoy device layer for Linux.

Port of the original vjoy.py — all vJoy, Axis, Button, Hat classes
adapted to work with the Linux uinput backend while preserving the
exact same high-level API contract for Gremlin's action plugins.
"""

import ctypes
import enum
import logging
import math
import sys
import threading
import time

import gremlin.common
import gremlin.spline

from .vjoy_interface import VJoyState, VJoyInterface

# Define VJoyError locally to avoid the circular import:
#   vjoy → gremlin.error → gremlin.joystick_handling → vjoy → deadlock
class VJoyError(Exception):
    """Exception raised when an error occurs within the vJoy module."""
    def __init__(self, value):
        self.value = value

logger = logging.getLogger("system")


# ====================================
# Enums
# ====================================

class AxisName(enum.Enum):
    """Enumeration of the valid axis names (identical to Windows vJoy DLL)."""
    X = 0x30
    Y = 0x31
    Z = 0x32
    RX = 0x33
    RY = 0x34
    RZ = 0x35
    SL0 = 0x36
    SL1 = 0x37


class HatType(enum.Enum):
    """Valid hat types."""
    Discrete = 0
    Continuous = 1


# ====================================
# Device query functions (module level)
# ====================================

def device_available(vjoy_id: int) -> bool:
    """Returns whether or not a device is available, i.e. can be acquired.

    :param vjoy_id: id of the vjoy device to check
    :return: True if the device is available, False otherwise
    """
    dev_free = VJoyInterface.GetVJDStatus(vjoy_id) == VJoyState.Free.value
    dev_acquire = VJoyInterface.AcquireVJD(vjoy_id)
    if dev_acquire:
        VJoyInterface.RelinquishVJD(vjoy_id)
    return dev_free and dev_acquire


def device_exists(vjoy_id: int) -> bool:
    """Returns whether or not a device exists.

    :param vjoy_id: id of the vjoy device to check
    :return: True if the device exists, False otherwise
    """
    state = VJoyInterface.GetVJDStatus(vjoy_id)
    return state == VJoyState.Owned.value


def axis_count(vjoy_id: int) -> int:
    """Returns the number of axes of the given vJoy device.

    :param vjoy_id: id of the vjoy device
    :return: number of axes
    """
    count = 0
    for axis in AxisName:
        if VJoyInterface.GetVJDAxisExist(vjoy_id, axis.value) > 0:
            count += 1
    return count


def button_count(vjoy_id: int) -> int:
    """Returns the number of buttons of the given vJoy device.

    :param vjoy_id: id of the vjoy device
    :return: number of buttons
    """
    return VJoyInterface.GetVJDButtonNumber(vjoy_id)


def hat_count(vjoy_id: int) -> int:
    """Returns the number of hats of the given vJoy device.

    :param vjoy_id: id of the vjoy device
    :return: number of hats
    """
    return VJoyInterface.GetVJDContPovNumber(vjoy_id)


def hat_configuration_valid(vjoy_id: int) -> bool:
    """Returns if the hats are configured properly.

    In order for hats to work properly they have to be set as continuous.

    :param vjoy_id: index of the vJoy device to query
    :return: True if the hats are configured properly, False otherwise
    """
    continuous_count = VJoyInterface.GetVJDContPovNumber(vjoy_id)
    discrete_count = VJoyInterface.GetVJDDiscPovNumber(vjoy_id)
    return continuous_count >= discrete_count


# ====================================
# Helper
# ====================================

def _error_string(vid: int, iid: int, value: str | float) -> str:
    """Creates an error string for the given inputs.

    :param vid: vjoy device id
    :param iid: input id
    :param value: input value
    :return: string representing the error
    """
    return "vjoy: {} input: {} value: {}".format(vid, iid, value)


# ====================================
# Axis class
# ====================================

class Axis:
    """Represents an analog axis in vJoy, allows setting the value of the axis
    on Linux (via uinput ABS events)."""

    # Shared across all instances since AXIS_MIN/MAX are constant per device
    _HALF_RANGE: int = int((VJoyInterface.AXIS_MAX - VJoyInterface.AXIS_MIN) / 2)

    def __init__(self, vjoy_dev: 'VJoy', axis_id: int) -> None:
        self.vjoy_dev = vjoy_dev
        self.vjoy_id = vjoy_dev.vjoy_id
        self.axis_id = axis_id
        self._value = 0.0

        # Retrieve axis min/max (always -32768..32767 on Linux)
        self._min_value = VJoyInterface.AXIS_MIN
        self._max_value = VJoyInterface.AXIS_MAX

        self._deadzone_fn = lambda x: deadzone(x, -1.0, -0.0, 0.0, 1.0)
        self._response_curve_fn = lambda x: x

    def set_response_curve(self, spline_type: str, control_points: "list[tuple[float, float]]") -> None:
        """Sets the response curve for this axis.

        :param spline_type: 'cubic-spline' or 'cubic-bezier-spline'
        :param control_points: list of (float, float) control points
        """
        if spline_type == "cubic-spline":
            obj = gremlin.spline.CubicSpline(control_points)
        elif spline_type == "cubic-bezier-spline":
            obj = gremlin.spline.CubicBezierSpline(control_points)
        else:
            logger.error("Invalid spline type specified")
            self._response_curve_fn = lambda x: x
            return

        if callable(obj):
            self._response_curve_fn = obj
        else:
            logger.error("Spline object is not callable: %s", type(obj))
            self._response_curve_fn = lambda x: x

    def set_deadzone(self, low: float, center_low: float, center_high: float, high: float) -> None:
        """Sets the deadzone for this axis.

        :param low: low deadzone limit
        :param center_low: lower center deadzone limit
        :param center_high: upper center deadzone limit
        :param high: high deadzone limit
        """
        self._deadzone_fn = lambda x: deadzone(x, low, center_low, center_high, high)

    @property
    def value(self) -> float:
        """Returns the axis position as a value between [-1, 1].

        :return: position of the axis as a value between [-1, 1]
        """
        self.vjoy_dev.used()
        return self._value

    @value.setter
    def value(self, value: float) -> None:
        """Sets the position of the axis based on a value between [-1, 1].

        :param value: the position of the axis in the range [-1, 1]
        """
        self.vjoy_dev.ensure_ownership()

        # Clamp to [-1, 1]
        clamped = min(1.0, max(-1.0, value))

        # Apply deadzone
        clipped = self._deadzone_fn(clamped)
        # Apply response curve
        self._value = self._response_curve_fn(clipped)

        # Debug logging: trace curve application
        # logging.getLogger("system").debug(
        #     "vJoy.AXIS[%d]: input=%.4f -> clamped=%.4f -> deadzone=%.4f -> curve=%.4f -> raw=%.0f",
        #     self.axis_id, value, clamped, clipped, self._value,
        #     int(self._value * VJoyInterface.AXIS_MAX)
        # )

        # Map from [-1, 1] → [-32768, 32767] integer
        raw_value = int(self._value * VJoyInterface.AXIS_MAX)
        if not VJoyInterface.SetAxis(raw_value, self.vjoy_id, self.axis_id):
            raise VJoyError(
                "Failed setting axis value - {}".format(
                    _error_string(self.vjoy_id, self.axis_id, self._value)
                )
            )
        self.vjoy_dev.used()

    def set_absolute_value(self, value: float) -> None:
        """Sets the axis position bypassing deadzone and response curves.

        :param value: position in the range [-1, 1]
        """
        self._value = value
        raw_value = int(self._value * VJoyInterface.AXIS_MAX)
        if not VJoyInterface.SetAxis(raw_value, self.vjoy_id, self.axis_id):
            raise VJoyError(
                "Failed setting axis value - {}".format(
                    _error_string(self.vjoy_id, self.axis_id, self._value)
                )
            )
        self.vjoy_dev.used()


# ====================================
# Button class
# ====================================

class Button:
    """Represents a button in vJoy, allows pressing and releasing it."""

    def __init__(self, vjoy_dev: 'VJoy', button_id: int) -> None:
        self.vjoy_dev = vjoy_dev
        self.vjoy_id = vjoy_dev.vjoy_id
        self.button_id = button_id
        self._is_pressed = False

    @property
    def is_pressed(self) -> bool:
        """Returns whether or not the button is pressed.

        :return: True if the button is pressed, False otherwise
        """
        self.vjoy_dev.used()
        return self._is_pressed

    @is_pressed.setter
    def is_pressed(self, is_pressed: bool) -> None:
        self.vjoy_dev.ensure_ownership()
        # Coerce to boolean gracefully to handle None/0/1 from events/functors
        if is_pressed is None:
            is_pressed = False
        self._is_pressed = bool(is_pressed)
        if not VJoyInterface.SetBtn(self._is_pressed, self.vjoy_id, self.button_id):
            raise VJoyError(
                "Failed setting button value - {}".format(
                    _error_string(self.vjoy_id, self.button_id, self._is_pressed)
                )
            )
        self.vjoy_dev.used()


# ====================================
# Hat (POV) class
# ====================================

class Hat:
    """Represents a continuous hat in vJoy, allows setting the direction."""

    # Discrete directions: (x, y) → vJoy value
    to_discrete_direction: "dict[tuple[int, int], int]" = {
        (0, 1): 0,
        (1, 0): 1,
        (0, -1): 2,
        (-1, 0): 3,
        (0, 0): -1,
    }

    # Continuous directions: (x, y) → millidegrees
    to_continuous_direction: "dict[tuple[int, int], int]" = {
        (0, 0): -1,
        (0, 1): 0,
        (1, 1): 4500,
        (1, 0): 9000,
        (1, -1): 13500,
        (0, -1): 18000,
        (-1, -1): 22500,
        (-1, 0): 27000,
        (-1, 1): 31500,
    }

    def __init__(self, vjoy_dev: 'VJoy', hat_id: int, hat_type: HatType) -> None:
        self.vjoy_dev = vjoy_dev
        self.vjoy_id = vjoy_dev.vjoy_id
        self.hat_id = hat_id
        self._direction = (0, 0)
        self.hat_type = hat_type

    @property
    def direction(self) -> tuple[int, int]:
        """Returns the current direction of the hat.

        :return: current direction as tuple (x, y)
        """
        self.vjoy_dev.used()
        return self._direction

    @direction.setter
    def direction(self, direction: tuple[int, int] | int) -> None:
        """Sets the direction of the hat.

        :param direction: (x, y) tuple or angle in millidegrees
        """
        self.vjoy_dev.ensure_ownership()

        if self.hat_type == HatType.Discrete:
            self._set_discrete_direction(direction)  # type: ignore[arg-type]
        elif self.hat_type == HatType.Continuous:
            self._set_continuous_direction(direction)  # type: ignore[arg-type]
        else:
            raise VJoyError(
                "Invalid hat type specified - {}".format(
                    _error_string(self.vjoy_id, self.hat_id, self.direction)
                )
            )
        self.vjoy_dev.used()

    def _set_discrete_direction(self, direction: tuple[int, int]) -> None:
        if direction not in self.to_discrete_direction:
            raise VJoyError(
                "Invalid direction specified - {}".format(
                    _error_string(self.vjoy_id, 0, direction)
                )
            )
        self._direction = direction
        if not VJoyInterface.SetDiscPov(
            self.to_discrete_direction[direction],
            self.vjoy_id,
            self.hat_id,
        ):
            raise VJoyError(
                "Failed to set hat direction - {}".format(
                    _error_string(self.vjoy_id, 0, self._direction)
                )
            )

    def _set_continuous_direction(self, direction: tuple[int, int]) -> None:
        if direction not in self.to_continuous_direction:
            raise VJoyError(
                "Invalid direction specified - {}".format(
                    _error_string(self.vjoy_id, 0, direction)
                )
            )
        self._direction = direction
        if not VJoyInterface.SetContPov(
            self.to_continuous_direction[direction],
            self.vjoy_id,
            self.hat_id,
        ):
            raise VJoyError(
                "Failed to set hat direction - {}".format(
                    _error_string(self.vjoy_id, 0, self._direction)
                )
            )


# ====================================
# VJoy device class
# ====================================

class VJoy:
    """Represents a vJoy device present on the system (Linux uinput version).

    Manages acquisition, axis/button/hat access, value processing, and device
     lifecycle. The virtual device presents as a generic virtual controller.
    """

    # Duration of inactivity after which the keep-alive routine fires (seconds)
    keep_alive_timeout: int = 60

    # Axis name mapping: DLL-axis → logical index
    axis_equivalence: "dict[AxisName, int]" = {
        AxisName.X: 1,
        AxisName.Y: 2,
        AxisName.Z: 3,
        AxisName.RX: 4,
        AxisName.RY: 5,
        AxisName.RZ: 6,
        AxisName.SL0: 7,
        AxisName.SL1: 8,
    }

    def __init__(self, vjoy_id: int) -> None:
        """Creates a new vJoy device handle.

        :param vjoy_id: id of the vJoy device (1-16)
        :raises VJoyError: on any failure during acquisition
        """
        self.vjoy_id: int | None = None
        self._axis: "dict[int, Axis]" = {}
        self._button: "dict[int, Button]" = {}
        self._hat: "dict[int, Hat]" = {}
        self._axis_lookup: "dict[int, int]" = {}
        self._axis_names: "dict[int, str]" = {}

        # Pre-flight checks
        if not VJoyInterface.vJoyEnabled():
            raise VJoyError("vJoy uinput interface is not available")

        if VJoyInterface.GetvJoyVersion() != 0x218:
            raise VJoyError(
                f"Running incompatible vJoy version, 2.1.8 required (got {VJoyInterface.GetvJoyVersion()})"
            )

        # On Linux a vJoy device may already be owned (e.g. created at
        # startup in joystick_gremlin.py).  If it's free we acquire it;
        # if it's owned we accept that and continue — the device survives
        # as long as the FD stays open.
        status = VJoyInterface.GetVJDStatus(vjoy_id)
        if status == VJoyState.Free.value:
            if not VJoyInterface.AcquireVJD(vjoy_id):
                raise VJoyError(
                    f"Failed to acquire the vJoy device - vid: {vjoy_id}"
                )
        elif status != VJoyState.Owned.value:
            raise VJoyError(
                f"Requested vJoy device is not available - vid: {vjoy_id}"
            )

        self.vjoy_id = vjoy_id
        self.pid = __import__('os').getpid()

        # Initialize all controls
        self._init_axes()
        self._init_buttons()
        self._init_hats()

        self._last_active = time.time()
        self._keep_alive_timer = threading.Timer(
            VJoy.keep_alive_timeout,
            self._keep_alive,
        )
        self._keep_alive_timer.start()

        # Reset all controls to neutral
        self.reset()

    def ensure_ownership(self) -> None:
        """Re-acquire the device if ownership was lost."""
        if self.vjoy_id is None:
            return

        owner_pid = VJoyInterface.GetOwnerPid(self.vjoy_id)
        if self.pid != owner_pid:
            if not VJoyInterface.AcquireVJD(self.vjoy_id):
                raise VJoyError(
                    f"Failed to re-acquire vJoy device — vid: {self.vjoy_id}"
                )

    @property
    def axis_count(self) -> int:
        return len(self._axis)

    @property
    def button_count(self) -> int:
        return len(self._button)

    @property
    def hat_count(self) -> int:
        return len(self._hat)

    def axis_name(self, axis_id: int | None = None, linear_index: int | None = None) -> str:
        """Returns the textual name of the requested axis.

        :param axis_id: absolute DLL axis ID (e.g. AxisName.X = 0x30)
        :param linear_index: logical index (1-8)
        :return: axis name string
        """
        if axis_id is not None:
            resolved = VJoy.axis_equivalence.get(axis_id, axis_id)
            if not self.is_axis_valid(axis_id=axis_id):
                raise VJoyError(
                    f"Invalid axis index requested - {_error_string(self.vjoy_id, axis_id, "")}"
                )
            return self._axis_names[resolved]
        elif linear_index is not None:
            if not self.is_axis_valid(linear_index=linear_index):
                raise VJoyError(
                    f"Invalid linear index for axis lookup - {_error_string(self.vjoy_id, linear_index, '')}"
                )
            return self._axis_names[linear_index]
        else:
            raise VJoyError("No axis_id or linear_index provided")

    def axis_id(self, linear_index: int) -> int:
        """Returns the absolute axis id corresponding to the relative one.

        :param linear_index: logical index
        :return: absolute axis constant (e.g. 0x30)
        """
        if not self.is_axis_valid(linear_index=linear_index):
            raise VJoyError(
                f"Invalid linear index for axis lookup - {_error_string(self.vjoy_id, linear_index, "")}"
            )
        return self._axis_lookup[linear_index]

    def axis(self, axis_id: int | None = None, linear_index: int | None = None) -> Axis:
        """Returns the axis object for the given index.

        :param axis_id: absolute DLL axis ID (e.g. 0x30 for X)
        :param linear_index: logical index (1-8)
        :return: Axis object
        """
        if axis_id is not None:
            # Direct linear index (1-based) lookup
            if axis_id in self._axis:
                return self._axis[axis_id]
            # Resolve DLL axis constant to linear index
            linear = self._axis_lookup.get(axis_id)
            if linear is None or linear not in self._axis:
                raise VJoyError(
                    f"Invalid axis index requested - {_error_string(self.vjoy_id, axis_id, '')}"
                )
            return self._axis[linear]
        elif linear_index is not None:
            if linear_index not in self._axis:
                raise VJoyError(
                    f"Invalid linear index - {_error_string(self.vjoy_id, linear_index, '')}"
                )
            return self._axis[linear_index]
        else:
            raise VJoyError("No axis_id or linear_index provided")

    def button(self, index: int) -> Button:
        """Returns the button object for the given index.

        :param index: 1-based button ID
        :return: Button object
        """
        if index not in self._button:
            raise VJoyError(
                f"Invalid button index requested - {_error_string(self.vjoy_id, index, '')}"
            )
        return self._button[index]

    def hat(self, index: int) -> Hat:
        """Returns the hat object for the given index.

        :param index: 1-based hat ID
        :return: Hat object
        """
        if index not in self._hat:
            raise VJoyError(
                f"Invalid hat index requested - {_error_string(self.vjoy_id, index, '')}"
            )
        return self._hat[index]

    def is_axis_valid(self, axis_id: int | None = None, linear_index: int | None = None) -> bool:
        """Returns whether the given axis index is valid on this device."""
        if axis_id is not None:
            # axis_id could be a DLL constant (48 for X) or a linear index
            linear = self._axis_lookup.get(axis_id) or axis_id
            return linear in self._axis
        elif linear_index is not None:
            return linear_index in self._axis
        else:
            raise VJoyError("No axis_id or linear_index provided")

    def is_button_valid(self, index: int) -> bool:
        """Returns whether the button index is valid."""
        return index in self._button

    def is_hat_valid(self, index: int) -> bool:
        """Returns whether the hat index is valid."""
        return index in self._hat

    def reset(self) -> None:
        """Resets all controls to default (neutral) state."""
        axis_states = {i: a.value for i, a in self._axis.items()}
        button_states = {i: b.is_pressed for i, b in self._button.items()}
        hat_states = {i: h.direction for i, h in self._hat.items()}

        success = VJoyInterface.ResetVJD(self.vjoy_id)  # type: ignore[union-attr]

        if success:
            for i in self._axis:
                self._axis[i].set_absolute_value(axis_states[i])
            for i in self._button:
                self._button[i].is_pressed = button_states[i]
            for i in self._hat:
                self._hat[i].direction = hat_states[i]
        else:
            logger.info("Could not reset vJoy device, is it in use by another app?")

    def used(self) -> None:
        """Updates the last-used timestamp."""
        self._last_active = time.time()

    def invalidate(self) -> None:
        """Releases all resources: resets device and closes the uinput node.
        
        On Linux the persistent vJoy device created at startup (joystick_gremlin.py)
        is managed by VJoyInterface and survives for the app lifetime. Calling
        RelinquishVJD here would destroy the /dev/uinput node during GremlinUi
        initialization (VJoyProxy.reset() → invalidate() → RelinquishVJD).
        The kernel cleans up all fd's at process exit; explicit release is
        required only on Windows to relinquish the COM-held lock.
        """
        if self.vjoy_id:
            try:
                self.reset()
            except Exception:
                pass
            # Cancel timer immediately to block any late-fire resets
            self._keep_alive_timer.cancel()
            # Double-check in case reset() already cleared or invalidated state
            if self.vjoy_id:
                # On Linux: do NOT destroy the uinput node. The device is
                # persistent (app-lifetime) and the kernel releases the fd
                # automatically when the process exits.
                if sys.platform.startswith("linux"):
                    self.vjoy_id = None
                else:
                    VJoyInterface.RelinquishVJD(self.vjoy_id)
                    self.vjoy_id = None

    def _keep_alive(self) -> None:
        """Timer callback that resets the device if idle."""
        if self._last_active + VJoy.keep_alive_timeout < time.time():
            self.reset()
        self._keep_alive_timer = threading.Timer(
            VJoy.keep_alive_timeout,
            self._keep_alive,
        )
        self._keep_alive_timer.start()

    def _init_axes(self) -> None:
        """Initialize all axes on this device."""
        for i, axis in enumerate(AxisName):
            if VJoyInterface.GetVJDAxisExist(self.vjoy_id, axis.value) > 0:  # type: ignore[arg-type]
                a = Axis(self, axis.value)
                self._axis[i + 1] = a
                self._axis_names[i + 1] = gremlin.common.AxisNames.to_string(
                    gremlin.common.AxisNames(i + 1)  # type: ignore[arg-type]
                )
                self._axis_lookup[len(self._axis_names)] = axis.value
                self._axis_lookup[axis.value] = i + 1

    def _init_buttons(self) -> None:
        """Initialize all buttons on this device."""
        for btn_id in range(1, VJoyInterface.GetVJDButtonNumber(self.vjoy_id) + 1):  # type: ignore[arg-type]
            self._button[btn_id] = Button(self, btn_id)

    def _init_hats(self) -> None:
        """Initialize all hats on this device."""
        hats: dict[int, Hat] = {}
        if VJoyInterface.GetVJDDiscPovNumber(self.vjoy_id) > 0:  # type: ignore[arg-type]
            msg = "vJoy is configured incorrectly. Hats must be 'Continuous' not '4 Directions'."
            logger.error(msg)
            raise VJoyError(msg)
        for hat_id in range(1, VJoyInterface.GetVJDContPovNumber(self.vjoy_id) + 1):  # type: ignore[arg-type]
            hats[hat_id] = Hat(self, hat_id, HatType.Continuous)
        self._hat = hats

    def __str__(self) -> str:
        return "vJoyId={} axis={} buttons={} hats={}".format(
            self.vjoy_id or -1,
            len(self._axis),
            len(self._button),
            len(self._hat),
        )


# ====================================
# Deadzone function (utility)
# ====================================

def deadzone(value: float, low: float, low_center: float, high_center: float, high: float) -> float:
    """Maps a raw value through a configurable deadzone.

    The following relationship between the limits has to hold:
    -1 <= low < low_center <= 0 <= high_center < high <= 1

    :param value: raw input value
    :param low: low deadzone limit
    :param low_center: lower center deadzone limit
    :param high_center: upper center deadzone limit
    :param high: high deadzone limit
    :return: corrected value
    """
    if value >= 0:
        return min(1, max(0, (value - high_center) / abs(high - high_center)))
    else:
        return max(-1, min(0, (value - low_center) / abs(low - low_center)))
