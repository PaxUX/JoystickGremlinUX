# -*- coding: utf-8; -*-
from __future__ import annotations

"""
Linux DILL backend - replaces the Windows DILL DLL with real evdev-based
controller detection and polling.

v1.5 - Good performance increase

Uses the evdev library to enumerate /dev/input/js* joystick devices
and their associated event nodes, poll their axis/button/hat state,
and fire callbacks when values change.
"""

import ctypes
import hashlib
import importlib
import logging
import os
import queue
import select
import struct
import sys
import threading
import time

import evdev
from evdev import ecodes as e

# Reverse lookup: integer → string name for evdev event types and codes
_ecodes_int_to_str = {v: k for k, v in e.ecodes.items()}
# ecodes_groups was removed in newer evdev versions; use e.ecodes directly.
# _ecodes_type_codes was for debugging only and is no longer referenced.
_ecodes_type_codes: dict[int, dict[int, str]] = {}
for k, v in e.ecodes.items():
    evtype = v >> 16  # high bits contain the event type
    if evtype not in _ecodes_type_codes:
        _ecodes_type_codes[evtype] = {}
    _ecodes_type_codes[evtype][v & 0xFFFF] = k  # lower bits are the code

# Import DILL base types from the parent package
_dill = importlib.import_module("dill")
_GUID = _dill._GUID
_GUID_Virtual = _dill._GUID_Virtual
GUID = _dill.GUID
_AxisMap = _dill._AxisMap
_DeviceSummaryCType = _dill._DeviceSummary
_JoystickInputData = _dill._JoystickInputData
_C_EVENT_CALLBACK = _dill.C_EVENT_CALLBACK
_C_DEVICE_CHANGE_CALLBACK = _dill.C_DEVICE_CHANGE_CALLBACK
_DWORD = ctypes.c_ulong

logger = logging.getLogger("system")
# Logging is configured by joystick_gremlin.py's configure_logger().
# We rely on the "system" logger's handlers being set up there.
# --verbose / -v enables DEBUG output on console; default is WARNING.

# Mapping from evdev ABS_ codes to DirectInput axis indices
# Xbox controllers use ABS_Z as LT (index 3) and ABS_RZ as RT (index 6)
# Flight sticks may use ABS_THROTTLE/RUDDER/GAS for additional axes
# NOTE: Hat buttons (ABS_HAT*) are NOT included here - they use separate hat handling
_ABS_TO_DINPUT_AXIS: dict[int, int] = {
# Xbox-style triggers and standard axes
    e.ABS_X:         1,   # X axis
    e.ABS_Y:         2,   # Y axis
    e.ABS_Z:         3,   # Front trigger (LT)
    e.ABS_RX:        4,   # X rotation axis
    e.ABS_RY:        5,   # Y rotation axis
    e.ABS_RZ:        6,   # Rear trigger (RT)
    e.ABS_RUDDER:    7,   # Rudder / Additional axis
    e.ABS_GAS:       8,   # Gas / Throttle / Additional axis
}

# Trigger axis names for UI display (maps DI index → human-readable name)
_TRIGGER_AXIS_NAMES: dict[int, str] = {
    3: "LT",      # ABS_Z (Xbox front trigger)
    6: "RT",      # ABS_RZ (Xbox rear trigger)
    7: "Rudder",  # ABS_RUDDER
    8: "Gas",     # ABS_GAS
    9: "Brake",   # ABS_BRAKE
    10: "Throttle",  # ABS_THROTTLE
    11: "LTRig",    # ABS_SL0 (flight stick)
    12: "RTRig",    #ABS_SL1 (flight stick)
}

# Set of trigger ABS code names that are 8-bit (0..255) or 10-bit (0..1023)
# not the standard ±16-bit (−32768..32767) range.
_TRIGGER_ABS_CODES: frozenset[int] = frozenset((e.ABS_Z, e.ABS_RZ, e.ABS_GAS, e.ABS_THROTTLE, e.ABS_BRAKE))


# Map evdev key codes to DirectInput button indices (1-based)
# Kept as-is for Windows compatibility reference; not used on Linux.
# linux_button_map: per-device {evdev_code -> sequential button_index}
#
# Linux button mapping: Pure sequential per-device. Button 1 = first button
# encountered, button 2 = second, etc., mapped purely by evdev code order
# to keep it logical, stable, and simple. No hardcoded conventions — just
# sequential 1-based indices per device, limited to 127 (arbitrary cap).
#
# Why sequential over Xbox convention?
#   - Mixed semantics is confusing: "Is button A index 1 or 4?"
#   - Index instability: unplugging/replugging shifts buttons
#   - Simpler for UI: "button 1" always means the first button
_KEY_TO_DINPUT_BUTTON: dict[int, int] = {}

# Common gamepad button mapping (Xbox-style, maps to DirectInput indices)
# Kept as-is for Windows compatibility reference; not used on Linux.
# _DINPUT_BUTTON_CODES = [
#     e.BTN_SOUTH,          # A / Cross         -> 1
#     e.BTN_EAST,           # B / Circle         -> 2
#     e.BTN_WEST,           # X / Square         -> 3
#     e.BTN_NORTH,          # Y / Triangle       -> 4
#     e.BTN_TL,             # L1                  -> 5
#     e.BTN_TR,             # R1                  -> 6
#     e.BTN_SELECT,         # Share               -> 7
#     e.BTN_START,          # Options             -> 8
#     e.BTN_THUMBL,         # L3                  -> 9
#     e.BTN_THUMBR,         # R3                  -> 10
#     e.BTN_TL2,            # L2                  -> 11
#     e.BTN_TR2,            # R2                  -> 12
#     e.BTN_MODE,           # Mic/Push            -> 13
#     e.BTN_GAMEPAD,        # PlayStation/Xbox     -> 14
#     e.BTN_TRIGGER_HAPPY1, # Extra triggers      -> 15
#     e.BTN_TRIGGER_HAPPY2, # Extra triggers      -> 16
#     e.BTN_TRIGGER_HAPPY3, # Extra triggers      -> 17
#     e.BTN_TRIGGER_HAPPY4, # Extra triggers      -> 18
#     e.BTN_TRIGGER_HAPPY5, # Extra triggers      -> 19
# ]
#
# for i, btn in enumerate(_DINPUT_BUTTON_CODES, start=1):
#     _KEY_TO_DINPUT_BUTTON[btn] = i


# ---------------------------------------------------------------------------
# Legacy GUID generation — preserved for reference during Linux migration.
# OLD seed:  f"{device_path}:{device_name}"   ← /dev/input/event{N} is
#            unconditionally unstable across replug/boot cycles.
# ---------------------------------------------------------------------------
# def _generate_guid(device_path: str, device_name: str) -> _dill.GUID:
#     """Generate a GUID for a Linux input device."""
#     import hashlib
#     seed = f"{device_path}:{device_name}".encode("utf-8")
#     raw = hashlib.sha1(seed).digest()
#
#     guid_struct = _GUID()
#     guid_struct.Data1 = int.from_bytes(raw[0:4], "little")
#     guid_struct.Data2 = int.from_bytes(raw[4:6], "little")
#     guid_struct.Data3 = int.from_bytes(raw[6:8], "little")
#     guid_struct.Data4[0] = raw[8]
#     guid_struct.Data4[1] = raw[9]
#     guid_struct.Data4[2] = raw[10]
#     guid_struct.Data4[3] = raw[11]
#     guid_struct.Data4[4] = raw[12]
#     guid_struct.Data4[5] = raw[13]
#     guid_struct.Data4[6] = raw[14]
#     guid_struct.Data4[7] = raw[15]
#     return _dill.GUID(guid_struct)


def _generate_guid(
    bus: str,
    vendor: int,
    product: int,
    device_name: str,
    button_count: int,
    device_index: str = "",
) -> _dill.GUID:
    """Generate a stable GUID for a Linux input device.

    Uses hardware-determined identity fields that survive replug/boot cycles.

    Seed components (in order of stability):
      1. Bus type (0003=USB, 0005=Bluetooth, 0001=ISA, etc.)
      2. USB Vendor ID (16-bit, e.g. 0x2341 for Arduino)
      3. USB Product ID (16-bit, e.g. 0x8036 for Leonardo)
      4. Device name from USB descriptor (stable per firmware)
      5. Total button count (structural property — doesn't change without
         hardware/feature-report change)
      6. device_index — additional unique identifier when needed
         (e.g. event node path for masquerading vJoy devices that share 
         vid:pid, or js number for uinput bus devices)

    Two devices with the same vid:pid but different button layouts will get
    different GUIDs. Masquerading vJoy devices with the same vid:pid but
    different event nodes also get different GUIDs via device_index.
    """
    import hashlib

            # Seed example: "0019:0000:0000:vJoy Linux:32:/dev/uinput"
    # (32 buttons, device_index=/dev/input/event17)
#PaxUX This needs to change do no use device index as it can change from seasion to seasion.
    seed = (
        #f"{bus}:{vendor:04x}:{product:04x}:{device_name}:{button_count}:{device_index}" ## <<---- remove device index
        f"{bus}:{vendor:04x}:{product:04x}:{device_name}:{button_count}" 
    ).encode("utf-8")
    raw = hashlib.sha1(seed).digest()

    guid_struct = _GUID()
    guid_struct.Data1 = int.from_bytes(raw[0:4], "little")
    guid_struct.Data2 = int.from_bytes(raw[4:6], "little")
    guid_struct.Data3 = int.from_bytes(raw[6:8], "little")
    guid_struct.Data4[0] = raw[8]
    guid_struct.Data4[1] = raw[9]
    guid_struct.Data4[2] = raw[10]
    guid_struct.Data4[3] = raw[11]
    guid_struct.Data4[4] = raw[12]
    guid_struct.Data4[5] = raw[13]
    guid_struct.Data4[6] = raw[14]
    guid_struct.Data4[7] = raw[15]
    return _dill.GUID(guid_struct)


def _create_device_summary(device_guid: _GUID, vendor: int, product: int, 
                          name: str, axis_count: int, button_count: int, 
                          hat_count: int, axis_map: list) -> _dill.DeviceSummary:
    """Create a ctypes DeviceSummary structure for a discovered device."""
    dbus_guid = device_guid.ctypes
    
    # vJoy's _AxisMap is fixed at 8 entries (matching DINPUT convention)
    # Cap axis_count to the actual mapping array size to prevent OOB in _init_axes().
    MAX_AXIS_MAP = 8
    capped_axis_count = min(axis_count, MAX_AXIS_MAP)
    
    # Build axis map array (up to 8 entries)
    axis_map_arr = (_AxisMap * MAX_AXIS_MAP)()
    for i in range(min(MAX_AXIS_MAP, len(axis_map))):
        axis_map_arr[i].linear_index = axis_map[i].linear_index
        axis_map_arr[i].axis_index = axis_map[i].axis_index
    for i in range(len(axis_map), MAX_AXIS_MAP):
        axis_map_arr[i].linear_index = 0
        axis_map_arr[i].axis_index = 0
    
    # Create the ctypes structure
    ctypes_summary = _DeviceSummaryCType()
    ctypes_summary.device_guid = dbus_guid
    ctypes_summary.vendor_id = ctypes.c_ulong(vendor)
    ctypes_summary.product_id = ctypes.c_ulong(product)
    ctypes_summary.joystick_id = ctypes.c_ulong(0)
    ctypes_summary.name = name.encode("utf-8")[:255].ljust(256, b'\x00')
    ctypes_summary.axis_count = ctypes.c_ulong(capped_axis_count)
    ctypes_summary.button_count = ctypes.c_ulong(button_count)
    ctypes_summary.hat_count = ctypes.c_ulong(hat_count)
    ctypes_summary.axis_map = axis_map_arr
    
    return _dill.DeviceSummary(ctypes_summary)


def _parse_proc_devices() -> dict:
    """Parse /proc/bus/input/devices to map jsX -> metadata.
    
    Returns a dict: {js_num: {'bus', 'vendor', 'product', 'name', 'event_node'}}
    """
    mappings = {}  # {'jsN': {'name': str, 'vendor': int, 'product': int}}
    try:
        content = open("/proc/bus/input/devices").read()
    except FileNotFoundError:
        logger.warning("Linux DILL: /proc/bus/input/devices not found")
        return mappings
    
    current_bus = ""
    current_vendor = 0
    current_product = 0
    current_name = "Unknown"
    current_event_node: str | None = None
    
    for line in content.splitlines():
        if line.startswith("I:"):  # Bus=xxx Vendor=xxx Product=xxx Version=xxx
            # Format: "I: Bus=0005 Vendor=054c Product=09cc Version=0705"
            parts = line[2:].strip().split()
            for part in parts:
                if part.startswith("Bus="):
                    current_bus = part[4:]
                elif part.startswith("Vendor="):
                    current_vendor = int(part[7:], 16)
                elif part.startswith("Product="):
                    current_product = int(part[8:], 16)
        elif line.startswith("N:"):  # Name
            current_name = line.split('"')[1]
        elif line.startswith("H:"):  # Handlers
            # Format: "H: Handlers=event16 js0"
            # Extract after "H: " to get "Handlers=event16 js0"
            handlers_line = line[3:].strip()
            # Split off "Handlers=" prefix
            if '=' in handlers_line:
                handlers_part = handlers_line.split('=', 1)[1]
            else:
                handlers_part = handlers_line
            handlers = handlers_part.split()
            
            # Extract event node for opening with evdev
            for h in handlers:
                if h.startswith("event"):
                    event_num = int(h[5:])
                    current_event_node = f"/dev/input/event{event_num}"
            
            # Extract js numbers for tracking
            for h in handlers:
                if h.startswith("js"):
                    js_num = int(h[2:])
                    mappings[js_num] = {
                        "bus": current_bus,
                        "vendor": current_vendor,
                        "product": current_product,
                        "name": current_name,
                        "event_node": current_event_node,  # Key addition!
                    }
    return mappings


class _LinuxDILL:
    """Linux DILL backend using evdev to poll physical gamepads.
    
    Provides the public DILL API surface implemented via evdev.
    
    IMPORTANT: Cannot open /dev/input/js* nodes directly - they are read-only
    for user space. Instead, open the corresponding event node from
    /proc/bus/input/devices (e.g., event16 for js0).
    """
    
    _device_map: dict[_dill.GUID, _dill.DeviceSummary] = {}
    _device_events: dict[_dill.GUID, evdev.InputDevice] = {}
    _device_state: dict[_dill.GUID, dict[int, int]] = {}  # {guid: {code: value}}
    _prev_state: dict[_dill.GUID, dict[int, int]] = {}
    _polling_thread: threading.Thread | None = None
    _disp_thread: threading.Thread | None = None
    _running: bool = False
    _state_lock = threading.Lock()  # Protects concurrent access to _device_map/_device_events/etc.
    
    input_event_callback_fn = None  # C_EVENT_CALLBACK
    device_change_callback_fn = None  # C_DEVICE_CHANGE_CALLBACK
    
    # Per-device button code mapping: {guid: {evdev_key_code: dinput_1based_index}}
    _btn_code_map: dict[_dill.GUID, dict[int, int]] = {}
    # Per-device reverse button index (dinput_1based → evdev code)
    _btn_index_map: dict[_dill.GUID, dict[int, int]] = {}
    # Per-device axis ABS range metadata: {guid: {abs_code: (abs_min, abs_max)}}
    # Captured from evdev InputDevice.capabilities() AbsInfo at discovery time.
    # Used at the event dispatch boundary to scale non-16-bit axes (triggers 0..1023,
    # 0..255 etc.) into the standard -32768..32767 number space expected downstream.
    _axis_abs_ranges: dict[_dill.GUID, dict[int, tuple[int, int]]] = {}
    
    # Maps masquerade event GUIDs (0x045E/0x028E) to injected vJoy GUIDs (0x1234/0xBEAD)
    # This ensures events from the actual uinput device are routed to the vJoy virtual device
    # so they appear in the correct UI tab (vJoy tab, not physical device tab).
    _masquerade_to_vjoy_map: dict = {}

    # Tracks previous hat millidegree values to avoid spurious re-fires when both
    # X and Y axis events fire for the same hat direction.
    _prev_hat_val: dict[tuple[int, int], int] = {}

    # GIL-unblock state: Non-blocking FDs, shared poll object, and event queue
    _evq = queue.Queue(maxsize=8192)  # (guid, ev_type, code, val, sec, usec)
    _poll: select.poll | None = None
    _poll_lock = threading.Lock()
    _fd_to_guid: dict[int, _dill.GUID] = {}  # os_fd -> guid
    _guid_to_fd: dict[_dill.GUID, int] = {}  # guid -> os_fd
    
    api_functions = {
        "init": {
            "arguments": [],
            "returns": None,
        },
        "set_input_event_callback": {
            "arguments": [_C_EVENT_CALLBACK],
            "returns": None,
        },
        "set_device_change_callback": {
            "arguments": [_C_DEVICE_CHANGE_CALLBACK],
            "returns": None,
        },
        "get_device_information_by_index": {
            "arguments": [ctypes.c_uint],
            "returns": _DeviceSummaryCType,
        },
        "get_device_information_by_guid": {
            "arguments": [_GUID],
            "returns": _DeviceSummaryCType,
        },
        "get_device_count": {
            "arguments": [],
            "returns": ctypes.c_uint,
        },
        "device_exists": {
            "arguments": [_GUID],
            "returns": ctypes.c_bool,
        },
        "get_axis": {
            "arguments": [_GUID, ctypes.c_uint],
            "returns": ctypes.c_long,
        },
        "get_button": {
            "arguments": [_GUID, ctypes.c_uint],
            "returns": ctypes.c_bool,
        },
        "get_hat": {
            "arguments": [_GUID, ctypes.c_uint],
            "returns": ctypes.c_long,
        },
        "get_device_name": {
            "arguments": [_GUID],
            "returns": ctypes.c_char_p,
        },
    }
    
    # ---- Public API ----
    
    @staticmethod
    def init():
        """Initialize the Linux input subsystem."""
        logger.info("Linux DILL backend initializing...")
        _LinuxDILL._running = True
        
        # Discover initial devices
        _LinuxDILL._discover_devices()
        
        # Start polling thread
        _LinuxDILL._polling_thread = threading.Thread(
            target=_LinuxDILL._poll_loop,
            daemon=True,
        )
        _LinuxDILL._polling_thread.start()
        
        # v2 GIL-unblock: start dispatcher thread
        _LinuxDILL._disp_thread = threading.Thread(
            target=_LinuxDILL._event_dispatcher,
            daemon=True,
            name="DILL-disp",
        )
        _LinuxDILL._disp_thread.start()
        
        # Mark as initialized — startup events will be silently ignored
        _LinuxDILL._initialized = True
        logger.info(
            "Linux DILL backend initialized — %d devices detected",
            len(_LinuxDILL._device_map),
        )
    
    @staticmethod
    def set_input_event_callback(callback):
        """Register the callback for input events."""
        _LinuxDILL.input_event_callback_fn = _C_EVENT_CALLBACK(callback)
    
    @staticmethod
    def set_device_change_callback(callback):
        """Register the callback for device plug/unplug events."""
        _LinuxDILL.device_change_callback_fn = _C_DEVICE_CHANGE_CALLBACK(callback)
    
    @staticmethod
    def get_device_count() -> int:
        """Return the number of joystick devices currently connected."""
        return len(_LinuxDILL._device_map)
    
    @staticmethod
    def get_device_information_by_index(index: int):
        """Get device info for device at given index (0-based).

        Parameters
        ----------
        index : int
            0-based device index

        Returns
        -------
        DeviceSummary
            Device information for the requested device

        Raises
        ------
        IndexError
            If the index is out of bounds
        """
        devices = sorted(
            _LinuxDILL._device_map.values(),
            key=lambda d: str(d.device_guid),
        )
        if 0 <= index < len(devices):
            return devices[index]
        raise IndexError(
            f"Device index {index} out of range (device_count={len(devices)})"
        )
    
    @staticmethod
    def get_device_information_by_guid(guid):
        """Get device info for a specific GUID.

        Parameters
        ----------
        guid : GUID
            Device GUID to look up

        Returns
        -------
        DeviceSummary | None
            Device information if found, None otherwise
        """
        if not hasattr(guid, "ctypes"):
            return None
        return _LinuxDILL._device_map.get(guid)
    
    @staticmethod
    def get_axis(guid, index: int) -> int:
        """Get the current raw axis value for a device.
        
        Parameters
        ----------
        guid : GUID
            Device GUID
        index : int
            DirectInput axis index (1-based)
            
        Returns
        ----- --
        int
            Raw value (typically -32768 to 32767), or 0 if not found
        """
        state = _LinuxDILL._device_state.get(guid)
        if state:
            for evdev_code, value in state.items():
                if _ABS_TO_DINPUT_AXIS.get(evdev_code) == index:
                    # Apply the same trigger scaling so polling returns the same
                    # -32768..32767 range as event dispatch.
                    device_ranges = _LinuxDILL._axis_abs_ranges.get(guid, {})
                    abs_min, abs_max = device_ranges.get(evdev_code, (-32768, 32767))
                    if abs_max - abs_min < 32768:
                        scaled = int((value - abs_min) * 32767 / (abs_max - abs_min))
                        return max(0, min(32767, scaled))
                    return value
        return 0
    
    @staticmethod
    def get_button(guid, index: int) -> bool:
        """Get the current state of a button for a device.
        
        Parameters
        ----------
        guid : GUID
            Device GUID
        index : int
            DirectInput button index (1-based)
            
        Returns
        -----
        bool
            True if the button is pressed
        """
        reverse = _LinuxDILL._btn_index_map.get(guid, {})
        evdev_code = reverse.get(index)
        if evdev_code is None:
            return False
        state = _LinuxDILL._device_state.get(guid)
        if state:
            return state.get(evdev_code, 0) != 0
        return False
    
    @staticmethod
    def get_hat(guid, index: int) -> int:
        """Get the hat/dpad state for a device.
        
        Uses DirectInput angle mapping in 4500-unit increments:
        4500=center, 0=up, 9000=right, 18000=down, 27000=left
        
        Parameters
        ----------
        guid : GUID
            Device GUID
        index : int
            Hat index (1-based)
            
        Returns
        ----- --
        int
            Hat state angle in 4500-unit increments, or -32768 if not found
        """
        state = _LinuxDILL._device_state.get(guid)
        if state:
            hat_x_code = e.ABS_HAT0X + (index - 1) * 2
            hat_y_code = e.ABS_HAT0Y + (index - 1) * 2
            x_raw = state.get(hat_x_code, 0)
            y_raw = state.get(hat_y_code, 0)
            
            # Normalize to {-1, 0, +1} — handle both evdev hats ({-1, 0, 1})
            # and vJoy hats ([-32768, 32767] from raw cos/sin).
            x = 1 if x_raw > 0 else (-1 if x_raw < 0 else 0)
            y = 1 if y_raw > 0 else (-1 if y_raw < 0 else 0)
            
            return _hat_x_y_to_millidegrees(x, y)
        return 4500  # center (neutral/default when hat not available)
    
    @staticmethod
    def get_device_name(guid) -> str:
        """Get the human-readable device name."""
        info = _LinuxDILL._device_map.get(guid)
        if info is None:
            return ""
        return info.name
    
    @staticmethod
    def device_exists(guid) -> bool:
        """Check if a device with the given GUID exists."""
        if guid is None:
            return False
        return guid in _LinuxDILL._device_map
    
    @staticmethod
    def initialize():
        """Initialize class methods with proper argument types and return types."""
        for fn_name, params in _LinuxDILL.api_functions.items():
            fn = getattr(_LinuxDILL, fn_name)
            if fn_name.startswith("_"):
                continue  # Skip internal methods
            if "arguments" in params:
                fn.argtypes = params["arguments"]
            if "returns" in params:
                fn.restype = params["returns"]
            else:
                fn.restype = None
    
    @staticmethod
    def _vjoy_guid_for(vjd_id: int):
        """Return a raw ctypes _GUID (not the GUID wrapper) for a vJoy device.
        
        Uses VJoyInterface's registry to ensure the GUID remains stable
        across DILL discovery cycles and prevents "ghost device" disappearance
        when GremlinUi triggers a device rescan.
        """
        import copy as _copy  # noqa: local import
        import vjoy_linux.vjoy_interface as _vjoy_interface
        
        try:
            VJoyInterface = _vjoy_interface.VJoyInterface
        except ImportError:
            return None

        if not hasattr(VJoyInterface, '_vjoy_guid_registry'):
            VJoyInterface._vjoy_guid_registry = {}

        # Return a fresh deep-copy of the cached raw ctypes _GUID.
        # Callers can safely modify the returned object since callers get their own copy.
        if vjd_id in VJoyInterface._vjoy_guid_registry:
            return _copy.deepcopy(VJoyInterface._vjoy_guid_registry[vjd_id])

        # Generate stable GUID using only the vJoy ID (not axis_count).
        seed = f"vJoy:{vjd_id}"
        raw = hashlib.new("sha1", seed.encode("utf-8")).digest()

        # Clone the base ctypes _GUID struct (NOT calling _GUID_Virtual — it's an instance, not a class).
        guid_base = _copy.deepcopy(_GUID_Virtual)
        guid_base.Data1 = int.from_bytes(raw[0:4], "little")
        guid_base.Data2 = int.from_bytes(raw[4:6], "little")
        guid_base.Data3 = int.from_bytes(raw[6:8], "little")
        guid_base.Data4[0] = raw[8]
        guid_base.Data4[1] = raw[9]
        guid_base.Data4[2] = raw[10]
        guid_base.Data4[3] = raw[11]
        guid_base.Data4[4] = raw[12]
        guid_base.Data4[5] = raw[13]
        guid_base.Data4[6] = raw[14]
        guid_base.Data4[7] = raw[15]

        # Store the raw ctypes _GUID (the Python GUID wrapper wraps this, but we store the raw one).
        VJoyInterface._vjoy_guid_registry[vjd_id] = guid_base
        return guid_base

    # ---- Internal Methods ----
    
    @staticmethod
    def _discover_devices():
        """Enumerate all physical joystick devices on Linux.
        
        CRITICAL FIX: /dev/input/js* nodes CANNOT be opened directly with
        evdev.InputDevice (they return EINVAL). Instead, open the
        corresponding event node from /proc/bus/input/devices.
        
        Uses /proc/bus/input/devices to map jsX -> eventY + metadata.
        Populates _device_map and _device_events.
        """
        # Get jsX -> metadata mappings from /proc/bus/input/devices
        proc_mappings = _parse_proc_devices()
        discovered: dict[_dill.GUID, _dill.DeviceSummary] = {}
        
        for js_num in sorted(proc_mappings.keys()):
            meta = proc_mappings[js_num]
            event_node = meta.get('event_node')  # Key: use event node, not js node!
            
            if not event_node or not os.path.exists(event_node):
                logger.warning(
                    "Linux DILL: No event node for js%d — skipping", js_num
                )
                continue
            
            # Skip pure virtual devices (not masquerade). Only allow masquerade (0x045E/0x028E)
            # through so that DILL can poll its events and route them to the vJoy alias.
            # Real Xbox controllers also use 0x045E/0x028E but appear on bus=0003 (USB) or
            # bus=0005 (Bluetooth), while vJoy masquerade always uses bus=0019 (uinput).
            if (meta.get('bus') == '0019'
                    and meta.get('vendor') == 0x1234
                    and meta.get('product') == 0xBEAD):
                logger.info(
                    "Linux DILL: skipping pure virtual vJoy device js%d (%s)", js_num, meta.get('name', 'unknown')
                )
                continue
            
            # CRITICAL FIX: Open the event node, NOT the js node
            try:
                dev = evdev.InputDevice(event_node)
            except (PermissionError, FileNotFoundError, OSError) as exc:
                logger.warning("Linux DILL: Cannot open %s — %s", event_node, exc)
                continue
            
            # Check if device has any joystick capability (axes OR buttons)
            caps = dev.capabilities()
            if e.EV_ABS not in caps and e.EV_KEY not in caps:
                continue
            
            logger.info(
                "Linux DILL: discovered js%d (via %s) = %s",
                js_num, event_node, dev.name
            )
            
            # Collect ABS/KEY codes from capabilities
            # caps.get(e.EV_ABS, ...) returns list of (code, AbsInfo) tuples
            # caps.get(e.EV_KEY, ...) returns list of ints directly
            # Capture per-device axis ABS ranges from evdev AbsInfo.
            # Each tuple is (abs_min, abs_max) as reported by the hardware.
            # This is the only reliable way to distinguish a 10-bit trigger (0..1023)
            # from a standard 16-bit axis (-32768..32767).
            _axis_abs_ranges: dict[int, tuple[int, int]] = {}
            for code, info in caps.get(e.EV_ABS, []):
                _axis_abs_ranges[code] = (info.min, info.max)

            all_abs = sorted(set([code for code, _ in caps.get(e.EV_ABS, [])]))
            abs_codes = [
                code for code in all_abs
                if code not in (
                    e.ABS_HAT0X, e.ABS_HAT0Y, e.ABS_HAT1X, e.ABS_HAT1Y,
                    e.ABS_HAT2X, e.ABS_HAT2Y, e.ABS_HAT3X, e.ABS_HAT3Y
                )
            ]
            hat_codes = [
                code for code in all_abs
                if e.ABS_HAT0X <= code <= e.ABS_HAT3Y
            ]
            key_codes = sorted(caps.get(e.EV_KEY, []))
            
            num_axes = len(abs_codes)
            num_hats = len(hat_codes)
            # Count buttons: codes >= BTN_0 covers the full BTN_* range (256+)
            # This includes standard Xbox buttons, BTN_0–BTN_7, and extended codes (704+)
            num_buttons = len([k for k in key_codes if k >= e.BTN_0])

            logging.getLogger("system").info(
                "Linux DILL: js%d axes=%d hats=%d btns=%d | abs_ranges=%s",
                js_num, len(abs_codes), len(hat_codes), num_buttons, _axis_abs_ranges,
            )
            
            # Accept devices with either axes or buttons
            if num_axes == 0 and num_buttons == 0:
                continue
            
            # Generate GUID from stable hardware identity fields.
            # For masquerade devices (0x045E/0x028E), use the event node
            # as the unique identifier so each masquerade device gets its
            # own GUID (even though they share vid:pid/name).
            guid = _generate_guid(
                bus=meta.get("bus", "0000"),
                vendor=meta.get("vendor", 0),
                product=meta.get("product", 0),
                device_name=meta.get("name", dev.name),
                button_count=num_buttons,
                device_index=event_node or str(js_num),
            )
            
            # If device already exists, reuse existing info and mark for keeping
            if guid in _LinuxDILL._device_map:
                discovered[guid] = _LinuxDILL._device_map[guid]
                continue
            
            # Skip duplicate GUIDs within this discovery (shouldn't happen)
            if guid in discovered:
                continue
            
            # Build axis map
            axis_map_entries = []
            for linear_idx, abs_code in enumerate(abs_codes):
                di_index = _ABS_TO_DINPUT_AXIS.get(abs_code, linear_idx + 1)
                ax = _AxisMap()
                ax.linear_index = linear_idx
                ax.axis_index = di_index
                axis_map_entries.append(ax)
            
            # Build per-device button map: pure sequential by sorted evdev code.
            # Button 1 = first BTN_* code (lowest numeric value), button 2 = next, etc.
            # No hardcoded conventions — indices are purely positional per device.
            sorted_button_codes = sorted(k for k in key_codes if k >= e.BTN_0)
            _LinuxDILL._btn_code_map[guid] = {
                code: idx
                for idx, code in enumerate(sorted_button_codes, start=1)
            }
            # Reverse map for O(1) get_button() lookups: index → code
            _LinuxDILL._btn_index_map[guid] = {
                idx: code for code, idx in _LinuxDILL._btn_code_map[guid].items()
            }
            
            # Create a DeviceSummary
            info = _create_device_summary(
                device_guid=guid,
                vendor=meta.get('vendor', 0),
                product=meta.get('product', 0),
                name=meta.get('name', dev.name),
                axis_count=num_axes,
                button_count=num_buttons,
                hat_count=num_hats,
                axis_map=axis_map_entries,
            )
            
            discovered[guid] = info
            _LinuxDILL._device_events[guid] = dev  # Store the event node device
            _LinuxDILL._device_state[guid] = {}
            _LinuxDILL._axis_abs_ranges[guid] = _axis_abs_ranges

            # v2 GIL-unblock: also open a non-blocking FD for the poll loop
            try:
                fd = os.open(event_node, os.O_RDONLY | os.O_NONBLOCK)
                _LinuxDILL._guid_to_fd[guid] = fd
                _LinuxDILL._fd_to_guid[fd] = guid
                logger.info("  fd=%d (non-blocking) for %s", fd, dev.name)
            except OSError as exc:
                logger.error("Cannot open non-blocking FD for %s: %s. Device dropped!", dev.name, exc)
            
            logger.info(
                "Discovered joystick: %s (js%d via %s)",
                dev.name, js_num, event_node
            )
        
        # Remove old devices — use setdefault()/pop() to protect against
        # _poll_loop() holding a concurrent snapshot (both paths race on
        # the same _device_events/_device_state/_btn_code_map/_btn_index_map dicts).
        for guid in list(_LinuxDILL._device_map.keys()):
            if guid not in discovered:
                # --- FIX: Protect vJoy devices from DILL's garbage collection ---
                # vJoy devices are managed by VJoyInterface (uinput backend),
                # not by DILL's evdev poller. Without this filter, the vJoy device masquerading
                # as an Xbox controller (0x045E/0x028E) or native vJoy (0x1234/0xBEAD) causes
                # GUID mismatches between _device_map and _device_events, leading to KeyError
                # in the poll loop (device disappears from map but poll thread still has it).
                info = _LinuxDILL._device_map[guid]
                if info.is_virtual:
                    continue
                # -------------------------------------------------------
                
                logger.debug("Device removed: %s", info.name)
                _LinuxDILL._device_map.pop(guid, None)
                _LinuxDILL._device_events.pop(guid, None)
                _LinuxDILL._device_state.pop(guid, None)
                _LinuxDILL._btn_code_map.pop(guid, None)
                _LinuxDILL._btn_index_map.pop(guid, None)
                
                # v2 GIL-unblock: also close the non-blocking FD
                fd = _LinuxDILL._guid_to_fd.pop(guid, None)
                if fd is not None:
                    _LinuxDILL._fd_to_guid.pop(fd, None)
                    try:
                        os.close(fd)
                    except OSError:
                        pass
        
        # ---- Inject virtual vJoy devices created via uinput ----
        # vJoy devices exist before DILL scans but were skipped by the VID/PID
        # filter above. They are managed by VJoyInterface (uinput backend) and
        # must be manually registered so joystick_devices_initialization() can
        # match them against the gremlin device tabs.
        
        # Lazy import to avoid polluting the global namespace and to break the
        # circular-import chain that would otherwise happen when importing the
        # VJoyInterface at module load time (vjoy → gremlin → dill → vJoyInterface).
        try:
            from vjoy_linux.vjoy_interface import VJoyInterface, VJoyState as _VJoyState
            _HAS_VJOY_INTERFACE = True
        except ImportError:
            _HAS_VJOY_INTERFACE = False
        
        if _HAS_VJOY_INTERFACE:
            import copy as _copy
            _vjoy_guid = GUID(_GUID_Virtual)  # Use same base GUID as vJoy on Windows
            
            for vjd_id in range(1, 17):  # vJoy IDs are 1-based
                status = VJoyInterface.GetVJDStatus(vjd_id)
                if status != _VJoyState.Owned.value:
                    continue
                
                try:
                    axis_count = VJoyInterface.GetVJDAxisCount(vjd_id)
                except Exception:
                    axis_count = 0
                
                # Get the actual device name from /proc/bus/input/devices
                # vJoy devices now present with native VID 0x1234 (no masquerade).
                # Use case-insensitive match on /proc's lowercase hex vendor.
                vjoy_uinput_name = "vJoy Linux"  # default fallback
                try:
                    proc_content = open("/proc/bus/input/devices").read()
                    for line in proc_content.splitlines():
                        if line.startswith("I:") and "1234" in line.lower():
                            idx = proc_content.splitlines().index(line)
                            for line2 in proc_content.splitlines()[idx+1:idx+5]:
                                if line2.startswith("N:"):
                                    vjoy_uinput_name = line2.split('"')[1]
                                    break
                            break
                except Exception:
                    pass  # Fall back to default name
                
                # Get the GUID seed string from vJoyInterface (the source of truth).
                # vJoyInterface only provides the seed — DILL is responsible for
                # converting it to a ctypes _GUID instance to avoid circular imports.
                vjoy_guid_seed = VJoyInterface.GetVJoyGuid(vjd_id)
                if vjoy_guid_seed is None:
                    logger.error("VJoyInterface returned None for vJoy device #%d", vjd_id)
                    continue
                
                # Convert the seed string to a dill._GUID ctypes instance
                raw = hashlib.sha1(vjoy_guid_seed.encode("utf-8")).digest()
                guid_for_device = _copy.deepcopy(_GUID_Virtual)
                guid_for_device.Data1 = int.from_bytes(raw[0:4], "little")
                guid_for_device.Data2 = int.from_bytes(raw[4:6], "little")
                guid_for_device.Data3 = int.from_bytes(raw[6:8], "little")
                guid_for_device.Data4[0] = raw[8]
                guid_for_device.Data4[1] = raw[9]
                guid_for_device.Data4[2] = raw[10]
                guid_for_device.Data4[3] = raw[11]
                guid_for_device.Data4[4] = raw[12]
                guid_for_device.Data4[5] = raw[13]
                                
                guid_for_device.Data4[6] = raw[14]
                guid_for_device.Data4[7] = raw[15]
                    
                vjoy_guid = GUID(guid_for_device)
                
                # Avoid duplicates — if same vjd_id was already added (e.g. re-discovery), keep it
                if vjoy_guid in _LinuxDILL._device_map:
                    continue
                
                # Build axis map for this vJoy device
                _axis_map_entries = []
                for linear_idx in range(1, axis_count + 1):
                    ax = _AxisMap()
                    ax.linear_index = linear_idx - 1
                    ax.axis_index = linear_idx
                    _axis_map_entries.append(ax)
                
                info = _create_device_summary(
                    device_guid=vjoy_guid,
                    vendor=0x1234,
                    product=0xBEAD,
                    name=vjoy_uinput_name,
                    axis_count=axis_count,
                    button_count=VJoyInterface.GetVJDButtonNumber(vjd_id),
                    hat_count=VJoyInterface.GetVJDContPovNumber(vjd_id),
                    axis_map=_axis_map_entries,
                )
                
                _LinuxDILL._device_map[vjoy_guid] = info
                # vJoy has no event node — it doesn't need polling
                logger.info(
                    "Injected virtual vJoy device #%d (id=%d, axes=%d)",
                    vjd_id, vjd_id, axis_count,
                )
        
        _LinuxDILL._device_map.update(discovered)
        logger.info("Discovered %d joystick devices", len(_LinuxDILL._device_map))
    
    @staticmethod
    def _fire_input_event(guid, input_type, index, value):
        """Fire a single input event callback."""
        vjoy_guid = _LinuxDILL._masquerade_to_vjoy_map.get(guid)
        if vjoy_guid is not None:
            guid = vjoy_guid
        
        cb = _LinuxDILL.input_event_callback_fn
        if cb is None:
            # Silently discard
            # (startup race) or it hasn't been set (shouldn't happen after init).
            # Events that fire before initialization can be lost safely.
            return

        data = _JoystickInputData()
        data.device_guid = guid.ctypes
        data.input_type = input_type
        data.input_index = index
        data.value = value
        cb(data)
    
    @staticmethod
    def term():
        """Shut down the Linux input subsystem. Closes FDs, stops threads."""
        logger.info("Linux DILL backend terminating...")
        _LinuxDILL._running = False
        
        # v2 GIL-unblock: close all non-blocking FDs
        for guid, fd in list(_LinuxDILL._guid_to_fd.items()):
            try:
                os.close(fd)
            except OSError:
                pass
        _LinuxDILL._fd_to_guid.clear()
        _LinuxDILL._guid_to_fd.clear()
        
        if _LinuxDILL._polling_thread is not None:
            _LinuxDILL._polling_thread.join(timeout=3.0)
        if _LinuxDILL._disp_thread is not None:
            _LinuxDILL._disp_thread.join(timeout=3.0)
        
        logger.info("Linux DILL backend terminated")

    @staticmethod
    def _fire_device_change(data, action):
        """Fire a device change event callback."""
        cb = _LinuxDILL.device_change_callback_fn
        if cb is None:
            return
        cb(data, action)
    
    @staticmethod
    def _poll_loop():
        """Non-blocking poll using V2 architecture.
        
        Replaces the per-device select.select + evdev.read_one() pattern with
        a single select.poll() on all non-blocking FDs. Events are parsed
        from raw bytes via struct.unpack and pushed to _evq — never blocking
        on slow callbacks or C extension I/O.

        Only used for device discovery (capabilities) — the FDs are closed
        when devices are removed.
        """
        import select as _select
        _struct = struct
        
        _EVENT_SIZE = 24
        _EVENT_FMT = '<qQHHi'
        
        logger.info("[INPUT_TRACE] V2-style poll loop started")
        _LinuxDILL._poll = _select.poll()
        
        while _LinuxDILL._running:
            # Snapshot FD map (thread-safe against discovery)
            with _LinuxDILL._poll_lock:
                fd_map = dict(_LinuxDILL._fd_to_guid)
            
            if not fd_map:
                time.sleep(0.016)
                continue
            
            # Recreate poll fresh each iteration to stay in sync with device events
            poll = _select.poll()
            for fd in fd_map:
                try:
                    poll.register(fd, _select.POLLIN)
                except OSError:
                    pass
            _LinuxDILL._poll = poll
            
            ready_events = _LinuxDILL._poll.poll(16)
            
            for fd, _ in ready_events:
                guid = fd_map.get(fd)
                if guid is None or guid not in _LinuxDILL._device_events:
                    continue
                
                try:
                    raw = os.read(fd, 4096)
                    if not raw:
                        continue
                    off = 0
                    while off + _EVENT_SIZE <= len(raw):
                        sec, usec, ev_type, code, val = _struct.unpack_from(
                            _EVENT_FMT, raw, off
                        )
                        off += _EVENT_SIZE
                        try:
                            _LinuxDILL._evq.put_nowait((guid, ev_type, code, val, sec, usec))
                        except queue.Full:
                            pass
                except BlockingIOError:
                    pass
                except OSError as exc:
                    logger.debug("Poll read error for FD %d: %s", fd, exc)
                except Exception:
                    logger.exception("Unexpected poll error")
        logger.info("V2 poll loop stopped")
                    
        logger.info("V2 poll loop stopped")
    
    @staticmethod
    def _event_dispatcher():
        """Drain the event queue and translate raw events into DILL callbacks.
        
        Runs in its own thread so slow Qt callbacks / UI updates never block
        the poll thread.  This is the key GIL-unblocking layer.
        """
        logger.info("Event dispatcher started")
        while _LinuxDILL._running:
            try:
                guid, ev_type, code, val, sec, usec = _LinuxDILL._evq.get(timeout=0.016)
            except queue.Empty:
                continue
                
            if guid not in _LinuxDILL._device_events:
                continue
                
            state = _LinuxDILL._device_state.get(guid, {})
            old_val = state.get(code, 0)
            
            if ev_type == e.EV_KEY:
                dinput_btn = _LinuxDILL._btn_code_map.get(guid, {}).get(code)
                if dinput_btn is not None and old_val != val:
                    state[code] = val  # update state before callback
                    _LinuxDILL._fire_input_event(guid, 2, dinput_btn, 1 if val else 0)
                    
            elif ev_type == e.EV_ABS:
                if e.ABS_HAT0X <= code <= e.ABS_HAT3Y:
                    hat_index = (code - e.ABS_HAT0X) // 2 + 1
                    
                    # Update state for this axis first
                    state[code] = val
                    
                    # Combine X+Y from state and convert to millidegrees.
                    # The hat can come from:
                    #   1. Physical controller: ±1 (evdev standard)
                    #   2. vJoy virtual device: [-32768, 32767] (raw cos/sin angle from SetContPov)
                    # Both normalize to {-1, 0, +1} before direction lookup so
                    # _hat_x_y_to_millidegrees always gets valid inputs.
                    hat_x_code = e.ABS_HAT0X + (hat_index - 1) * 2
                    hat_y_code = e.ABS_HAT0Y + (hat_index - 1) * 2
                    x_raw = state.get(hat_x_code, 0)
                    y_raw = state.get(hat_y_code, 0)
                    x = 1 if x_raw > 0 else (-1 if x_raw < 0 else 0)
                    y = 1 if y_raw > 0 else (-1 if y_raw < 0 else 0)
                    
                    millidegrees = _hat_x_y_to_millidegrees(x, y)
                    if millidegrees is not None:
                        # Track previous hat millidegrees to avoid spurious re-fires when both
                        # X and Y axis events fire for the same hat direction.
                        prev_key = (hash(guid), hat_index)
                        prev = _LinuxDILL._prev_hat_val.get(prev_key)
                        _LinuxDILL._prev_hat_val[prev_key] = millidegrees
                        if prev is None or prev != millidegrees:
                            _LinuxDILL._fire_input_event(
                                guid, 3, hat_index, millidegrees
                            )
                else:
                    dinput_axis = _ABS_TO_DINPUT_AXIS.get(code)
                    if dinput_axis is not None and old_val != val:
                        # Scale non-16-bit axes (triggers 0..1023, 0..255 etc.) to
                        # the standard -32768..32767 number space so everything
                        # downstream (calibration, /32768 normalization, action plugins)
                        # operates on a single uniform raw range.
                        device_ranges = _LinuxDILL._axis_abs_ranges.get(guid, {})
                        abs_min, abs_max = device_ranges.get(code, (-32768, 32767))
                        
                        # If the hardware reports a range smaller than 16-bit bipolar,
                        # it's a unipolar axis (trigger) that needs scaling.
                        if abs_max - abs_min < 32768:
                            # Linear scale: (val - abs_min) / (abs_max - abs_min) * 32767
                            scaled = int((val - abs_min) * 32767 / (abs_max - abs_min))
                            scaled = max(0, min(32767, scaled))
                            state[code] = scaled
                            _LinuxDILL._fire_input_event(guid, 1, dinput_axis, scaled)
                        else:
                            state[code] = val
                            _LinuxDILL._fire_input_event(guid, 1, dinput_axis, val)
                        
        logger.info("Event dispatcher stopped")


def _hat_x_y_to_millidegrees(x: int, y: int) -> int | None:
    """Convert evdev hat {-1, 0, 1} X/Y to DirectInput-compatible millidegrees (0-31500).

    evdev sends hat axes independently, so the value is just {-1, 0, +1}.
    Here we map the (x, y) pair to the millidegree value that the Windows
    vJoy driver would send, which is what util.dill_hat_lookup expects.
    """
    if x == 0 and y == 0:
        return 4500  # center
    elif x == 0 and y == -1:
        return 0     # up
    elif x == 1 and y == -1:
        return 4500  # up-right
    elif x == 1 and y == 0:
        return 9000  # right
    elif x == 1 and y == 1:
        return 13500 # down-right
    elif x == 0 and y == 1:
        return 18000 # down
    elif x == -1 and y == 1:
        return 22500 # down-left
    elif x == -1 and y == 0:
        return 27000 # left
    elif x == -1 and y == -1:
        return 31500 # up-left
    return None  # should never happen with valid evdev hat values
