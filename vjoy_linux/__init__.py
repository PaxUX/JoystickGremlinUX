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

"""Linux vJoy — virtual joystick output via /dev/uinput.

This module replaces the Windows vJoyInterface.dll with a Linux uinput-based
backend. It provides the same high-level API for Gremlin's action plugins:
VJoy, Axis, Button, Hat classes.
"""

# Define VJoyError locally to avoid circular import through gremlin.error
class VJoyError(Exception):
    """Exception raised when an error occurs within the vJoy module."""
    def __init__(self, value):
        self.value = value

from vjoy_linux.vjoy_interface import VJoyInterface, VJoyState

# Patch the interface module's VJoyError so it uses our local class
from vjoy_linux import vjoy_interface
vjoy_interface.VJoyError = VJoyError

__all__ = [
    "VJoyInterface",
    "VJoyState",
    "VJoyError",
]
