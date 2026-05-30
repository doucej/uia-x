"""Live test for list_sidebar_accounts - run directly against Quicken."""
import sys, time
sys.path.insert(0, '.')
from server.process_manager import get_process_manager
from skills.quicken import bridge_ext

import ctypes, ctypes.wintypes as wt
user32 = ctypes.windll.user32
EnumCB = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

root = None
def _fr(h, _):
    global root
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(h, buf, 256)
    cls = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(h, cls, 64)
    if "Quicken" in buf.value and cls.value == "QFRAME":
        root = h
        return False
    return True
user32.EnumWindows(EnumCB(_fr), 0)
print(f"Root: {hex(root)}")

pm = get_process_manager()
pm.attach(hwnd=root)

# Monkey-patch _sidebar_dblclick to print timing per item
_orig = bridge_ext._sidebar_dblclick
_item_num = [0]
def _timed_dblclick(root_hwnd, screen_x, screen_y, timeout=6.0, **kw):
    t0 = time.monotonic()
    result = _orig(root_hwnd, screen_x, screen_y, timeout=timeout, **kw)
    elapsed = time.monotonic() - t0
    import ctypes as _ct
    buf = _ct.create_unicode_buffer(256)
    _ct.windll.user32.GetWindowTextW(root_hwnd, buf, 256)
    _item_num[0] += 1
    print(f"  item[{_item_num[0]:02d}] {elapsed:.2f}s -> {buf.value[-40:]!r}", flush=True)
    return result
bridge_ext._sidebar_dblclick = _timed_dblclick

t0 = time.monotonic()
try:
    # First call — fresh scan, up to 20s budget
    result = bridge_ext.list_sidebar_accounts(None, resume=False, max_seconds=20.0)
    call_num = 1
    while not result["done"]:
        elapsed = time.monotonic() - t0
        print(f"\n--- call {call_num} done: {result['scanned']}/{result['total']} items, "
              f"{len(result['accounts'])} accounts found, {elapsed:.1f}s elapsed ---")
        call_num += 1
        result = bridge_ext.list_sidebar_accounts(None, resume=True, max_seconds=20.0)

    elapsed = time.monotonic() - t0
    accounts = result["accounts"]
    print(f"\nDone: {len(accounts)} accounts in {elapsed:.1f}s ({call_num} calls)")
    for a in accounts:
        print(f"  section={a['section']:20s} name={a['name']!r}")
except Exception as e:
    import traceback; traceback.print_exc()
    print(f"Error: {e}")
