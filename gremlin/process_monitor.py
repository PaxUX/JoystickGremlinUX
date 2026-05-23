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
import ctypes.wintypes
import os
import time
import threading
import sys

from PyQt5 import QtCore

# ========== Linux stubs for pywin32 imports ==========

win32gui = None
win32process = None
_OPENHANDLE = 0
_QUERYFULLPROCESSIMAGENA = None
_CLOSEHANDLE = 0

if sys.platform == "win32":
    try:
        import win32gui as _wgui
        import win32process as _wproc
        win32gui = _wgui
        win32process = _wproc

        _kernel32 = ctypes.windll.kernel32

        # PROQUERYSIZE constants
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

        # Load OpenProcess, QueryFullProcessImageNameA, CloseHandle

        class QUERYFULLPROCESSIMAGENA_CTYPES(ctypes.Structure):
            _fields_ = [("FileName", ctypes.c_char * 1024)]

        _OPENHANDLE = getattr(_kernel32, "OpenProcess", None)
        _QUERYFULLPROCESSIMAGENA = getattr(_kernel32, "QueryFullProcessImageNameA", None)
        _CLOSEHANDLE = getattr(_kernel32, "CloseHandle", None)
    except ImportError:
        pass
else:
    # Linux stubs: kernel32 methods
    PROCESS_QUERY_LIMITED_INFORMATION = 0

    # stub _buffer as a class attribute so ProcessMonitor can still access it
    class _kernel32_stub:
        pass

    kernel32 = _kernel32_stub()


# ========= Helper: resolve active foreground window PID / process name =========

def _get_foreground_process_name():
    """Return the path to the active foreground executable, or empty string."""
    if sys.platform == "win32" and win32gui is not None:
        try:
            hwnd = win32gui.GetForegroundWindow()
            _, pid = win32process.GetWindowThreadProcessId(hwnd)

            h = _OPENHANDLE(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            if h:
                buf = ctypes.create_string_buffer(1024)
                size = ctypes.wintypes.DWORD(1024)
                _QUERYFULLPROCESSIMAGENA(h, 0, buf, ctypes.byref(size))
                _CLOSEHANDLE(h)
                path = os.path.normpath(buf.value.decode("utf-8", "ignore"))
                return path.replace("\\", "/")
        except Exception:
            pass
    else:
        # Linux: read from /proc
        try:
            # Try to determine /proc-based foreground window name via wmctrl or xdotool
            import subprocess
            try:
                proc = subprocess.run(
                    ["xdotool", "getactivewindow", "getwindowpid"],
                    capture_output=True, text=True, timeout=2
                )
                pid = int(proc.stdout.strip())
                exe = ""
                if os.path.exists(f"/proc/{pid}/exe"):
                    exe = os.path.realpath(f"/proc/{pid}/exe")
                if exe:
                    return exe
            except (FileNotFoundError, Exception):
                pass
        except Exception:
            pass

    return ""


# ========= ProcessMonitor (Qt QObject) =========

class ProcessMonitor(QtCore.QObject):

    """Monitors the currently active window process.

    This class continuously monitors the active window and whenever
    it changes the path to the executable is retrieved and signaled
    to the rest of the system using Qt's signal / slot mechanism.
    """

    # Signal emitted when the active window changes
    process_changed = QtCore.pyqtSignal(str)

    # Definition of the flags for limited information queries
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    # kernel32.dll library handle
    kernel32 = ctypes.windll.kernel32 if sys.platform == "win32" else None

    def __init__(self):
        """Creates a new instance."""
        QtCore.QObject.__init__(self)
        self._buffer = ctypes.create_string_buffer(1024) if sys.platform == "win32" else None
        self._buffer_size = ctypes.wintypes.DWORD(1024) if sys.platform == "win32" else None
        self._current_path = ""
        self._current_pid = -1
        self.running = False
        self._update_thread = None

    def start(self):
        """Starts monitoring the current process."""
        if not self.running:
            self.running = True
            self._update_thread = threading.Thread(target=self._update)
            self._update_thread.start()

    def stop(self):
        """Stops monitoring the current process."""
        self.running = False
        if self._update_thread is not None:
            self._update_thread.join()

    def _update(self):
        """Monitors the active process for changes."""
        while self.running:
            try:
                current_path = _get_foreground_process_name() or ""

                # On Windows we also use the old PID-based approach
                if sys.platform == "win32" and win32gui is not None:
                    hwnd = win32gui.GetForegroundWindow()
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                    if pid != self._current_pid:
                        self._current_pid = pid
                        handle = self.kernel32.OpenProcess(
                            ProcessMonitor.PROCESS_QUERY_LIMITED_INFORMATION,
                            False,
                            pid
                        )

                        self._buffer_size = ctypes.wintypes.DWORD(1024)
                        self.kernel32.QueryFullProcessImageNameA(
                            handle,
                            0,
                            self._buffer,
                            ctypes.byref(self._buffer_size)
                        )
                        self.kernel32.CloseHandle(handle)

                        current_path = os.path.normpath(
                            str(self._buffer.value)[2:-1]
                        ).replace("\\", "/")

                if current_path != self._current_path:
                    self._current_path = current_path
                    self.process_changed.emit(self._current_path)
            except Exception:
                pass

            time.sleep(1.0)

    @property
    def current_path(self):
        """Returns the path to the currently active executable.

        :return path to the currently active executable
        """
        return self._current_path


# ========= list_current_processes =========

def list_current_processes():
    """Returns a list of executable paths to currently active processes.

    :return list of active process executable paths
    """
    if sys.platform == "win32":
        try:
            from win32com.client import GetObject
            wmi = GetObject('winmgmts:')
            processes = wmi.InstancesOf("Win32_Process")
            process_list = []
            for entry in processes:
                executable = entry.Properties_("ExecutablePath").Value
                if executable is not None:
                    process_list.append(os.path.normpath(executable).replace("\\", "/"))
            return sorted(set(process_list))
        except ImportError:
            pass

    # Linux: use /proc
    process_list = []
    for pid_dir in os.listdir("/proc"):
        if not pid_dir.isdigit():
            continue
        try:
            exe_path = os.path.realpath(os.path.join("/proc", pid_dir, "exe"))
            if exe_path != "/proc/self/exe":
                process_list.append(exe_path)
        except (PermissionError, FileNotFoundError, OSError):
            # PID may have exited, or /proc/NNN/exe is unreadable (e.g., root-only)
            continue
    return sorted(set(process_list))
