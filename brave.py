from evdev import InputDevice

device = InputDevice('/dev/input/event4')  # Replace with your device

print(f"Device: {device.name}")
print(f"Path: {device.path}")
print(f"Phys: {device.phys}")
print(f"Vendor: {device.info.vendor:04x}, Product: {device.info.product:04x}")

print("\n--- Full Capabilities ---")
for event_type, codes in device.capabilities(verbose=True).items():
    print(f"{event_type}:")
    for code in codes:
        if isinstance(code, tuple) and len(code) == 2:
            name, value = code
            if 'ABS_' in name:
                abs_info = device.absinfo(getattr(evdev.ecodes, name))
                print(f"  {name} ({value}): {abs_info}")
            else:
                print(f"  {name} ({value})")
        else:
            print(f"  {code}")   
