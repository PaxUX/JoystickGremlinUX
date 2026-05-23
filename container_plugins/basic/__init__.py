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

from xml.etree import ElementTree

from PyQt5 import QtCore, QtGui, QtWidgets

import gremlin
import gremlin.ui.common
import gremlin.ui.input_item


class BasicContainerWidget(gremlin.ui.input_item.AbstractContainerWidget):

    """Basic container which holds a single action.

    Shows a macro-specific instruction text box at the top when the
    assigned action is a Macro.
"""

    def __init__(self, profile_data, parent=None):
        gremlin.ui.input_item.AbstractContainerWidget.__init__(
            self, profile_data, parent
        )

    # ====== Overriden from base class ======

    def _select_tab(self, view_type):
        """Override: always show the Action tab so the instruction is
        visible and the action selector works."""
        # Find and select the Action tab
        for idx in range(self.dock_tabs.count()):
            if self.dock_tabs.tabText(idx) == "Action":
                self.dock_tabs.setCurrentIndex(idx)
                break
        # Also update the profile data's view type
        if hasattr(self.profile_data, "current_view_type"):
            self.profile_data.current_view_type = (
                gremlin.ui.common.ContainerViewTypes.Action
            )

    def redraw(self):
        gremlin.ui.common.clear_layout(self.action_layout)

        # Only show the Macro instruction when the assigned action is a Macro.
        has_macro_action = self._has_macro_action()
        if has_macro_action:
            self._add_instruction_box(self.action_layout)

        if self.profile_data.action_sets and \
                len(self.profile_data.action_sets[0]) > 0:
            # Action is assigned — render it
            for action in self.profile_data.action_sets[0]:
                wrapped = gremlin.ui.input_item.BasicActionWrapper(action)
                wrapped.closed.connect(self._create_closed_cb(action))
                self.action_layout.addWidget(wrapped)
            self.action_layout.addStretch(10)
        else:
            # No action yet — show the action-selector dropdown (below the
            # instruction box which was already added)
            if self.profile_data.get_device_type() == gremlin.common.DeviceType.VJoy:
                sel = gremlin.ui.common.ActionSelector(
                    gremlin.common.DeviceType.VJoy,
                )
            else:
                sel = gremlin.ui.common.ActionSelector(
                    self.profile_data.parent.input_type,
                )
            sel.action_added.connect(self._add_action)
            self.action_layout.addWidget(sel)

    def _create_action_ui(self):
        """Creates the UI components."""
        # Only add the instruction when the assigned action is a Macro.
        if self._has_macro_action():
            self._add_instruction_box(self.action_layout)

        if self.profile_data.action_sets and \
                len(self.profile_data.action_sets[0]) > 0:
            assert len(self.profile_data.action_sets) == 1
            self.profile_data.create_or_delete_virtual_button()
            widget = self._create_action_set_widget(
                self.profile_data.action_sets[0],
                "Basic",
                gremlin.ui.common.ContainerViewTypes.Action,
            )
            self.action_layout.addWidget(widget)
            widget.redraw()
            widget.model.data_changed.connect(self.container_modified.emit)
        else:
            if self.profile_data.get_device_type() == gremlin.common.DeviceType.VJoy:
                sel = gremlin.ui.common.ActionSelector(
                    gremlin.common.DeviceType.VJoy,
                )
            else:
                sel = gremlin.ui.common.ActionSelector(
                    self.profile_data.parent.input_type,
                )
            sel.action_added.connect(self._add_action)
            self.action_layout.addWidget(sel)

    # ====== Helper methods ======

    def _has_macro_action(self):
        """Return True if the currently assigned action is a Macro.

        :return True if the action is a Macro, False otherwise
        """
        if self.profile_data.action_sets and \
                len(self.profile_data.action_sets[0]) > 0:
            for action in self.profile_data.action_sets[0]:
                if action.tag == "macro":
                    return True
        return False

    def _add_instruction_box(self, layout):
        """Add the instruction text box at the TOP of the given layout.

        The box contains 3 lines of instructional text in orange font.
        It's placed at index 0 so it stays above all other widgets.
        """
        # Container widget for the instruction text
        widget = QtWidgets.QWidget()
        widget_layout = QtWidgets.QVBoxLayout(widget)
        widget_layout.setContentsMargins(6, 4, 6, 4)
        widget_layout.setSpacing(0)

        # Single text box with all 3 lines
        instr = QtWidgets.QLabel(
"Use Macro to build multi-step trigger sequences. Add new item, then map with drop down the required action.\n" \
"Recording doesn't function as you would expected. The 'Macro Setting:' Doesn't function, but is being left\n" \
"encase of further development. Macro is limited to only run one at a time. Multiple activations will be \n" \
"queued up. Running Macros in Parallel leads to issues with button press/release misalighment.\n" \
"For now cleanup & button management isn't worth implementing."
        )
        instr.setFont(QtGui.QFont("Sans", 10))
        instr.setStyleSheet(
            "color: #555; "
            "background: #fdf5e6; "
            "border: 1px solid #f0dcc0; "
            "border-radius: 4px; "
            "padding: 6px;"
        )
        instr.setAlignment(QtCore.Qt.AlignCenter)
        instr.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        widget_layout.addWidget(instr)

        # Add at position 0 (TOP of the layout) so it's above the
        # action selector or action widgets
        layout.insertWidget(0, widget)

    # ====== Other methods (unchanged) ======

    def _create_condition_ui(self):
        if len(self.profile_data.action_sets) > 0 and \
                self.profile_data.activation_condition_type == "action":
            assert len(self.profile_data.action_sets) == 1
            widget = self._create_action_set_widget(
                self.profile_data.action_sets[0],
                "Basic",
                gremlin.ui.common.ContainerViewTypes.Condition,
            )
            self.activation_condition_layout.addWidget(widget)
            widget.redraw()
            widget.model.data_changed.connect(self.container_modified.emit)

    def _add_action(self, action_name):
        plugin_manager = gremlin.plugin_manager.ActionPlugins()
        action_item = plugin_manager.get_class(action_name)(self.profile_data)
        self.profile_data.add_action(action_item)
        self.container_modified.emit()

    def _handle_interaction(self, widget, action):
        pass

    def _get_window_title(self):
        if len(self.profile_data.action_sets) > 0:
            return ", ".join(a.name for a in self.profile_data.action_sets[0])
        return "Basic"


class BasicContainerFunctor(gremlin.base_classes.AbstractFunctor):

    """Executes the contents of the associated basic container."""

    def __init__(self, container):
        super().__init__(container)
        self.action_set = gremlin.execution_graph.ActionSetExecutionGraph(
            container.action_sets[0],
        )

    def process_event(self, event, value):
        return self.action_set.process_event(event, value)


class BasicContainer(gremlin.base_classes.AbstractContainer):

    """Represents a container which holds exactly one action."""

    name = "Basic"
    tag = "basic"

    input_types = [
        gremlin.common.InputType.JoystickAxis,
        gremlin.common.InputType.JoystickButton,
        gremlin.common.InputType.JoystickHat,
        gremlin.common.InputType.Keyboard,
    ]
    interaction_types = []

    functor = BasicContainerFunctor
    widget = BasicContainerWidget

    def __init__(self, parent=None):
        super().__init__(parent)

    def add_action(self, action, index=-1):
        assert isinstance(action, gremlin.base_classes.AbstractAction)

        if action.get_input_type() == gremlin.common.InputType.JoystickAxis:
            remap_sets = []
            curve_sets = []
            for container in self.parent.containers:
                for action_set in container.action_sets:
                    for t_action in action_set:
                        if t_action.tag == "response-curve":
                            curve_sets.append(action_set)
                        elif t_action.tag == "remap":
                            remap_sets.append(action_set)

            if action.tag == "remap" and len(curve_sets) == 1 and \
                    len(remap_sets) == 0:
                curve_sets[0].append(action)
            elif action.tag == "response-curve" and len(remap_sets) == 1 and \
                    len(curve_sets) == 0:
                remap_sets[0].append(action)
            else:
                if index == -1:
                    self.action_sets.append([])
                    index = len(self.action_sets) - 1
                self.action_sets[index].append(action)
        else:
            if index == -1:
                self.action_sets.append([])
                index = len(self.action_sets) - 1
            self.action_sets[index].append(action)

        self.create_or_delete_virtual_button()

    def _parse_xml(self, node):
        pass

    def _generate_xml(self):
        node = ElementTree.Element("container")
        node.set("type", "basic")
        as_node = ElementTree.Element("action-set")
        for action in self.action_sets[0]:
            as_node.append(action.to_xml())
        node.append(as_node)
        return node

    def _is_container_valid(self):
        return len(self.action_sets) == 1


# Plugin definitions
version = 1
name = "basic"
create = BasicContainer
