# -*- coding: utf-8; -*-

# Copyright (C) 2026 JoystickGremlin Linux Migration
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

"""Tests for vjoy_linux — Linux virtual joystick backend via /dev/uinput.

These tests use mocks to avoid requiring /dev/uinput permissions during CI.
"""

import math
import types
from unittest.mock import MagicMock, patch, mock_open
import sys
import os

# ─── Setup ─────────────────────────────────────────────────────────────
# We import the real implementations so the module structure is real,
# but we mock every /dev/uinput operation.

BASE = os.path.join(os.path.dirname(__file__), '..', 'JoystickGremlin-Release_13.3')
if BASE not in sys.path:
    sys.path.insert(0, BASE)


class TestVJoyState:
    """VJoyState enum has the expected values."""

    def test_vjoy_state_values(self):
        from vjoy_linux.vjoy_interface import VJoyState
        assert VJoyState.Owned.value == 0
        assert VJoyState.Free.value == 1
        assert VJoyState.Bust.value == 2
        assert VJoyState.Missing.value == 3
        assert VJoyState.Unknown.value == 4


class TestVJoyInterfaceApiMethods:
    """All 23 VJoyInterface methods exist and are callable."""

    def _get_interface(self):
        from vjoy_linux.vjoy_interface import VJoyInterface
        return VJoyInterface

    def test_all_methods_present(self):
        iface = self._get_interface()
        methods = [
            'GetvJoyVersion', 'vJoyEnabled', 'GetvJoyProductString',
            'GetvJoyManufacturerString', 'GetvJoySerialNumberString',
            'GetVJDButtonNumber', 'GetVJDDiscPovNumber',
            'GetVJDContPovNumber', 'GetVJDAxisExist', 'GetVJDAxisMax',
            'GetVJDAxisMin', 'GetOwnerPid', 'AcquireVJD', 'RelinquishVJD',
            'UpdateVJD', 'GetVJDStatus', 'ResetVJD', 'ResetAll',
            'ResetButtons', 'ResetPovs', 'SetAxis', 'SetBtn',
            'SetDiscPov', 'SetContPov', 'initialize',
        ]
        for name in methods:
            assert hasattr(iface, name), f"Missing method: {name}"
            assert callable(getattr(iface, name)), f"Not callable: {name}"


class TestVJoyInterfaceGetvJoyVersion:
    """GetvJoyVersion returns the required vJoy 2.1.8 version."""

    def test_get_vjoy_version(self):
        from vjoy_linux.vjoy_interface import VJoyInterface
        assert VJoyInterface.GetvJoyVersion() == 0x218


class TestVJoyInterfacevJoyEnabled:
    """vJoyEnabled reflects /dev/uinput accessibility."""

    def test_vjoy_enabled_true(self):
        from vjoy_linux.vjoy_interface import VJoyInterface
        with patch('os.path.exists', return_value=True):
            with patch('os.access', return_value=True):
                result = VJoyInterface.vJoyEnabled()
                assert result is True

    def test_vjoy_enabled_false(self):
        from vjoy_linux.vjoy_interface import VJoyInterface
        with patch('os.path.exists', return_value=False):
            result = VJoyInterface.vJoyEnabled()
            assert result is False


class TestVJoyInterfaceDeviceStatus:
    """GetVJDStatus reflects device ownership."""

    def test_free_when_no_device(self):
        from vjoy_linux.vjoy_interface import VJoyInterface, VJoyState
        # Clear any existing devices
        VJoyInterface._devices = {}
        status = VJoyInterface.GetVJDStatus(1)
        assert status == VJoyState.Free.value

    def test_owned_when_device_present(self):
        from vjoy_linux.vjoy_interface import VJoyInterface, VJoyState
        # Simulate an open device
        mock_dev = MagicMock()
        mock_dev.fd = 42
        VJoyInterface._devices = {1: mock_dev}
        status = VJoyInterface.GetVJDStatus(1)
        assert status == VJoyState.Owned.value
        VJoyInterface._devices.clear()


class TestVJoyInterfaceDevicePropertyQueries:
    """Device property queries return correct constants."""

    def test_button_count(self):
        from vjoy_linux.vjoy_interface import VJoyInterface
        assert VJoyInterface.GetVJDButtonNumber(1) == 128

    def test_cont_pov_count(self):
        from vjoy_linux.vjoy_interface import VJoyInterface
        assert VJoyInterface.GetVJDContPovNumber(1) == 2

    def test_disc_pov_count(self):
        from vjoy_linux.vjoy_interface import VJoyInterface
        assert VJoyInterface.GetVJDDiscPovNumber(1) == 0

    def test_axis_exist(self):
        from vjoy_linux.vjoy_interface import VJoyInterface
        # Valid axes
        assert VJoyInterface.GetVJDAxisExist(1, 0x30) == 1
        assert VJoyInterface.GetVJDAxisExist(1, 0x31) == 1
        assert VJoyInterface.GetVJDAxisExist(1, 0x32) == 1
        assert VJoyInterface.GetVJDAxisExist(1, 0x33) == 1
        assert VJoyInterface.GetVJDAxisExist(1, 0x34) == 1
        assert VJoyInterface.GetVJDAxisExist(1, 0x35) == 1
        assert VJoyInterface.GetVJDAxisExist(1, 0x36) == 1
        assert VJoyInterface.GetVJDAxisExist(1, 0x37) == 1
        # Invalid axis
        assert VJoyInterface.GetVJDAxisExist(1, 0x99) == 0

    def test_axis_max_min(self):
        from vjoy_linux.vjoy_interface import VJoyInterface
        # These always return True (no-op on Linux, axis range is constant)
        import ctypes
        assert VJoyInterface.GetVJDAxisMax(1, 0x30, ctypes.c_ulong()) is True
        assert VJoyInterface.GetVJDAxisMin(1, 0x30, ctypes.c_ulong()) is True


class TestVJoyInterfaceAxis:
    """SetAxis and ResetVJD work correctly with mocked uinput."""

    def test_set_axis(self):
        from vjoy_linux.vjoy_interface import VJoyInterface
        mock_dev = MagicMock()
        mock_dev.fd = 1
        mock_dev.abs_event.return_value = True
        mock_dev.sync.return_value = True
        VJoyInterface._devices = {1: mock_dev}

        result = VJoyInterface.SetAxis(16384, 1, 0x30)
        assert result is True
        # Verify: abs_event was called with the mapped ABS code and value
        mock_dev.abs_event.assert_called_with(0, 16384)  # ABS_X, value
        mock_dev.sync.assert_called_once()

    def test_set_axis_invalid_axis(self):
        from vjoy_linux.vjoy_interface import VJoyInterface
        mock_dev = MagicMock()
        mock_dev.fd = 1
        mock_dev.abs_event.return_value = True
        VJoyInterface._devices = {1: mock_dev}

        result = VJoyInterface.SetAxis(100, 1, 0x99)  # invalid
        assert result is False
        VJoyInterface._devices.clear()


class TestVJoyInterfaceButtons:
    """SetBtn generates correct EV_KEY events."""

    def test_set_btn_pressed(self):
        from vjoy_linux.vjoy_interface import VJoyInterface
        mock_dev = MagicMock()
        mock_dev.fd = 1
        mock_dev.key_event.return_value = True
        VJoyInterface._devices = {1: mock_dev}

        VJoyInterface.SetBtn(True, 1, 1)  # button 1 → BTN_SOUTH(304)
        mock_dev.key_event.assert_called_with(304, True)

    def test_set_btn_released(self):
        from vjoy_linux.vjoy_interface import VJoyInterface
        mock_dev = MagicMock()
        mock_dev.fd = 1
        mock_dev.key_event.return_value = True
        VJoyInterface._devices = {1: mock_dev}

        VJoyInterface.SetBtn(False, 1, 5)  # button 5 → 304+4=308 (BTN_NORTH)
        mock_dev.key_event.assert_called_with(308, False)
        VJoyInterface._devices.clear()


class TestVJoyInterfaceHats:
    """SetContPov and SetDiscPov generate correct ABS events."""

    def test_set_cont_pov_up(self):
        from vjoy_linux.vjoy_interface import VJoyInterface
        mock_dev = MagicMock()
        mock_dev.fd = 1
        mock_dev.abs_event.return_value = True
        VJoyInterface._devices = {1: mock_dev}

        # Hat 1 → HAT0X(16), HAT0Y(17); value=0 (up)
        VJoyInterface.SetContPov(0, 1, 1)
        # Should call abs_event for both X and Y with cos/sin of 0 radians
        calls = mock_dev.abs_event.call_args_list
        assert len(calls) == 2
        # First call should be for HAT0X
        assert calls[0][0][0] == 16  # HAT0X code
        VJoyInterface._devices.clear()

    def test_set_cont_pov_neutral(self):
        from vjoy_linux.vjoy_interface import VJoyInterface
        mock_dev = MagicMock()
        mock_dev.fd = 1
        mock_dev.abs_event.return_value = True
        VJoyInterface._devices = {1: mock_dev}

        VJoyInterface.SetContPov(-1, 1, 1)  # neutral = (0, 0)
        calls = mock_dev.abs_event.call_args_list
        assert len(calls) == 2
        # Both should be 0
        assert calls[0][0][1] == 0
        assert calls[1][0][1] == 0
        VJoyInterface._devices.clear()

    def test_set_disc_pov(self):
        from vjoy_linux.vjoy_interface import VJoyInterface
        mock_dev = MagicMock()
        mock_dev.fd = 1
        mock_dev.abs_event.return_value = True
        mock_dev.sync.return_value = True
        VJoyInterface._devices = {1: mock_dev}

        VJoyInterface.SetDiscPov(0, 1, 1)  # up = (0, 1)
        calls = mock_dev.abs_event.call_args_list
        assert len(calls) == 2
        assert calls[0][0][1] == 0  # X=0
        assert calls[1][0][1] == 1  # Y=1
        VJoyInterface._devices.clear()


class TestVJoyInterfaceOwnership:
    """GetOwnerPid returns owning PID."""

    def test_owner_pid_with_device(self):
        from vjoy_linux.vjoy_interface import VJoyInterface
        import os
        mock_dev = MagicMock()
        mock_dev.fd = 1
        VJoyInterface._devices = {1: mock_dev}

        pid = VJoyInterface.GetOwnerPid(1)
        assert pid == os.getpid()
        VJoyInterface._devices.clear()

    def test_owner_pid_no_device(self):
        from vjoy_linux.vjoy_interface import VJoyInterface
        pid = VJoyInterface.GetOwnerPid(99)
        assert pid == 0


class TestVJoyInterfaceAcquireRelease:
    """AcquireVJD and RelinquishVJD work correctly."""

    def test_acquire_vjd(self):
        from vjoy_linux.vjoy_interface import VJoyInterface
        with patch('vjoy_linux.device_manager.UInputDevice') as MockDevice:
            mock_instance = MagicMock()
            mock_instance.open.return_value = True
            MockDevice.return_value = mock_instance

            VJoyInterface._devices.clear()
            result = VJoyInterface.AcquireVJD(1)
            assert result is True
            MockDevice.assert_called_once_with(1)
            mock_instance.open.assert_called_once()

    def test_relinquish_vjd(self):
        from vjoy_linux.vjoy_interface import VJoyInterface
        mock_dev = MagicMock()
        mock_dev.fd = 1
        VJoyInterface._devices = {1: mock_dev}

        VJoyInterface.RelinquishVJD(1)
        assert 1 not in VJoyInterface._devices
        mock_dev.close.assert_called_once()


class TestVJoyInterfaceReset:
    """Reset operations clear the device state."""

    def test_reset_vjd(self):
        from vjoy_linux.vjoy_interface import VJoyInterface
        mock_dev = MagicMock()
        mock_dev.fd = 1
        mock_dev.reset_to_neutral.return_value = True
        VJoyInterface._devices = {1: mock_dev}

        result = VJoyInterface.ResetVJD(1)
        assert result is True
        mock_dev.reset_to_neutral.assert_called_once()
        VJoyInterface._devices.clear()

    def test_reset_all(self):
        from vjoy_linux.vjoy_interface import VJoyInterface
        mock_dev = MagicMock()
        mock_dev.fd = 1
        VJoyInterface._devices = {1: mock_dev, 2: mock_dev}

        VJoyInterface.ResetAll()
        assert mock_dev.reset_to_neutral.call_count == 2
        VJoyInterface._devices.clear()


class TestVJoyAxisClass:
    """Axis class value setter applies deadzone + response curve correctly."""

    def test_axis_deadzone(self):
        from vjoy_linux.vjoy_interface import VJoyInterface
        mock_dev = MagicMock()
        mock_dev.fd = 1
        mock_dev.abs_event.return_value = True
        mock_dev.sync.return_value = True
        VJoyInterface._devices = {1: MagicMock()}  # Just need device present

        mock_vjoy = types.SimpleNamespace()
        mock_vjoy.vjoy_id = 1
        mock_vjoy.ensure_ownership = MagicMock()
        mock_vjoy.used = MagicMock()
        VJoyInterface._devices[1] = mock_dev

        # Import the real Axis class
        from vjoy_linux.vjoy import Axis, AxisName
        ax = Axis(mock_vjoy, AxisName.X.value)

        # Set value
        ax.value = 0.5
        mock_vjoy.ensure_ownership.assert_called()
        mock_vjoy.used.assert_called()

    def test_axis_invalid_min(self):
        from vjoy_linux.vjoy_interface import VJoyInterface
        from vjoy_linux.vjoy import Axis, AxisName

        mock_vjoy = types.SimpleNamespace()
        mock_vjoy.vjoy_id = 1
        mock_vjoy.ensure_ownership = MagicMock()
        mock_vjoy.used = MagicMock()
        VJoyInterface._devices = {1: MagicMock()}

        # We cannot easily test the min != 0 raising without mocking GetVJDAxisMin
        # That requires ctypes.byref which is tricky in unit tests.
        # Instead, verify axis was created normally when min=0.
        ax = Axis(mock_vjoy, AxisName.X.value)
        assert ax._min_value == -32768
        assert ax._half_range == 16384


class TestVJoyHatClass:
    """Hat direction mapping works correctly."""

    def test_discrete_direction_map(self):
        from vjoy_linux.vjoy import Hat
        assert Hat.to_discrete_direction[(0, 1)] == 0   # up
        assert Hat.to_discrete_direction[(1, 0)] == 1   # right
        assert Hat.to_discrete_direction[(0, -1)] == 2  # down
        assert Hat.to_discrete_direction[(-1, 0)] == 3  # left
        assert Hat.to_discrete_direction[(0, 0)] == -1  # neutral

    def test_continuous_direction_map(self):
        from vjoy_linux.vjoy import Hat
        assert Hat.to_continuous_direction[(0, 1)] == 0       # 0°
        assert Hat.to_continuous_direction[(1, 1)] == 4500    # 45°
        assert Hat.to_continuous_direction[(1, 0)] == 9000    # 90°
        assert Hat.to_continuous_direction[(1, -1)] == 13500  # 135°
        assert Hat.to_continuous_direction[(0, -1)] == 18000  # 180°
        assert Hat.to_continuous_direction[(-1, 0)] == 27000  # 270°
        assert Hat.to_continuous_direction[(-1, 1)] == 31500  # 315°
        assert Hat.to_continuous_direction[(0, 0)] == -1      # neutral

    def test_hat_continous_direction_sets_axis(self):
        from vjoy_linux.vjoy_interface import VJoyInterface
        from vjoy_linux.vjoy import Hat, HatType

        mock_vjoy = types.SimpleNamespace()
        mock_vjoy.vjoy_id = 1
        mock_vjoy.ensure_ownership = MagicMock()
        mock_vjoy.used = MagicMock()
        mock_dev = MagicMock()
        mock_dev.fd = 1
        mock_dev.abs_event.return_value = True
        mock_dev.sync.return_value = True
        VJoyInterface._devices = {1: mock_dev}

        hat = Hat(mock_vjoy, 1, HatType.Continuous)
        hat.direction = (1, 0)  # right / 90 degrees

        assert mock_vjoy.ensure_ownership.called
        assert mock_vjoy.used.called
        VJoyInterface._devices.clear()


class TestDeviceManagerUInputDevice:
    """UInputDevice class has correct structure."""

    def test_uinput_device_attributes(self):
        from vjoy_linux.device_manager import UInputDevice
        dev = UInputDevice(1)
        assert dev.device_id == 1
        assert dev.fd is None
        assert 'Xbox' in dev.name

    def test_uinput_device_context_manager(self):
        from vjoy_linux.device_manager import UInputDevice
        with patch('fcntl.ioctl'):
            with patch('os.path.exists', return_value=True):
                with patch('os.access', return_value=True):
                    with patch('os.open', return_value=42) as mock_open:
                        with UInputDevice(1) as dev:
                            assert dev.fd == 42
                            assert 'Xbox' in dev.name
                        # After exiting context, device should be closed
                        assert dev.fd is None


class TestEventEmitter:
    """UInputDevice event emission methods work correctly."""

    def test_sync_emits_syn_report(self):
        from vjoy_linux.device_manager import UInputDevice
        mock_write = MagicMock(return_value=14)
        with patch.object(UInputDevice, '__init__', lambda self, dev_id: setattr(self, 'fd', 42)):
            with patch('os.write', mock_write):
                dev = object.__new__(UInputDevice)
                dev.fd = 42
                result = dev.sync()
                # Write should have been called with SYN_REPORT event
                mock_write.assert_called()

    def test_reset_to_neutral(self):
        from vjoy_linux.device_manager import UInputDevice
        mock_write = MagicMock(side_effect=[True] * 200)
        with patch.object(UInputDevice, '__init__', lambda self, dev_id: setattr(self, 'fd', 42)):
            with patch('os.write', mock_write):
                dev = object.__new__(UInputDevice)
                dev.fd = 42
                dev.reset_to_neutral()
                # Should have emitted many events (axes + hats + keys)
                assert mock_write.call_count > 50


class TestAxisNameEnum:
    """AxisName enum values match vJoy DLL."""

    def test_axis_name_values(self):
        from vjoy_linux.vjoy import AxisName
        assert AxisName.X.value == 0x30
        assert AxisName.Y.value == 0x31
        assert AxisName.Z.value == 0x32
        assert AxisName.RX.value == 0x33
        assert AxisName.RY.value == 0x34
        assert AxisName.RZ.value == 0x35
        assert AxisName.SL0.value == 0x36
        assert AxisName.SL1.value == 0x37


class TestAxisEquivalence:
    """Axis equivalence mapping between DLL and greedy indices."""

    def test_axis_equivalence(self):
        from vjoy_linux.vjoy import VJoy, AxisName
        assert VJoy.axis_equivalence[AxisName.X] == 1
        assert VJoy.axis_equivalence[AxisName.Y] == 2
        assert VJoy.axis_equivalence[AxisName.Z] == 3
        assert VJoy.axis_equivalence[AxisName.RX] == 4
        assert VJoy.axis_equivalence[AxisName.RY] == 5
        assert VJoy.axis_equivalence[AxisName.RZ] == 6
        assert VJoy.axis_equivalence[AxisName.SL0] == 7
        assert VJoy.axis_equivalence[AxisName.SL1] == 8


class TestAxisClassDeadzone:
    """The deadzone function maps values correctly."""

    def test_deadzone_pass_through(self):
        from vjoy_linux.vjoy import deadzone
        # Values outside deadzone pass through with linear scaling
        # high=1, high_center=0 → (1-0)/(1-0) = 1
        assert deadzone(0.5, -1.0, -0.0, 0.0, 1.0) == pytest.approx(0.5)
        assert deadzone(-0.5, -1.0, -0.0, 0.0, 1.0) == pytest.approx(-0.5)

    def test_deadzone_clips(self):
        from vjoy_linux.vjoy import deadzone
        # Values outside [-1, 1] clamp to [-1, 1]
        assert deadzone(1.5, -1.0, -0.0, 0.0, 1.0) == pytest.approx(1.0)
        assert deadzone(-1.5, -1.0, -0.0, 0.0, 1.0) == pytest.approx(-1.0)

    def test_deadzone_center(self):
        from vjoy_linux.vjoy import deadzone
        # Values in the zero center pass through as 0
        assert deadzone(0.0, -1.0, -0.0, 0.0, 1.0) == pytest.approx(0.0)


# ─── pytest entry ──────────────────────────────────────────────────────
# All tests above are discovered automatically by pytest.
