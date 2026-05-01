#!/usr/bin/env python3
"""
Simple check: are there any visible dialogs or windows with 'split' in the title?
"""
import ctypes

user32 = ctypes.windll.user32

def find_all_windows():
    """Find all visible top-level windows."""
    windows = []
    
    def enum_windows(h: int, _: int) -> bool:
        if user32.IsWindowVisible(h):
            txt_buf = ctypes.create_unicode_buffer(256)
            cls_buf = ctypes.create_unicode_buffer(64)
            user32.GetWindowTextW(h, txt_buf, 256)
            user32.GetClassNameW(h, cls_buf, 64)
            windows.append((h, txt_buf.value, cls_buf.value))
        return True
    
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    user32.EnumWindows(WNDENUMPROC(enum_windows), 0)
    return windows

print("Visible top-level windows:")
for hwnd, title, cls in find_all_windows():
    marker = "[SPLIT?]" if "split" in title.lower() else "        "
    print(f"{marker} hwnd=0x{hwnd:08x} cls={cls:20s} title={title[:60]}")
