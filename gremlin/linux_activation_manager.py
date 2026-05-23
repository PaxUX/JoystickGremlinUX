# -*- coding: utf-8; -*-

"""Linux-only deferred activation manager.

On Wayland, calling ``QSystemTrayIcon.show()``, ``QMainWindow.show()``,
or starting the ``ProcessMonitor`` before ``QApplication.exec_()`` can
produce repeated ``QWindow::requestActivate() rejected by compositor``
log spam because the compositor refuses programmatic focus before the
event loop is running.

This module queues those operations during ``GremlinUi.__init__`` and
replays them safely after the event loop starts — via a ``QTimer.singleShot(0)``
callback that arrives on the very first event-loop iteration.
"""

import sys
import logging

from PyQt5 import QtCore

logger = logging.getLogger("system")


class LinuxActivationManager:
    """Singleton that defers tray / window / process-monitor visibility
    until the Qt event loop has started.

    Call-sites in ``joystick_gremlin.py`` register callbacks; the manager
    only executes them once, inside the first event-loop iteration.
    """

    __slots__ = (
        "_window_to_show",
        "_tray_icon_to_show",
        "_process_monitor_to_start",
        "_start_minimized",
        "_boot_triggered",
        "_boot_callback",
    )

    def __init__(self) -> None:
        self._window_to_show: object | None = None
        self._tray_icon_to_show: object | None = None
        self._process_monitor_to_start: object | None = None
        self._start_minimized: bool = False
        self._boot_triggered: bool = False
        self._boot_callback: object | None = None

    # --- registration -------------------------------------------------------

    def set_window_to_show(self, window: "QtWidgets.QMainWindow | None") -> None:
        self._window_to_show = window

    def set_tray_icon_to_show(self, icon: "QtWidgets.QSystemTrayIcon | None") -> None:
        self._tray_icon_to_show = icon

    def set_process_monitor_to_start(self, monitor: object | None) -> None:
        self._process_monitor_to_start = monitor

    def set_start_minimized(self, minimized: bool) -> None:
        self._start_minimized = minimized

    # --- execution ----------------------------------------------------------

    def boot(self) -> None:
        """Execute all queued operations on the event-loop thread.
        No-ops after first call.
        """
        if self._boot_triggered:
            return
        self._boot_triggered = True

        tray = self._tray_icon_to_show
        window = self._window_to_show
        monitor = self._process_monitor_to_start
        minimized = self._start_minimized

        if tray is not None:
            tray.show()

        if window is not None:
            window.show()
            if minimized:
                window.setHidden(True)

        if monitor is not None:
            monitor.start()

    def register_single_shot(self) -> None:
        """Install the ``singleShot(0)`` callback to trigger boot
        on the first event-loop iteration.

        Call this BEFORE ``app.exec_()`` in the main entry point.
        """
        if self._boot_callback is not None:
            # Already registered
            return

        # Create the callback — we use a closure that captures self
        # because by the time the timer fires, the manager object is already
        # in memory (it won't be garbage collected).
        def _boot_callback() -> None:
            self.boot()
        self._boot_callback = _boot_callback
        QtCore.QTimer.singleShot(0, _boot_callback)
        logger.info("[LinuxActivation] Deferred boot registered (singleShot(0))")


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

_instance: LinuxActivationManager | None = None


def get_activation_manager() -> LinuxActivationManager:
    """Return the singleton manager (created lazily, Linux only)."""
    global _instance
    if _instance is None:
        _instance = LinuxActivationManager()
    return _instance


def register_early() -> None:
    """Call this in the main entry point BEFORE ``app.exec_()`` to
    install the deferred boot callback.

    This must be called after the ``LinuxActivationManager`` singleton
    has been populated (via ``set_*`` methods in ``GremlinUi.__init__``).
    """
    if sys.platform.startswith("linux"):
        get_activation_manager().register_single_shot()


def finalize_on_exit() -> None:
    """Clean up the activation manager.  Intended for application shutdown."""
    global _instance
    if _instance is not None:
        _instance._boot_callback = None
        _instance = None
