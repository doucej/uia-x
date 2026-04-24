"""Enumerate the QWAcctBarHolder sidebar structure to understand section layout."""
import ctypes
import ctypes.wintypes as wt
import sys

user32 = ctypes.windll.user32
EnumCB = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

# Find Quicken root (QWidget window whose title contains "Quicken")
root = None
def _find_root(h, _):
    global root
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(h, buf, 256)
    if "Quicken" in buf.value:
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(h, cls, 64)
        if cls.value == "QWidget":
            root = h
            return False
    return True
user32.EnumWindows(EnumCB(_find_root), 0)
print(f"Root HWND: {hex(root) if root else None}")
if not root:
    sys.exit(1)

# Find QWAcctBarHolder
holder = None
def _find_holder(h, _):
    global holder
    cls = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(h, cls, 64)
    if cls.value == "QWAcctBarHolder":
        holder = h
        return False
    return True
user32.EnumChildWindows(root, EnumCB(_find_holder), 0)
print(f"Holder HWND: {hex(holder) if holder else None}")
if not holder:
    print("No holder found!")
    sys.exit(1)

# Walk direct children using GetWindow chain
GW_CHILD = 5
GW_HWNDNEXT = 2
cls_buf = ctypes.create_unicode_buffer(64)
txt_buf = ctypes.create_unicode_buffer(256)

print("\n--- Direct children of QWAcctBarHolder ---")
h = user32.GetWindow(holder, GW_CHILD)
idx = 0
while h:
    user32.GetClassNameW(h, cls_buf, 64)
    user32.GetWindowTextW(h, txt_buf, 256)
    r = wt.RECT()
    user32.GetWindowRect(h, ctypes.byref(r))
    vis = bool(user32.IsWindowVisible(h))
    print(f"  [{idx:02d}] {hex(h)} cls={cls_buf.value!r:30s} vis={vis} txt={txt_buf.value!r}")
    # If QWListViewer, also walk its children
    if cls_buf.value == "QWListViewer":
        ch = user32.GetWindow(h, GW_CHILD)
        ci = 0
        while ch:
            cls2 = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(ch, cls2, 64)
            vis2 = bool(user32.IsWindowVisible(ch))
            SM = user32.SendMessageW
            count = SM(ch, 0x018B, 0, 0)  # LB_GETCOUNT if ListBox
            print(f"      [{ci}] {hex(ch)} cls={cls2.value!r:20s} vis={vis2} LB_COUNT={count}")
            ch = user32.GetWindow(ch, GW_HWNDNEXT)
            ci += 1
    h = user32.GetWindow(h, GW_HWNDNEXT)
    idx += 1

# Also count how many ListBoxes are found by EnumChildWindows (visible vs hidden)
print("\n--- All ListBoxes under holder (EnumChildWindows) ---")
lb_all = []
def _scan_all_lb(h, _):
    user32.GetClassNameW(h, cls_buf, 64)
    if cls_buf.value == "ListBox":
        vis = bool(user32.IsWindowVisible(h))
        parent = user32.GetParent(h)
        user32.GetClassNameW(parent, cls_buf, 64)
        pcls = cls_buf.value
        user32.GetWindowTextW(parent, txt_buf, 256)
        ptxt = txt_buf.value
        SM = user32.SendMessageW
        count = SM(h, 0x018B, 0, 0)
        lb_all.append((h, vis, pcls, ptxt, count))
    return True
user32.EnumChildWindows(holder, EnumCB(_scan_all_lb), 0)
for lb, vis, pcls, ptxt, count in lb_all:
    print(f"  {hex(lb)} vis={vis} parent_cls={pcls!r:20s} parent_txt={ptxt!r} items={count}")
print(f"\nTotal ListBoxes: {len(lb_all)}, visible: {sum(v for _,v,*_ in lb_all)}, hidden: {sum(not v for _,v,*_ in lb_all)}")
