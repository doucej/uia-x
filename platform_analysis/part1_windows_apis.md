# Part 1: Windows-Only APIs in Quicken Skill (skills/quicken/bridge_ext.py)

## Summary
`bridge_ext.py` contains **4774 lines** of code with heavy Windows API dependencies that cannot run on macOS.

---

## 1. ctypes.windll.user32 API Calls (26 direct calls)

### Window Enumeration & Queries
- `user32.IsWindow(hwnd)` - Check if window handle is valid (line 24)
- `user32.IsWindowVisible(h)` - Check window visibility (lines 142, 164, 453, 643, 649, 760, 809, 912, 954, 1107, 1213, 1227)
- `user32.GetWindowRect(h, &rect)` - Get window rectangle/screen coords (lines 121, 127, 405, 3051)
- `user32.GetClassNameW(h, buf, size)` - Get window class name (lines 148, 167, 395, 452, 595, 623, 736, 843)
- `user32.GetWindowTextW(h, buf, size)` - Get window title text (lines 169, 455, 627)
- `user32.GetParent(h)` - Get parent window (line 639)
- `user32.GetWindow(h, 4)` - GW_OWNER (lines 144, 708, 717)

### Window Messages (Win32 messaging)
- `user32.SendMessageW(hwnd, msg, wp, lp)` - Synchronous message (lines 615, 792, 1458)
- `user32.SendMessageTimeoutW(...)` - Timeout version (lines 250, 1473)
- `user32.PostMessageW(hwnd, msg, wp, lp)` - Asynchronous message (lines 282, 683, 685, 760, 762, 832)
- `user32.PostMessageW(..., WM_CLOSE, 0, 0)` - Close dialog (line 202)

### Input Synthesis (Mouse/Keyboard)
- `user32.SetForegroundWindow(hwnd)` - Bring window to foreground (lines 117, 213, 822, 832, 1276)
- `user32.SetCursorPos(x, y)` - Move cursor (lines 131, 823, 1327)
- `user32.mouse_event(flags, x, y, data, extra)` - Legacy mouse event (lines 133, 135, 825, 826, 1330, 1331)
- `user32.ClientToScreen(hwnd, &point)` - Client-to-screen coordinate conversion (lines 1267, 1851, 2159, 2906, 3051)

### Enumeration
- `user32.EnumWindows(WNDENUMPROC, lParam)` - Enumerate all top-level windows (line 153)
- `user32.EnumChildWindows(hwnd, WNDENUMPROC, lParam)` - Enumerate child windows (lines 175, 400, 459, 485, 601)

### Other Win32 Calls
- `user32.GetDeviceCaps(dc, 88)` - LOGPIXELSX for DPI (from process_manager.py)

---

## 2. Windows-Specific Constants (Hardcoded)

### Window Messages
- `WM_CLOSE = 0x0010` - Close window message (line 78)
- `WM_LBUTTONDOWN`, `WM_LBUTTONUP` - Mouse button messages (lines 683, 685, 760, 762)
- `WM_COMMAND = 273` (implied in `open_reconcile` function)
- `BM_CLICK` - Button click message (line 67, 1212)
- `MK_LBUTTON` - Mouse button mask (lines 683, 685, 760)

### Mouse Event Flags
- `MOUSEEVENTF_LEFTDOWN = 0x0002` (lines 133, 825, 1330)
- `MOUSEEVENTF_LEFTUP = 0x0004` (lines 135, 826, 1331)

### Window Styles & GW_* Constants
- `GW_owner = 4` - Get owner window (line 144)

### Control-Specific Messages
- `LB_SETSEL = 0x0187` - ListBox selection (used in `_expand_single_section`)
- `LB_GETITEMRECT = 0x0198` - Get item rectangle (line 2897)
- `LB_GETTOPINDEX = 0x018E` - Get top index (line 1213)
- `LB_SETTOPINDEX = 0x018F` - Set top index (line 1217)
- `WM_CHAR = 0x0102` - Character message
- `EN_CHANGE = 0x0301` - Edit change notification

### Win32 Structure Types
- `ctypes.wintypes.RECT` - Rectangle structure
- `ctypes.wintypes.POINT` - Point structure

---

## 3. Windows-Specific DLL Imports

### ctypes.windll References
- `ctypes.windll.user32` - Win32 API (26 references in bridge_ext)
- `ctypes.windll.kernel32` - Kernel API (lines 3675, 3738)
- `ctypes.windll.gdi32` - GDI
- `ctypes.windll.psapi` - Process Status API

---

## 4. Quicken-Specific Window Classes (Windows UIA)

These class names are specific to Quicken's Win32 UI:
- `QWinDlg` - Quicken dialog window class
- `QWinPopup` - Quicken popup class
- `#32770` - Generic dialog class
- `QWMDI` - Quicken MDI container
- `QWAcctBarHolder` - Account bar holder
- `QC_button` - Quicken custom button class
- `QWHtmlView` - Quicken HTML view control

---

## 5. Windows COM/UIA Assumptions

### pywinauto/UIA Dependency
- Uses `pywinauto.controls.uiawrapper.UIAWrapper` for UIA operations
- Relies on COM-based UI Automation provider
- Uses TreeScope.Subtree for element enumeration

### MSAA (Microsoft Active Accessibility)
- Uses LegacyIAccessiblePattern for MSAA fallback
- MSAA role constants like `ROLE_SYSTEM_PUSHBUTTON = 43 (0x2B)`

---

## Abstraction Needed

| Windows API | macOS Equivalent | Status |
|------|------|------|
| `user32.IsWindow()` | `AXUIElementIsValid()` or PID+window ID check | Partial |
| ` user32.GetWindowRect()` | `get_frame()` in macOS util | Existing |
| `user32.GetClassNameW()` | `role_name()` or AX class attribute | Need mapping |
| `user32.GetWindowTextW()` | `get_title()` or `get_description()` | Existing |
| `user32.SendMessageW()` | `AXUIElementPerformAction()` or `ax_set_attribute()` | Partial (action-based) |
| `user32.PostMessageW()` | `CGEventPost()` with key/mouse events | Partial |
| `SetForegroundWindow()` | CGEvent focus event | Need |
| `SetCursorPos()` | `CGEventSetLocation()` | Existing (mouse_click_quartz) |
| `mouse_event()` | `CGEventMouseClick()` | Existing |
| `EnumWindows()` | `list_all_windows()` or `NSWorkspace` | Existing |
| `EnumChildWindows()` | `get_children()` via AX hierarchy | Existing |
| `ClientToScreen()` | Coordinate conversion + AX frame | Need |
| `GetDeviceCaps(DPI)` | `CGDisplayPixelsPerInch()` | Need |

