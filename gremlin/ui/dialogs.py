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

import os
import subprocess
import sys
# === pywinreg / winreg stub (for Linux bootstrapping)
try:
    import winreg
except ImportError:
    class _fake_winreg:
        HKEY_CURRENT_USER = None
        HKCU = None
        @staticmethod
        def OpenKey(*a, **k):
            class _h:
                def Close(self): pass
                def __enter__(self): return self
                def __exit__(self, *a): pass
            return _h()
        @staticmethod
        def QueryInfoKey(h): return (0, 0)
        @staticmethod
        def EnumValue(h, i): return (None, None, 0)
    winreg = _fake_winreg()

from PyQt5 import QtCore, QtGui, QtWidgets

import dill

import gremlin
import gremlin.version
from . import common, ui_about


class OptionsUi(common.BaseDialogUi):

    """UI allowing the configuration of a variety of options."""

    def __init__(self, parent=None):
        """Creates a new options UI instance.

        :param parent the parent of this widget
        """
        super().__init__(parent)

        # Actual configuration object being managed
        self.config = gremlin.config.Configuration()
        self.setMinimumWidth(400)

        self.setWindowTitle("Options")

        self.main_layout = QtWidgets.QVBoxLayout(self)
        self.tab_container = QtWidgets.QTabWidget()
        self.main_layout.addWidget(self.tab_container)

        self._create_general_page()
        self._create_profile_page()
        self._create_hidguardian_page()

    def _create_general_page(self):
        """Creates the general options page."""
        self.general_page = QtWidgets.QWidget()
        self.general_layout = QtWidgets.QVBoxLayout(self.general_page)

        # Number of vJoy devices (bold label, at top of page)
        self.vjoy_count_layout = QtWidgets.QHBoxLayout()
        self.vjoy_count_label = QtWidgets.QLabel("Number of vJoy devices")
        vjoy_font = self.vjoy_count_label.font()
        vjoy_font.setBold(True)
        self.vjoy_count_label.setFont(vjoy_font)
        self.vjoy_count_value = QtWidgets.QSpinBox()
        self.vjoy_count_value.setRange(1, 16)
        self.vjoy_count_value.setValue(self.config.vjoy_device_count)
        self.vjoy_count_value.valueChanged.connect(self._vjoy_device_count)
        self.vjoy_count_layout.addWidget(self.vjoy_count_label)
        self.vjoy_count_layout.addWidget(self.vjoy_count_value)
        self.vjoy_count_layout.addStretch()
        self.general_layout.insertLayout(0, self.vjoy_count_layout)
        # Space after the vJoy control before the rest of the options
        self.general_layout.insertSpacing(1, 6)

        # Highlight input option
        self.highlight_input = QtWidgets.QCheckBox(
            "Highlight currently used input"
        )
        self.highlight_input.clicked.connect(self._highlight_input)
        self.highlight_input.setChecked(self.config.highlight_input)

        # Switch to highlighted device
        self.highlight_device = QtWidgets.QCheckBox(
            "Highlight swaps device tabs"
        )
        self.highlight_device.clicked.connect(self._highlight_device)
        self.highlight_device.setChecked(self.config.highlight_device)

        # Close to system tray option
        self.close_to_systray = QtWidgets.QCheckBox(
            "Closing minimizes to system tray"
        )
        self.close_to_systray.clicked.connect(self._close_to_systray)
        self.close_to_systray.setChecked(self.config.close_to_tray)

        # Activate profile on launch
        self.activate_on_launch = QtWidgets.QCheckBox(
            "Activate profile on launch"
        )
        self.activate_on_launch.clicked.connect(self._activate_on_launch)
        self.activate_on_launch.setChecked(self.config.activate_on_launch)

        # Start minimized option
        self.start_minimized = QtWidgets.QCheckBox(
            "Start Joystick Gremlin minimized"
        )
        self.start_minimized.clicked.connect(self._start_minimized)
        self.start_minimized.setChecked(self.config.start_minimized)

        # Start on user login
        # self.start_with_windows = QtWidgets.QCheckBox(
        #     "Start Joystick Gremlin with Windows"
        # )
        # self.start_with_windows.clicked.connect(self._start_windows)
        # self.start_with_windows.setChecked(self._start_windows_enabled())

        # Show message on mode change
        self.show_mode_change_message = QtWidgets.QCheckBox(
            "Show message when changing mode"
        )
        self.show_mode_change_message.clicked.connect(
            self._show_mode_change_message
        )
        self.show_mode_change_message.setChecked(
            self.config.mode_change_message
        )

        # Default action selection
        self.default_action_layout = QtWidgets.QHBoxLayout()
        self.default_action_label = QtWidgets.QLabel("Default action")
        self.default_action_dropdown = QtWidgets.QComboBox()
        self.default_action_layout.addWidget(self.default_action_label)
        self.default_action_layout.addWidget(self.default_action_dropdown)
        self._init_action_dropdown()
        self.default_action_layout.addStretch()

        # Macro axis polling rate
        self.macro_axis_polling_layout = QtWidgets.QHBoxLayout()
        self.macro_axis_polling_label = \
            QtWidgets.QLabel("Macro axis polling rate")
        self.macro_axis_polling_value = common.DynamicDoubleSpinBox()
        self.macro_axis_polling_value.setRange(0.001, 1.0)
        self.macro_axis_polling_value.setSingleStep(0.05)
        self.macro_axis_polling_value.setDecimals(3)
        self.macro_axis_polling_value.setValue(
            self.config.macro_axis_polling_rate
        )
        self.macro_axis_polling_value.valueChanged.connect(
            self._macro_axis_polling_rate
        )
        self.macro_axis_polling_layout.addWidget(self.macro_axis_polling_label)
        self.macro_axis_polling_layout.addWidget(self.macro_axis_polling_value)
        self.macro_axis_polling_layout.addStretch()

        # Macro axis minimum change value
        self.macro_axis_minimum_change_layout = QtWidgets.QHBoxLayout()
        self.macro_axis_minimum_change_label = \
            QtWidgets.QLabel("Macro axis minimum change value")
        self.macro_axis_minimum_change_value = common.DynamicDoubleSpinBox()
        self.macro_axis_minimum_change_value.setRange(0.00001, 1.0)
        self.macro_axis_minimum_change_value.setSingleStep(0.01)
        self.macro_axis_minimum_change_value.setDecimals(5)
        self.macro_axis_minimum_change_value.setValue(
            self.config.macro_axis_minimum_change_rate
        )
        self.macro_axis_minimum_change_value.valueChanged.connect(
            self._macro_axis_minimum_change_value
        )
        self.macro_axis_minimum_change_layout.addWidget(
            self.macro_axis_minimum_change_label
        )
        self.macro_axis_minimum_change_layout.addWidget(
            self.macro_axis_minimum_change_value
        )
        self.macro_axis_minimum_change_layout.addStretch()

        self.general_layout.addWidget(self.highlight_input)
        self.general_layout.addWidget(self.highlight_device)
        self.general_layout.addWidget(self.close_to_systray)
        self.general_layout.addWidget(self.activate_on_launch)
        self.general_layout.addWidget(self.start_minimized)
#        self.general_layout.addWidget(self.start_with_windows)
        self.general_layout.addWidget(self.show_mode_change_message)
        self.general_layout.addLayout(self.default_action_layout)
        self.general_layout.addLayout(self.macro_axis_polling_layout)
        self.general_layout.addLayout(self.macro_axis_minimum_change_layout)

        self.general_layout.addStretch()
        self.tab_container.addTab(self.general_page, "General")

    def _create_profile_page(self):
        """Creates the profile options page."""
        self.profile_page = QtWidgets.QWidget()
        self.profile_page_layout = QtWidgets.QVBoxLayout(self.profile_page)

        # Autoload profile option
        self.autoload_checkbox = QtWidgets.QCheckBox(
            "Automatically load profile based on current application"
        )
        self.autoload_checkbox.clicked.connect(self._autoload_profiles)
        self.autoload_checkbox.setChecked(self.config.autoload_profiles)

        # Executable dropdown list
        self.executable_layout = QtWidgets.QHBoxLayout()
        self.executable_label = QtWidgets.QLabel("Executable")
        self.executable_selection = QtWidgets.QComboBox()
        self.executable_selection.setMinimumWidth(300)
        self.executable_selection.currentTextChanged.connect(
            self._show_executable
        )
        self.executable_add = QtWidgets.QPushButton()
        self.executable_add.setIcon(QtGui.QIcon("gfx/button_add.png"))
        self.executable_add.clicked.connect(self._new_executable)
        self.executable_remove = QtWidgets.QPushButton()
        self.executable_remove.setIcon(QtGui.QIcon("gfx/button_delete.png"))
        self.executable_remove.clicked.connect(self._remove_executable)
        self.executable_edit = QtWidgets.QPushButton()
        self.executable_edit.setIcon(QtGui.QIcon("gfx/button_edit.png"))
        self.executable_edit.clicked.connect(self._edit_executable)
        self.executable_list = QtWidgets.QPushButton()
        self.executable_list.setIcon(QtGui.QIcon("gfx/list_show.png"))
        self.executable_list.clicked.connect(self._list_executables)

        self.executable_layout.addWidget(self.executable_label)
        self.executable_layout.addWidget(self.executable_selection)
        self.executable_layout.addWidget(self.executable_add)
        self.executable_layout.addWidget(self.executable_remove)
        self.executable_layout.addWidget(self.executable_edit)
        self.executable_layout.addWidget(self.executable_list)
        self.executable_layout.addStretch()

        self.profile_layout = QtWidgets.QHBoxLayout()
        self.profile_field = QtWidgets.QLineEdit()
        self.profile_field.textChanged.connect(self._update_profile)
        self.profile_field.editingFinished.connect(self._update_profile)
        self.profile_select = QtWidgets.QPushButton()
        self.profile_select.setIcon(QtGui.QIcon("gfx/button_edit.png"))
        self.profile_select.clicked.connect(self._select_profile)

        self.profile_layout.addWidget(self.profile_field)
        self.profile_layout.addWidget(self.profile_select)

        self.profile_page_layout.addWidget(self.autoload_checkbox)
        self.profile_page_layout.addLayout(self.executable_layout)
        self.profile_page_layout.addLayout(self.profile_layout)
        self.profile_page_layout.addStretch()

        self.tab_container.addTab(self.profile_page, "Profiles")

        self.populate_executables()

    def _create_hidguardian_page(self):
        self.hg_page = QtWidgets.QWidget()
        self.hg_page_layout = QtWidgets.QVBoxLayout(self.hg_page)

        # Display instructions for non admin users
        if not gremlin.util.is_user_admin():
            label = QtWidgets.QLabel(
                "Feature not available under Linux"
                ""
                ""
                # "In order to use HidGuardian to both specify the devices to "
                # "hide via HidGuardian as well as have Gremlin see them, "
                # "Gremlin has to be run as Administrator."
            )
            label.setStyleSheet("QLabel { background-color : '#FFF4B0'; }")
            label.setWordWrap(True)
            label.setFrameShape(QtWidgets.QFrame.Box)
            label.setMargin(10)
            self.hg_page_layout.addWidget(label)

        else:
            # Get list of devices affected by HidGuardian
            hg = gremlin.hid_guardian.HidGuardian()
            hg_device_list = hg.get_device_list()

            self.hg_device_layout = QtWidgets.QGridLayout()
            self.hg_device_layout.addWidget(
                QtWidgets.QLabel("<b>Device Name</b>"), 0, 0
            )
            self.hg_device_layout.addWidget(
                QtWidgets.QLabel("<b>Hidden</b>"), 0, 1
            )

            devices = gremlin.joystick_handling.joystick_devices()
            devices_added = []
            for i, dev in enumerate(devices):
                # Don't add vJoy to this list
                if dev.name == "vJoy Device":
                    continue

                # For identical VID / PID devices only add one instance
                vid_pid_key = (dev.vendor_id, dev.product_id)
                if vid_pid_key in devices_added:
                    continue

                # Set checkbox state based on whether or not HidGuardian tracks
                # the device. Add a callback with pid/vid to add / remove said
                # device from the list of devices handled by HidGuardian
                self.hg_device_layout.addWidget(
                    QtWidgets.QLabel(dev.name), i+1, 0
                )
                checkbox = QtWidgets.QCheckBox("")
                checkbox.setChecked(vid_pid_key in hg_device_list)
                checkbox.stateChanged.connect(self._create_hg_cb(dev))
                self.hg_device_layout.addWidget(checkbox, i+1, 1)
                devices_added.append(vid_pid_key)

            self.hg_page_layout.addLayout(self.hg_device_layout)

            self.hg_page_layout.addStretch()
            label = QtWidgets.QLabel(
                "After making changes to the devices hidden by HidGuardian "
                "the devices that should now be hidden or shown to other"
                "applications need to be unplugged and plugged back in for "
                "the changes to take effect."
            )
            label.setStyleSheet("QLabel { background-color : '#FFF4B0'; }")
            label.setWordWrap(True)
            label.setFrameShape(QtWidgets.QFrame.Box)
            label.setMargin(10)
            self.hg_page_layout.addWidget(label)

        self.tab_container.addTab(self.hg_page, "HidGuardian")

    def closeEvent(self, event):
        """Closes the calibration window.

        :param event the close event
        """
        self.config.save()
        super().closeEvent(event)

    def populate_executables(self, executable_name=None):
        """Populates the profile drop down menu.

        :param executable_name name of the executable to pre select
        """
        self.profile_field.textChanged.disconnect(self._update_profile)
        self.executable_selection.clear()
        executable_list = self.config.get_executable_list()
        for path in executable_list:
            self.executable_selection.addItem(path)
        self.profile_field.textChanged.connect(self._update_profile)

        # Select the provided executable if it exists, otherwise the first one
        # in the list
        index = 0
        if executable_name is not None and executable_name in executable_list:
            index = self.executable_selection.findText(executable_name)
        self.executable_selection.setCurrentIndex(index)

    def _autoload_profiles(self, clicked):
        """Stores profile autoloading preference.

        :param clicked whether or not the checkbox is ticked
        """
        self.config.autoload_profiles = clicked
        self.config.save()

    def _activate_on_launch(self, clicked):
        """Stores activation of profile on launch preference.

        :param clicked whether or not the checkbox is ticked
        """
        self.config.activate_on_launch = clicked
        self.config.save()

    def _close_to_systray(self, clicked):
        """Stores closing to system tray preference.

        :param clicked whether or not the checkbox is ticked
        """
        self.config.close_to_tray = clicked
        self.config.save()

    def _start_minimized(self, clicked):
        """Stores start minimized preference.

        :param clicked whether or not the checkbox is ticked
        """
        self.config.start_minimized = clicked
        self.config.save()

    def _start_windows(self, clicked):
        """Set registry entry to launch Joystick Gremlin on login.

        :param clicked True if launch should happen on login, False otherwise
        """
        if clicked:
            path = os.path.abspath(sys.argv[0])
            subprocess.run(
                r'reg add "HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run" /V "Joystick Gremlin" /t REG_SZ /F /D "{}"'.format(path)
            )
        else:
            subprocess.run(
                r'reg delete "HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run" /F /V "Joystick Gremlin"'
            )

        if sys.platform.startswith("win"):
            self.activateWindow()

    def _start_windows_enabled(self):
        """Returns whether or not Gremlin should launch on login.

        :return True if Gremlin launches on login, False otherwise
        """
        key_handle = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run"
            )
        key_info = winreg.QueryInfoKey(key_handle)

        for i in range(key_info[1]):
            value_info = winreg.EnumValue(key_handle, i)
            if value_info[0] == "Joystick Gremlin":
                return True
        return False

    def _highlight_input(self, clicked):
        """Stores preference for input highlighting.

        :param clicked whether or not the checkbox is ticked
        """
        self.config.highlight_input = clicked
        self.config.save()

    def _highlight_device(self, clicked):
        """Stores preference for device highlighting.

        :param clicked whether or not the checkbox is ticked
        """
        self.config.highlight_device = clicked
        self.config.save()

    def _list_executables(self):
        """Shows a list of executables for the user to pick."""
        self.executable_list_view = ProcessWindow()
        self.executable_list_view.process_selected.connect(self._add_executable)
        self.executable_list_view.show()

    def _add_executable(self, fname):
        """Adds the provided executable to the list of configurations.

        :param fname the executable for which to add a mapping
        """
        if fname not in self.config.get_executable_list():
            self.config.set_profile(fname, "")
            self.populate_executables(fname)
        else:
            self.executable_selection.setCurrentIndex(
                self.executable_selection.findText(fname)
            )

    def _edit_executable(self):
        """Allows editing the path of an executable."""
        new_text, flag = QtWidgets.QInputDialog.getText(
            self,
            "Change Executable / RegExp",
            "Change the executable text or enter a regular expression to use.",
            QtWidgets.QLineEdit.Normal,
            self.executable_selection.currentText()
        )

        # If the user did click on ok update the entry
        old_entry = self.executable_selection.currentText()
        if flag:
            if old_entry not in self.config.get_executable_list():
                self._add_executable(new_text)
            else:
                self.config.set_profile(
                    new_text,
                    self.config.get_profile(old_entry)
                )
                self.config.remove_profile(old_entry)
                self.populate_executables(new_text)

    def _new_executable(self):
        """Prompts the user to select a new executable to add to the
        profile.
        """
        fname, _ = QtWidgets.QFileDialog.getOpenFileName(
            None,
            "Path to executable",
            "C:\\",
            "Executable (*.exe)"
        )
        if fname != "":
            self._add_executable(fname)

    def _remove_executable(self):
        """Removes the current executable from the configuration."""
        self.config.remove_profile(self.executable_selection.currentText())
        self.populate_executables()

    def _select_profile(self):
        """Displays a file selection dialog for a profile.

        If a valid file is selected the mapping from executable to
        profile is updated.
        """
        fname, _ = QtWidgets.QFileDialog.getOpenFileName(
            None,
            "Path to executable",
            gremlin.util.userprofile_path(),
            "Profile (*.xml)"
        )
        if fname != "":
            self.profile_field.setText(fname)
            self.config.set_profile(
                self.executable_selection.currentText(),
                self.profile_field.text()
            )

    def _show_executable(self, exec_path):
        """Displays the profile associated with the given executable.

        :param exec_path path to the executable to shop
        """
        self.profile_field.setText(self.config.get_profile(exec_path))

    def _show_mode_change_message(self, clicked):
        """Stores the user's preference for mode change notifications.

        :param clicked whether or not the checkbox is ticked"""
        self.config.mode_change_message = clicked
        self.config.save()

    def _update_profile(self):
        """Updates the profile associated with the current executable."""
        self.config.set_profile(
            self.executable_selection.currentText(),
            self.profile_field.text()
        )

    def _init_action_dropdown(self):
        """Initializes the action selection dropdown menu."""
        plugins = gremlin.plugin_manager.ActionPlugins()

        for act in sorted(plugins.repository.values(), key=lambda x: x.name):
            self.default_action_dropdown.addItem(act.name)
        self.default_action_dropdown.setCurrentText(self.config.default_action)
        self.default_action_dropdown.currentTextChanged.connect(
            self._update_default_action
        )

    def _update_default_action(self, value):
        """Updates the config with the newly selected action name.

        :param value the name of the newly selected action
        """
        self.config.default_action = value
        self.config.save()

    def _macro_axis_polling_rate(self, value):
        """Updates the config with the newly set polling rate.

        :param value the new polling rate
        """
        self.config.macro_axis_polling_rate = value
        self.config.save()

    def _macro_axis_minimum_change_value(self, value):
        """Updates the config with the newly set minimum change value.

        :param value the new minimum change value
        """
        self.config.macro_axis_minimum_change_rate = value

    def _vjoy_device_count(self, value):
        """Updates the config with the number of vJoy devices.

        :param value the number of vJoy devices
        """
        self.config.vjoy_device_count = value

    def _create_hg_cb(self, *params):
        return lambda x: self._update_hg_device(x, *params)

    def _update_hg_device(self, state, device):
        hg = gremlin.hid_guardian.HidGuardian()
        if state == QtCore.Qt.Checked:
            hg.add_device(device.vendor_id, device.product_id)
        else:
            hg.remove_device(device.vendor_id, device.product_id)


class ProcessWindow(common.BaseDialogUi):

    """Displays active processes in a window for the user to select."""

    # Signal emitted when the user selects a process
    process_selected = QtCore.pyqtSignal(str)

    def __init__(self, parent=None):
        """Creates a new instance.

        :param parent the parent of the widget
        """
        super().__init__(parent)

        self.setWindowTitle("Select Application")
        self.resize(550, 650)

        self.main_layout = QtWidgets.QVBoxLayout(self)

        # Helpful label for UX
        self.info_label = QtWidgets.QLabel(
            "Double-click a running application to map a profile to it, or type its path manually."
        )
        self.info_label.setWordWrap(True)
        self.info_label.setStyleSheet("QLabel { color: gray; }")
        self.main_layout.addWidget(self.info_label)

        # Search / filter box
        self.filter_edit = QtWidgets.QLineEdit()
        self.filter_edit.setPlaceholderText("Filter processes by name or path...")
        self.filter_edit.textChanged.connect(self._filter_processes)
        self.main_layout.addWidget(self.filter_edit)

        # Proxy model for filtering
        self.list_model = QtCore.QStringListModel()
        self.proxy_model = QtCore.QSortFilterProxyModel()
        self.proxy_model.setSourceModel(self.list_model)
        self.proxy_model.setFilterCaseSensitivity(QtCore.Qt.CaseInsensitive)

        # List view
        self.list_view = QtWidgets.QListView()
        self.list_view.setModel(self.proxy_model)
        self.list_view.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.list_view.doubleClicked.connect(self._select_on_double_click)
        self.main_layout.addWidget(self.list_view)

        # Action buttons
        self.button_layout = QtWidgets.QHBoxLayout()

        self.copy_button = QtWidgets.QPushButton("Copy Path")
        self.copy_button.clicked.connect(self._copy_path)
        self.button_layout.addWidget(self.copy_button)

        self.select_button = QtWidgets.QPushButton("Select")
        self.select_button.clicked.connect(self._select)
        self.button_layout.addWidget(self.select_button)

        self.main_layout.addLayout(self.button_layout)

        # Populate initial list
        self._refresh_processes()

    def _refresh_processes(self):
        """Refreshes the process list, filtering out system daemons."""
        raw_processes = gremlin.process_monitor.list_current_processes()

        # Gaming/platform keywords to always include regardless of path
        gaming_names = {
            'steam', 'steamwebhelper', 'proton', 'wine', 'minecraft',
            'lutris', 'dosbox', 'emulationstation', 'retroarch', 'kodi',
            'blender', 'vscode', 'chromium', 'firefox', 'google-chrome',
            'sublime_text', 'nautilus', 'dolphin', 'thunar', 'nemo',
            'gedit', 'code',
        }

        # User-space path prefixes (Linux)
        linux_user_prefixes = ('/home/', '/opt/', '/mnt/', '/media/', '/run/user/')

        filtered_processes = []
        for path in raw_processes:
            basename = os.path.basename(path).lower()

            if sys.platform == 'win32':
                # On Windows: show anything matching gaming names or in common user dirs
                user_dirs = ('\\Program Files\\', '\\Program Files (x86)\\', '\\Users\\')
                matches = basename in gaming_names or any(
                    path.upper().startswith(d.replace('\\', '\\').upper())
                    for d in user_dirs
                )
                if matches:
                    filtered_processes.append(path)
            else:
                # On Linux: show processes in user space + gaming platforms
                is_user_app = (
                    path.startswith('/home/') or path.startswith('/opt/') or
                    path.startswith('/mnt/') or path.startswith('/media/') or
                    path.startswith('/run/user/')
                )
                is_gaming = basename in gaming_names
                if is_user_app or is_gaming:
                    filtered_processes.append(path)

        self.list_model.setStringList(sorted(filtered_processes))

    def _filter_processes(self, text):
        """Updates the filter regex based on the search box."""
        self.proxy_model.setFilterFixedString(text)

    def _select_on_double_click(self, index):
        """Emits process_selected when an item is double-clicked."""
        mapped_index = self.proxy_model.mapToSource(index)
        if mapped_index.isValid():
            path = mapped_index.data()
            if path:
                self.process_selected.emit(path)
                self.close()

    def _select(self):
        """Emits the process signal when the select button is pressed."""
        current_index = self.list_view.currentIndex()
        if current_index.isValid():
            mapped_index = self.proxy_model.mapToSource(current_index)
            if mapped_index.isValid():
                path = mapped_index.data()
                self.process_selected.emit(path)
                self.close()

    def _copy_path(self):
        """Copies the currently selected process path to the clipboard."""
        current_index = self.list_view.currentIndex()
        if current_index.isValid():
            mapped_index = self.proxy_model.mapToSource(current_index)
            if mapped_index.isValid():
                path = mapped_index.data()
                clipboard = QtWidgets.QApplication.clipboard()
                clipboard.setText(path)
                QtWidgets.QMessageBox.information(
                    self,
                    "Path Copied",
                    f"Path copied to clipboard:\n\n{path}"
                )


class LogWindowUi(common.BaseDialogUi):

    """Window displaying log file content.

    Uses tail-based incremental updates to avoid loading entire log files
    into memory, which caused std::bad_alloc crashes on long-running systems.
    """

    # Maximum total content across all tabs (bytes). Oldest lines are dropped
    # when this limit is exceeded to prevent QTextDocument from growing without bound.
    MAX_CONTENT_BYTES = 10 * 1024 * 1024  # 10 MB
    # Number of lines to read from the tail on initial load.
    INITIAL_TAIL_LINES = 10000

    def __init__(self,  parent=None):
        """Creates a new instance.

        :param parent the parent of this widget
        """
        super().__init__(parent)

        self.setWindowTitle("Log Viewer")
        self.setMinimumWidth(600)

        self.main_layout = QtWidgets.QVBoxLayout(self)
        self.tab_container = QtWidgets.QTabWidget()
        self.main_layout.addWidget(self.tab_container)

        self._ui_elements = {}
        # Track file sizes so we can do incremental appends instead of full reloads.
        self._file_sizes = {}
        self._create_log_display(
            os.path.join(gremlin.util.userprofile_path(), "system.log"),
            "System"
        )
        self._create_log_display(
            os.path.join(gremlin.util.userprofile_path(), "user.log"),
            "User"
        )
        self.watcher = gremlin.util.FileWatcher([
            os.path.join(gremlin.util.userprofile_path(), "system.log"),
            os.path.join(gremlin.util.userprofile_path(), "user.log")
        ])
        self.watcher.file_changed.connect(self._reload)

    def closeEvent(self, event):
        """Handles closing of the window.

        :param event the closing event
        """
        self.watcher.stop()
        super().closeEvent(event)

    def _tail_file(self, fname, n_lines):
        """Read the last n_lines from fname without loading the entire file.

        Walks the file backwards in 4 KB chunks looking for newline separators
        to determine line boundaries.

        :param fname path to the file
        :param n_lines number of trailing lines to return
        :return text content (str), or "" if file is empty/unreadable
        """
        try:
            size = os.path.getsize(fname)
        except OSError:
            return ""

        if size == 0:
            return ""

        with open(fname, "rb") as f:
            # Seek backwards and count newlines
            chunk_size = 4096
            data = b""
            offset = size
            newlines_seen = 0
            while newlines_seen <= n_lines and offset > 0:
                chunk_len = min(chunk_size, offset)
                offset -= chunk_len
                f.seek(offset)
                chunk = f.read(chunk_len)
                data = chunk + data

                # Count newlines in the new chunk
                newlines_seen += chunk.count(b"\n")

            # Decode, then slice to last n_lines
            text = data.decode("utf-8", errors="replace")
            lines = text.splitlines()
            if newlines_seen > n_lines:
                cut = newlines_seen - n_lines
                lines = lines[cut:]
            return "\n".join(lines)

    def _create_log_display(self, fname, title):
        """Creates a new tab displaying log file contents.

        :param fname path to the file whose content to display
        :param title the title of the tab
        """
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        log_display = QtWidgets.QTextEdit()
        log_display.setReadOnly(True)
        log_display.setWordWrapMode(QtGui.QTextOption.WrapAtWordBoundaryOrAnywhere)

        # Initial load: use tail to avoid loading entire file into memory.
        log_display.setPlainText(self._tail_file(fname, self.INITIAL_TAIL_LINES))

        # Track file size for incremental updates.
        try:
            self._file_sizes[fname] = os.path.getsize(fname)
        except OSError:
            self._file_sizes[fname] = 0

        button = QtWidgets.QPushButton("Clear log")
        button.clicked.connect(lambda: self._clear_log(fname))
        layout.addWidget(log_display)
        layout.addWidget(button)

        self._ui_elements[fname] = {
            "page": page,
            "layout": layout,
            "button": button,
            "log_display": log_display
        }

        self.tab_container.addTab(
            self._ui_elements[fname]["page"],
            title
        )

    def _enforce_content_limit(self, widget):
        """Drop oldest lines if QTextEdit content exceeds MAX_CONTENT_BYTES.

        :param widget QTextEdit to check / trim
        """
        text = widget.toPlainText()
        # Encode to bytes using UTF-8 to get real byte size.
        while len(text.encode("utf-8", errors="replace")) > self.MAX_CONTENT_BYTES:
            # Remove first 500 lines at a time to avoid chopping in the middle of a line.
            lines = text.splitlines(True)  # keep trailing newlines
            num_lines = len(lines)
            if num_lines <= 500:
                break
            # Remove the first chunk.
            remove_count = min(500, num_lines)
            text = "".join(lines[remove_count:])
            widget.setPlainText(text)

    def _clear_log(self, fname):
        """Clears the specified log file.

        :param fname path to the file to clear
        """
        try:
            with open(fname, "w"):
                pass  # truncate
        except OSError:
            pass

        widget = self._ui_elements[fname]["log_display"]
        widget.setPlainText("")
        self._file_sizes[fname] = 0

    def _reload(self, fname):
        """Appends only new content to the given log tab.

        Tracks the file size seen at the last update and reads only
        bytes that have been appended since then.

        :param fname path to the file whose content to update
        """
        ui_entry = self._ui_elements[fname]
        widget = ui_entry["log_display"]
        old_size = self._file_sizes.get(fname, 0)

        try:
            new_size = os.path.getsize(fname)
        except OSError:
            return

        # File shrank -> probably truncated / log-rotated. Reload from tail.
        if new_size < old_size:
            widget.setPlainText(self._tail_file(fname, self.INITIAL_TAIL_LINES))
            self._file_sizes[fname] = new_size
            return

        # Incremental: read only the new portion appended to the file.
        if new_size > old_size:
            try:
                with open(fname, "rb") as f:
                    f.seek(old_size)
                    delta = f.read(new_size - old_size).decode("utf-8", errors="replace")
                    if delta:
                        # Use QTextCursor to append efficiently (O(delta))
                        # rather than setText which triggers a full QTextDocument
                        # C++ parse (O(total_content) -- the root cause of
                        # std::bad_alloc crashes on files >100MB).
                        if delta.endswith("\n"):
                            cursor = widget.textCursor()
                            cursor.movePosition(QtGui.QTextCursor.End)
                            cursor.insertText(delta.strip("\n"))
                        else:
                            # No trailing newline: insert at end + save cursor
                            cursor = widget.textCursor()
                            cursor.movePosition(QtGui.QTextCursor.End)
                            cursor.insertText(delta)

                        # Drop oldest lines if content grows past the hard cap.
                        self._enforce_content_limit(widget)
            except OSError:
                pass

            self._file_sizes[fname] = new_size

        # If user hasn't scrolled up, auto-follow the bottom.
        scroll = widget.verticalScrollBar()
        if scroll.value() >= scroll.maximum() - 5:
            scroll.setValue(scroll.maximum())


class AboutUi(common.BaseDialogUi):

    """Widget which displays information about the application."""

    def __init__(self, parent=None):
        """Creates a new about widget.

        This creates a simple widget which shows version information
        and various software licenses.

        :param parent parent of this widget
        """
        super().__init__(parent)
        self.ui = ui_about.Ui_About()
        self.ui.setupUi(self)

        about_html = open(gremlin.util.resource_path("about/about.html")).read()
        about_html = about_html.replace("{VERSION}", gremlin.version.get_version())
        self.ui.about.setHtml(about_html)

        self.ui.jg_license.setHtml(
            open(gremlin.util.resource_path("about/joystick_gremlin.html")).read()
        )

        license_list = [
            "about/third_party_licenses.html",
            "about/modernuiicons.html",
            "about/pyqt.html",
            "about/pywin32.html",
            "about/qt5.html",
            "about/reportlab.html",
            "about/vjoy.html",
        ]
        third_party_licenses = ""
        for fname in license_list:
            third_party_licenses += open(gremlin.util.resource_path(fname)).read()
            third_party_licenses += "<hr>"
        self.ui.third_party_licenses.setHtml(third_party_licenses)


class ModeManagerUi(common.BaseDialogUi):

    """Enables the creation of modes and configuring their inheritance."""

    # Signal emitted when mode configuration changes
    modes_changed = QtCore.pyqtSignal()

    def __init__(self, profile_data, parent=None):
        """Creates a new instance.

        :param profile_data the data being profile whose modes are being
            configured
        :param parent the parent of this widget
        """
        super().__init__(parent)
        self._profile = profile_data
        self.setWindowTitle("Mode Manager")

        self.mode_dropdowns = {}
        self.mode_rename = {}
        self.mode_delete = {}
        self.mode_callbacks = {}

        self._create_ui()

        # Disable keyboard event handler
        el = gremlin.event_handler.EventListener()
        el.keyboard_hook.stop()

    def closeEvent(self, event):
        """Emits the closed event when this widget is being closed.

        :param event the close event details
        """
        # Re-enable keyboard event handler
        el = gremlin.event_handler.EventListener()
        el.keyboard_hook.start()
        super().closeEvent(event)

    def _create_ui(self):
        """Creates the required UII elements."""
        self.main_layout = QtWidgets.QVBoxLayout(self)
        self.mode_layout = QtWidgets.QGridLayout()

        self.main_layout.addLayout(self.mode_layout)
        self.main_layout.addStretch()
        self.add_button = QtWidgets.QPushButton("Add Mode")
        self.add_button.clicked.connect(self._add_mode_cb)

        label = QtWidgets.QLabel(
            "Modes are by default self contained configurations. Specifying "
            "a parent for a mode causes the the mode \"inherits\" all actions "
            "defined in the parent, unless the mode configures its own actions "
            "for specific inputs."
        )
        label.setStyleSheet("QLabel { background-color : '#FFF4B0'; }")
        label.setWordWrap(True)
        label.setFrameShape(QtWidgets.QFrame.Box)
        label.setMargin(10)
        self.main_layout.addWidget(label)

        self.main_layout.addWidget(self.add_button)

        self._populate_mode_layout()

    def _populate_mode_layout(self):
        """Generates the mode layout UI displaying the different modes."""
        # Clear potentially existing content
        common.clear_layout(self.mode_layout)
        self.mode_dropdowns = {}
        self.mode_rename = {}
        self.mode_delete = {}
        self.mode_callbacks = {}

        # Obtain mode names and the mode they inherit from
        mode_list = {}
        for device in self._profile.devices.values():
            for mode in device.modes.values():
                if mode.name not in mode_list:
                    # FIXME: somewhere a mode's name is not set
                    if mode.name is None:
                        continue
                    mode_list[mode.name] = mode.inherit

        # Add header information
        self.mode_layout.addWidget(QtWidgets.QLabel("<b>Name</b>"), 0, 0)
        self.mode_layout.addWidget(QtWidgets.QLabel("<b>Parent</b>"), 0, 1)

        # Create UI element for each mode
        row = 1
        for mode, inherit in sorted(mode_list.items()):
            self.mode_layout.addWidget(QtWidgets.QLabel(mode), row, 0)
            self.mode_dropdowns[mode] = QtWidgets.QComboBox()
            self.mode_dropdowns[mode].addItem("None")
            self.mode_dropdowns[mode].setMinimumContentsLength(20)
            for name in sorted(mode_list.keys()):
                if name != mode:
                    self.mode_dropdowns[mode].addItem(name)

            self.mode_callbacks[mode] = self._create_inheritance_change_cb(mode)
            self.mode_dropdowns[mode].currentTextChanged.connect(
                self.mode_callbacks[mode]
            )
            self.mode_dropdowns[mode].setCurrentText(inherit)

            # Rename mode button
            self.mode_rename[mode] = QtWidgets.QPushButton(
                QtGui.QIcon("gfx/button_edit.png"), ""
            )
            self.mode_layout.addWidget(self.mode_rename[mode], row, 2)
            self.mode_rename[mode].clicked.connect(
                self._create_rename_mode_cb(mode)
            )
            # Delete mode button
            self.mode_delete[mode] = QtWidgets.QPushButton(
                QtGui.QIcon("gfx/mode_delete"), ""
            )
            self.mode_layout.addWidget(self.mode_delete[mode], row, 3)
            self.mode_delete[mode].clicked.connect(
                self._create_delete_mode_cb(mode)
            )

            self.mode_layout.addWidget(self.mode_dropdowns[mode], row, 1)
            row += 1

    def _create_inheritance_change_cb(self, mode):
        """Returns a lambda function callback to change the inheritance of
        a mode.

        This is required as otherwise lambda functions created within a
        function do not behave as desired.

        :param mode the mode for which the callback is being created
        :return customized lambda function
        """
        return lambda x: self._change_mode_inheritance(mode, x)

    def _create_rename_mode_cb(self, mode):
        """Returns a lambda function callback to rename a mode.

        This is required as otherwise lambda functions created within a
        function do not behave as desired.

        :param mode the mode for which the callback is being created
        :return customized lambda function
        """
        return lambda: self._rename_mode(mode)

    def _create_delete_mode_cb(self, mode):
        """Returns a lambda function callback to delete the given mode.

        This is required as otherwise lambda functions created within a
        function do not behave as desired.

        :param mode the mode to remove
        :return lambda function to perform the removal
        """
        return lambda: self._delete_mode(mode)

    def _change_mode_inheritance(self, mode, inherit):
        """Updates the inheritance information of a given mode.

        :param mode the mode to update
        :param inherit the name of the mode this mode inherits from
        """
        # Check if this inheritance would cause a cycle, turning the
        # tree structure into a graph
        has_inheritance_cycle = False
        if inherit != "None":
            all_modes = list(self._profile.devices.values())[0].modes
            cur_mode = inherit
            while all_modes[cur_mode].inherit is not None:
                if all_modes[cur_mode].inherit == mode:
                    has_inheritance_cycle = True
                    break
                cur_mode = all_modes[cur_mode].inherit

        # Update the inheritance information in the profile
        if not has_inheritance_cycle:
            for name, device in self._profile.devices.items():
                if inherit == "None":
                    inherit = None
                device.ensure_mode_exists(mode)
                device.modes[mode].inherit = inherit
            self.modes_changed.emit()

    def _rename_mode(self, mode_name):
        """Asks the user for the new name for the given mode.

        If the user provided name for the mode is invalid the
        renaming is aborted and no change made.

        :param mode_name new name for the mode
        """
        # Retrieve new name from the user
        name, user_input = QtWidgets.QInputDialog.getText(
                self,
                "Mode name",
                "",
                QtWidgets.QLineEdit.Normal,
                mode_name
        )
        if user_input:
            if name in gremlin.profile.mode_list(self._profile):
                gremlin.util.display_error(
                    "A mode with the name \"{}\" already exists".format(name)
                )
            else:
                # Update the renamed mode in each device
                for device in self._profile.devices.values():
                    device.modes[name] = device.modes[mode_name]
                    device.modes[name].name = name
                    del device.modes[mode_name]

                    # Update inheritance information
                    for mode in device.modes.values():
                        if mode.inherit == mode_name:
                            mode.inherit = name

                self.modes_changed.emit()

            self._populate_mode_layout()

    def _delete_mode(self, mode_name):
        """Removes the specified mode.

        Performs an update of the inheritance of all modes that inherited
        from the deleted mode.

        :param mode_name the name of the mode to delete
        """
        # Obtain mode from which the mode we want to delete inherits
        parent_of_deleted = None
        for mode in list(self._profile.devices.values())[0].modes.values():
            if mode.name == mode_name:
                parent_of_deleted = mode.inherit

        # Assign the inherited mode of the the deleted one to all modes that
        # inherit from the mode to be deleted
        for device in self._profile.devices.values():
            for mode in device.modes.values():
                if mode.inherit == mode_name:
                    mode.inherit = parent_of_deleted

        # Remove the mode from the profile
        for device in self._profile.devices.values():
            del device.modes[mode_name]

        # Update the ui
        self._populate_mode_layout()
        self.modes_changed.emit()

    def _add_mode_cb(self, checked):
        """Asks the user for a new mode to add.

        If the user provided name for the mode is invalid no mode is
        added.

        :param checked flag indicating whether or not the checkbox is active
        """
        name, user_input = QtWidgets.QInputDialog.getText(None, "Mode name", "")
        if user_input:
            if name in gremlin.profile.mode_list(self._profile):
                gremlin.util.display_error(
                    "A mode with the name \"{}\" already exists".format(name)
                )
            else:
                for device in self._profile.devices.values():
                    new_mode = gremlin.profile.Mode(device)
                    new_mode.name = name
                    device.modes[name] = new_mode
                self.modes_changed.emit()

            self._populate_mode_layout()


class DeviceInformationUi(common.BaseDialogUi):

    """Widget which displays information about all connected joystick
    devices."""

    def __init__(self, parent=None):
        """Creates a new instance.

        :param parent the parent widget
        """
        super().__init__(parent)

        self.devices = gremlin.joystick_handling.joystick_devices()

        self.setWindowTitle("Device Information")
        self.main_layout = QtWidgets.QGridLayout(self)

        self.main_layout.addWidget(QtWidgets.QLabel("<b>Name</b>"), 0, 0)
        self.main_layout.addWidget(QtWidgets.QLabel("<b>Axes</b>"), 0, 1)
        self.main_layout.addWidget(QtWidgets.QLabel("<b>Buttons</b>"), 0, 2)
        self.main_layout.addWidget(QtWidgets.QLabel("<b>Hats</b>"), 0, 3)
        self.main_layout.addWidget(QtWidgets.QLabel("<b>Vendor ID</b>"), 0, 4)
        self.main_layout.addWidget(QtWidgets.QLabel("<b>Product ID</b>"), 0, 5)
        self.main_layout.addWidget(QtWidgets.QLabel("<b>GUID"), 0, 6)

        for i, entry in enumerate(self.devices):
            self.main_layout.addWidget(
                QtWidgets.QLabel(entry.name), i+1, 0
            )
            self.main_layout.addWidget(
                QtWidgets.QLabel(str(entry.axis_count)), i+1, 1
            )
            self.main_layout.addWidget(
                QtWidgets.QLabel(str(entry.button_count)), i+1, 2
            )
            self.main_layout.addWidget(
                QtWidgets.QLabel(str(entry.hat_count)), i+1, 3
            )
            self.main_layout.addWidget(
                QtWidgets.QLabel("{:04X}".format(entry.vendor_id)), i+1, 4
            )
            self.main_layout.addWidget(
                QtWidgets.QLabel("{:04X}".format(entry.product_id)), i+1, 5
            )
            guid_field = QtWidgets.QLineEdit()
            guid_field.setText(str(entry.device_guid))
            guid_field.setReadOnly(True)
            guid_field.setMinimumWidth(230)
            guid_field.setMaximumWidth(230)
            self.main_layout.addWidget(guid_field, i+1, 6)

        self.close_button = QtWidgets.QPushButton("Close")
        self.close_button.clicked.connect(lambda: self.close())
        self.main_layout.addWidget(self.close_button, len(self.devices)+1, 3)


class SwapDevicesUi(common.BaseDialogUi):

    """UI Widget that allows users to swap identical devices."""

    def __init__(self, profile, parent=None):
        """Creates a new instance.

        :param profile the current profile
        :param parent the parent of this widget
        """
        super().__init__(parent)

        self.profile = profile

        # Create UI elements
        self.setWindowTitle("Swap Devices")
        self.main_layout = QtWidgets.QVBoxLayout(self)
        self._create_swap_ui()

    def _create_swap_ui(self):
        """Displays possible groups of swappable devices."""
        common.clear_layout(self.main_layout)

        profile_modifier = gremlin.profile.ProfileModifier(self.profile)
        device_list = profile_modifier.device_information_list()

        device_layout = QtWidgets.QGridLayout()
        for i, data in enumerate(device_list):
            # Ignore the keyboard
            if data.device_guid == dill.GUID_Keyboard:
                continue

            # Ignore devices with no remappable entries
            if (data.containers + data.conditions + data.merge_axis) == 0:
                continue

            # UI elements for this devic
            name = QtWidgets.QLabel(data.name)
            name.setAlignment(QtCore.Qt.AlignTop)
            labels = QtWidgets.QLabel("Containers\nConditions\nMerge Axis")
            counts = QtWidgets.QLabel("{:d}\n{:d}\n{:d}".format(
                data.containers, data.conditions, data.merge_axis
            ))
            counts.setAlignment(QtCore.Qt.AlignRight)
            record_button = QtWidgets.QPushButton(
                "Assigned to: {} - {}".format(data.device_guid, data.name)
            )
            record_button.clicked.connect(
                self._create_request_user_input_cb(data.device_guid)
            )

            # Combine labels and counts into it's own layout
            layout = QtWidgets.QHBoxLayout()
            layout.addWidget(labels)
            layout.addWidget(counts)
            layout.addStretch()

            # Put everything together
            device_layout.addWidget(name, i, 0)
            device_layout.addLayout(layout, i, 1)
            device_layout.addWidget(record_button, i, 2, QtCore.Qt.AlignTop)

        self.main_layout.addLayout(device_layout)
        self.main_layout.addStretch()

    def _create_request_user_input_cb(self, device_guid):
        """Creates the callback handling user device selection.

        :param device_guid GUID of the associated device
        :return callback function for user input selection handling
        """
        return lambda: self._request_user_input(
            lambda event: self._user_input_cb(event, device_guid)
        )

    def _user_input_cb(self, event, device_guid):
        """Processes input events to update the UI and model.

        :param event the input event to process
        :param device_guid GUID of the selected device
        """
        profile_modifier = gremlin.profile.ProfileModifier(self.profile)
        profile_modifier.change_device_guid(
            device_guid,
            event.device_guid
        )

        self._create_swap_ui()

    def _request_user_input(self, callback):
        """Prompts the user for the input to bind to this item.

        :param callback function to call with the accepted input
        """
        self.input_dialog = common.InputListenerWidget(
            callback,
            [
                gremlin.common.InputType.JoystickAxis,
                gremlin.common.InputType.JoystickButton,
                gremlin.common.InputType.JoystickHat
            ],
            return_kb_event=False,
            multi_keys=False
        )

        # Display the dialog centered in the middle of the UI
        root = self
        while root.parent():
            root = root.parent()
        geom = root.geometry()

        self.input_dialog.setGeometry(
            geom.x() + geom.width() // 2 - 150,
            geom.y() + geom.height() // 2 - 75,
            300,
            150
        )
        self.input_dialog.show()
