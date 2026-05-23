#!/usr/bin/env python3
"""
Debug Xbox controller discovery and event flow.
Checks /proc, permissions, evdev, and DILL initialization.
Run this script and press LT/RT to see if events reach the UI.
"""
import sys
import os
import struct

sys.path.insert(0, '/home/paxux/vscode/germlin/JoystickGremlin-Release_13.3')

def step1_check_proc():
    """Check if Xbox is in /proc/bus/input/devices"""
    print("=" * 60)
    print("STEP 1: /proc/bus/input/devices check")
    print("=" * 60)
    
    with open('/proc/bus/input/devices') as f:
        content = f.read()
    
    for block in content.split('\n\n'):
        lines = block.split('\n')
        for line in lines:
            if '20d6' in line or 'x-box pad' in line.lower() or 'Controller' in line:
                print(f"  Found: {line.strip()}")
                return True
    
    print("  ✗ Xbox controller NOT found in /proc!")
    return False

def step2_check_permissions():
    """Check if event4 is readable"""
    print()
    print("=" * 60)
    print("STEP 2: Device permissions check")
    print("=" * 60)
    
    event4 = '/dev/input/event4'
    if os.path.exists(event4):
        mode = oct(os.stat(event4).st_mode)[-3:]
        print(f"  {event4} permissions: {mode}")
        if int(mode[-1]) & 4:  # Check if other has read access
            print("  ✓ Event device is readable by current user")
            return True
        else:
            print("  ✗ Event device NOT readable by current user!")
            print(f"     Fix: sudo chmod 666 {event4}")
            return False
    else:
        print(f"  ✗ {event4} does not exist")
        return False

def step3_check_evdev():
    """Check if evdev library works"""
    print()
    print("=" * 60)
    print("STEP 3: evdev library check")
    print("=" * 60)
    
    try:
        import evdev
        print("  ✓ evdev library is installed")
        
        # List all js devices
        js_devices = [d for d in evdev.list_devices() if 'js' in str(d)]
        print(f"\n  Connected joystick devices:")
        for d in js_devices:
            dev = evdev.InputDevice(d)
            caps = dev.capabilities()
            has_abs = evdev.ecodes.EV_ABS in caps
            has_key = evdev.ecodes.EV_KEY in caps
            print(f"    {d}")
            print(f"      Name: {dev.name}")
            print(f"      EV_ABS: {bool(has_abs)}, EV_KEY: {bool(has_key)}")
            print(f"      Phys: {dev.phys}")
        return True
        
    except ImportError:
        print("  ✗ evdev not installed")
        return False

def step4_check_dill():
    """Test DILL backend"""
    print()
    print("=" * 60)
    print("STEP 4: DILL backend test")
    print("=" * 60)
    
    # Check AXIS_MAP_LEN exists (we added this)
    try:
        from dill import AXIS_MAP_LEN
        print(f"  ✓ AXIS_MAP_LEN = {AXIS_MAP_LEN}")
    except ImportError:
        print("  ✗ AXIS_MAP_LEN not defined! Fix: add 'AXIS_MAP_LEN = 8' to dill/__init__.py")
        AXIS_MAP_LEN = None
    
    if AXIS_MAP_LEN:
        # Try DILL initialization
        try:
            import dill
            dill.DILL.init()
            count = dill.DILL.get_device_count()
            print(f"  ✓ DILL.init() succeeded with {count} devices")
            
            for i in range(count):
                try:
                    dev = dill.DILL.get_device_information_by_index(i)
                    virt = "virtual" if hasattr(dev, 'is_virtual') and dev.is_virtual else "physical"
                    print(f"    {i}: {dev.name} ({virt})")
                except IndexError:
                    pass
            
            return True
        except Exception as e:
            print(f"  ✗ DILL initialization failed: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    return False

def step5_test_event_dispatch():
    """Test if events actually reach the callback"""
    print()
    print("=" * 60)
    print("STEP 5: Event dispatch test")
    print("=" * 60)
    print("  Press LT/RT on your controller...")
    print("  (Events should appear below if DILL event queue works)\n")
    
    events_received = []
    
    def on_event(data):
        events_received.append(data)
        print(f"  [EVENT] type={data.input_type} idx={data.input_index} val={data.value}")
    
    import dill
    dill.DILL.set_input_event_callback(on_event)
    
    # Give it a few seconds to detect
    for _ in range(5):
        sys.stdout.write("\rWaiting for events... Press LT/RT!          ")
        sys.stdout.flush()
        if events_received:
            break
    print("\n  Events received:", len(events_received))
    
    return len(events_received) > 0

if __name__ == '__main__':
    import sys
    
    results = {}
    results['proc'] = step1_check_proc()
    results['perms'] = step2_check_permissions()
    results['evdev'] = step3_check_evdev()
    results['dill'] = step4_check_dill()
    results['event'] = step5_test_event_dispatch()
    
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for check, result in results.items():
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"  {check}: {status}")
    
    if all(results.values()):
        print("\n✓ Everything is working! If input still doesn't show in UI,")
        print("  the issue is in the device tab or mode config (not discovery).")
    elif results.get('perms') == False:
        print("\n✗ The most likely fix is to change permissions on /dev/input/event4")
    elif results.get('proc') == False:
        print("\n✗ DILL won't find your controller in /proc")
    elif results.get('event') == False:
        print("\n✗ Events aren't reaching the pipeline")
