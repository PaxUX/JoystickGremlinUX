# -*- coding: utf-8 -*-

"""
Canonical key code tables — single source of truth.

This module defines three maps for every key:
  1. vk_to_evdev(VK code) → evdev code (for injection)
  2. evdev_to_vk(evdev code) → VK code (for input capture)
  3. vk_display_name(vk) → str (for UI)

All mappings are built once at import time from _EVDEV_TO_VK.
If evdev is missing, all lookups gracefully return None.
"""

import logging

logger = logging.getLogger("system")

# ── 1. Canonical mapping: evdev name → VK code ──
_EVDEV_TO_VK: dict[str, int] = {
    # ── Function keys (F1–F24) ──
    "KEY_ESC":      0x1B,
    "KEY_F1":       0x70, "KEY_F2":       0x71, "KEY_F3":       0x72,
    "KEY_F4":       0x73, "KEY_F5":       0x74, "KEY_F6":       0x75,
    "KEY_F7":       0x76, "KEY_F8":       0x77, "KEY_F9":       0x78,
    "KEY_F10":      0x79, "KEY_F11":      0x7A, "KEY_F12":      0x7B,
    "KEY_F13":      0x7C, "KEY_F14":      0x7D, "KEY_F15":      0x7E,
    "KEY_F16":      0x7F, "KEY_F17":      0x80, "KEY_F18":      0x81,
    "KEY_F19":      0x82, "KEY_F20":      0x83, "KEY_F21":      0x84,
    "KEY_F22":      0x85, "KEY_F23":      0x86, "KEY_F24":      0x87,

    # ── Modifiers ──
    "KEY_LEFTCTRL":  0xA2, "KEY_RIGHTCTRL":  0xA3,
    "KEY_LEFTSHIFT":  0xA0, "KEY_RIGHTSHIFT":  0xA1,
    "KEY_LEFTALT":    0xA4, "KEY_RIGHTALT":    0xA5,
    "KEY_LEFTMETA":   0x5B, "KEY_RIGHTMETA":   0x5C,
    "KEY_MENU":       0x5D, "KEY_CAPSLOCK":    0x14,
    "KEY_NUMLOCK":    0x90, "KEY_SCROLLLOCK":  0x91,
    # Fn keys on laptops (no VK → passthrough to raw code)
    "KEY_FN":         0x00, "KEY_FN_ESC":     0x00,

    # ── Navigation / Arrows ──
    "KEY_HOME":   0x24, "KEY_END":   0x23,
    "KEY_PAGEUP": 0x21, "KEY_PAGEDOWN": 0x22,
    "KEY_UP":     0x26, "KEY_LEFT":   0x25,
    "KEY_DOWN":   0x28, "KEY_RIGHT":  0x27,
    "KEY_INSERT": 0x2D, "KEY_DELETE": 0x2E,

    # ── Editing ──
    "KEY_ENTER":    0x0D, "KEY_BACKSPACE": 0x08,
    "KEY_TAB":      0x09, "KEY_PAUSE": 0x13,
    "KEY_SYSRQ":    0x2C, "KEY_PRINT":    0x2C,  # both = PrintScreen
    "KEY_SLEEP":    0x5F, "KEY_HELP":      0x2F,
    "KEY_CANCEL":   0x03,  # Cancel key

    # ── Digits (VK values, not ASCII) ──
    "KEY_1": 0x31, "KEY_2": 0x32, "KEY_3": 0x33, "KEY_4": 0x34,
    "KEY_5": 0x35, "KEY_6": 0x36, "KEY_7": 0x37, "KEY_8": 0x38,
    "KEY_9": 0x39, "KEY_0": 0x30,
    "KEY_SPACE": 0x20,

    # ── Punctuation (QWERTY) ──
    "KEY_GRAVE":       0xC0, "KEY_MINUS":      0xBD,
    "KEY_EQUAL":       0xBB, "KEY_LEFTBRACE":  0xDB,
    "KEY_RIGHTBRACE":  0xDD, "KEY_BACKSLASH":  0xDC,
    "KEY_SEMICOLON":   0xBA, "KEY_APOSTROPHE": 0xDE,
    "KEY_COMMA":       0xBC, "KEY_DOT":        0xBE,
    "KEY_SLASH":       0xBF,
    # 102nd key (UK layout) — also VK 0xC0
    "KEY_102ND":       0xC0,

    # ── Letters (A=0x41 … Z=0x5A) ──
    "KEY_A": 0x41, "KEY_B": 0x42, "KEY_C": 0x43, "KEY_D": 0x44,
    "KEY_E": 0x45, "KEY_F": 0x46, "KEY_G": 0x47, "KEY_H": 0x48,
    "KEY_I": 0x49, "KEY_J": 0x4A, "KEY_K": 0x4B, "KEY_L": 0x4C,
    "KEY_M": 0x4D, "KEY_N": 0x4E, "KEY_O": 0x4F, "KEY_P": 0x50,
    "KEY_Q": 0x51, "KEY_R": 0x52, "KEY_S": 0x53, "KEY_T": 0x54,
    "KEY_U": 0x55, "KEY_V": 0x56, "KEY_W": 0x57, "KEY_X": 0x58,
    "KEY_Y": 0x59, "KEY_Z": 0x5A,

    # ── Numpad ──
    "KEY_KP0":       0x60, "KEY_KP1":       0x61, "KEY_KP2":       0x62,
    "KEY_KP3":       0x63, "KEY_KP4":       0x64, "KEY_KP5":       0x65,
    "KEY_KP6":       0x66, "KEY_KP7":       0x67, "KEY_KP8":       0x68,
    "KEY_KP9":       0x69, "KEY_KPDOT":       0x6E,
    "KEY_KPASTERISK": 0x6A, "KEY_KPPLUS":     0x6B,
    "KEY_KPMINUS":    0x6D, "KEY_KPSLASH":    0x6F,
    "KEY_KPENTER":    0x0D,  # same VK as Enter on Linux

    # ── Media / Browser keys (Microsoft VK range) ──
    "KEY_VOLUMEDOWN": 0xB6, "KEY_VOLUMEUP":   0xB7,
    "KEY_MUTE":          0xB5,
    "KEY_PLAYPAUSE":     0xB3, "KEY_PREVIOUSSONG": 0xB1, "KEY_NEXTSONG":      0xB0,
    "KEY_STOPCD":        0xB2, "KEY_MAIL":        0xB4,
    "KEY_BRIGHTNESSUP":  0xE1, "KEY_BRIGHTNESSDOWN": 0xE0,
    "KEY_SCREENLOCK":    0x92, "KEY_WWW":         0xAD,
    "KEY_SEARCH":        0xAE, "KEY_CALC":        0xE6,
    "KEY_BOOKMARKS":     0xF4, "KEY_COMPUTER":    0xF1,
    "KEY_BACK":          0xE3, "KEY_FORWARD":     0xE4,
    "KEY_NEW":           0xEA,

    # ── Mouse buttons (BTN_ prefix, no KEY_) ──
    "BTN_LEFT":     0x01, "BTN_RIGHT":    0x02,
    "BTN_MIDDLE":   0x04, "BTN_SIDE":     0x05,
    "BTN_EXTRA":    0x06,
}

# ── 2. Display names (VK → friendly name) ──
_VK_NAMES: dict[int, str] = {
    # ── Mouse buttons ──
    0x01: "MouseLeft", 0x02: "MouseRight", 0x04: "MouseMiddle",
    0x05: "MouseX1", 0x06: "MouseX2",

    # ── Basic keys ──
    0x03: "Cancel",    0x08: "Back",    0x09: "Tab",
    0x0D: "Enter",     0x13: "Pause",   0x14: "CapsLock",
    0x1B: "Esc",       0x20: "Space",   0x2C: "Print",
    0x2D: "Insert",    0x2E: "Delete",

    # ── Modifier ──
    0x10: "Shift",    0x11: "Ctrl",      0x12: "Alt",
    0x2F: "Help",
    0x5B: "LeftWin",  0x5C: "RightWin",  0x5D: "Apps",
    0x90: "NumLock",  0x91: "ScrollLock",

    # ── Letters A-Z ──
    0x41: "A", 0x42: "B", 0x43: "C", 0x44: "D",
    0x45: "E", 0x46: "F", 0x47: "G", 0x48: "H",
    0x49: "I", 0x4A: "J", 0x4B: "K", 0x4C: "L",
    0x4D: "M", 0x4E: "N", 0x4F: "O", 0x50: "P",
    0x51: "Q", 0x52: "R", 0x53: "S", 0x54: "T",
    0x55: "U", 0x56: "V", 0x57: "W", 0x58: "X",
    0x59: "Y", 0x5A: "Z",

    # ── Digits ──
    0x30: "0", 0x31: "1", 0x32: "2", 0x33: "3",
    0x34: "4", 0x35: "5", 0x36: "6", 0x37: "7",
    0x38: "8", 0x39: "9",

    # ── Function keys ──
    0x70: "F1",  0x71: "F2",  0x72: "F3",  0x73: "F4",
    0x74: "F5",  0x75: "F6",  0x76: "F7",  0x77: "F8",
    0x78: "F9",  0x79: "F10", 0x7A: "F11", 0x7B: "F12",
    0x7C: "F13", 0x7D: "F14", 0x7E: "F15", 0x7F: "F16",
    0x80: "F17", 0x81: "F18", 0x82: "F19", 0x83: "F20",
    0x84: "F21", 0x85: "F22", 0x86: "F23", 0x87: "F24",

    # ── Punctuation ──
    0xC0: "Accent",     0xBB: "Equals",
    0xBD: "Minus",      0xBC: "Comma",
    0xBE: "Period",     0xBF: "Slash",
    0xBA: "Semicolon",  0xDE: "Quote",

    # ── Numpad ──
    0x60: "Numpad0", 0x61: "Numpad1", 0x62: "Numpad2", 0x63: "Numpad3",
    0x64: "Numpad4", 0x65: "Numpad5", 0x66: "Numpad6", 0x67: "Numpad7",
    0x68: "Numpad8", 0x69: "Numpad9",
    0x6E: "NumpadDot",  0x6A: "NumpadMultiply",
    0x6B: "NumpadAdd",  0x6D: "NumpadSubtract",
    0x6F: "NumpadDivide", 0x6C: "NumpadEnter",

    # ── Navigation ──
    0x24: "Home",   0x23: "End",   0x21: "PageUp",  0x22: "PageDown",
    0x25: "Left",   0x26: "Up",    0x27: "Right",   0x28: "Down",

    # ── Media / Browser keys (Microsoft VK) ──
    0xB5: "Mute",           0xB6: "VolumeDown",  0xB7: "VolumeUp",
    0xB3: "PlayPause",      0xB1: "PrevSong",    0xB0: "NextSong",
    0xB2: "StopCD",         0xB4: "Mail",
    0xE1: "BrightnessUp",   0xE0: "BrightnessDown",
    0x92: "ScreenLock",     0xAD: "WWW",
    0xAE: "BrowserSearch",  0xE6: "Calculator",
    0xE7: "Mail",           0xF4: "BrowserBookmarks",
    0xF1: "Computer",
    0xE3: "BrowserBack",    0xE4: "BrowserForward",
    0xEA: "BrowserNew",
}


# ── 3. Build lookup tables ──

_vk_to_evdev: dict[int, int] | None = None
_evdev_to_vk: dict[int, int] | None = None


def _build_mappings() -> tuple[dict[int, int], dict[int, int]]:
    """Resolve evdev names → actual evdev codes and vice versa.

    :return: (vk_to_evdev, evdev_to_vk)
    """
    try:
        from evdev import ecodes
    except ImportError:
        logger.error("[KEY_CODES] evdev not available — lookups will fail.")
        return None, None

    vk_to_evdev: dict[int, int] = {}
    evdev_to_vk: dict[int, int] = {}

    for name, vk in _EVDEV_TO_VK.items():
        code = getattr(ecodes, name, None)
        if code is not None:
            vk_to_evdev[vk] = code
            evdev_to_vk[code] = vk

    return vk_to_evdev, evdev_to_vk

_vk_to_evdev, _evdev_to_vk = _build_mappings()


# ── 4. Public API ──


def vk_to_evdev(vk: int) -> int | None:
    """Convert a VK code to an evdev code (for injection).

    :param vk: Windows Virtual Key code
    :return: evdev code, or None if unknown
    """
    if _vk_to_evdev is None:
        return None
    return _vk_to_evdev.get(vk)


def evdev_to_vk(evdev_code: int) -> int:
    """Convert an evdev code to a VK code (for input capture).

    :param evdev_code: Raw event code from evdev
    :return: VK code, or the raw code passthrough if unmapped
    """
    if _evdev_to_vk is None:
        return evdev_code
    return _evdev_to_vk.get(evdev_code, evdev_code)


def vk_display_name(vk: int) -> str:
    """Get the display name for a VK code (for UI).

    :param vk: Windows Virtual Key code
    :return: Display name (e.g. "V", "Enter", "VolumeDown")
    """
    return _VK_NAMES.get(vk, f"Key({vk:#x})")


# ── 5. Validation ──


def validate() -> list[str]:
    """Check that mappings are internally consistent.

    :return: List of error messages (empty if everything is OK)
    """
    errors: list[str] = []

    if _vk_to_evdev is None or _evdev_to_vk is None:
        errors.append("Evdev not available — skipping validation")
        return errors

    for evdev_name, vk in _EVDEV_TO_VK.items():
        code = getattr(ecodes, evdev_name, None)
        if code is None:
            errors.append(f"Unresolved evdev name: {evdev_name}")
            continue

        if _evdev_to_vk.get(code) != vk:
            errors.append(
                f"RoundTrip mismatch: {evdev_name} (evdev {code}) → vk {vk} "
                f"but reverse gives {(_evdev_to_vk.get(code))}"
            )
        if _vk_to_evdev.get(vk) != code:
            errors.append(
                f"RoundTrip mismatch: VK {vk:#x} → evdev {_vk_to_evdev.get(vk):02X} "
                f"but forward gives {code:02X}"
            )

    if errors:
        logger.warning("[KEY_CODES] Validation errors: %d", len(errors))
    else:
        logger.debug("[KEY_CODES] Validated OK")

    return errors
