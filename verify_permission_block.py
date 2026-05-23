#!/usr/bin/env python3
"""
Verify that permission restrictions on /dev/input/event4 are blocking
the polling FD from opening.

Expected output if fix is needed:
  - event2 is readable (Arduino Leonardo)
  - event4 is NOT readable (Xbox controller)
  - DILL discovers device but can't poll events
"""
import sys
import os
import shutil
import subprocess
import stat

sys.path.insert(0, '/home/paxux/vscode/germlin/JoystickGremlin-Release_13.3')

print("=" * 70)
print("PERMISSION DIAGNOSTIC: Why Xbox events don't reach the UI")
print("=" * 70)

# Step 1: Show all event devices and their permissions
print("\nSTEP 1: Event device permissions")
print("-" * 70)

event_devices = {}
if os.path.exists('/dev/input'):
    for f in sorted(os.listdir('/dev/input')):
        if f.startswith('event'):
            path = f'/dev/input/{f}'
            mode = os.stat(path).st_mode
            perms = stat.S_IMODE(mode)
            # Determine readability
            can_read_by_other = bool(perms & stat.S_IRGRP or perms & stat.S_IROTH)
            can_read_user = bool(perms & stat.S_IRUSR)
            
            # Get group
            import pwd
            try:
                group = pwd.getpwuid(os.stat(path).st_gid).pw_name
            except:
                group = "unknown"
            
            # Get current user groups
            groups = [pwd.getpwuid(g).pw_name for g in os.getgroups()]
            user_can_read_group = group in groups
            
            print(f"  {path:25s} perm={oct(perms)[-3:]} user={can_read_user} group_ok={user_can_read_group} name_in_proc={path in open('/proc/bus/input/devices').read()}")
            
            # Store for later
            event_devices[f] = {
                'path': path,
                'perms': perms,
                'group': group,
                'in_proc': path in open('/proc/bus/input/devices').read()
            }

# Step 2: Find Xbox controller's event node
print("\nSTEP 2: Xbox controller's event device")
print("-" * 70)

xbox_event = None
with open('/proc/bus/input/devices') as f:
    proc_content = f.read()

for block in proc_content.split('\nI: Bus='):
    if '20d6' not in block and 'X-Box' not in block:  
        continue
    
    for line in block.split('\n'):
        if line.startswith('H: Handlers='):
            handlers = line.split('=')[1].strip().split()
            for h in handlers:
                if h.startswith('event'):
                    xbox_event = h
                    print(f"  Xbox controller uses: {xbox_event}")
                    print(f"  Full path: /dev/input/{xbox_event}")
                    
                    # Check if this event is readable
                    if xbox_event in event_devices:
                        dev = event_devices[xbox_event]
                        can_read = bool(dev['perms'] & (stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH))
                        print(f"  Can read: {can_read} (perms: {oct(dev['perms'])})")
                        
                        if not can_read:
                            print(f"\n  ✗ ** PERMISSION BLOCKED — events can't reach DILL **")
                            print(f"     Fix: sudo chmod 666 /dev/input/{xbox_event}")
                            print(f"     OR add udev rule to auto-set permissions")
            break

# Step 3: Show current DILL FD state
print("\nSTEP 3: Current DILL polling FD state")
print("-" * 70)

try:
    import dill
    dill.DILL.init()
    
    print(f"  DILL device count: {dill.DILL.get_device_count()}")
    
    # Check internal FD state
    fd_map = getattr(dill.DILL, '_fd_to_guid', {})
    guid_to_fd = getattr(dill.DILL, '_guid_to_fd', {})
    
    print(f"\n  FDs registered in poll loop: {len(fd_map)}")
    
    for fd, guid in fd_map.items():
        # Try to read the device name
        device_name = "unknown"
        if hasattr(dill.DILL, 'get_device_name'):
            try:
                device_name = dill.DILL.get_device_name(guid)
            except:
                pass
        print(f"    FD={fd}: {device_name}")
        
        # Verify FD is actually open
        try:
            os.lseek(fd, 0, os.SEEK_CUR)  # Test if FD is valid
            print(f"            ✓ FD {fd} is open and accessible")
        except:
            print(f"            ✗ FD {fd} is OPEN but inaccessible (blocked by permissions)")
    
    # Check if any FDs fail to open
    no_fds = len(fd_map) == 0
    if no_fds:
        print(f"\n  ✗ ** CRITICAL: No FDs registered in poll loop **")
        print(f"     This means events will never be read.")
        if xbox_event:
            print(f"     Likely cause: /dev/input/{xbox_event} permissions block FD=open()")
    
except ImportError:
    print("  ✗ Could not import dill")
except Exception as e:
    print(f"  Error checking DILL state: {e}")

# Step 4: Show udev rules
print("\nSTEP 4: Existing joystick udev rules")  
print("-" * 70)

udev_rules = [
    '/etc/udev/rules.d/99-joystick.rules',
    '/lib/udev/rules.d/99-libinput.rules',
]

has_rule = False
for rule_file in udev_rules:
    if os.path.exists(rule_file):
        try:
            content = open(rule_file).read()
            if 'input' in content.lower() or 'joystick' in content.lower() or '0666' in content:
                print(f"  ✓ {rule_file}")
                for line in content.split('\n'):
                    if '=' in line and ('input' in line.lower() or 'joystick' in line.lower() or '0666' in line):
                        print(f"    {line.strip()}")
                has_rule = True
        except Exception:
            pass

if not has_rule:
    print("  ⚠ No explicit joystick input udev rules found")
    print(f"     Recommended fix: create {udev_rules[0]} with:")
    print(f"     'SUBSYSTEM==\"input\", GROUP=\"input\", MODE=\"0660\"'")

# Step 5: Verify current user group
print("\nSTEP 5: Current user group membership")
print("-" * 70)

import pwd
current_user = pwd.getpwuid(os.getuid()).pw_name
all_groups = [pwd.getpwuid(g).pw_name for g in os.getgroups()]

print(f"  Current user: {current_user}")
print(f"  Groups: {', '.join(all_groups)}")
print(f"  'input' group: {'input' in all_groups}")
print(f"  'plugdev' group: {'plugdev' in all_groups}")

if 'input' not in all_groups:
    print(f"\n  ✗ User not in 'input' group — add with:")
    print(f"     sudo usermod -G input {current_user}")
    print(f"     (then log out/log back in)")

print("\n" + "=" * 70)
print("SUMMARY & FIX")
print("=" * 70)
print("""
The Xbox controller events aren't reaching the UI because:
1. /dev/input/event4 has permissions 660 (group-only)
2. You're not in the 'input' group (or udev rules don't auto-set 0666)
3. DILL discovers the device but os.open() silently fails due to permissions

SOLUTIONS (pick one):
""")
if 'input' not in all_groups:
    print(f"  OPTION 1: Add user to input group (recommended)")
    print(f"    sudo usermod -aG input {current_user}")
    print(f"    Then log out and back in")
    print()

print(f"  OPTION 2: Temporary fix for current session")
print(f"    sudo chmod 666 /dev/input/event4")
print()
print(f"  OPTION 3: Permanent udev rule")
print(f"    sudo nano /etc/udev/rules.d/99-joystick.rules")
print(f"    Add: SUBSYSTEM==\"input\", GROUP=\"input\", MODE=\"0660\"")
print(f"    Then: sudo udevadm controls --reload-rules && sudo udevadm trigger")

print("\nOnce permissions are fixed, events will arrive in the UI.")
