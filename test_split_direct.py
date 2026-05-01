#!/usr/bin/env python3
"""
Direct test of split reading - calls the bridge functions directly.
"""
import sys
import time

sys.path.insert(0, 'C:\\uiax')

# Set up mock environment
import ctypes
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

# Monkey-patch the process manager
from server import process_manager
original_get_pm = process_manager.get_process_manager
process_manager.get_process_manager = lambda: MockProcessManager()

# Now import and test
from skills.quicken import bridge_ext

class MockBridge:
    pass

bridge = MockBridge()

# Test 1: Select first transaction
print("\n=== Test 1: Select first transaction ===")
result = bridge_ext.select_register_row(bridge, 0)
print(result)
time.sleep(0.5)

# Test 2: Read its splits (should open the split dialog)
print("\n=== Test 2: Read splits from first transaction ===")
result = bridge_ext.read_transaction_splits(bridge, row_index=0)
if result.get("ok"):
    print(f"✓ SUCCESS! Found {result['count']} splits")
    for split in result.get("splits", []):
        print(f"  [{split['index']}] {split['category']:20s} | {split['memo']:20s} | {split['amount']}")
    
    # Check if split dialog is still open
    time.sleep(0.5)
    split_dlg = user32.FindWindowW("QWinDlg", None)
    if split_dlg:
        print(f"\n✓ Split dialog is still open (for editing): hwnd=0x{split_dlg:08x}")
    else:
        print("\n✗ Split dialog was closed")
else:
    print(f"✗ FAILED: {result}")
