# Part 5: What Will NOT Translate to macOS - Summary

## Fundamental Gaps: Windows vs macOS

### 1. Win32 Message-Based Architecture
**Windows Quicken** uses Win32 window messages as a communication protocol:
- `SendMessageW(WM_CLOSE)` to dismiss dialogs
- `SendMessageW(LB_GETITEMRECT)` to query list box item positions
- `PostMessageW(WM_LBUTTONDOWN/UP)` to simulate clicks
- `WM_CHAR` to inject text into edit controls

**macOS Quicken** has no equivalent messaging system. macOS apps use:
- Direct method calls via accessibility actions
- GUI scripting with `CGEvent`
- Text input via `NSPasteboard`
- No equivalent to Win32 message queue

**Impact**: ~30-40% of bridge_ext.py code cannot be ported directly.

---

### 2. Owner-Drawn Controls & Screen Capture Dependency
**Windows Quicken** has proprietary owner-drawn controls:
- Transaction register rows are owner-drawn grid (not standard Windows ListView)
- Investment portfolio values are rendered via `QWHtmlView`
- Balance totals use custom rendering
- Sidebar items are owner-drawn in listbox

**Solution on Windows**: Screen capture + OCR to extract values that aren't exposed via Win32 APIs.

**macOS Problem**: 
- macOS Quicken may use similar owner-drawn controls
- Screen capture + OCR works but performance unclear
- No visibility into whether macOS Quicken exposes these as AX elements

**Impact**: `read_register_rows()` and `read_register_state()` may require complete re-implementation.

---

### 3. Quicken UI Framework Differences
**Windows Quicken** (~2500 lines of bridge_ext):
- Custom window classes: `QWinDlg`, `QWinPopup`, `QWMDI`, `QWAcctBarHolder`
- MDI (Multiple Document Interface) for tabs
- Toolbar combos for account selection
- Sidebar with ListBox for account enumeration

**macOS Quicken** uses native Cocoa:
- Different window hierarchy
- Tabs via `NSTabView` or similar
- Different sidebar/navigation model
- Likely uses different account selection UI

**Impact**: Sidebar account enumeration (`_sweep_scan_sidebar` ~900 lines) will need complete rewrite.

---

### 4. Dialog Handling - Modal Dialogs Blocking Automation
**Windows Implementation** (lines 56-218):
```
Problem: Modal QWinDlg dialogs block automation, SendMessageW stalls inside modal loop
Solution: Physical mouse_event(SetForegroundWindow + SetCursorPos + click) to dismiss
```

**Why this is Windows-specific**:
- Windows modal loops intercept messages and prevent `SendMessageW` from returning
- Physical mouse events bypass the message loop
- Win32 provides `SetForegroundWindow` + `SetCursorPos` + `mouse_event` precisely for this workaround

**macOS equivalent**:
- macOS has different modal behavior
- `CGEventPost` mouse events work differently
- May not need the same workaround
- But requires extensive testing on actual macOS Quicken to confirm

**Impact**: `_dismiss_modal_dialogs()` function may work differently or not be needed.

---

### 5. Coordinate System & Screen Position Assumptions
**Windows code** (lines 1267, 1851, 2159, 2906, 3051):
```python
user32.ClientToScreen(hwnd, byref(pt))  # Convert client coords to screen
screen_x, screen_y = pt.x, pt.y
user32.SetCursorPos(screen_x, screen_y)  # Move cursor to screen position
user32.mouse_event(...)  # Click
```

This workflow is deeply embedded in:
- `_sidebar_dblclick()` - clicks at cached screen coords
- `_expand_single_section()` - calculates ListBox item screen position
- Entire sidebar scroll/click loop

**macOS equivalent**:
- AX frame coordinates are relative to screen origin
- `CGEventMouseClick()` works with screen coordinates
- BUT: Requires coordinate conversion from AX hierarchy
- DPI scaling handling differs between Windows and macOS

**Impact**: All coordinate-based click operations need re-testing on macOS.

---

### 6. Keyboard Input Injection
**Windows** (`SendMessageW(WM_CHAR, char_code)` pattern):
- Injects individual character messages
- Works even when window not focused
- Can be intercepted/modified by dialogs

**macOS** (`type_text_quartz()` pattern):
- Uses `CGEventPost` with keyboard events
- Requires focus or explicit target
- Different key code mapping (Windows VK_* vs macOS kVK_*)

**Current code** uses both approaches:
- `read_register_rows()` uses arrow keys (Tab, Down, Ctrl+Home)
- `set_register_filter()` uses `WM_CHAR` text injection

**Impact**: Keyboard-based navigation works but may need focus/timing adjustments on macOS.

---

### 7. ListBox Operations - Platform-Specific Message Protocol
**Windows** uses message API on ListBox HWND:
- `LB_GETCOUNT` - get item count
- `LB_GETITEMRECT` - get item rectangle at index
- `LB_GETTOPINDEX` - which item is at top
- `LB_SETTOPINDEX` - scroll to show item at top
- `LB_SETSEL` - select item by index

**macOS** uses AX element tree:
- `get_children()` returns array of AXElement
- `get_frame()` gives position
- `ax_perform_action("AXPress")` to select
- No index-based operations

This impacts 5+ functions that work with ListBox items:
- `_combo_get_items()` - enumerate combobox items
- `_find_sidebar_accounts()` - scan ListBox for account buttons
- `_expand_single_section()` - expand/collapse sections
- `_sweep_scan_sidebar()` - iterate items with scroll position

**Impact**: These functions work on principles (traversing hierarchies) but details completely different.

---

### 8. Reconcile Workflow - Quicken App-Specific Feature
**Windows Implementation** (lines 4339-4611):
- Sends `WM_COMMAND 103` to QFRAME window to trigger "Open Reconcile"
- Handles multi-step dialog flow: "Choose Account" → "Reconcile Details"
- Uses physical click to handle modal dialogs
- Sets date/balance fields via message passing

**Why it won't translate**:
- Reconcile command ID (103) is specific to Windows Quicken
- Dialog hierarchy may be different on macOS
- macOS Quicken may have different reconcile UX
- Requires knowledge of macOS Quicken's message protocol (if it exists)

**Impact**: `open_reconcile()` function (~270 lines) likely needs complete rewrite or may not be implementable.

---

### 9. Window Enumeration & Class-Based Lookup
**Windows pattern** (lines 141-153, 378-424):
```python
def _enum_cb(h, _):
    user32.GetClassNameW(h, cls, 64)
    if cls.value == "QWinDlg":
        # found dialog
        return False  # stop enum
```

Uses `EnumWindows` callback with class name matching.

**macOS pattern**:
- Use `list_all_windows()` which returns list
- Match by role, title, or app identifier
- No window class names in the Win32 sense

**Impact**: Window finding logic is similar (enumerate, filter) but implementation details differ.

---

### 10. DPI Scaling & Resolution Handling
**Windows** (from process_manager.py):
```python
dpi = ctypes.windll.gdi32.GetDeviceCaps(dc, 88)  # LOGPIXELSX
dpi_scale = dpi / 96.0
```

Quicken on Windows may render at different DPI - code caches and adjusts screen coordinates.

**macOS**:
- Native DPI is 72 or 144 (Retina displays)
- AX frame coordinates already account for scaling
- Different scaling model

**Impact**: Coordinate-based operations (sidebar double-click) may need DPI adjustment.

---

## Summary Table: Portability Analysis

| Component | Windows Lines | Portability | Critical Path |
|-----------|---|---|---|
| Modal dialog dismissal | 162 | LOW - Different modal model | Blocking issue |
| Sidebar enumeration | 1700+ | MEDIUM - Different UI structure | Core feature |
| Register row reading | 500+ | LOW - Owner-drawn grid | Core data |
| Account navigation | 800+ | MEDIUM - Multiple fallback paths | Core workflow |
| Register state reading | 300+ | MEDIUM - Custom controls | Context feature |
| Reconcile workflow | 270 | LOW - App-specific feature | Optional feature |
| ComboBox operations | 150 | HIGH - AX has this | Foundational |
| Input synthesis | 200+ | HIGH - Quartz supports it | Foundational |
| Screen capture/OCR | 160 | MEDIUM - Tools exist but differ | Custom feature |
| Filter/search operations | 200+ | HIGH - Keyboard input works | Common task |

---

## What Needs Complete Re-implementation for macOS

1. **Sidebar account enumeration** (~1700 lines)
   - Windows uses ListBox with `LB_GETITEMRECT` + physical coordinates
   - macOS needs to understand Quicken's Cocoa sidebar structure
   - Must discover what AX elements are available for accounts

2. **Register row reading** (~500 lines)
   - Windows uses Ctrl+Home to first row, Tab navigation, GetWindowText
   - Quicken registers are owner-drawn
   - macOS may require screen OCR or reverse-engineered AX extensions

3. **Reconcile workflow** (~270 lines)
   - Windows sends WM_COMMAND 103 to trigger reconcile
   - No equivalent command ID on macOS
   - May require scripting Quicken's menu system

4. **Dialog dismissal strategy** (~160 lines)
   - Physical mouse_event workaround may not be needed
   - But unknown if macOS Quicken has modal blocking issues
   - Requires empirical testing

5. **Account navigation fallback paths** (~200 lines)
   - Sidebar double-click uses cached screen coords
   - MDI tab finding/clicking uses QWMDI class
   - Combo box selection uses message passing
   - All three methods need redesign

---

## Features That WILL Work on macOS

1. **Basic element finding** - AX selectors work similar to Windows
2. **Text extraction** - `get_title()`, `get_description()` available
3. **Keyboard input** - `type_text_quartz()` exists and works
4. **Mouse clicks** - `mouse_click_quartz()` works at screen coords
5. **Element invoke** - `AXUIElementPerformAction()` for click/press
6. **Hierarchy traversal** - `get_children()` works same as Windows

---

## Recommendation

**Do NOT attempt a 1:1 port of `bridge_ext.py` to macOS.**

Instead:

1. **Create `QuickenMacOS` backend** with macOS-specific implementations
2. **Use different algorithms** for sidebar account discovery (analyze actual macOS Quicken UI)
3. **Accept feature gaps** - some Windows features may not be implementable on macOS
4. **Prioritize core workflows**:
   - Account navigation (required)
   - Register state reading (required)
   - Transaction row reading (required)
   - Reconcile (optional - may not work)
5. **Test empirically** - Quicken for Mac may have different UI than expectations
