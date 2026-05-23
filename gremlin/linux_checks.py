# -*- coding: utf-8; -*-

"""
Linux system capability checks.

Runs at import time on Linux. Prints warnings to stdout for any missing
capabilities. These warnings are always shown (via print), not logged, so
the user never misses them.

All checks are platform-guarded — on Windows nothing runs.
"""

import os
import sys
import subprocess

# On non-Linux this module is a no-op — import it unconditionally from
# gremlin/__init__.py; it will short-circuit and do nothing on Windows.
if not sys.platform.startswith("linux"):
    sys.modules[__name__] = type(sys)("linux_checks")
    sys.modules[__name__]._checks_ran = True


_checks_ran = False


def _check_bin(name: str) -> bool:
    """Return True if *name* is found on PATH."""
    try:
        result = subprocess.run(
            ["which", name],
            capture_output=True, text=True, timeout=2,
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except (FileNotFoundError, OSError):
        return False


_ffplay_available = _check_bin("ffplay") if sys.platform.startswith("linux") else False


def ffplay_available() -> bool:
    """Return True if ffplay is available (cached at import time)."""
    return _ffplay_available


def _check_input_group_membership() -> bool:
    """Return True if the current user is in the 'input' group.

    /dev/input/event* devices are 660 root:input by default. The user must
    be in the 'input' group to read them.  Also allows root (uid 0) as an
    alternative path to device access.
    """
    if os.getuid() == 0:
        return True

    try:
        user_gid = os.getgid()
        groups = os.getgroups()
    except OSError:
        return False

    valid_gids = [user_gid] + groups

    try:
        import grp
        input_gid = grp.getgrnam("input").gr_gid
        if input_gid in valid_gids:
            return True
    except (ImportError, KeyError, OSError):
        # 'input' group does not exist on this system — nothing to check
        return True

    return False


def _check_uinput_access() -> bool:
    """Return True if /dev/uinput exists and is writable."""
    path = "/dev/uinput"
    if not os.path.exists(path):
        return False
    return os.access(path, os.W_OK)


def _print_separator() -> None:
    """Print a visual separator line in the console output."""
    print("=" * 70)


def _run_checks() -> None:
    """Run all system capability checks and print warnings for failures.

    Non-fatal checks collect warnings and display them at the end.
    Fatal checks (evdev, PyQt5, reportlab) exit immediately on failure.
    """
    global _checks_ran
    if _checks_ran:
        return
    _checks_ran = True

    _warnings: list[str] = []

    _print_separator()
    print("JoystickGremlinUX — Linux System Checks")
    print("=" * 70)

    # 1. 'input' group — required to access /dev/input/event* devices
    in_input_group = _check_input_group_membership()
    print(f"  {'user in input group':<40} {'OK' if in_input_group else 'MISSING'}")
    if not in_input_group:
        _warnings.append(
            f"WARNING: Current user is not in the 'input' group. "
            f"Physical joystick/keyboard/mouse input will NOT be detected.\n\n"
            f"Fix:\n"
            f"  sudo usermod -aG input $USER\n\n"
            f"Then log out and back in (group change needs a new session)."
        )

    # 2. /dev/uinput — the single most important requirement
    uinput_ok = _check_uinput_access()
    print(f"  {'/dev/uinput accessible':<40} {'OK' if uinput_ok else 'MISSING'}")
    if not uinput_ok:
        _warnings.append(
            f"WARNING: /dev/uinput not found or not writable. "
            f"Virtual devices (vJoy joystick, virtual keyboard, "
            f"virtual mouse) will NOT work.\n\n"
            f"Fix (choose one):\n"
            f"  1. sudo modprobe uinput                        # load kernel module\n"
            f"  2. sudo usermod -aG input $USER                # add user to 'input' group\n"
            f"  3. sudo chmod 666 /dev/uinput                  # quick hack (resets on reboot)"
        )

    # 3. xdotool — mouse injection backend (used by macro & map_to_mouse plugins)
    xdotool_ok = _check_bin("xdotool")
    print(f"  {'xdotool (mouse injection x11)':<40} {'OK' if xdotool_ok else 'MISSING'}")
    if not xdotool_ok:
        _warnings.append(
            "WARNING: xdotool not found — mouse injection plugins (macro, "
            "map_to_mouse) will crash on use.\n"
            "Install: sudo apt install xdotool"
        )

    # 4. ffplay — used by play_sound plugin
    ffplay_ok = _check_bin("ffplay")
    print(f"  {'ffplay (play_sound plugin)':<40} {'OK' if ffplay_ok else 'MISSING'}")
    if not ffplay_ok:
        _warnings.append(
            "WARNING: ffplay not found — the play_sound action plugin will not work.\n"
            "Install: sudo apt install ffmpeg"
        )

    # 5. TTS backends (espeak-ng + aplay, or spd-say)
    espeak_ok = _check_bin("espeak-ng")
    aplay_ok = _check_bin("aplay")
    spd_say_ok = _check_bin("spd-say")
    tts_ok = (espeak_ok and aplay_ok) or spd_say_ok
    if espeak_ok and aplay_ok:
        tts_status = "OK"
    elif spd_say_ok:
        tts_status = "fallback"
    else:
        tts_status = "MISSING"
    print(f"  {'TTS (espeak-ng + aplay)':<40} {'OK' if tts_status == 'OK' else (tts_status if tts_status == 'fallback' else 'MISSING')}")
    if not tts_ok:
        _warnings.append(
            "WARNING: No TTS audio backend found — text-to-speech will not function.\n"
            "Install: sudo apt install espeak-ng alsa-utils"
        )

    # Non-fatal warnings displayed before core requirement checks
    if _warnings:
        print()
        for w in _warnings:
            print(w)
        print()

    # 6. evdev Python library (required for physical device polling)
    # This is a core requirement — without it the app cannot function on Linux.
    evdev_ok = False
    try:
        import evdev  # noqa: F401
        evdev_ok = True
    except ImportError:
        pass
    print(f"  {'evdev (physical devices)':<40} {'OK' if evdev_ok else 'MISSING'}")
    if not evdev_ok:
        sys.stderr.write(
            "\nFATAL: Python evdev library not installed — physical joystick/keyboard/"
            "mouse input will NOT be detected.\n"
            "Install: pip install evdev\n\n"
        )
        os._exit(1)

    # 7. PyQt5 (must be importable for the app to function at all)
    # This is a core requirement — without it the app cannot function on Linux.
    pyqt_ok = False
    try:
        from PyQt5 import QtWidgets  # noqa: F401
        pyqt_ok = True
    except ImportError:
        pass
    print(f"  {'PyQt5 (GUI)':<40} {'OK' if pyqt_ok else 'MISSING'}")
    if not pyqt_ok:
        sys.stderr.write(
            "\nFATAL: PyQt5 not installed — the application WILL crash on startup.\n"
            "Install: pip install PyQt5\n\n"
        )
        os._exit(1)

    # 8. reportlab (required for cheatsheet PDF generation)
    # The cheatsheet module is a core feature; without reportlab the application
    # cannot generate PDF documentation and will crash on use.
    reportlab_ok = False
    try:
        import reportlab  # noqa: F401
        reportlab_ok = True
    except ImportError:
        pass
    print(f"  {'reportlab (cheatsheet PDF)':<40} {'OK' if reportlab_ok else 'MISSING'}")
    if not reportlab_ok:
        sys.stderr.write(
            "\nFATAL: reportlab not installed — the cheatsheet PDF generator will\n"
            "not function and the application is considered broken.\n"
            "Install: pip install reportlab\n\n"
        )
        os._exit(1)

    # Summary
    _print_separator()
    print("All system checks passed — ready to go!")
    _print_separator()


# Run checks immediately on import
_run_checks()
