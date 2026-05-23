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

from __future__ import annotations

import importlib
import logging
import os
import random
import string
import sys
import time

import dill

import gremlin
from gremlin import event_handler, input_devices, \
    joystick_handling, macro, sendinput, user_plugin, util
import vjoy as vjoy_module


class CodeRunner:

    """Runs the actual profile code."""

    def __init__(self):
        """Creates a new code runner instance."""
        self.event_handler = event_handler.EventHandler()
        self.event_handler.add_plugin(input_devices.JoystickPlugin())
        self.event_handler.add_plugin(input_devices.VJoyPlugin())
        self.event_handler.add_plugin(input_devices.KeyboardPlugin())

        self._inheritance_tree = None
        self._vjoy_curves = VJoyCurves()
        self._merge_axes = []
        self._running = False

    def is_running(self):
        """Returns whether or not the code runner is executing code.

        :return True if code is being executed, False otherwise
        """
        return self._running

    def start(self, inheritance_tree, settings, start_mode, profile):
        """Starts listening to events and loads all existing callbacks.

        :param inheritance_tree tree encoding inheritance between the
            different modes
        :param settings profile settings to apply at launch
        :param start_mode the mode in which to start Gremlin
        :param profile the profile to use when generating all the callbacks
        """
        # Reset states to their default values
        self._inheritance_tree = inheritance_tree
        self._reset_state()

        # Check if we want to override the start mode as determined by the
        # heuristic
        if settings.startup_mode is not None:
            if settings.startup_mode in gremlin.profile.mode_list(profile):
                start_mode = settings.startup_mode

        # Set default macro action delay
        gremlin.macro.MacroManager().default_delay = settings.default_delay

        # Retrieve list of current paths searched by Python
        system_paths = [os.path.normcase(os.path.abspath(p)) for p in sys.path]

        # Load the generated code
        try:
            # Populate custom module variable registry
            var_reg = user_plugin.variable_registry
            for plugin in profile.plugins:
                # Perform system path mangling for import statements
                path, _ = os.path.split(
                    os.path.normcase(os.path.abspath(plugin.file_name))
                )
                if path not in system_paths:
                    system_paths.append(path)

                # Load module specification so we can later create multiple
                # instances if desired
                spec = importlib.util.spec_from_file_location(
                    "".join(random.choices(string.ascii_lowercase, k=16)),
                    plugin.file_name
                )

                # Process each instance in turn
                for instance in plugin.instances:
                    # Skip all instances that are not fully configured
                    if not instance.is_configured():
                        continue

                    # Store variable values in the registry
                    for var in instance.variables.values():
                        var_reg.set(
                            plugin.file_name,
                            instance.name,
                            var.name,
                            var.value
                        )

                    # Load the modules
                    tmp = importlib.util.module_from_spec(spec)
                    tmp.__gremlin_identifier = (plugin.file_name, instance.name)
                    spec.loader.exec_module(tmp)

            # Update system path list searched by Python
            sys.path = system_paths

            # Create callbacks fom the user code
            callback_count = 0
            for dev_id, modes in input_devices.callback_registry.registry.items():
                for mode, events in modes.items():
                    for event, callback_list in events.items():
                        for callback in callback_list.values():
                            self.event_handler.add_callback(
                                dev_id,
                                mode,
                                event,
                                callback[0],
                                callback[1]
                            )
                            callback_count += 1

            # Add a fake keyboard action which does nothing to the callbacks
            # in every mode in order to have empty modes be "present"
            for mode_name in gremlin.profile.mode_list(profile):
                self.event_handler.add_callback(
                    0,
                    mode_name,
                    None,
                    lambda x: x,
                    False
                )

            # Create input callbacks based on the profile's content
            for device in profile.devices.values():
                for mode in device.modes.values():
                    for input_items in mode.config.values():
                        for input_item in input_items.values():
                            # Only add callbacks for input items that actually
                            # contain actions
                            if len(input_item.containers) == 0:
                                continue

                            event = event_handler.Event(
                                event_type=input_item.input_type,
                                device_guid=device.device_guid,
                                identifier=input_item.input_id
                            )

                            # Create possibly several callbacks depending
                            # on the input item's content
                            callbacks = []
                            for container in input_item.containers:
                                if not container.is_valid():
                                    logging.getLogger("system").warning(
                                        "Incomplete container ignored"
                                    )
                                    continue
                                callbacks.extend(container.generate_callbacks())

                            for cb_data in callbacks:
                                if cb_data.event is None:
                                    self.event_handler.add_callback(
                                        device.device_guid,
                                        mode.name,
                                        event,
                                        cb_data.callback,
                                        input_item.always_execute
                                    )
                                else:
                                    self.event_handler.add_callback(
                                        dill.GUID_Virtual,
                                        mode.name,
                                        cb_data.event,
                                        cb_data.callback,
                                        input_item.always_execute
                                    )

            # Create merge axis callbacks
            for entry in profile.merge_axes:
                merge_axis = MergeAxis(
                    entry["vjoy"]["vjoy_id"],
                    entry["vjoy"]["axis_id"],
                    entry["operation"]
                )
                self._merge_axes.append(merge_axis)

                # Lower axis callback
                event = event_handler.Event(
                    event_type=gremlin.common.InputType.JoystickAxis,
                    device_guid=entry["lower"]["device_guid"],
                    identifier=entry["lower"]["axis_id"]
                )
                self.event_handler.add_callback(
                    event.device_guid,
                    entry["mode"],
                    event,
                    merge_axis.update_axis1,
                    False
                )

                # Upper axis callback
                event = event_handler.Event(
                    event_type=gremlin.common.InputType.JoystickAxis,
                    device_guid=entry["upper"]["device_guid"],
                    identifier=entry["upper"]["axis_id"]
                )
                self.event_handler.add_callback(
                    event.device_guid,
                    entry["mode"],
                    event,
                    merge_axis.update_axis2,
                    False
                )

            # Create vJoy response curve setups
            self._vjoy_curves.profile_data = profile.vjoy_devices
            self.event_handler.mode_changed.connect(
                self._vjoy_curves.mode_changed
            )
            # logging.getLogger("system").log(
            #     logging.DEBUG,
            #     "CodeRunner.start: connected mode_changed signal, profile_data set with %d vjoy_devices",
            #     len(self._vjoy_curves.profile_data) if self._vjoy_curves.profile_data else 0
            # )

            # Use inheritance to build input action lookup table
            self.event_handler.build_event_lookup(inheritance_tree)

            # Set vJoy axis default values
            for vid, data in settings.vjoy_initial_values.items():
                vjoy_proxy = joystick_handling.VJoyProxy()[vid]
                for aid, value in data.items():
                    vjoy_proxy.axis(linear_index=aid).set_absolute_value(value)

            # Connect signals
            evt_listener = event_handler.EventListener()
            kb = input_devices.Keyboard()
            evt_listener.keyboard_event.connect(
                self.event_handler.process_event
            )
            evt_listener.joystick_event.connect(
                self.event_handler.process_event
            )
            evt_listener.virtual_event.connect(
                self.event_handler.process_event
            )
            evt_listener.keyboard_event.connect(kb.keyboard_event)
            evt_listener.gremlin_active = True

            input_devices.periodic_registry.start()
            macro.MacroManager().start()

            self.event_handler.change_mode(start_mode)
            self.event_handler.resume()
            self._running = True

            # Now that devices are initialized, apply initial response curves
            self._vjoy_curves.mode_changed(start_mode)

            sendinput.MouseController().start()
        except ImportError as e:
            util.display_error(
                "Unable to launch due to missing user plugin: {}"
                .format(str(e))
            )

    def stop(self):
        """Stops listening to events and unloads all callbacks."""
        # Disconnect all signals
        if self._running:
            evt_lst = event_handler.EventListener()
            kb = input_devices.Keyboard()
            evt_lst.keyboard_event.disconnect(self.event_handler.process_event)
            evt_lst.joystick_event.disconnect(self.event_handler.process_event)
            evt_lst.virtual_event.disconnect(self.event_handler.process_event)
            evt_lst.keyboard_event.disconnect(kb.keyboard_event)
            evt_lst.gremlin_active = False
            self.event_handler.mode_changed.disconnect(
                self._vjoy_curves.mode_changed
            )
        self._running = False

        # Empty callback registry
        input_devices.callback_registry.clear()
        self.event_handler.clear()

        # Stop periodic events and clear registry
        input_devices.periodic_registry.stop()
        input_devices.periodic_registry.clear()

        macro.MacroManager().stop()
        sendinput.MouseController().stop()

        # Remove all claims on VJoy devices
        joystick_handling.VJoyProxy.reset()

    def _reset_state(self):
        """Resets all states to their default values."""
        self.event_handler._active_mode =\
            list(self._inheritance_tree.keys())[0]
        self.event_handler._previous_mode =\
            list(self._inheritance_tree.keys())[0]
        input_devices.callback_registry.clear()


class VJoyCurves:

    """Handles setting response curves on vJoy devices."""

    def __init__(self):
        """Creates a new instance"""
        self.profile_data = None

    def mode_changed(self, mode_name):
        """Called when the mode changes and updates vJoy response curves.

        :param mode_name the name of the new mode
        """
        # logging.getLogger("system").debug(
        #     "VJoyCurves.mode_changed CALLED with mode=%s profile_data=%s",
        #     mode_name, "SET" if self.profile_data else "NONE"
        # )
        if not self.profile_data:
            # logging.getLogger("system").debug(
            #     "VJoyCurves.mode_changed: SKIPPED (no profile data)"
            # )
            return

        # logging.getLogger("system").debug(
        #     "VJoyCurves.mode_changed: mode=%s, vjoy_devices=%d",
        #     mode_name, len(self.profile_data)
        # )

        vjoy = gremlin.joystick_handling.VJoyProxy()
        active_vjoy = {vid: dev for vid, dev in gremlin.joystick_handling.VJoyProxy.vjoy_devices.items()}
        # logging.getLogger("system").debug(
        #     "VJoyCurves: live vJoy devices: %s",
        #     {vid: dev.axis_count for vid, dev in active_vjoy.items()}
        # )
        # logging.getLogger("system").debug(
        #     "VJoyCurves: profile vjoy_devices=%d", len(self.profile_data)
        # )
        # for guid, device in self.profile_data.items():
        #     logging.getLogger("system").debug(
        #         "VJoyCurves: profile device guid=%s type=%s modes=%s",
        #         str(guid), device.type, list(device.modes.keys())
        #     )
        #     for mname, mode_obj in device.modes.items():
        #         axes_config = mode_obj.config.get(gremlin.common.InputType.JoystickAxis, {})
        #         logging.getLogger("system").debug(
        #             "VJoyCurves:   device guid=%s mode=%s axes_in_config=%d",
        #             str(guid), mname, len(axes_config)
        #         )
        #         for aid, data in axes_config.items():
        #             has_curve = False
        #             if len(data.containers) > 0 and data.containers[0].action_sets:
        #                 for act_set in data.containers[0].action_sets:
        #                     for act in act_set:
        #                         if hasattr(act, 'mapping_type'):
        #                             has_curve = True
        #                             logging.getLogger("system").debug(
        #                                 "VJoyCurves:   AXIS %d has response-curve: mapping_type=%s",
        #                                 aid, act.mapping_type
        #                             )

        for guid, device in self.profile_data.items():
            # logging.getLogger("system").debug(
            #     "VJoyCurves: checking device guid=%s, modes=%s",
            #     str(guid), list(device.modes.keys())
            # )
            if mode_name in device.modes:
                mode = device.modes[mode_name]
                # Check all config types present
                # logging.getLogger("system").debug(
                #     "VJoyCurves: mode config keys=%s",
                #     list(mode.config.keys())
                # )
                if gremlin.common.InputType.JoystickAxis in mode.config:
                    for aid, data in mode.config[
                            gremlin.common.InputType.JoystickAxis
                    ].items():
                        # logging.getLogger("system").debug(
                        #     "VJoyCurves: axis_id=%d, containers=%d",
                        #     aid, len(data.containers)
                        # )
                        if len(data.containers) == 0:
                            continue
                        # logging.getLogger("system").debug(
                        #     "VJoyCurves: containers[0].action_sets=%d, actions[0]=%d",
                        #     len(data.containers[0].action_sets),
                        #     len(data.containers[0].action_sets[0]) if data.containers[0].action_sets else 0
                        # )
                        # Get vJoy device id from guid
                        vjoy_id = joystick_handling.vjoy_id_from_guid(guid)
                        # aid is from the profile — it's a linear axis index (1-based).
                        # The vJoy C module / is_axis_valid need DLL axis constants (48=X, 49=Y, etc.)
                        inv_lookup = {v: k for k, v in vjoy[vjoy_id]._axis_lookup.items()}
                        axis_id = inv_lookup.get(aid)
                        if axis_id is None:
                            logging.getLogger("system").debug(
                                "VJoyCurves: SKIP -> no DLL constant for linear index %d",
                                aid
                            )
                            continue
                        # logging.getLogger("system").debug(
                        #     "VJoyCurves: resolved vjoy_id=%d, axis_id=%d (linear=%d)",
                        #     vjoy_id, axis_id, aid
                        # )

                        is_valid = vjoy[vjoy_id].is_axis_valid(axis_id=axis_id)
                        # logging.getLogger("system").debug(
                        #     "VJoyCurves: is_axis_valid(axis_id=%d, vjoy_id=%d)=%s",
                        #     axis_id, vjoy_id, is_valid
                        # )
                        if len(data.containers) > 0 and is_valid:
                            if data.containers[0].action_sets and len(data.containers[0].action_sets) > 0 and data.containers[0].action_sets[0]:
                                action = data.containers[0].action_sets[0][0]
                                # logging.getLogger("system").debug(
                                #     "VJoyCurves: >>>>>>>> APPLYING curve to vJoy[%d].axis[%d] (linear=%d): type=%s, points=%s, deadzone=%s",
                                #     vjoy_id, axis_id, aid, action.mapping_type, action.control_points, action.deadzone
                                # )
                                vjoy[vjoy_id].axis(axis_id=axis_id).set_deadzone(*action.deadzone)
                                vjoy[vjoy_id].axis(axis_id=axis_id).set_response_curve(
                                    action.mapping_type,
                                    action.control_points
                                )
                                # logging.getLogger("system").debug(
                                #     "VJoyCurves: AFTER set_response_curve -> axis._response_curve_fn=%s",
                                #     vjoy[vjoy_id].axis(axis_id=axis_id)._response_curve_fn
                                # )
            #                 else:
            #                     logging.getLogger("system").debug(
            #                         "VJoyCurves: SKIP -> action_sets[0] is empty"
            #                     )
            #             elif not len(data.containers) > 0:
            #                 logging.getLogger("system").debug(
            #                     "VJoyCurves: SKIP -> no containers (containers=%d)",
            #                     len(data.containers)
            #                 )
            #             else:
            #                 logging.getLogger("system").debug(
            #                     "VJoyCurves: SKIP -> axis not valid (is_valid=%s, axis_id=%d)",
            #                     is_valid, axis_id
            #                 )
            # else:
            #     logging.getLogger("system").debug(
            #         "VJoyCurves: NO MATCH for mode '%s' in device %s (modes: %s)",
            #         mode_name, str(guid), list(device.modes.keys())
            #     )


class MergeAxis:

    """Merges inputs from two distinct axes into a single one."""

    def __init__(
            self,
            vjoy_id: int,
            input_id: int,
            operation: gremlin.common.MergeAxisOperation
    ):
        self.axis_values = [0.0, 0.0]
        self.vjoy_id = vjoy_id
        self.input_id = input_id
        self.operation = operation

    def _update(self):
        """Updates the merged axis value."""
        value = 0.0
        if self.operation == gremlin.common.MergeAxisOperation.Average:
            value = (self.axis_values[0] - self.axis_values[1]) / 2.0
        elif self.operation == gremlin.common.MergeAxisOperation.Minimum:
            value = min(self.axis_values[0], self.axis_values[1])
        elif self.operation == gremlin.common.MergeAxisOperation.Maximum:
            value = max(self.axis_values[0], self.axis_values[1])
        elif self.operation == gremlin.common.MergeAxisOperation.Sum:
            value = gremlin.util.clamp(
                self.axis_values[0] + self.axis_values[1],
                -1.0,
                1.0
            )
        else:
            raise gremlin.error.GremlinError(
                "Invalid merge axis operation detected, \"{}\"".format(
                    str(self.operation)
                )
            )

        gremlin.joystick_handling.VJoyProxy()[self.vjoy_id]\
            .axis(self.input_id).value = value

    def update_axis1(self, event: gremlin.event_handler.Event):
        """Updates information for the first axis.

        :param event data event for the first axis
        """
        self.axis_values[0] = event.value
        self._update()

    def update_axis2(self, event: gremlin.event_handler.Event):
        """Updates information for the second axis.

        :param event data event for the second axis
        """
        self.axis_values[1] = event.value
        self._update()
