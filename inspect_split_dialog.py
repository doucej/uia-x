#!/usr/bin/env python3
"""
Debug script to inspect the current split dialog if one is open.
"""
import ctypes
import sys
import time

user32 = ctypes.windll.user32

def dump_window_tree(hwnd: int, indent: int = 0) -> None:
    """Recursively dump window tree."""
    if not user32.IsWindow(hwnd):
        return

    cls_buf = ctypes.create_unicode_buffer(64)
    txt_buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, cls_buf, 64)
    user32.GetWindowTextW(hwnd, txt_buf, 256)

    cls = cls_buf.value
    txt = txt_buf.value[:50]
    is_visible = "V" if user32.IsWindowVisible(hwnd) else "H"
    print(f"{'  ' * indent}[{is_visible}] {cls:20s} {txt!r:50s} hwnd=0x{hwnd:08x}")

    # Enumerate children
    h = user32.GetWindow(hwnd, 5)  # GW_CHILD
    while h:
        dump_window_tree(h, indent + 1)
        h = user32.GetWindow(h, 2)  # GW_HWNDNEXT

def main():
    # Find Quicken main window - try by class name first, then use explicit hwnd
    qw_hwnd = user32.FindWindowW("qw", None)
    if not qw_hwnd:
        # Fallback: use the hwnd from get_process output
        import subprocess
        result = subprocess.run(
            ["powershell", "-Command", 
             "Get-Process qw -ErrorAction SilentlyContinue | Select-Object -ExpandProperty MainWindowHandle"],
            capture_output=True, text=True
        )
        try:
            qw_hwnd = int(result.stdout.strip())
        except (ValueError, IndexError):
            print("ERROR: Quicken process not found")
            return

    print(f"Found Quicken hwnd=0x{qw_hwnd:08x}")
    print("\nWindow tree:")
    dump_window_tree(qw_hwnd)

    # Count QREdit controls (split dialog indicator)
    qredits = []
    def enum_qredits(h: int, _: int) -> bool:
        cls_buf = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(h, cls_buf, 64)
        if cls_buf.value == "QREdit":
            qredits.append(h)
        return True

    try:
        from ctypes import wintypes
    except ImportError:
        from ctypes import wintypes as ctypes_wintypes
        wintypes = ctypes_wintypes

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    user32.EnumChildWindows(qw_hwnd, WNDENUMPROC(enum_qredits), 0)
    print(f"\n\nTotal QREdit controls: {len(qredits)}")
    if len(qredits) > 3:
        print("  ✓ Split dialog appears to be OPEN (>3 QREdits suggests split container visible)")
    else:
        print("  ✗ Split dialog NOT visible (only register QREdits)")

if __name__ == "__main__":
    main()
