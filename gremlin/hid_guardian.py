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


import re
import sys
import os

from gremlin.error import HidGuardianError
import gremlin.util

# == Linux stub for winreg ==
winreg = None
if sys.platform == "win32":
    try:
        import winreg as winreg
    except ImportError:
        pass


class _winreg_stub:
    """Minimal stub for winreg so that HidGuardian code can run on Linux."""
    HKEY_LOCAL_MACHINE = None
    KEY_READ = 0
    KEY_WRITE = 0x20000000
    KEY_ALL_ACCESS = 0xF0037
    REG_MULTI_SZ = 7
    REG_DWORD = 4

    @staticmethod
    def OpenKey(*args, **kwargs):
        class _FakeHandle:
            def Close(self):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass
        return _FakeHandle()

    @staticmethod
    def OpenKeyEx(*args, **kwargs):
        return _winreg_stub.OpenKey(*args, **kwargs)

    @staticmethod
    def CreateKey(*args, **kwargs):
        return _winreg_stub.OpenKey(*args, **kwargs)

    @staticmethod
    def QueryValueEx(handle, name):
        return [None, 7]

    @staticmethod
    def SetValueEx(handle, name, reserved, vtype, value):
        pass

    @staticmethod
    def QueryInfoKey(handle):
        return (0, 0)

    @staticmethod
    def EnumKey(handle, index):
        raise OSError("No more keys")

    @staticmethod
    def EnumValue(handle, index):
        raise OSError("No more values")

    @staticmethod
    def DeleteKey(handle, name):
        pass

    @staticmethod
    def DeleteValue(handle, name):
        pass

winreg = winreg or _winreg_stub()


def _open_key(sub_key, access=winreg.KEY_READ):
    """Opens a key and returns the handle to it.

    :param sub_key the key to open
    :param access the access rights to use when opening the key
    :return the handle to the opened key
    """
    try:
        return winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            str(sub_key),
            access=access
        )
    except OSError:
        raise HidGuardianError(
            "Unable to open sub key \"{}\"".format(sub_key)
        )


def _clear_key(handle):
    """Clears a given key of any sub keys.

    :param handle the handle to the key which should be cleared
    """
    info = winreg.QueryInfoKey(handle)

    # No sub keys which means the parent can delete this now
    if info[0] == 0:
        return

    # Recursively clear sub keys
    for _ in range(info[0]):
        key = winreg.EnumKey(handle, 0)
        new_hdl = winreg.OpenKey(handle, key)
        _clear_key(new_hdl)
        winreg.DeleteKey(handle, key)


def _read_value(handle, value_name, value_type):
    """Reads a value from a key and returns it.

    :param handle the handle from which to read the value
    :param value_name the name of the value to read
    :param value_type the expected type of the value being read
    :return the read value and its type, returns a value of None if the value
        did not exist
    """
    try:
        data = winreg.QueryValueEx(handle, value_name)
        if data[1] != value_type:
            raise HidGuardianError(
                "Read invalid data type, {} expected {}".format(
                    data[1],
                    value_type
                )
            )
        return data
    except FileNotFoundError:
        # The particular value doesn't exist, return None instead
        return [None, value_type]
    except PermissionError:
        raise HidGuardianError(
            "Unable to read value \"{}\", insufficient permissions".format(
                value_name
            )
        )


def _write_value(handle, value_name, data):
    """Writes data to provided handle's value.

    :param handle the key handle to write the value of
    :param value_name name of the value to be written
    :param data data to be written, content and type
    """
    try:
        winreg.SetValueEx(handle, value_name, 0, data[1], data[0])
    except PermissionError:
        raise HidGuardianError(
            "Unable to write value \"{}\", insufficient permissions".format(
                value_name
            )
        )


class HidGuardian:

    """Interfaces with HidGuardians registry configuration."""

    root_path = r"SYSTEM\CurrentControlSet\Services\HidGuardian\Parameters"
    process_path = r"SYSTEM\CurrentControlSet\Services\HidGuardian\Parameters\Whitelist"
    storage_value = "AffectedDevices"

    def __init__(self):
        """Creates a new instance, ensuring proper initial state."""
        self._is_admin = gremlin.util.is_user_admin()
        if not self._is_admin:
            return

        # On Linux there is no HIDGuardian — stub is sufficient
        if sys.platform != "win32":
            return

        try:
            # Ensure we have the needed parameter entries
            handle = winreg.CreateKey(
                winreg.HKEY_LOCAL_MACHINE,
                HidGuardian.root_path
            )
            data = _read_value(
                handle,
                HidGuardian.storage_value,
                winreg.REG_MULTI_SZ
            )
            if data[0] is None:
                _write_value(
                    handle,
                    HidGuardian.storage_value,
                    [[], winreg.REG_MULTI_SZ]
                )
            handle.Close()

            # Ensure we can create per process keys
            handle = winreg.CreateKey(
                winreg.HKEY_LOCAL_MACHINE,
                HidGuardian.process_path
            )
            handle.Close()
        except OSError:
            raise HidGuardianError("Failed to initialize HidGuardian interface")

    def add_device(self, vendor_id, product_id):
        """Adds a new device to the list of devices managed by HidGuardian.

        :param vendor_id the USB vendor id
        :param product_id the USB product id
        """
        if not self._is_admin:
            return

        # On Linux — no-op
        if sys.platform != "win32":
            return

        # Add device to the list of devices that HidGuardian is intercepting
        handle = _open_key(HidGuardian.root_path, winreg.KEY_ALL_ACCESS)
        data = _read_value(
            handle,
            HidGuardian.storage_value,
            winreg.REG_MULTI_SZ
        )

        device_string = self._create_device_string(vendor_id, product_id)
        if data[0] is None:
            data[0] = []
        if device_string not in data[0]:
            data[0].append(device_string)
            _write_value(handle, HidGuardian.storage_value, data)

        # Update device list for any existing Gremlin process keys
        for pid in self._get_gremlin_process_ids():
            self._synchronize_process(pid)

    def remove_device(self, vendor_id, product_id):
        """Removes a device from the list of devices managed by HidGuardian

        :param vendor_id the USB vendor id
        :param product_id the USB product id
        """
        if not self._is_admin:
            return

        # On Linux — no-op
        if sys.platform != "win32":
            return

        # Get list of current devices and remove the specified one from it
        handle = _open_key(HidGuardian.root_path, winreg.KEY_ALL_ACCESS)
        data = _read_value(
            handle,
            HidGuardian.storage_value,
            winreg.REG_MULTI_SZ
        )

        device_string = self._create_device_string(vendor_id, product_id)
        if data[0] is not None:
            if device_string in data[0]:
                data[0].remove(device_string)
                _write_value(handle, HidGuardian.storage_value, data)

    def add_process(self, pid):
        """Adds a process to the list of processes that HidGuardian ignores.

        :param pid the process id of the process to whitelist
        """
        if not self._is_admin:
            return

        # On Linux — no-op
        if sys.platform != "win32":
            return

        # Ensure process is not already whitelisted
        if self.is_process_whitelisted(pid):
            return

        # Ensure the process key exists
        handle = _open_key(
            os.path.join(HidGuardian.process_path, str(pid)),
            winreg.KEY_ALL_ACCESS
        )

        info = winreg.QueryInfoKey(handle)
        if info[0] == 0:
            _write_value(
                handle,
                HidGuardian.storage_value,
                [[], winreg.REG_MULTI_SZ]
            )

        # Synchronize process with current list of devices
        self._synchronize_process(pid)

    def remove_process(self, pid):
        """Removes a process from the list of processes that HidGuardian ignores.

        :param pid the process id of the process to blacklist
        """
        if not self._is_admin:
            return

        # On Linux — no-op
        if sys.platform != "win32":
            return

        key = os.path.join(HidGuardian.process_path, str(pid))
        handle = _open_key(key, winreg.KEY_ALL_ACCESS)
        winreg.DeleteKey(winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            HidGuardian.process_path
        ), str(pid))
        handle.Close()

    def reset(self):
        """Clears HidGuardian of all state."""
        if not self._is_admin:
            return

        # On Linux — no-op
        if sys.platform != "win32":
            return

        handle = _open_key(HidGuardian.root_path, winreg.KEY_ALL_ACCESS)
        _clear_key(handle)
        winreg.DeleteKey(winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            HidGuardian.process_path
        ), HidGuardian.storage_value)
        winreg.DeleteKey(winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            HidGuardian.process_path
        ), HidGuardian.process_path)
        handle.Close()

    def is_device_managed(self, vendor_id, product_id):
        """Returns whether the device is managed by HidGuardian.

        :param vendor_id the USB vendor id
        :param product_id the USB product id
        :return True if the device is managed, False otherwise
        """
        if not self._is_admin:
            return False

        # On Linux — no-op
        if sys.platform != "win32":
            return False

        # Ensure the process key exists
        handle = _open_key(HidGuardian.root_path, winreg.KEY_READ)
        data = _read_value(
            handle, HidGuardian.storage_value, winreg.REG_MULTI_SZ
        )

        return self._create_device_string(
            vendor_id, product_id
        ) in data[0]

    def is_process_whitelisted(self, pid):
        """Checks whether the given process is whitelisted.

        :param pid the process id to check
        :return True of the process is whitelisted, False otherwise
        """
        if not self._is_admin:
            return False

        # On Linux — no-op
        if sys.platform != "win32":
            return False

        key = os.path.join(HidGuardian.process_path, str(pid))
        handle = _open_key(key, winreg.KEY_READ)
        handle.Close()
        return True

    def _create_device_string(self, vendor_id, product_id):
        """Converts a pair of vendor and product IDs into a string.

        :param vendor_id the USB vendor id
        :param product_id the USB product id
        :return string which can be used as an entry in the list of device GUIDs
        """
        return "VID_{:04X}&PID_{:04X}".format(vendor_id, product_id)

    def _synchronize_process(self, pid):
        """Synchronizes a single process' device list with the global list.

        :param pid the process id for which to synchronize the list
        """
        if not self._is_admin:
            return

        # On Linux — no-op
        if sys.platform != "win32":
            return

        handle = _open_key(
            os.path.join(HidGuardian.process_path, str(pid)),
            winreg.KEY_ALL_ACCESS
        )
        data = _read_value(
            handle, HidGuardian.storage_value, winreg.REG_MULTI_SZ
        )

        # Read global device list
        config_handle = _open_key(
            HidGuardian.root_path, winreg.KEY_READ
        )
        config_data = _read_value(
            config_handle,
            HidGuardian.storage_value,
            winreg.REG_MULTI_SZ
        )

        # Clear the process device list
        data[0] = []
        _write_value(handle, HidGuardian.storage_value, data)

        # Re-add it using the new global list
        data[0] = config_data[0][:]
        _write_value(handle, HidGuardian.storage_value, data)
        config_handle.Close()

    def _get_gremlin_process_ids(self):
        """Returns all process IDs corresponding to running Gremlin processes.

        :return list of process ids
        """
        handle = _open_key(HidGuardian.process_path, winreg.KEY_READ)
        process_ids = []
        try:
            for key_index in range(
                winreg.QueryInfoKey(handle)[0]
            ):
                process_id = winreg.EnumKey(handle, key_index)

                # If the key does not represent a valid process ID
                if not re.match("^[0-9]+$", process_id):
                    continue
                try:
                    int_process_id = int(process_id)
                except ValueError:
                    continue
                process_ids.append(int_process_id)
        except OSError:
            # The key is empty which is also valid
            pass

        winreg.CloseKey(handle)
        return process_ids


class HidGuardianController:

    """Centralized management of HIDGuardian state."""

    def __init__(self, pid):
        """Creates a new instance with the given pid.

        :param pid the process id which needs access to joystick input
        """
        self._pid = pid
        self._guardian = HidGuardian()

    def __del__(self):
        """Cleans up by clearing the process and device lists."""
        self._guardian.reset()

    def add_device(self, vendor_id, product_id):
        """Adds the device with the given vendor and product id for the
        managed process to be granted access by HidGuardian.

        :param vendor_id the USB vendor id
        :param product_id the USB product id
        """
        self._guardian.add_device(vendor_id, product_id)
        self._guardian.add_process(self._pid)

    def remove_device(self, vendor_id, product_id):
        """Removes the device with the given vendor and product id for the
        managed process to be denied access by HidGuardian.

        :param vendor_id the USB vendor id
        :param product_id the USB product id
        """
        # Remove the process from the whitelist for now
        self._guardian.remove_process(self._pid)

        # Ensure the device is no longer managed by HidGuardian
        if self._guardian.is_device_manageable(vendor_id, product_id):
            if self._guardian.is_device_managed_by_other_processes(
                vendor_id, product_id
            ):
                self._guardian.remove_device(vendor_id, product_id)
            # Re-add the process since we removed it for the previous operation
            self._guardian.add_process(self._pid)
        else:
            # Ensure the process is added to the whitelist
            self._guardian.add_process(self._pid)
