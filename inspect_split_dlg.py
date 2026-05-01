#!/usr/bin/env python3
"""
Inspect the structure of the open Split Transaction dialog.
"""
import ctypes

user32 = ctypes.windll.user32

def dump_window_tree(hwnd: int, indent: int = 0, max_depth: int = 10) -> None:
    """Recursively dump window tree."""
    if not user32.IsWindow(hwnd) or indent > max_depth:
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
        dump_window_tree(h, indent + 1, max_depth)
        h = user32.GetWindow(h, 2)  # GW_HWNDNEXT

# Find the Split Transaction dialog
split_dlg = user32.FindWindowW("QWinDlg", None)
if not split_dlg:
    print("Split Transaction dialog not found")
    exit(1)

print(f"Found Split Transaction dialog: hwnd=0x{split_dlg:08x}")
print("\nDialog structure:")
dump_window_tree(split_dlg, max_depth=5)

# Count QREdit controls
qredits = []
def enum_qredits(h: int, _: int) -> bool:
    cls_buf = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(h, cls_buf, 64)
    if cls_buf.value == "QREdit":
        txt_buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(h, txt_buf, 256)
        qredits.append((h, txt_buf.value))
    return True

WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
user32.EnumChildWindows(split_dlg, WNDENUMPROC(enum_qredits), 0)

print(f"\n\nQREdit controls in split dialog: {len(qredits)}")
for hwnd, txt in qredits:
    print(f"  hwnd=0x{hwnd:08x} text={txt!r}")
