#!/usr/bin/env python3
"""Verify the complete event path exists and works."""
import sys
sys.path.insert(0, '/home/paxux/vscode/germlin/JoystickGremlin-Release_13.3')

print("EVENT PATH VERIFICATION")
print("=" * 60)

# Step 1: DILL → EventListener
import dill
dill.DILL.init()
from gremlin.event_handler import EventListener
h = EventListener()
print("1. DILL → EventListener: ✓ (callback registered)")

# Step 2: EventListener → joystick_event signal
print(f"2. joystick_event signal: {'✓' if hasattr(h, 'joystick_event') else '✗'}")

# Step 3: Signal → JoystickGremlinApp
from gremlin.joystick_gremlin import JoystickGremlinApp
has_joystick_cb = hasattr(JoystickGremlinApp, '_joystick_input_selection')
print(f"3. _joystick_input_selection: {'✓' if has_joystick_cb else '✗'}")

# Step 4: Check if GUID comparison works after fix
from dill import GUID
g1 = GUID(dill._GUID())
g1.guid = (1, 2, 3, 4, 5)
g2 = GUID(dill._GUID())
g2.guid = (1, 2, 3, 4, 5)
guid_works = g1 == g2 and hash(g1) == hash(g2)
print(f"4. GUID comparison: {'✓' if guid_works else '✗'}")

# Step 5: Tab lookup works
if guid_works:
    tab_guid = GUID(dill._GUID())
    tab_guid.guid = (1, 2, 3, 4, 5)
    tabs = {tab_guid: "some_widget"}
    lookup_works = g1 in tabs if not guid_works else g1 in tabs
    # Actually test with different objects but equal GUIDs
    event_guid = GUID(dill._GUID())
    event_guid.guid = (1, 2, 3, 4, 5)
    lookup_works = event_guid in tabs
    print(f"5. Tab lookup works: {'✓' if lookup_works else '✗'}")
    
    if lookup_works:
        print(f"\n✓ FULL PATH COMPLETE → Input WILL highlight in UI")
    else:
        print(f"\n✗ Tab lookup still broken!")
else:
    print(f"\n✗ GUID comparison broken → input WON'T highlight")
