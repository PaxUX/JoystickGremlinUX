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

"""
Gremlin — core application package.

On Linux this package automatically runs system capability checks (__init__.py
side-effect) and re-exports public APIs for the rest of the application.
"""

# === Early imports: system capability checks (Linux only) ============
# These run before any other gremlin module.  On Windows they short-circuit
# silently.  Warnings are emitted to stdout (print) so the user always sees
# them.
import gremlin.linux_checks  # noqa: F401, side-effect only

import gremlin.actions
import gremlin.base_classes
import gremlin.cheatsheet
import gremlin.code_runner
import gremlin.common
import gremlin.config
import gremlin.control_action
import gremlin.error
import gremlin.event_handler
import gremlin.execution_graph
import gremlin.fsm
import gremlin.hid_guardian
import gremlin.hints
import gremlin.input_devices
import gremlin.joystick_handling

# *** CRITICAL: TTS must be imported before plugin_manager ***
# TextToSpeechFunctor (action_plugins/text_to_speech) accesses gremlin.tts
# at class-definition time (class attribute: tts = gremlin.tts.TextToSpeech()).
# If plugin_manager loads the text_to_speech plugin before gremlin.tts is imported,
# gremlin.tts doesn't exist as a module-level attribute and the plugin load fails.
import gremlin.macro
import gremlin.plugin_manager
import gremlin.process_monitor
import gremlin.profile
import gremlin.repeater
import gremlin.shared_state
import gremlin.sendinput
import gremlin.spline

# --- TTS is the LAST gremlin module to import ---
# It must come after all core modules that might be imported transitively.
import gremlin.tts

import gremlin.windows_event_hook
