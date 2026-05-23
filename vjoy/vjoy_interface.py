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
import enum
import os
import sys


# ==================================================================
# VJoyState enum — pure enum, no external dependencies
# ==================================================================
class VJoyState(enum.Enum):

    """Enumeration of the possible VJoy device states."""

    Owned = 0       # The device is owned by the current application
    Free = 1        # The device is not owned by any application
    Bust = 2        # The device is owned by another application
    Missing = 3     # The device is not present
    Unknown = 4     # Unknown type of error


# ========================================================================
# Linux stub VJoyInterface class (no-op replacement for Windows DLL)
# ==========================================================================
class _StubVJoyInterface:
    """Stub implementation of VJoyInterface for Linux where the native DLL 
    is unavailable. Provides the same class-level API surface as the Windows
    version but all methods are no-ops.
    """
    
    api_functions = {
        "GetvJoyVersion": {"arguments": [], "returns": ctypes.c_short},
        "vJoyEnabled": {"arguments": [], "returns": ctypes.c_bool},
        "GetvJoyProductString": {"arguments": [], "returns": ctypes.c_wchar_p},
        "GetvJoyManufacturerString": {"arguments": [], "returns": ctypes.c_wchar_p},
        "GetvJoySerialNumberString": {"arguments": [], "returns": ctypes.c_wchar_p},
        "GetVJDButtonNumber": {"arguments": [ctypes.c_uint], "returns": ctypes.c_int},
        "GetVJDDiscPovNumber": {"arguments": [ctypes.c_uint], "returns": ctypes.c_int},
        "GetVJDContPovNumber": {"arguments": [ctypes.c_uint], "returns": ctypes.c_int},
        "GetVJDAxisExist": {"arguments": [ctypes.c_uint, ctypes.c_uint], "returns": ctypes.c_int},
        "GetVJDAxisMax": {"arguments": [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p], "returns": ctypes.c_bool},
        "GetVJDAxisMin": {"arguments": [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p], "returns": ctypes.c_bool},
        "GetOwnerPid": {"arguments": [ctypes.c_uint], "returns": ctypes.c_int},
        "AcquireVJD": {"arguments": [ctypes.c_uint], "returns": ctypes.c_bool},
        "RelinquishVJD": {"arguments": [ctypes.c_uint], "returns": None},
        "UpdateVJD": {"arguments": [ctypes.c_uint, ctypes.c_void_p], "returns": ctypes.c_bool},
        "GetVJDStatus": {"arguments": [ctypes.c_uint], "returns": ctypes.c_int},
        "ResetVJD": {"arguments": [ctypes.c_uint], "returns": ctypes.c_bool},
        "ResetAll": {"arguments": [], "returns": None},
        "ResetButtons": {"arguments": [ctypes.c_uint], "returns": ctypes.c_bool},
        "ResetPovs": {"arguments": [ctypes.c_uint], "returns": ctypes.c_bool},
        "SetAxis": {"arguments": [ctypes.c_long, ctypes.c_uint, ctypes.c_uint], "returns": ctypes.c_bool},
        "SetBtn": {"arguments": [ctypes.c_bool, ctypes.c_uint, ctypes.c_ubyte], "returns": ctypes.c_bool},
        "SetDiscPov": {"arguments": [ctypes.c_int, ctypes.c_uint, ctypes.c_ubyte], "returns": ctypes.c_bool},
        "SetContPov": {"arguments": [ctypes.c_ulong, ctypes.c_uint, ctypes.c_ubyte], "returns": ctypes.c_bool},
    }
    
    @classmethod
    def initialize(cls):
        """Initialize stub class (no-op on Linux)."""
        # Stub methods are class attributes that return stub values
        # matching the expected types for each Win32 function signature
        def _stub_int(*args: "Unpack[tuple[int, ...]]", **kwargs: "Unpack[dict[str, int]]") -> int:
            return 0
        
        def _stub_bool(*args: "Unpack[tuple[int, ...]]", **kwargs: "Unpack[dict[str, int]]") -> bool:
            return True
        
        def _stub_wchar(*args: "Unpack[tuple[int, ...]]", **kwargs: "Unpack[dict[str, int]]") -> str:
            return ""
        
        for fn_name, fn_params in cls.api_functions.items():
            ret_type = fn_params.get("returns", None)
            if ret_type is None:
                setattr(cls, fn_name, lambda *a: None)
            elif ret_type == ctypes.c_int or ret_type == ctypes.c_short:
                setattr(cls, fn_name, _stub_int)
            elif ret_type == ctypes.c_bool:
                setattr(cls, fn_name, _stub_bool)
            elif ret_type == ctypes.c_long:
                setattr(cls, fn_name, _stub_int)
            elif ret_type == ctypes.c_ubyte:
                setattr(cls, fn_name, _stub_int)
            elif ret_type == ctypes.c_wchar_p:
                setattr(cls, fn_name, _stub_wchar)
            else:
                setattr(cls, fn_name, _stub_int)


# ==================================================================================
# Try loading the Windows DLL, fall back to stub if it fails (Linux)
# =====================================================================================
_try_windows = True
_VJoyInterface: type | None = None

if _try_windows:
    GremlinError = None  # Will be set below if needed
    try:
        # Import needed locally to avoid circular dependency with gremlin.error
        from gremlin.error import GremlinError

        # ======================================================================================================
        # Windows VJoyInterface class (original code - preserved for reference)
        # =======================================================================================================
        class _WindowsVJoyInterface:

            """Allows low level interaction with VJoy devices via ctypes."""

            # Attempt to find the correct location of the dll for development
            # and installed use cases.
            dev_path = os.path.join(os.path.dirname(__file__), "vJoyInterface.dll")
            if os.path.isfile("vJoyInterface.dll"):
                dll_path = "vJoyInterface.dll"
            elif os.path.isfile(dev_path):
                dll_path = dev_path
            else:
                raise GremlinError("Unable to locate vjoy dll")

            vjoy_dll = ctypes.cdll.LoadLibrary(dll_path)

            # Declare argument and return types for all the functions
            # exposed by the dll
            api_functions = {
                # General vJoy information
                "GetvJoyVersion": {
                    "arguments": [],
                    "returns": ctypes.c_short
                },
                "vJoyEnabled": {
                    "arguments": [],
                    "returns": ctypes.c_bool
                },
                "GetvJoyProductString": {
                    "arguments": [],
                    "returns": ctypes.c_wchar_p
                },
                "GetvJoyManufacturerString": {
                    "arguments": [],
                    "returns": ctypes.c_wchar_p
                },
                "GetvJoySerialNumberString": {
                    "arguments": [],
                    "returns": ctypes.c_wchar_p
                },

                # Device properties
                "GetVJDButtonNumber": {
                    "arguments": [ctypes.c_uint],
                    "returns": ctypes.c_int
                },
                "GetVJDDiscPovNumber": {
                    "arguments": [ctypes.c_uint],
                    "returns": ctypes.c_int
                },
                "GetVJDContPovNumber": {
                    "arguments": [ctypes.c_uint],
                    "returns": ctypes.c_int
                },
                # API claims this should return a bool, however, this is untrue and
                # is an int, see:
                # http://vjoystick.sourceforge.net/site/index.php/forum/5-Discussion/1026-bug-with-getvjdaxisexist
                "GetVJDAxisExist": {
                    "arguments": [ctypes.c_uint, ctypes.c_uint],
                    "returns": ctypes.c_int
                },
                "GetVJDAxisMax": {
                    "arguments": [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p],
                    "returns": ctypes.c_bool
                },
                "GetVJDAxisMin": {
                    "arguments": [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p],
                    "returns": ctypes.c_bool
                },

                # Device management
                "GetOwnerPid": {
                    "arguments": [ctypes.c_uint],
                    "returns": ctypes.c_int
                },
                "AcquireVJD": {
                    "arguments": [ctypes.c_uint],
                    "returns": ctypes.c_bool
                },
                "RelinquishVJD": {
                    "arguments": [ctypes.c_uint],
                    "returns": None,
                },
                "UpdateVJD": {
                    "arguments": [ctypes.c_uint, ctypes.c_void_p],
                    "returns": ctypes.c_bool
                },
                "GetVJDStatus": {
                    "arguments": [ctypes.c_uint],
                    "returns": ctypes.c_int
                },

                # Reset functions
                "ResetVJD": {
                    "arguments": [ctypes.c_uint],
                    "returns": ctypes.c_bool
                },
                "ResetAll": {
                    "arguments": [],
                    "returns": None
                },
                "ResetButtons": {
                    "arguments": [ctypes.c_uint],
                    "returns": ctypes.c_bool
                },
                "ResetPovs": {
                    "arguments": [ctypes.c_uint],
                    "returns": ctypes.c_bool
                },

                # Set values
                "SetAxis": {
                    "arguments": [ctypes.c_long, ctypes.c_uint, ctypes.c_uint],
                    "returns": ctypes.c_bool
                },
                "SetBtn": {
                    "arguments": [ctypes.c_bool, ctypes.c_uint, ctypes.c_ubyte],
                    "returns": ctypes.c_bool
                },
                "SetDiscPov": {
                    "arguments": [ctypes.c_int, ctypes.c_uint, ctypes.c_ubyte],
                    "returns": ctypes.c_bool
                },
                "SetContPov": {
                    "arguments": [ctypes.c_ulong, ctypes.c_uint, ctypes.c_ubyte],
                    "returns": ctypes.c_bool
                },
            }

            @classmethod
            def initialize(cls):
                """Initializes the functions as class methods."""
                for fn_name, params in cls.api_functions.items():
                    dll_fn = getattr(cls.vjoy_dll, fn_name)
                    if "arguments" in params:
                        dll_fn.argtypes = params["arguments"]
                    if "returns" in params:
                        dll_fn.restype = params["returns"]
                    setattr(cls, fn_name, dll_fn)

        _VJoyInterface = _WindowsVJoyInterface
        _VJoyInterface.initialize()

    except Exception:
        # DLL loading failed (Linux or DLL missing on Windows) — use stub
        _VJoyInterface = _StubVJoyInterface
        _VJoyInterface.initialize()

# Fallback if even the GremlinError import fails
if _VJoyInterface is None:
    _VJoyInterface = _StubVJoyInterface
    _VJoyInterface.initialize()


# Public export
VJoyInterface = _VJoyInterface
