#!/usr/bin/env python3
import sys
import os
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

def main():
    print("="*60)
    print("vJoy Linux Bridge Test")
    print("Reads physical controller → writes to /dev/uinput")
    print("="*60)

    # 1. Find physical controller
    import evdev
    devices = [evdev.InputDevice(path) for path in evdev.list_devices()]
    
    controller = None
    for dev in devices:
        name_lower = dev.name.lower()
        if any(kw in name_lower for kw in ['xbox', 'play', 'dual', 'controller', 'gamepad', 'wireless']):
            controller = dev
            break
            
    if not controller:
        print("❌ No gamepad detected. Plug one in and rerun.")
        sys.exit(1)
    print(f"✅ Physical controller: {controller.name}")

    # 2. Import vJoy Linux backend
    from vjoy_linux.vjoy_interface import VJoyInterface, VJoyState

    # 3. Acquire virtual device
    print("\n📡 Creating virtual vJoy device #1...")
    if not VJoyInterface.AcquireVJD(1):
        print("❌ Failed to create virtual device.")
        print("   Check /dev/uinput permissions:")
        print("   sudo usermod -aG input $USER  &&  newgrp input")
        sys.exit(1)
    
    print("✅ Virtual device created! (JG Virtual Controller)")
    print("🔍 Verify live with: `evtest` or `jstest-gtk`")
    print("⏹️  Close this script to destroy the virtual device.\n")

    # 4. Event bridge loop
    print("🎮 Move your controller axes/buttons...")
    try:
        import select
        while True:
            ready, _, _ = select.select([controller.fd], [], [], 0.1)
            if ready:
                for event in controller.read():
                    # Forward axes
                    if event.type == evdev.ecodes.EV_ABS:
                        VJoyInterface.SetAxis(event.value, 1, event.code)
                    # Forward buttons (map to vJoy 1-based index)
                    elif event.type == evdev.ecodes.EV_KEY:
                        if 304 <= event.code <= 431:
                            VJoyInterface.SetBtn(bool(event.value), 1, event.code - 304 + 1)
    except KeyboardInterrupt:
        pass
    finally:
        VJoyInterface.RelinquishVJD(1)
        print("\n👋 Virtual device destroyed.")

if __name__ == '__main__':
    main()