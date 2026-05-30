# Part 3: Quicken Semantic Abstraction Gaps

## Overview
The Quicken skill (`bridge_ext.py`) implements high-level financial application semantics:
- Sidebar navigation and account discovery
- Register view state reading
- Transaction row reading
- Reconcile dialog workflow

These semantics are NOT accessible via standard UIA/AXAPI - they require Quicken-specific knowledge.

---

## Quicken-Specific Functions (59 total, 10 critical)

### 1. Modal Dialog Dismissal (lines 56-218)
```python
def _dismiss_modal_dialogs(root_hwnd: int, *, max_rounds=3) -> bool
```
- **Windows-specific**: Enumerates `QWinDlg`, `QWinPopup`, `#32770` window classes
- **Uses**: `SetForegroundWindow`, physical mouse click (only method that works)
- **macOS gap**: No equivalent to dialog class names or physical mouse click workaround
- **Impact**: Blocks any automation if dialogs (error, save, report) are present

### 2. ComboBox Operations (lines 297-424)
```python
def _combo_get_items(hwnd: int) -> list[str]
def _find_account_combo(root_hwnd: int) -> tuple[int, list[str]]
```
- **Windows-specific**: Uses `SendMessageW(WM_GETTEXTLENGTH, 0)`, `LB_GETCOUNT`
- **macOS gap**: macOS AXComboBox has different property model (`AXSelectedTitles`, `AXTitle`)
- **Impact**: `list_accounts()` tool cannot work without this abstraction

### 3. Sidebar Account Discovery (lines 584-2288)
```python
def _find_sidebar_holder(root_hwnd: int) -> int
def _expand_sidebar_sections(holder: int) -> bool
def _sweep_scan_sidebar(root_hwnd: int, max_seconds=600) -> list[dict]
def list_sidebar_accounts(bridge, resume=False, force_rescan=False) -> list[dict]
```
- **Windows-specific**: 
  - Relies on `QWAcctBarHolder` class name
  - Uses `LB_GETITEMRECT` scroll position calculation
  - Physical double-click at hardcoded screen coordinates
  - Wheel scroll with `mouse_event(0x0002, ...)`
- **macOS gap**: 
  - No sidebar holder class equivalent
  - ListBox rectangle calculation needs AX hierarchy traversal
  - Double-click semantics differ (AXAction vs mouse_event)
  - Scroll wheel uses Quartz CGEvent
- **Impact**: Most critical function for account navigation

### 4. Account Navigation (lines 2421-3194)
```python
def navigate_to_account(bridge, account_name) -> dict
```
Three methods, all Windows-specific:
1. **Sidebar double-click**: Uses cached screen coords + `mouse_event`
2. **MDI tab click**: Finds `QWMDI` child window + `PostMessageW`
3. **Toolbar combobox**: Uses combo box with `SendMessageW`
- **macOS gap**: All three methods need complete re-implementation

### 5. Register State Reading (lines 3195-3510)
```python
def read_register_state(bridge) -> dict
```
Reads account name, balance total, transaction count, reconcile mode, filter text
- **Windows-specific**: Uses `GetWindowTextW` on various custom controls
- **macOS gap**: Balance total and reconcile mode are custom-drawn, not exposed via AX
- **Impact**: Agentic workflow relies on this for context

### 6. Transaction Row Reading (lines 3643-4128)
```python
def read_register_rows(bridge, max_rows=50) -> list[dict]
```
Returns array of {date, payee, check_num, category, memo, payment, deposit, balance}
- **Windows-specific**:
  - Uses Ctrl+Home to navigate to first row
  - Uses Tab + Down arrow navigation (keyboard, not AI)
  - Uses `GetWindowTextW` on each row cell
- **macOS gap**:
  - RXTransactionList is owner-drawn grid, no AX accessibility
  - Keyboard navigation via Quartz CGEvent instead of SendKeys
  - Row cell extraction needs screen OCR or custom bridge
- **Impact**: Most critical data extraction function

### 7. Register Filter Setting (lines 4129-4338)
```python
def set_register_filter(bridge, text) -> dict
```
Types search term into filter box
- **Windows-specific**: Uses `SendMessageW(WM_CHAR, ...)` to inject text
- **macOS gap**: macOS uses `type_text_quartz()` but focus handling differs
- **Impact**: Filtering transactions by payee/amount requires this

### 8. Reconcile Dialog (lines 4339-4611)
```python
def open_reconcile(bridge, account_name, statement_date, ending_balance, ...) -> dict
```
Full reconcile workflow with dialog handling
- **Windows-specific**:
  - Sends `WM_COMMAND 103` to QFRAME to open reconcile
  - Enumerates `QWinPopup` report windows
  - Fills date/amount fields via `SendMessageW`
  - Uses physical click for "Choose Reconcile Account" dialog
- **macOS gap**: Reconcile workflow is app-specific, no AX equivalent
- **Impact**: Critical for bank reconciliation automation

---

## Abstraction Gap Summary

| Quicken Function | Windows Implementation | macOS AXAPI Equivalent | Feasibility |
|-----------------|----------------------|----------------------|-------------|
| `_dismiss_modal_dialogs` | Win32 class enum + mouse_event | AX dialog detection + action | Medium (requires custom window detection) |
| `_combo_get_items` | `SendMessageW` + LB_* messages | `AXSelectedTitles` + enumeration | High (AX supports this directly) |
| Sidebar enumeration | `QBcctBarHolder` + LB rectangles | AX hierarchy traversal | Medium (different container model) |
| `_sidebar_dblclick` | Physical click at screen coords | AXPerformAction("AXClick") | High (AX has click action) |
| `read_register_state` | Win32 text extraction | AX value extraction | Medium (custom controls not exposed) |
| `read_register_rows` | Tab navigation + GetWindowText | Owner-drawn grid OCR | Low (requires screen capture) |
| `set_register_filter` | `SendMessageW(WM_CHAR)` | `type_text_quartz` | High (keyboard input works) |
| `open_reconcile` | WM_COMMAND + dialog enum | App-specific workflow | Low (Quicken mac UX different) |

---

## Missing Abstractions Needed

1. **Platform-agnostic element selector**
   - Currently: `{"by": "name", "value": "X"}` works across Windows/Mac/Linux
   - Missing: MSAA role matching fallback for macOS

2. **Element role mapping**
   - Windows: `ControlType.Button` → role=""button""
   - macOS: `role_name(Element)` → "button"
   - Missing: Unified role taxonomy across platforms

3. **Screen coordinate translation**
   - Windows: Win32 HWND + ClientToScreen
   - macOS: AX frame (relative to screen origin)
   - Missing: Unified absolute coordinate system

4. **Dialog/window enumeration**
   - Windows: `EnumWindows` + class name filters
   - macOS: `list_all_windows()` + bundle ID/title filters
   - Missing: Unified window finder with role filtering

5. **Input synthesis abstraction**
   - Windows: `SendInput` + `SendMessageW`
   - macOS: `CGEventPost` with key codes
   - Missing: Unified keyboard/mouse event API

6. **OCR/screen text extraction**
   - Windows: `ImageGrab` + `OcrEngine`
   - macOS: `CGWindowListCreateImage` + Tesseract/Vision
   - Missing: Unified OCR with platform-specific backends
