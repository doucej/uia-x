#!/usr/bin/env python3
"""
Debug split button clicking.
"""
import sys
import time
import ctypes

sys.path.insert(0, 'C:\\uiax')

user32 = ctypes.windll.user32

# Find Quicken window
qw_hwnd = user32.FindWindowW("QFRAME", None)
if not qw_hwnd:
    print("ERROR: Quicken (QFRAME) not found")
    sys.exit(1)

print(f"Found Quicken: hwnd=0x{qw_hwnd:08x}")

# Mock the process manager
class MockAttached:
    def __init__(self):
        self.hwnd = qw_hwnd

class MockProcessManager:
    def __init__(self):
        self.attached = MockAttached()

# Monkey-patch
from server import process_manager
process_manager.get_process_manager = lambda: MockProcessManager()

# Import after patching
from skills.quicken import bridge_ext

class MockBridge:
    pass

bridge = MockBridge()

# Step 1: Select first transaction
print("\n1. Selecting first transaction...")
result = bridge_ext.select_register_row(bridge, 0)
print(f"   Result: {result}")
time.sleep(0.5)

# Step 2: Look for split button
from skills.quicken.bridge_ext import _find_button_in_hwnd, _read_hwnd_text

split_btn = _find_button_in_hwnd(qw_hwnd, "split", "splits")
print(f"\n2. Split button search:")
print(f"   Found: {split_btn is not None}")
if split_btn:
    print(f"   hwnd: 0x{split_btn:08x}")
    text = _read_hwnd_text(split_btn)
    print(f"   text: {text!r}")
    
    # Check if button is visible
    is_visible = user32.IsWindowVisible(split_btn)
    is_window = user32.IsWindow(split_btn)
    print(f"   visible: {is_visible}, exists: {is_window}")
    
    # Step 3: Try clicking
    print(f"\n3. Clicking Split button with BM_CLICK...")
    BM_CLICK = 0x00F5
    result = user32.SendMessageW(split_btn, BM_CLICK, 0, 0)
    print(f"   SendMessageW returned: {result}")
    time.sleep(1)
    
    # Step 4: Check if split dialog appeared
    print(f"\n4. Checking for QWinDlg...")
    split_dlg = user32.FindWindowW("QWinDlg", None)
    if split_dlg:
        print(f"   ✓ Split dialog opened! hwnd=0x{split_dlg:08x}")
        
        # Try to get the title
        txt = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(split_dlg, txt, 256)
        print(f"   title: {txt.value!r}")
    else:
        print(f"   ✗ Split dialog did not open")
        
        # Check if any QWinDlg exists
        print(f"\n5. Searching all top-level windows for QWinDlg...")
        hwnd = 0
        count = 0
        while True:
            hwnd = user32.FindWindowExW(0, hwnd, "QWinDlg", None)
            if not hwnd:
                break
            count += 1
            txt = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, txt, 256)
            print(f"   Found QWinDlg #{count}: hwnd=0x{hwnd:08x} title={txt.value!r}")
else:
    print("   ✗ Split button not found!")
