#!/usr/bin/env python3
"""
Test the split dialog reading functionality.
"""
import sys
import time
import subprocess
import json

sys.path.insert(0, 'C:\\uiax')

# Start the server
print("Starting MCP server...")
server_proc = subprocess.Popen(
    ['python', '-m', 'uiax.server'],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    cwd='C:\\uiax'
)
time.sleep(2)

try:
    from skills.quicken import bridge_ext
    from server.process_manager import ProcessManager
    
    # Attach to Quicken
    pm = ProcessManager()
    result = pm.attach_by_class("qw")
    if not result:
        print("ERROR: Could not attach to Quicken window")
        sys.exit(1)
    
    print(f"✓ Attached to Quicken: hwnd=0x{pm.attached.hwnd:08x}")
    
    # Create a bridge instance (this is what the MCP server uses)
    class MockBridge:
        pass
    
    bridge = MockBridge()
    
    # Test: read first transaction's splits
    print("\n--- Testing: select_register_row(0) ---")
    result = bridge_ext.select_register_row(bridge, 0)
    print(json.dumps(result, indent=2))
    time.sleep(0.5)
    
    print("\n--- Testing: read_transaction_splits(row_index=0) ---")
    result = bridge_ext.read_transaction_splits(bridge, row_index=0)
    print(json.dumps(result, indent=2, default=str))
    
    if result.get("ok"):
        print(f"\n✓ SUCCESS! Found {result.get('count', 0)} splits")
        for split in result.get("splits", []):
            print(f"  [{split['index']}] {split['category']:20s} | {split['memo']:20s} | {split['amount']}")
    else:
        print(f"\n✗ FAILED: {result.get('error')}")

finally:
    # Cleanup
    print("\nStopping server...")
    server_proc.terminate()
    try:
        server_proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        server_proc.kill()
