#!/usr/bin/env python3
"""
Read the contents of visible Edit controls in the split dialog ListBox.
"""
import ctypes
import time

user32 = ctypes.windll.user32

# Find the Split Transaction dialog
split_dlg = user32.FindWindowW("QWinDlg", None)
if not split_dlg:
    print("Split Transaction dialog not found")
    exit(1)

print(f"Found Split Transaction dialog: hwnd=0x{split_dlg:08x}")

# Find all Edit controls in the split dialog (these are the split rows)
edits = []
def enum_edits(h: int, _: int) -> bool:
    cls_buf = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(h, cls_buf, 64)
    if cls_buf.value == "Edit":
        # Check if this Edit is in a ListBox (not just a stray one)
        is_visible = user32.IsWindowVisible(h)
        edits.append((h, is_visible))
    return True

WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
user32.EnumChildWindows(split_dlg, WNDENUMPROC(enum_edits), 0)

print(f"\nEdit controls found: {len(edits)}")
print("Visible Edit controls (split rows):")
for hwnd, is_visible in edits:
    if is_visible:
        # Read the text from this Edit control
        text_len = user32.GetWindowTextLengthW(hwnd)
        if text_len > 0:
            txt_buf = ctypes.create_unicode_buffer(text_len + 1)
            user32.GetWindowTextW(hwnd, txt_buf, text_len + 1)
            text = txt_buf.value
        else:
            text = "(empty)"
        
        # Get position
        r_int = (ctypes.c_long * 4)()
        user32.GetWindowRect(hwnd, ctypes.byref(r_int))
        
        print(f"  hwnd=0x{hwnd:08x} pos=({r_int[0]:4d},{r_int[1]:4d})-({r_int[2]:4d},{r_int[3]:4d}) text={text!r}")
