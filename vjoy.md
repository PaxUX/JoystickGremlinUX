# vJoy for Linux — Implementation Specification

> **Target Path**: `vjoy_linux/` (New module alongside `vjoy/`)
> **Objective**: Provide a Linux-native virtual joystick output layer that emulates the `vJoyInterface.dll` API contract for `JoystickGremlin`.
> **Compatibility Goal**: Seamless detection and configuration in Native Linux (SDL2/Qt), Wine, and Steam/Proton.

---

## 1. Architecture Overview

### 1.1 The Physical Transport (The "Xbox Spoof")
To ensure 100% compatibility with games and anti-cheat that look for "real" hardware without configuration, the Linux backend **does not** present itself as a generic `js` device.

Instead, the `vjoy_linux` backend creates a `/dev/uinput` device that masquerades as a **Standard Xbox 360 Wireless Controller**.

**HID Descriptor Requirements**:
*   **Bus**: `BUS_USB`
*   **Vendor ID**: `0x045E` (Microsoft)
*   **Product ID**: `0x028E` (Xbox 360 Wireless Receiver)
*   **Name**: `Xbox 360 Wireless Controller`
*   **Capabilities**:
    *   `EV_KEY`: 128 buttons (Standard Xbox face buttons, bumpers, thumbsticks, `BTN_START`, `BTN_BACK`).
    *   `ABS_X/Y/Z/RX/RY/RZ`: 6 axes (Analog sticks, Triggers).
    *   `ABS_HAT0X/Y` / `ABS_HAT1X/Y`: 2 POV hats (D-Pads).
    *   `ABS_MISC` / `ABS_RUDDER`: Reserved/Unused but present in standard descriptor for completeness.
    *   `SYN_REPORT`: Required to sync the HID packet buffer.

### 1.2 Module Structure
We create a strictly separated replacement for the Windows DLL binding and high-level device wrappers:

| Path | Function |
|---|---|
| `vjoy_linux/vjoy_interface.py` | Low-level `uinput` writer. Exports C-like functions to Python app (e.g., `uinput_SetAxis`, `uinput_Acquire`, `uinput_SetBtn`). |
| `vjoy_linux/device_manager.py` | Manages the `/dev/uinput` node creation, HID descriptor injection, and permissions via `fcntl.ioctl`. |
| `vjoy_linux/vjoy.py` | Adapted high-level classes (`VJoy`, `Axis`, `Button`, `Hat`). Logic is ported from original `vjoy.py` but backend methods point to `vjoy_interface`. |

---

## 2. The "Xbox Spoof" HID Descriptor
*Critical for Steam/Proton/Wine recognition.*

The `vjoy_linux` backend must inject a binary HID report descriptor that matches an Xbox 360 Wireless Controller perfectly.

**Key Mappings for Input (uinput):**
*   **A Button**: `BTN_SOUTH` (Keycode 304)
*   **B Button**: `BTN_EAST` (Keycode 305)
*   **X Button**: `BTN_NORTH` (Keycode 307)
*   **Y Button**: `BTN_WEST` (Keycode 308)
*   **Left/Right Bumper**: `BTN_TL` / `BTN_TR` (Keycode 309/310)
*   **Left Stick (Click)**: `BTN_THUMBL` (Keycode 311)
*   **Right Stick (Click)**: `BTN_THUMBR` (Keycode 312)
*   **Start/Back**: `BTN_START` / `BTN_SELECT`
*   **Left Stick Axis**: `ABS_X`, `ABS_Y`
*   **Right Stick Axis**: `ABS_RX`, `ABS_RY`
*   **Triggers**: `ABS_GAS`, `ABS_BRAKE`
*   **D-Pad (Hat 0)**: `ABS_HAT0X`, `ABS_HAT0Y`
*   **Extra Hat (Hat 1)**: `ABS_HAT1X`, `ABS_HAT1Y`

---

## 3. API Surface Translation
The original `vJoyInterface.dll` exposed ~20 Windows functions. `vjoy_linux` must expose an identical Python-callable API surface so the upstream `vjoy.py` application logic requires `no changes`.

### 3.1 Low-Level Interface (`vjoy_interface.py`)
| Windows DLL Export | Linux Stub Implementation |
|---|---|
| `GetvJoyVersion` | Return `0x218` (2.1.8) to match driver expectations. |
| `vJoyEnabled` | Check if `uinput` module is available and `/dev/uinput` is writable. |
| `GetVJDStatus(id)` | Check if the vJoy device ID exists and the backend fd is open. |
| `AcquireVJD(id)` | Create the virtual `/dev/uinput` device with the Xbox descriptor. |
| `RelinquishVJD(id)` | Remove the device node (close the uinput fd). |
| `SetAxis(val, id, axis)` | `uinput.abs_event(ABS_*, val).sync()` |
| `SetBtn(pressed, id, btn)` | `uinput.key_event(BTN_*, pressed).sync()` |
| `SetContPov(val, id, hat)` | `uinput.abs_event(ABS_HAT_*, val).sync()` |
| `ResetVJD(id)` | Synthesize neutral packets for all axes/buttons (0, 0 for hats). |

### 3.2 Value Ranges & Scaling
The `vjoy.py` classes handle the logic, but the interface knows the hardware limits:

*   **Axes**: 
    *   Input: Float `[-1.0, 1.0]`
    *   Internal: `deadzone` -> `response_curve` -> mapped to `[0, 32767]` (Standard 16-bit HID range).
*   **Buttons**:
    *   Input: `bool`
    *   Output: `1` (pressed) or `0` (released)
*   **POV Hats (`SetContPov`)**:
    *   Input: `int` (0..36000 millidegrees, or -1 for neutral).
    *   Mapping: 
        *   `(0,0)` -> `-1` (Neutral)
        *   `(0,1)` -> `0`
        *   `(1,1)` -> `4500`
        *   `(1,0)` -> `9000`
        *   `(-1,1)` -> `31500` (etc. standard 8-way circular table).

---

## 4. High-Level Device Management (`vjoy.py`)
The `VJoy` singleton manages the state of a specific device index (1-16).

1.  **Initialization**: 
    *   Validate backend availability.
    *   Call `AcquireVJD()`.
    *   Start `_keep_alive_timer` (60s loop) to prevent apps from timing out the device.
2.  **Accessors**:
    *   `Axis(index)`: Returns an `Axis` object linked to this device.
    *   `Button(index)`: Returns a `Button` object.
    *   `Hat(index)`: Returns a `Hat` object.
3.  **Error Handling**:
    *   Catches `PermissionError` (user needs uinput group).
    *   Catches `OSError` (device busy or uinput unavailable).
4.  **Invalidate**:
    *   Stops timer.
    *   Calls `ResetVJD()`.
    *   Calls `RelinquishVJD()`.

---

## 5. Profile & Persistence
Profile XML must remain identical to Windows for cross-platform config sharing.

### 5.1 Settings (`<vjoy-inputs>`)
```xml
<vjoy-inputs>
    <vjoy-input id="1"/>
</vjoy-inputs>
```
*(Treats the first virtual device as a joystick input source).*

### 5.2 Default Values (`<vjoy-axes>`)
```xml
<vjoy-axes>
    <vjoy id="1">
        <axis id="1" value="0.0"/>
        <!-- axis id=1 maps to AxisName.X via the translation table -->
    </vjoy>
</vjoy-axes>
```

---

## 6. Linux Infrastructure & Deployment

### 6.1 Permission Strategy
`/dev/uinput` is restricted by default. The app cannot run correctly without it.

**Required Action**: Ship a `udev` rule (`50-joystick-gremlin.rules`) during install.
*Target:* Allow the `input` group (or `jgremlin` group) to read/write `/dev/uinput`.
```bash
SUBSYSTEM=="uinput", GROUP="input", MODE="0660"
```

### 6.2 Entry Point (`joystick_gremlin.py`)
The main app must initialize `vjoy_linux` instead of `vjoy`:
```python
# Pseudo-code
if sys.platform == 'linux':
    from vjoy_linux import vjoy as VJoyModule
    from vjoy_linux import vjoy_interface
else:
    from vjoy import vjoy as VJoyModule    # Windows path
    from vjoy import vjoy_interface
```

## 7. Migration Checklist
- [ ] **`vjoy_interface.py` (Linux)**: Create `uinput` writer with Xbox HID descriptor and all 20 function exports.
- [ ] **`device_manager.py`**: Logic to create/remove the `/dev/uinput` node dynamically.
- [ ] **`vjoy.py` (Linux)**: Adapt `VJoy` class to use the new Linux interface but keep existing Axis/Button/Hat math.
- [ ] **`joystick_gremlin.py`**: Swap imports for Linux.
- [ ] **`joystick_gremlin.spec`**: Add `50-joystick-gremlin` to the `datas` list for packaging.

---

### Next Steps
1.  Review this spec for any logical gaps in the Xbox HID mapping or API surface.
2.  Once confirmed, I will draft the `Stubs.md` to track these specific files and begin implementing the `vjoy_interface.py` Linux uinput backend.
