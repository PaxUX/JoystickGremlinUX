#!/usr/bin/env python3
"""Debug Xbox controller discovery and event routing."""
import os
import sys
import struct
import select

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'JoystickGremlin-Release_13.3'))

print("=" * 70)
print("STEP 1: Check /proc/bus/input/devices for Xbox controller")
print("=" * 70)

with open('/proc/bus/input/devices') as f:
    content = f.read()

for block in content.split('\nI: Bus='):
    lines = block.split('\n')
    bus = vendor = product = name = event = js = None
    for line in lines:
        if line.startswith('I: Bus='): bus = line.split()[1]
        elif line.startswith('V: Vendor='): vendor = int(line.split('Vendor=')[1].strip(), 16)
        elif line.startswith('P: Product='): product = int(line.split('Product=')[1].strip(), 16)
        elif line.startswith('N: Name='): name = line.split('"')[1]
        elif line.startswith('H: Handlers='):
            for h in line.split('=')[1].strip().split():
                if h.startswith('event'): event = h
                elif h.startswith('js'): js = h
    
    if js and bus == '0003' and vendor == 0x20d6 and product == 0x2005:
        print(f"  FOUND Xbox controller:")
        print(f"    Bus: {bus}")
        print(f"    VID/PID: 0x{vendor:04x}/0x{product:04x}")
        print(f"    Name: {name}")
        print(f"    Handlers: event={event}, js={js}")
        print(f"    ✓ Device is discoverable")
        break
else:
    print("  ✗ Xbox controller NOT found!")

print("\n" + "=" * 70)
print("STEP 2: Try opening the event device with evdev")
print("=" * 70)

# Check if event4 exists and is readable
event4 = '/dev/input/event4'
if os.path.exists(event4):
    print(f"  {event4} exists")
    print(f"  Permissions: {oct(os.stat(event4).st_mode)[-3:]}")
else:
    print(f"  ✗ {event4} does not exist!")
    print(f"  Available event devices:")
    for f in sorted(os.listdir('/dev/input')):
        if f.startswith('event'):
            p = f'/dev/input/{f}'
            print(f"    {p} ({oct(os.stat(p).st_mode)[-3:]})")

try:
    import evdev
    # Find js1/device
    for dev in evdev.list_devices():
        d = evdev.InputDevice(dev)
        caps = d.capabilities()
        if js0 in str(dev) or js1 in str(dev):
            if evdev.ecodes.EV_ABS in caps and evdev.ecodes.EV_KEY in caps:
                abs_keys = [c[0] for c in caps.get(evdev.ecodes.EV_ABS, [])]
                is_virtual = (d.phys or '').find('input0') >= 0 or 'virtual' in (d.phys or '').lower()
                print(f"\n  Device: {d.name}")
                print(f"  phys: {d.phys}")
                print(f"  event node: {dev}")
                print(f"  has EV_ABS: {bool(caps.get(evdev.ecodes.EV_ABS))}")
                print(f"  has EV_KEY: {bool(caps.get(evdev.ecodes.EV_KEY))}")
                print(f"  ABS codes: {sorted(abs_keys)}")
                
                # Check if it has triggers
                if evdev.ecodes.ABS_Z in abs_keys:
                    print(f"  ✓ Has ABS_Z (LT trigger)")
                if evdev.ecodes.ABS_RZ in abs_keys:
                    print(f"  ✓ Has ABS_RZ (RT trigger)")
                if evdev.ecodes.ABS_X in abs_keys:
                    print(f"  ✓ Has ABS_X")
                if evdev.ecodes.ABS_Y in abs_keys:
                    print(f"  ✓ Has ABS_Y")
                
                break
except ImportError:
    print("  ✗ evdev not installed")

print("\n" + "=" * 70)
print("STEP 3: Try opening the event directly to read events")
print("=" * 70)

# Try opening event4 directly
try:
    ev_fd = os.open(event4, os.O_RDONLY | os.O_NONBLOCK)
    print(f"  Successfully opened {event4} (fd={ev_fd})")
    
    # Prepare to read events
    _EVENT_SIZE = 24
    _EVENT_FMT = '<qQHHi'
    
    print("  Press LT/RT now and watch for events...")
    print("  (Ctrl+C to stop)")
    
    poll = select.poll()
    poll.register(ev_fd, select.POLLIN)
    
    for _ in range(10):  # Poll up to 10 times
        ready = poll.poll(1000)  # 1 sec timeout
        for fd, event in ready:
            raw = os.read(fd, 2048)
            if raw:
                off = 0
                while off + _EVENT_SIZE <= len(raw):
                    sec, usec, ev_type, code, val = struct.unpack_from(_EVENT_FMT, raw, off)
                    off += _EVENT_SIZE
                    type_names = {0x01: 'EV_KEY', 0x03: 'EV_ABS', 0x00: 'EV_SYN'}
                    type_name = type_names.get(ev_type, f'0x{ev_type:02x}')
                    print(f"  Event: type={type_name} code={code} value={val}")
        
    os.close(ev_fd)
    print("  ✓ Raw device events are visible!")
    
except PermissionError:
    print("  ✗ Permission denied! Try: sudo chmod 666 /dev/input/event4")
except FileNotFoundError:
    print(f"  ✗ {event4} not found")
except OSError as e:
    print(f"  ✗ OS error: {e}")

print("\n" + "=" * 70)
print("STEP 4: Check if DILL can import and initialize")
print("=" * 70)

try:
    import dill
    print(f"  ✓ dill imported (AXIS_MAP_LEN={dill.AXIS_MAP_LEN})")
    
    # Check if DILL.init works
    dill.DILL.init()
    count = dill.DILL.get_device_count()
    print(f"  ✓ DILL.init() returned with {count} devices discovered")
    
    # List discovered devices
    for i in range(count):
        try:
            dev = dill.DILL.get_device_information_by_index(i)
            is_virt = dev.is_virtual if hasattr(dev, 'is_virtual') else 'N/A'
            print(f"    Device {i}: {dev.name} (axes={dev.axis_count}, buttons={dev.button_count}, hats={dev.hat_count}, virtual={is_virt})")
        except IndexError:
            print(f"    Device {i}: (not available)")
    
    # Test triggering input events
    # Start a simple callback
    def my_callback(data):
        print(f"\n  CALLBACK RECEIVED: type={data.input_type} idx={data.input_index} val={data.value}")
    
    dill.DILL.set_input_event_callback(my_callback)
    print(f"\n  Callback registered. Press LT/RT to trigger events...")
    print("  (If no events appear, the polling thread may not be working)")
    
except ImportError as e:
    print(f"  ✗ dill import failed: {e}")
except Exception as e:
    print(f"  ✗ DILL error: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 70)
print("DIAGNOSIS COMPLETE")
print("=" * 70)
