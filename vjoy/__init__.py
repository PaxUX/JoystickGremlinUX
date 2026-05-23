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

"""Platform-aware vJoy module loader.

On Windows, loads the native vJoyInterface.dll binding from the bundled
`vjoy/vjoy.py` submodule.

On Linux, loads the uinput-based backend from `vjoy_linux.vjoy`, making the
high-level API (`VJoy`, `Axis`, `Button`, `Hat`, etc.) available via the same
`vjoy` import path as the Windows version.

Exposes both `vjoy.vjoy` and `vjoy.vjoy_interface` in sys.modules so that
`from vjoy import vjoy` and `from vjoy import vjoy_interface` all resolve
to the Linux backends.

Exports a local ``VJoyError`` to break the circular-import cycle:
    vjoy → gremlin → gremlin.error → vjoy.vjoy_interface → gremlin.error  (deadlock)

By defining it here, ``vjoy.*`` no longer needs any ``gremlin.*`` imports.

Exported names:
    vjoy   — The high-level vJoy module (same API on both platforms)
    vjoy_interface — The low-level interface module (same API on both platforms)
    VJoyError — Exception exposed for action-plugins that need it
"""

import sys
import os


# Local error class — defined here to avoid circular import through gremlin.*
class VJoyError(Exception):
    """Exception raised when an error occurs within the vJoy module."""
    def __init__(self, value):
        self.value = value


if sys.platform.startswith("linux"):
    # Linux: redirect all vJoy symbols through vjoy_linux
    import logging as _logging

    # Import Linux backends
    from vjoy_linux import vjoy_interface as _vjoy_interface_linux
    from vjoy_linux import vjoy as _vjoy_linux

    # CRITICAL: Register Linux modules under the vjoy namespace in sys.modules
    # This allows "from vjoy import vjoy" and "from vjoy import vjoy_interface"
    # to resolve to the Linux backends, not the Windows files on disk.
    sys.modules["vjoy.vjoy_interface"] = _vjoy_interface_linux
    sys.modules["vjoy.vjoy"] = _vjoy_linux

    # Make the module attributes available at module level so that
    # gremlin/joystick_handling.py can do: from vjoy import vjoy, vjoy_interface
    vjoy_interface = _vjoy_interface_linux
    vjoy = _vjoy_linux

    # Re-export VJoyError so action_plugins can import it here instead of via gremlin
    vjoy_interface.VJoyError = VJoyError

    # Patch gremlin.error if it's already loaded — ensures any gremlin code
    # that imported VJoyError before now points to *our* VJoyError.
    try:
        import gremlin.error  # may be partially initialized
        gremlin.error.VJoyError = VJoyError  # type: ignore[attr-defined]
    except Exception:
        _logging.getLogger("system").debug(
            "vjoy: gremlin.error not ready yet — VJoyError patched after initialization"
        )

    # If gremlin.joystick_handling has loaded vjoy as its own module, patch it too
    try:
        import gremlin.joystick_handling  # may be partially initialized
        gremlin.joystick_handling.VJoyError = VJoyError  # type: ignore[attr-defined]
    except Exception:
        pass

else:
    # Windows: load the native DLL binding from vJoyInterface.dll
    import os
    from gremlin.error import GremlinError

    dev_path = os.path.join(os.path.dirname(__file__), "vJoyInterface.dll")
    if os.path.isfile("vJoyInterface.dll"):
        dll_path = "vJoyInterface.dll"
    elif os.path.isfile(dev_path):
        dll_path = dev_path
    else:
        raise GremlinError("Unable to locate vJoy Interface DLL")

    vjoy_interface_dll = __import__("ctypes").cdll.LoadLibrary(dll_path)

    from vjoy.vjoy_interface import _GUID
    from vjoy.vjoy_interface import VJoyError
    from vjoy.vjoy_interface import VJoyInterface

    # Patch VJoyState with the Windows vJoy interface's native VJoyState
    from vjoy.vjoy_interface import VJoyState

    # Patch vJoyInterface's VJoyError with our local exception class
    VJoyInterface.VJoyError = VJoyError

    _VJoyState = VJoyState