# Quicken Classic Premier — Skill Reference

> **File**: Jdouc's Quicken Data  
> **Version**: Quicken Classic Premier  
> **Automation stack**: PowerShell .NET UIA + Python ctypes (PrintWindow for screenshots)

---

## 1. Window & Process

| Property | Value |
|----------|-------|
| HWND | 722032 (may change on reopen) |
| Class | QFRAME |
| PID | 3148 (may change) |
| Title pattern | `Quicken Classic Premier - * - [*]` |

**Find window (PowerShell):**
```powershell
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$qw = [System.Windows.Automation.AutomationElement]::RootElement.FindFirst(
    [System.Windows.Automation.TreeScope]::Children,
    (New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::ClassNameProperty, "QFRAME"
    ))
)
```

**Find window (Python ctypes):**
```python
import ctypes
hwnd = ctypes.windll.user32.FindWindowW('QFRAME', None)
```

---

## 2. UI Region Map

```
┌─────────────────────────────────────────────────────────────────────┐
│  Title Bar: "Quicken Classic Premier - Jdouc's Quicken Data - [X]" │
├─────────────────────────────────────────────────────────────────────┤
│  Menu: File | Edit | View | Tools | Mobile & Web | Reports | Help  │
├─────────────────────────────────────────────────────────────────────┤
│  Toolbar: ← → | sync | refresh | ? |    [Search transactions   🔍] │
├──────────────┬──────────────────────────────────────────────────────┤
│  ACCOUNTS    │  HOME | SPENDING | BILLS & INCOME | PLANNING |       │
│  ────────    │  INVESTING | PROPERTY & DEBT | MOBILE & WEB          │
│  All Trans.  ├──────────────────────────────────────────────────────┤
│              │  [Sub-tab: Dashboard]                                 │
│  ▼ Banking   │                                                       │
│    Checking  │  ┌─────────────────┐  ┌─────────────────┐           │
│    Cr. Card  │  │ Investment Top  │  │ Portfolio Value │           │
│              │  │ Movers          │  │ $2,500          │           │
│  ▼ Investing │  └─────────────────┘  └─────────────────┘           │
│    Brokerage │                                                       │
│              │  ┌─────────────────┐  ┌─────────────────┐           │
│  Net Worth   │  │ Top Spending    │  │ Top Payees      │           │
│  $2,500.00   │  │ Categories      │  │                 │           │
│  Credit Score│  └─────────────────┘  └─────────────────┘           │
└──────────────┴──────────────────────────────────────────────────────┘
```

---

## 3. Accounts

| Account | Type | Balance |
|---------|------|---------|
| Checking | Banking | $1,234.00 |
| Credit Card | Banking | -$1,234.00 |
| Brokerage | Investing | $2,500.00 |

---

## 4. Key UIA Class Names

| Class | Purpose |
|-------|---------|
| `QFRAME` | Main application window |
| `QWNavigator` | Left sidebar navigation |
| `QC_button` | Quicken custom button (nav items, account links) |
| `QWNavBtnTray` | Bottom of sidebar (account groups) |
| `QWAcctBarHolder` | Account group container |
| `QWListViewer` | Account group (Banking, Investing) |
| `ListBox` | Account list within a group |
| `QW_MAIN_TOOLBAR` | Top toolbar |
| `QWPanel` | Panel (search bar area) |
| `MDIClient` | Main content MDI host |
| `QWMDI` | MDI child window (register/view) |
| `QWSnapHolder` | Snap/widget holder in MDI child |

---

## 5. Navigation Patterns

### Open an account register (CONFIRMED WORKING)

**Mechanism**: Double-click on the `QWListViewer` item in the sidebar account bar.

- Account groups (Banking, Investing) must be **expanded first** by single-clicking the group header
- Individual account rows are `QWListViewer` controls (NOT `QC_button` — those are invisible overlays)
- `QC_button` items for accounts have `WS_VISIBLE=0`; `QWListViewer` items have `WS_VISIBLE=1`
- **Single click** = selects/highlights the account but stays on current view
- **Double-click** = opens the account register in MDI

**Python navigation recipe:**
```python
import ctypes, ctypes.wintypes as wt, time

user32 = ctypes.windll.user32

def get_rect(hwnd):
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return (r.left, r.top, r.right-r.left, r.bottom-r.top)

def dblclick(x, y, delay=1.5):
    user32.SetCursorPos(x, y)
    time.sleep(0.1)
    for _ in range(2):
        user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
        user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP
        time.sleep(0.1)
    time.sleep(delay)

main = user32.FindWindowW("QFRAME", None)
user32.SetForegroundWindow(main)

# HWNDs (may change on Quicken restart — re-enumerate if needed):
# Sidebar container: QWAcctBarHolder HWND=132958
# Banking header:    QC_button HWND=132954 ctrl_id=2101  y≈151
# Checking row:      QWListViewer HWND=132946 ctrl_id=2201  y≈176
# Credit Card row:   QWListViewer HWND=132930 ctrl_id=2201  y≈195
# Investing header:  QC_button HWND=132928 ctrl_id=2104  y≈221
# Brokerage row:     QWListViewer HWND=132910 ctrl_id=2204  y≈247

# 1. Expand Banking section (if collapsed):
banking_rect = get_rect(132954)
user32.SetCursorPos(banking_rect[0]+banking_rect[2]//2, banking_rect[1]+banking_rect[3]//2)
user32.mouse_event(0x0002, 0, 0, 0, 0)
user32.mouse_event(0x0004, 0, 0, 0, 0)
time.sleep(0.5)

# 2. Double-click the account row
checking_rect = get_rect(132946)  # QWListViewer for Checking
cx = checking_rect[0] + checking_rect[2]//2
cy = checking_rect[1] + checking_rect[3]//2
dblclick(cx, cy)
```

### Nav tab switching (WM_COMMAND — CONFIRMED WORKING)
```python
# Ctrl IDs: HOME=32300, SPENDING=32301, BILLS&INCOME=32302, PLANNING=32303,
#           INVESTING=32304, PROPERTY&DEBT=32305, MOBILE&WEB=32306
# Parent: QWNavigator HWND=198862
# Example: switch to SPENDING
user32.SendMessageW(198862, 0x0111, 32301, 198856)  # WM_COMMAND
```

### All Transactions view
```python
user32.SendMessageW(132958, 0x0111, 2000, 132956)  # WM_COMMAND to QWAcctBarHolder
```

### Return to Home
```python
user32.SendMessageW(198862, 0x0111, 32300, 198422)  # WM_COMMAND to QWNavigator
```

### IMPORTANT: Session must be connected (not disconnected)
- `SetCursorPos`, `mouse_event`, `SendInput` all fail with **error 5 (ACCESS_DENIED)** when session is disconnected
- `GetCursorPos` also fails — always check this first
- Fix: reconnect session via `tscon 2 /dest:console` (requires admin) or re-RDP into the machine

## 6. Register UI Structure

### Banking registers (Checking, Credit Card)

UIA class `QWMDI` with title = account name. Key child elements:
| UIA Name | Class | Purpose |
|----------|-------|---------|
| `TxList` | `QWClass_TransactionList` | Transaction grid/register rows |
| `TxToolbar` | `QWClass_TxToolbar` | Toolbar above transaction list |
| `Save` | `QC_button` | Save current transaction edit |
| `More actions` | `QC_button` | Dropdown for delete/void/etc. |
| `Split transaction` | `QC_button` | Open split editor |
| `C` | `QC_button` | Reconcile/cleared flag button |
| `All Dates` | `QWComboBox` | Date filter combo |
| `Any Type` | `QWComboBox` | Transaction type filter |
| `All Transactions` | `QWComboBox` | All/uncleared filter |
| `Ending Balance: X` | `Static` | Current balance display |
| `N Transaction(s)` | `Static` | Transaction count |

### Brokerage register (Investment)

UIA class `QWMDI` with title = "Brokerage". Key child elements:
| UIA Name | Class | Purpose |
|----------|-------|---------|
| `Enter Transactions` | `QC_button` | Open investment transaction dialog |
| `Holdings` | `QC_button` | Switch to holdings view |
| `Actions` | `QC_button` | Account actions menu |
| `Find` | `QC_button` | Find transaction |
| `Placeholder Entries (N)` | `QC_button` | Placeholder management |

---

**Only method that works** (screen capture via `Screen.CopyFromScreen` fails in this session):
```python
import ctypes, ctypes.wintypes as w, struct
from PIL import Image

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
hwnd = user32.FindWindowW('QFRAME', None)

rect = w.RECT()
user32.GetWindowRect(hwnd, ctypes.byref(rect))
width = rect.right - rect.left
height = rect.bottom - rect.top

hwndDC = user32.GetWindowDC(hwnd)
mDC = gdi32.CreateCompatibleDC(hwndDC)
bmp = gdi32.CreateCompatibleBitmap(hwndDC, width, height)
gdi32.SelectObject(mDC, bmp)
user32.PrintWindow(hwnd, mDC, 2)  # PW_RENDERFULLCONTENT

BITMAPINFOHEADER = struct.pack('<IiiHHIIiiII', 40, width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
buf = ctypes.create_string_buffer(width * height * 4)
gdi32.GetDIBits(mDC, bmp, 0, height, buf, BITMAPINFOHEADER, 0)

img = Image.frombytes('RGBA', (width, height), bytes(buf), 'raw', 'BGRA')
img.save(r'path\to\screenshot.png')

gdi32.DeleteDC(mDC); user32.ReleaseDC(hwnd, hwndDC); gdi32.DeleteObject(bmp)
```

---

## 7. Transaction Entry — CONFIRMED WORKFLOWS (Phase 2)

> **Desktop is disconnected**: SetCursorPos/SendInput fail. All UI interaction via SendMessage/PostMessage.

### Architecture Overview
- `QWClass_TransactionList` (TxList) — the register grid. Manages the edit row.
- `QREdit` — a single floating edit control that repositions to the active field. Two HWNDs:
  - 198672: text fields (Date, Check#, Payee, Memo, Category)
  - 198688: numeric fields (Payment/Deposit, Amount)
  - **HWND numbers are stable within a session but change across restarts**

### Field Tab Order (Checking register)
| Tab pos | Field | QREdit HWND | Screen X | Width |
|---------|-------|-------------|----------|-------|
| 1 | Date | 198672 | ~279 | 65 |
| 2 | Check # | 198672 | ~363 | 29 |
| 3 | Payee | 198672 | ~411 | 244 |
| 4 | Memo | 198672 | ~658 | 244 |
| 5 | Category | 198672 | ~905 | 243 |
| 6 | Payment/Deposit | 198688 | ~1279 | 53 |
| 7 | Amount | 198688 | ~1351 | 53 |
→ Tab from Amount wraps through 3 invisible fields back to Date

### Navigate between fields
```python
VK_TAB = 0x09; WM_KEYDOWN = 0x0100
# MUST use PostMessage (not SendMessage) — SendMessage blocks forever
user32.PostMessageW(txlist, WM_KEYDOWN, VK_TAB, 1|(0x0F<<16))
time.sleep(0.15)  # wait for field to reposition
```

### Set text in current field
```python
WM_SETTEXT = 0x000C
user32.SendMessageW(qredit_hwnd, WM_SETTEXT, 0, ctypes.c_wchar_p("value"))
# For text fields: qredit_hwnd = 198672
# For numeric fields: qredit_hwnd = 198688
```

### Full transaction entry recipe
```python
txlist = 198488  # QWClass_TransactionList
qredit_txt = 198672; qredit_num = 198688
WM_KEYDOWN=0x0100; WM_SETTEXT=0x000C; VK_TAB=0x09; VK_RETURN=0x0D

def tab(): user32.PostMessageW(txlist, WM_KEYDOWN, VK_TAB, 1|(0x0F<<16)); time.sleep(0.15)
def settext(h, v): user32.SendMessageW(h, WM_SETTEXT, 0, ctypes.c_wchar_p(v))

# Start: focus is on Date field (new row auto-selected after last Enter)
tab()          # → Check#
tab()          # → Payee
settext(qredit_txt, "Test Payee")
tab()          # → Memo
settext(qredit_txt, "Test Memo")
tab()          # → Category (skip: leave blank)
tab()          # → Payment/Deposit
settext(qredit_num, "10.00")
tab()          # → Amount (auto-populated usually; or set explicitly)
settext(qredit_num, "10.00")

# SAVE: Enter on numeric QREdit commits the transaction
user32.PostMessageW(qredit_num, WM_KEYDOWN, VK_RETURN, 1|(0x1C<<16))
time.sleep(0.8)  # wait for save
```

### Cancel/discard an unsaved row
```python
VK_ESCAPE = 0x1B
user32.PostMessageW(txlist, WM_KEYDOWN, VK_ESCAPE, 1|(0x01<<16))
time.sleep(0.5)
```

### Select a row (navigate between rows)
```python
VK_UP=0x26; VK_DOWN=0x28
user32.PostMessageW(txlist, WM_KEYDOWN, VK_UP, 1|(0x48<<16))   # previous row
user32.PostMessageW(txlist, WM_KEYDOWN, VK_DOWN, 1|(0x50<<16)) # next row
time.sleep(0.5)
```

### Delete a transaction (CONFIRMED WORKING)
```python
WM_COMMAND=0x0111; IDYES=6
# 1. Send Delete command
user32.PostMessageW(hwnd, WM_COMMAND, 7106, 0)  # Edit > Transaction > Delete
time.sleep(0.8)
# 2. Dismiss confirmation dialog
# Dialog class #32770, title "Quicken " — find it:
# user32.EnumWindows(...) → find #32770
# 3. Confirm with WM_COMMAND IDYES
user32.SendMessageW(dialog_hwnd, WM_COMMAND, IDYES, 0)
time.sleep(0.5)
```

---

## 8. Menu Navigation (Toolbar + WM_COMMAND)

### Menu bar is ToolbarWindow32 at HWND 855768
```
Button positions (client coords, stable):
  [0] "&File"        center=(17, 10)   rect=(0,0,34,21)
  [1] "&Edit"        center=(52, 10)   rect=(34,0,70,21)
  [2] "&View"        center=(90, 10)   rect=(70,0,111,21)
  [3] "&Tools"       center=(132, 10)  rect=(111,0,154,21)
  [4] "&Mobile && Web" center=(200,10) rect=(154,0,247,21)
  [5] "&Reports"     center=(275, 10)  rect=(247,0,303,21)
  [6] "&Help"        center=(323, 10)  rect=(303,0,344,21)
```

### Open a menu (non-blocking)
```python
TB = 855768  # ToolbarWindow32
WM_LBUTTONDOWN=0x0201; WM_LBUTTONUP=0x0202; MK_LBUTTON=1
edit_cx, edit_cy = 52, 10
lp = (edit_cy<<16)|edit_cx
user32.PostMessageW(TB, WM_LBUTTONDOWN, MK_LBUTTON, lp)
user32.PostMessageW(TB, WM_LBUTTONUP, 0, lp)
time.sleep(0.3)
```
⚠️ Use **PostMessage** not SendMessage — SendMessage blocks while menu is open

### Read menu item IDs from open popup
```python
MN_GETHMENU = 0x01E1
# Find the #32768 popup window
def find_popup():
    res = []
    def cb(h, lp):
        cls = ctypes.create_unicode_buffer(64); user32.GetClassNameW(h, cls, 64)
        if cls.value == '#32768' and user32.IsWindowVisible(h): res.append(h)
        return True
    user32.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool,w.HWND,w.LPARAM)(cb), 0)
    return res

popup = find_popup()[0]
hm = user32.SendMessageW(popup, MN_GETHMENU, 0, 0)
trans_sub = user32.GetSubMenu(hm, 4)  # Edit menu index 4 = Transaction
# Enumerate: GetMenuItemCount, GetMenuStringW, GetMenuItemID
```

### Known WM_COMMAND IDs (Edit > Transaction submenu)
| ID | Menu Item |
|----|-----------|
| 101 | Save |
| 7111 | Restore transaction (Esc) |
| 103 | Split... (Ctrl+S) |
| 7138 | Notes and flags... |
| 7113 | Attachments... |
| 7122 | Copy transaction(s) |
| 7127 | Cut transaction(s) |
| 7123 | Paste transaction(s) |
| 7141 | Edit transaction(s) |
| 7104 | New (Ctrl+N) |
| **7106** | **Delete (Ctrl+D)** ← use this |
| 7139 | Undo delete |
| 7121 | Insert transaction (Ctrl+I) |
| 7108 | Memorize payee (Ctrl+M) |
| 7107 | Void transaction(s) |
| 351 | Find... (Ctrl+F) |
| 7110 | Go To matching transfer |

### Execute a menu command directly (skip opening menu)
```python
# No need to open the menu — send WM_COMMAND directly to QFRAME
user32.PostMessageW(hwnd, WM_COMMAND, 7106, 0)  # Delete selected transaction
```

---

## 9. Dialog Handling

### Confirm delete dialog
```python
# After WM_COMMAND 7106, Quicken shows #32770 dialog "Quicken "
# Find it:
def find_confirm_dialog(hwnd_main):
    found = []
    def cb(h, lp):
        if user32.IsWindowVisible(h):
            cls = ctypes.create_unicode_buffer(64); user32.GetClassNameW(h, cls, 64)
            txt = ctypes.create_unicode_buffer(256); user32.GetWindowTextW(h, txt, 256)
            r = ctypes.wintypes.RECT(); user32.GetWindowRect(h, ctypes.byref(r))
            if cls.value == '#32770' and r.right - r.left < 600:
                found.append(h)
        return True
    user32.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool,w.HWND,w.LPARAM)(cb), 0)
    return found

dlg = find_confirm_dialog(hwnd)[0]
user32.SendMessageW(dlg, 0x0111, 6, 0)  # WM_COMMAND IDYES=6 → confirm
```

### Close any dialog with ESC
```python
user32.PostMessageW(dialog_hwnd, WM_KEYDOWN, VK_ESCAPE, 1|(0x01<<16))
```

---

## 10. Known Pitfalls
- **HWNDs change** across sessions (QFRAME, QREdit, TxList, ToolbarWindow32 all stable within session; re-enumerate on restart)
- `SendMessage WM_KEYDOWN VK_TAB` to TxList → **blocks forever** (modal Category autocomplete loop)
- `SendMessage WM_LBUTTONDOWN` to QWClass_TransactionList → Quicken validates cursor pos via GetCursorPos; always returns processed but has NO effect
- Toolbar action buttons (Save/More actions/Split) shown on row are invisible (IsVisible=0); don't use BM_CLICK on them
- The inline row action icons (checkmark/gear/X) are custom-drawn by TxList; no separate HWNDs
- `SendMessage` to ToolbarWindow32 to open menu → **blocks forever** (menu modal loop); use PostMessage
- `Screen.CopyFromScreen` and `PIL.ImageGrab.grab()` fail in disconnected session — use PrintWindow
- ESC sent to TxList cancels the entire edit row (returns to Date); don't use for dropdown dismissal

---

## 11. Recovery Strategies
- **Unexpected dialog**: Find #32770 via EnumWindows, send WM_COMMAND IDCANCEL=2 or IDNO=7 or ESC
- **Return to Home**: `user32.SendMessageW(qw_navigator, WM_COMMAND, 32300, 0)`
- **App unresponsive**: Wait 2-3 seconds; Quicken may be updating display
- **Crashed**: Re-launch `Start-Process "C:\Program Files (x86)\Quicken\qw.exe"`, then open data file

## 12. Sidebar HWND Reference (may change on restart)

| Control | Class | HWND | ctrl_id | Screen pos (y) | Notes |
|---------|-------|------|---------|----------------|-------|
| All Transactions | QC_button | 132956 | 2000 | ~127 | Always visible |
| Banking header | QC_button | 132954 | 2101 | ~151 | Click to expand/collapse |
| Checking row | **QWListViewer** | **132946** | 2201 | ~176 | **Dblclick to open** |
| Credit Card row | **QWListViewer** | **132930** | 2201 | ~195 | **Dblclick to open** |
| Investing header | QC_button | 132928 | 2104 | ~221 | Click to expand/collapse |
| Brokerage row | **QWListViewer** | **132910** | 2204 | ~247 | **Dblclick to open** |

### Re-enumerate sidebar at runtime:
```python
acctbar = user32.FindWindowW("QFRAME", None)  # get main first
# Walk children of QWAcctBarHolder, filter QWListViewer with non-zero rect
child = user32.GetWindow(acctbar_hwnd, 5)  # GW_CHILD
while child:
    if get_class(child) == "QWListViewer" and get_rect(child)[2] > 0:
        # Found an account row — get y for hit testing
        pass
    child = user32.GetWindow(child, 2)  # GW_HWNDNEXT
```

---

---

## 13. Split Transactions (Phase 3) — CONFIRMED WORKING

### Split dialog mechanics
- **Open**: `user32.PostMessageW(qframe, WM_COMMAND, 103, 0)`
- **Dialog class**: `QWinDlg` (top-level, NOT child of QFRAME)
- **Screenshot**: use `PrintWindow(split_dlg_hwnd, ...)` directly
- **Dialog HWND**: enumerate visible `QWinDlg` windows after sending WM_COMMAND 103

### Split dialog structure
| Element | Class | Purpose |
|---------|-------|---------|
| Category Edit | `Edit` (HWND ~330248) | Category field for active row |
| Memo Edit | `Edit` (HWND ~395970) | Memo field (hidden until active) |
| Amount Edit | `Edit` (HWND ~395994) | Amount field (hidden until active) |
| ListBox | `ListBox` (HWND ~1966750) | Row grid |
| OK button | `QC_button` or `Button` (HWND ~1706290) | Save split |
| Cancel | `QC_button` (HWND ~461110) | Discard |
| Split Total | `Static` | Running total |
| Remainder | `Static` | Unallocated amount |
| Transaction Total | `Static` | Original transaction amount |

### Split dialog field navigation recipe
The dialog has Category→Memo→Amount columns per row. Tab cycles columns, not rows.

```python
split_dlg = <QWinDlg HWND>
listbox = <ListBox HWND>
WM_SETTEXT=0x000C; WM_KEYDOWN=0x0100; VK_TAB=0x09
WM_LBUTTONDOWN=0x0201; WM_LBUTTONUP=0x0202; MK_LBUTTON=1

def get_visible_edits():
    """Returns list of (hwnd, x, y, w, h, visible, text) for Edit children of split_dlg."""
    ...  # enumerate dialog children, filter 'Edit' class, check visibility

# Step 1: click ListBox at row N Category column (x≈0-190, y depends on row)
# Row 1 item rect: LB_GETITEMRECT → y_top, y_bot
# Click center of Category column (x≈100, y=center) in ListBox
lp = (row_cy<<16)|100
user32.PostMessageW(listbox, WM_LBUTTONDOWN, MK_LBUTTON, lp)
time.sleep(0.05); user32.PostMessageW(listbox, WM_LBUTTONUP, 0, lp); time.sleep(0.2)
# Category Edit (330248) becomes visible

# Step 2: Set category text
user32.SendMessageW(330248, WM_SETTEXT, 0, ctypes.c_wchar_p("Food & Dining"))

# Step 3: Tab to Memo, Tab to Amount
user32.PostMessageW(330248, WM_KEYDOWN, VK_TAB, 1|(0x0F<<16)); time.sleep(0.2)
# Memo Edit (395970) becomes visible/focused
user32.PostMessageW(395970, WM_KEYDOWN, VK_TAB, 1|(0x0F<<16)); time.sleep(0.2)
# Amount Edit (395994) becomes visible
user32.SendMessageW(395994, WM_SETTEXT, 0, ctypes.c_wchar_p("15.00"))

# Step 4: Tab from Amount → activates row 2 Category
user32.PostMessageW(395994, WM_KEYDOWN, VK_TAB, 1|(0x0F<<16)); time.sleep(0.3)
# Category Edit (330248) moves to row 2

# Step 5: Fill row 2 same way, then Tab×2 to Amount
# (Amount Edit HWND 395994 reused for all rows)
```

### Critical split pitfall: Tab target
- Tab must be sent to the **currently active Edit** (e.g., 330248 or 395970), NOT the dialog or ListBox
- Sending Tab to the dialog moves ListBox row focus (selects next row), not column focus
- The same Edit HWNDs (330248, 395970, 395994) are REUSED across rows — they MOVE to the active row

### Save split
```python
# Method 1: WM_LBUTTONDOWN to OK button
ok_hwnd = 1706290  # re-enumerate to confirm
user32.PostMessageW(ok_hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lp_center)
time.sleep(0.05); user32.PostMessageW(ok_hwnd, WM_LBUTTONUP, 0, lp_center)
time.sleep(0.5)
# ← returns to register with transaction in edit mode

# Method 2 (fallback): WM_COMMAND 1 to dialog (IDOK)
user32.PostMessageW(split_dlg, WM_COMMAND, 1, 0)
```

### Save the transaction after split dialog closes
```python
# After OK closes split dialog, row is still in edit mode in register
# WM_COMMAND 101 (Save) to QFRAME reliably commits it:
user32.PostMessageW(qframe, WM_COMMAND, 101, 0)
time.sleep(1.0)
```

### Split in register: how to identify
- Category column shows blank/italic placeholder (no single category assigned)
- Split icon (≡) may appear in Category column

---

## 14. Transfers (Phase 4) — CONFIRMED WORKING

### Transfer syntax
- Enter `[Account Name]` as the **Category** field value
- Example: `[Credit Card]` creates a transfer from Checking to Credit Card
- Quicken automatically creates the matching entry in the target account
- Use exact account name as shown in sidebar, wrapped in square brackets

### Transfer entry recipe
```python
# Same as normal transaction entry; change category field:
tab(2)                              # → Payee
settext(qredit_txt, "Transfer Test")
tab()                               # → Memo (skip)
tab()                               # → Category
settext(qredit_txt, "[Credit Card]")
tab()                               # → Payment
tab()                               # → Amount
settext(qredit_num, "100.00")
user32.PostMessageW(qredit_num, WM_KEYDOWN, VK_RETURN, 1|(0x1C<<16))
```

### Transfer confirmation
- After saving, the transaction shows `[Credit Card]` in Category column
- The target account (Credit Card) automatically receives a matching transaction
- Both sides visible in their respective registers

### Deleting a transfer
- Deleting a transfer transaction shows **TWO** `#32770 "Quicken "` dialogs:
  - First: confirm delete from current account
  - Second: confirm delete of matching entry in other account
- Send IDYES=6 to each dialog in sequence

### Dashboard navigation via QWNavigator
The top-tab dashboard buttons (HOME, SPENDING, etc.) are `QC_button` children of `QWNavigator` (HWND 263490).
- Tab button screen positions (at QFRAME 0,0 origin, QWNavigator origin 5,52):
  | Tab | Screen center | QWNav client |
  |-----|--------------|--------------|
  | HOME | (296,111) | (291,59) |
  | SPENDING | (383,111) | (378,59) |
  | BILLS & INCOME | (499,111) | (494,59) |
  | PLANNING | (615,111) | (610,59) |
  | INVESTING | (717,111) | (712,59) |
  | PROPERTY & DEBT | (839,111) | (834,59) |
  | MOBILE & WEB | (973,111) | (968,59) |
- **Limitation**: PostMessage WM_LBUTTONDOWN to QWNavigator fails with cursor validation after initial navigation; unreliable.

### ⭐ Return to last register from any dashboard
```python
# WM_MDIACTIVATE sent to QWMDI restores the register view
qwmdi = 395038  # QWMDI child — may change on restart
user32.PostMessageW(qwmdi, 0x0222, 0, qwmdi)  # WM_MDIACTIVATE=0x0222
time.sleep(0.5)
# Window title returns to "[Checking]" (or whatever account was open)
```

### Account sidebar navigation
- Sidebar `QWListViewer` / `ListBox` HWNDs do NOT respond to PostMessage WM_LBUTTONDOWN
- `BM_CLICK` on `QC_button` sidebar items returns 0 (not processed)
- **Current workaround**: Use WM_MDIACTIVATE to return to last register; switch accounts via keyboard navigation if possible

---

## 15. Navigation Reference

### HWND Reference (session-specific — re-enumerate on restart)
| Name | Class | HWND (example) | Notes |
|------|-------|----------------|-------|
| QFRAME | QFRAME | 2097686 | Main window |
| QWNavigator | QWNavigator | 263490 | Sidebar + content |
| QWAcctBarHolder | QWAcctBarHolder | 263890 | Account sidebar |
| QWMDI | QWMDI | 395038 | Register/dashboard MDI child |
| TxList | QWClass_TransactionList | 198488 | Transaction register |
| QREdit (text) | QREdit | 198672 | Text fields in register |
| QREdit (num) | QREdit | 198688 | Numeric fields in register |
| ToolbarWindow32 | ToolbarWindow32 | 855768 | Menu bar |
| "2 Transactions" | Static | 198482 | Transaction count |
| "Ending Balance:" | Static | 263972 | Balance display |

### Static ID HWNDs for status polling
```python
# Read transaction count:
buf = ctypes.create_unicode_buffer(128); user32.GetWindowTextW(198482, buf, 128)
# Read balance:
buf = ctypes.create_unicode_buffer(128); user32.GetWindowTextW(263972, buf, 128)
```

---

---

## 16. Brokerage / Investment Register (Phase 5) — CONFIRMED WORKING

### Architecture
- **brok_mdi** (`QWMDI` child for Brokerage) — MDI child window containing the register
- **QWListViewer** (`ctrl=982`) — custom list control; manages the register display  
- **ListBox** (`ctrl=1`) — underlying selection widget; 0-based index; `LB_GETCOUNT` / `LB_SETCURSEL` work  
- **Enter Transactions dialog** — a top-level `QWinDlg` (NOT child of QFRAME)

### Navigate to Brokerage register
```python
WM_COMMAND=0x0111
# WM_COMMAND 7301 opens Account List panel + activates Brokerage register
user32.PostMessageW(qframe, WM_COMMAND, 7301, 0)
time.sleep(1.0)
# Title bar changes to "[Brokerage]"
```

### Re-activate Brokerage MDI child (from dashboard)
```python
WM_MDIACTIVATE = 0x0222
user32.PostMessageW(mdiclient, WM_MDIACTIVATE, 0, brok_mdi)
time.sleep(0.5)
```

### Open "Enter Transactions" dialog
```python
# brok_mdi must be the active MDI child first
user32.PostMessageW(brok_mdi, WM_COMMAND, 981, 0)
time.sleep(2.5)  # dialog takes 2-3 seconds to appear
# Find dialog: EnumWindows → QWinDlg class, IsWindowVisible, rect > 200x200
```

### Investment Transaction Types (combo box, ctrl_id=100)
| Index | Action name | Description |
|-------|-------------|-------------|
| 0 | — (blank) | None |
| 1 | Buy | Purchase shares |
| 2 | Sell | Sell shares |
| 3 | BuyX | Buy with transfer |
| 4 | ReinvDiv | Reinvest Dividend |
| 5 | Inc (Div) | Dividend income |
| 26 | XIn | Cash Transfer In |
| 27 | XOut | Cash Transfer Out |

### Change transaction type
```python
CB_SETCURSEL = 0x014E; CBN_SELCHANGE = 1
txtype_h = user32.GetDlgItem(dlg, 100)  # combo box ctrl_id=100
user32.SendMessageW(txtype_h, CB_SETCURSEL, idx, 0)
# Fire CBN_SELCHANGE notification — REQUIRED or dialog doesn't update fields
user32.SendMessageW(dlg, WM_COMMAND, (CBN_SELCHANGE << 16) | 100, txtype_h)
time.sleep(2.5)  # Wait for dialog to redraw; fields change completely
```

### Field ctrl_ids by transaction type
| Type | ctrl_id | Field |
|------|---------|-------|
| Buy (1) | 104 | Security name |
| Buy (1) | 2802 | Shares |
| Buy (1) | 2803 | Price |
| Buy (1) | 2804 | Commission |
| Buy (1) | 2805 | Total |
| Buy (1) | 108 | Memo |
| Sell (2) | 104 | Security name |
| Sell (2) | 2821 | Price received |
| Sell (2) | 2822 | Shares |
| Sell (2) | 2823 | Commission |
| Sell (2) | 2825 | Total sale |
| Sell (2) | 108 | Memo |
| Inc/Div (5) | 104 | Security name |
| Inc/Div (5) | 2101 | Dividend amount |
| Inc/Div (5) | 2103 | Interest amount |
| Inc/Div (5) | 111 | Transfer account |
| Inc/Div (5) | 108 | Memo |
| ReinvDiv (4) | 104 | Security name |
| ReinvDiv (4) | 2001 | Reinvest amount |
| ReinvDiv (4) | 2002 | Shares |
| ReinvDiv (4) | 2029 | Commission |
| ReinvDiv (4) | 108 | Memo |
| XIn (26) | 2201 | Amount |
| XIn (26) | 111 | Transfer account |
| XIn (26) | 2205 | Description |
| XIn (26) | 108 | Memo |

### Set a field value (CRITICAL: use visible Edit controls only)
```python
WM_SETTEXT = 0x000C

def set_vis(dlg, ctrl_id):
    """Find the VISIBLE Edit control with the given ctrl_id — dialog has many invisible dups."""
    result = []
    def cb(h, lp):
        cls = ctypes.create_unicode_buffer(32)
        user32.GetClassNameW(h, cls, 32)
        if cls.value == 'Edit' and user32.GetDlgCtrlID(h) == ctrl_id and user32.IsWindowVisible(h):
            result.append(h)
        return True
    user32.EnumChildWindows(dlg, ctypes.WINFUNCTYPE(ctypes.c_bool, w.HWND, w.LPARAM)(cb), 0)
    return result[0] if result else None

h = set_vis(dlg, 2802)  # e.g., Shares field for Buy
if h:
    user32.SendMessageW(h, WM_SETTEXT, 0, ctypes.c_wchar_p("10"))
```
⚠️ `GetWindowText` always returns '' after `WM_SETTEXT` — Quicken stores internally. Verify via screenshot.

### Save the investment transaction
```python
# WM_COMMAND 32762 to QWinDlg = Enter/Done button
user32.PostMessageW(dlg, WM_COMMAND, 32762, 0)
time.sleep(3.0)  # dialog closes; transaction appears in register
```

### Complete recipe: Buy transaction
```python
# 1. Open dialog
user32.PostMessageW(brok_mdi, WM_COMMAND, 981, 0); time.sleep(2.5)
dlg = find_qwindlg()  # EnumWindows → QWinDlg, visible, rect>200x200

# 2. Select "Buy" type (idx=1)
txtype_h = user32.GetDlgItem(dlg, 100)
user32.SendMessageW(txtype_h, CB_SETCURSEL, 1, 0)
user32.SendMessageW(dlg, WM_COMMAND, (1<<16)|100, txtype_h)
time.sleep(2.5)

# 3. Set Security
user32.SendMessageW(set_vis(dlg,104), WM_SETTEXT, 0, ctypes.c_wchar_p("Apple Inc"))

# 4. Set Shares, Price, (optionally Commission)
user32.SendMessageW(set_vis(dlg,2802), WM_SETTEXT, 0, ctypes.c_wchar_p("10"))
user32.SendMessageW(set_vis(dlg,2803), WM_SETTEXT, 0, ctypes.c_wchar_p("50"))

# 5. Set Memo
user32.SendMessageW(set_vis(dlg,108), WM_SETTEXT, 0, ctypes.c_wchar_p("Test Buy"))

# 6. Save
user32.PostMessageW(dlg, WM_COMMAND, 32762, 0); time.sleep(3.0)
```

### Select a row in the investment register
```python
LB_SETCURSEL = 0x0186; LB_GETITEMRECT = 0x0198
WM_LBUTTONDOWN=0x0201; WM_LBUTTONUP=0x0202

# Method 1: LB_SETCURSEL (sets ListBox internal selection; doesn't activate register row)
r = user32.SendMessageW(lb, LB_SETCURSEL, idx, 0)  # returns idx on success, -1 on fail

# Method 2: WM_LBUTTONDOWN/UP to ListBox at item rect (CONFIRMED ACTIVATES register row!)
rc = wt.RECT()
user32.SendMessageW(lb, LB_GETITEMRECT, idx, ctypes.byref(rc))
y_center = (rc.top + rc.bottom) // 2
x = 400  # arbitrary x in middle of row
lparam = (y_center << 16) | (x & 0xFFFF)
user32.PostMessageW(lb, WM_LBUTTONDOWN, 1, lparam); time.sleep(0.15)
user32.PostMessageW(lb, WM_LBUTTONUP, 0, lparam); time.sleep(0.5)
# Now WM_COMMAND 7106 will delete this row
```

### Delete an investment transaction
```python
# 1. Activate the row (WM_LBUTTONDOWN/UP to ListBox at item rect — see above)
# 2. Send Delete command to QFRAME
user32.PostMessageW(qframe, WM_COMMAND, 7106, 0); time.sleep(0.8)
# 3. Confirm delete dialog (#32770, title "Quicken ")
dlg = find_confirm_dialog()
user32.SendMessageW(dlg, WM_COMMAND, 6, 0)  # IDYES=6
time.sleep(0.5)
# Note: indices shift down by 1 after each delete — recalculate before next delete
```

### Investment register row index layout (example)
```
Index 0: Opening Balance / Deposit
Index 1: First transaction...
Index N: Last saved transaction
Index N+1: Blank new-transaction row (cursor lands here after save or delete)
```

### Known pitfalls — Brokerage
- Dialog type change requires BOTH `CB_SETCURSEL` + `CBN_SELCHANGE` notification; `CB_SETCURSEL` alone does nothing
- Must wait 2.5s after `CBN_SELCHANGE` before accessing updated fields
- Two `QWinDlg` instances sometimes visible: one is a ghost at (100,100) — use the one with rect > 200×200
- After saving each transaction, cursor moves to blank new row (idx=N+1) — fine for next entry
- LB_SETCURSEL alone does NOT activate the row for deletion; must use WM_LBUTTONDOWN at item Y coord
- Row indices shift by 1 after each delete — must adjust idx accordingly
- `WM_COMMAND 7106` (Delete) requires the row to be "activated" (shows Enter/Edit/Delete buttons)
- Immediately after saving via WM_COMMAND 32762, the blank new row is already "active" — no navigation needed for next transaction

---

---

## 17. Reports (Phase 6) — CONFIRMED WORKING

### How reports open
- Reports open as **QWinPopup** floating windows (NOT embedded in QFRAME)
- They appear as tabs in the bottom taskbar of Quicken but are behind the main window
- **To view**: `ShowWindow(hwnd, 9)` (SW_RESTORE) + `BringWindowToTop(hwnd)` — then screenshot the QWinPopup directly
- **To close**: `PostMessageW(hwnd, WM_CLOSE, 0, 0)`
- `find_popups()`: `EnumWindows` → class `QWinPopup`, `IsWindowVisible`, `GetWindowRect` width > 100

### Open a report (WM_COMMAND to QFRAME)
```python
WM_COMMAND = 0x0111
user32.PostMessageW(qframe, WM_COMMAND, report_id, 0)
time.sleep(2.5)  # Reports take 1-3 seconds to appear
```

### Report WM_COMMAND IDs
| ID | Report |
|----|--------|
| 7495 | Reports & Graphs Center |
| **7403** | **Banking Summary** |
| 7414 | Cash Flow |
| 7407 | Cash Flow by Tag |
| 7402 | Missing Checks |
| **7496** | **Reconciliation (report)** |
| **7401** | **Transaction** |
| 7399 | Current Spending vs. Average by Category |
| 7398 | Current Spending vs. Average by Payee |
| 7415 | Cash Flow Comparison |
| 7404 | Income and Expense Comparison by Category |
| **7423** | **Capital Gains** |
| **7425** | **Investing Activity** |
| **7422** | **Investment Income** |
| **7421** | **Investment Performance** |
| **7424** | **Investment Transactions** ← shows all brokerage trades |
| **7420** | **Portfolio Value** |
| **7476** | **Portfolio Value and Cost Basis** ← shows chart + table |
| 7400 | Account Balances |
| **7410** | **Net Worth** |
| 7413 | Itemized Categories |
| 7473 | Spending by Category |
| 7472 | Income and Expense by Category |
| 7471 | EasyAnswer |

### Report window structure
Every report QWinPopup has a standard toolbar:
```
Back | History | Forward | Delete | [separator] | Email | Display | Print | Export | Save | Find/Replace | Customize | Help
```
Plus filter dropdowns (Date range, Column/Subtotal/Sort by) below the toolbar.

### Report content observations
| Report | Key content |
|--------|-------------|
| Banking Summary | Category breakdown; empty if no banking transactions |
| Investment Transactions | All trades with date, account, action, security, symbol, memo, shares, price |
| Portfolio Value and Cost Basis | Bar chart + table: Security, Cost Basis, Balance columns |
| Net Worth | Assets vs Liabilities over time |

---

## 18. Reconciliation (Phase 6) — CONFIRMED MAPPED (not completed)

### Open reconciliation
```python
# Method 1: Tools > Reconcile an Account (WM_COMMAND 7203)
user32.PostMessageW(qframe, WM_COMMAND, 7203, 0)
time.sleep(2.0)
# Opens "Choose Reconcile Account" dialog (#32770)
```

### Choose Reconcile Account dialog
| Field | Class | HWND (example) | cid | Notes |
|-------|-------|----------------|-----|-------|
| Account combo | QWComboBox | 596246 | 102 | CB_GETCOUNT/CB_GETLBTEXT/CB_SETCURSEL work |
| OK | QC_button | 2364264 | 32767 | |
| Cancel | QC_button | 597034 | 32766 | |

```python
# Read accounts
CB_GETCOUNT=0x0146; CB_GETLBTEXT=0x0148; CB_SETCURSEL=0x014E
n = user32.SendMessageW(cb, CB_GETCOUNT, 0, 0)
for i in range(n):
    buf=ctypes.create_unicode_buffer(256)
    user32.SendMessageW(cb, CB_GETLBTEXT, i, buf)
    print(f'[{i}] {buf.value}')
# Accounts listed: Brokerage, Checking, Credit Card (alphabetical)

# Select Checking
user32.SendMessageW(cb, CB_SETCURSEL, 1, 0)  # idx=1 = Checking
user32.SendMessageW(dlg, WM_COMMAND, (1<<16)|102, cb)  # CBN_SELCHANGE

# Click OK
user32.SendMessageW(ok_btn, WM_COMMAND, 32767, 0)
time.sleep(2.0)
# Opens "Reconcile Details" dialog (#32770)
```

### Reconcile Details dialog
Fields (all Edit controls, with date pickers):
| cid | Field | Example pre-fill |
|-----|-------|-----------------|
| 482 | Ending statement date | today |
| 484 | Prior balance | account balance |
| 486 | Ending balance | (empty — must fill) |
| 488 | Service charge | (empty) |
| 489 | Date | today |
| 490 | Category | (empty) |
| 492 | Interest earned | (empty) |
| 493 | Date | today |
| 494 | Category | Interest Inc |

**Cancel without changes:**  
```python
user32.PostMessageW(reconcile_details_hwnd, 0x0100, 0x1B, 0)  # WM_KEYDOWN VK_ESCAPE
```

**Open via WM_COMMAND (from Checking register):**
```python
# WM_COMMAND 7203 → Reconcile dialog (blocks while open — use threading)
user32.SendMessageW(qframe, WM_COMMAND, 7203, 0)
```

---

## 20. Phase 7 — Error Recovery & Robustness

### 20.1 Scenario Results

| Scenario | Method | Result |
|----------|--------|--------|
| Baseline read | `GetWindowTextW` on Static HWNDs | ✅ PASS |
| Investment nav | BM_CLICK on sidebar QWListViewer | ✅ PASS |
| Unsaved edit → nav away | BM_CLICK on different account | ✅ PASS — Quicken discards silently |
| Crash recovery | Dismiss crash dialog + relaunch qw.exe | ✅ PASS |
| Split dialog → cancel | WM_COMMAND 103 + UIA `uia_invoke` Close | ✅ PASS |
| Report open → close | WM_COMMAND 7403 + WM_CLOSE on QWinPopup | ✅ PASS |
| Reconcile open → cancel | WM_COMMAND 7203 + PostMessage VK_ESCAPE | ✅ PASS |
| Bad input in amount | WM_CHAR non-numeric → silently rejected | ✅ PASS (no dialog) |
| Delete transaction dialog | **BLOCKED** — requires RDP (sticky edit mode) | ⚠️ PARTIAL |

### 20.2 Key Discoveries

#### Sticky Edit Mode
Once Quicken enters transaction edit mode, **only hardware input can exit it**.  
`PostMessageW` / `SendMessageW` with VK_ESCAPE to txlist, QFRAME, or edit fields all fail.  
The only escape without RDP:
- Navigate AWAY via BM_CLICK (but only if Quicken allows navigation while editing — it discards the edit silently)
- Relaunch Quicken (nuclear option)

#### Silent Input Rejection  
Quicken's numeric edit fields (amount, price, shares) silently discard non-numeric keystrokes.  
No error dialog fires. The field simply stays empty.  
Attempting to Save with an empty amount also fails silently (no dialog, count unchanged).

#### ESC Works on Standard Dialogs (`#32770`)
`PostMessageW(hwnd, WM_KEYDOWN, VK_ESCAPE, 0)` dismisses `#32770` dialogs (Reconcile Details, etc.)  
Does NOT work on custom Quicken list/editor controls.

#### BM_CLICK Navigates Sidebar Without RDP
`SendMessageW(qwlistviewer_hwnd, BM_CLICK=0xF5, 0, 0)` triggers account navigation  
Works even when RDP is disconnected (no hardware input required).  
Does NOT work if Quicken is in sticky edit mode.

#### WM_COMMAND Navigation
Report and dialog commands:
| Command | Action |
|---------|--------|
| 103 | Open Split Transaction dialog (modal) |
| 7203 | Open Reconcile Account dialog (modal) |
| 7403 | Open Banking Summary report (QWinPopup) |
| 7405 | Edit menu → Delete (may vary by session) |

#### Post-Crash State
After crash and relaunch:
- New QFRAME HWND (differs from pre-crash)
- Sidebar collapses — needs expand via BM_CLICK on expand button
- WM_COMMAND navigation IDs may shift (account ordering changes)
- Leftover modal dialogs may be open (`EnumWindows` to detect them)

### 20.3 Recovery Patterns

#### Pattern A: Standard Dialog Recovery
```python
def dismiss_dialog(hwnd):
    """Dismiss any #32770 or QWinDlg using ESC."""
    import ctypes
    u = ctypes.windll.user32
    u.PostMessageW(hwnd, 0x0100, 0x1B, 0)  # WM_KEYDOWN VK_ESCAPE
    time.sleep(0.3)
    return not u.IsWindowVisible(hwnd)
```

#### Pattern B: WM_CLOSE for Report Popups  
```python
def close_popup(hwnd):
    """Close QWinPopup (report windows, etc.) via WM_CLOSE."""
    import ctypes
    u = ctypes.windll.user32
    u.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
    time.sleep(0.3)
    return not u.IsWindowVisible(hwnd)
```

#### Pattern C: Global Recovery to Stable State
```python
def recover_to_stable(qframe):
    """Dismiss all visible dialogs and return to register view."""
    import ctypes, ctypes.wintypes as wt
    u = ctypes.windll.user32
    # 1. Close all visible QWinDlg / #32770 dialogs
    closed = []
    def closer(h, lp):
        cls = ctypes.create_unicode_buffer(64); u.GetClassNameW(h, cls, 64)
        if u.IsWindowVisible(h) and cls.value in ['#32770', 'QWinDlg', 'QWinPopup']:
            u.PostMessageW(h, 0x0010, 0, 0)  # WM_CLOSE
            closed.append(h)
        return True
    u.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)(closer), 0)
    time.sleep(0.5 * len(closed))
    # 2. Verify QFRAME title (confirm back in main window)
    buf = ctypes.create_unicode_buffer(256)
    u.GetWindowTextW(qframe, buf, 256)
    return 'Quicken Classic Premier' in buf.value
```

#### Pattern D: Crash Detection and Recovery
```python
def detect_crash():
    """Return crash dialog HWND if Quicken crashed, else None."""
    import ctypes, ctypes.wintypes as wt
    u = ctypes.windll.user32
    found = [None]
    def find_crash(h, lp):
        txt = ctypes.create_unicode_buffer(256); u.GetWindowTextW(h, txt, 256)
        if 'Crash Report' in txt.value or 'stopped working' in txt.value.lower():
            found[0] = h
        return True
    u.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)(find_crash), 0)
    return found[0]

def recover_from_crash(crash_hwnd, qw_exe=r'C:\Program Files (x86)\Quicken\qw.exe'):
    """Dismiss crash dialog and relaunch Quicken."""
    import ctypes, subprocess, time
    u = ctypes.windll.user32
    u.PostMessageW(crash_hwnd, 0x0010, 0, 0)
    time.sleep(2)
    subprocess.Popen([qw_exe])
    time.sleep(25)  # wait for full load
    # Re-find QFRAME and expand sidebar
```

### 20.4 Robustness Checklist
Before each automation run:
1. `EnumWindows` — check for any modal dialogs (close them)
2. Verify QFRAME is visible and not minimized
3. Expand sidebar if needed (HWND of expand button changes per session)
4. Confirm correct account register is active (check QFRAME title)
5. Read transaction count Static to confirm baseline

---

## 21. Phase 8 — UIA-X-First Quicken Skill

### 21.1 Architecture

```
┌──────────────────────────────────────────┐
│          UIA-X-First Skill Layer         │
│                                          │
│  Navigation   →  BM_CLICK + WM_COMMAND  │
│  Reading      →  uia_read_display /      │
│                  uia_find_all(actions=0) │
│  Typing       →  type_text (SendInput)   │
│  Clicking     →  mouse_click (SendInput) │
│  Inspection   →  uia_find_all / inspect  │
│  Dialogs      →  uia_invoke / ESC post   │
│                                          │
│  Fallback (RDP disconnected):           │
│    All mouse/kbd → Win32 SendMessage    │
└──────────────────────────────────────────┘
```

### 21.2 Tool Matrix (UIA-X vs Win32)

| Task | UIA-X Tool | Win32 Fallback | Notes |
|------|-----------|----------------|-------|
| Find QFRAME | `select_window(hwnd=N)` | `FindWindowW('QFRAME',NULL)` | Must use hwnd= not process_name |
| Read balance/count | `uia_read_display()` | `GetWindowTextW` on Static HWNDs | UIA-X reads labels, Win32 needs HWND |
| Click sidebar account | — | `SendMessageW(hwnd, BM_CLICK, 0, 0)` | UIA-X `uia_invoke` does NOT work here |
| Navigate register | `mouse_click(x,y)` ¹ | `SendMessageW(qwlistviewer, BM_CLICK)` | ¹ Requires active RDP |
| Type in field | `type_text(text)` ¹ | `PostMessageW(WM_CHAR, ch, 0)` per char | ¹ Requires active RDP |
| Click standard button | `uia_invoke(name='OK')` | `SendMessageW(hwnd, BM_CLICK)` | Standard buttons work via UIA |
| Click QC_button | `uia_legacy_invoke(target)` | `SendMessageW(hwnd, BM_CLICK)` | Pane role, legacy works |
| Close standard dialog | `uia_invoke(name='Cancel')` | `PostMessageW(WM_KEYDOWN, VK_ESCAPE)` | Both work |
| Close QWinPopup | — | `PostMessageW(hwnd, WM_CLOSE)` | UIA-X has no close primitive for popups |
| Send WM_COMMAND | — | `SendMessageW(qframe, WM_COMMAND, id)` | No UIA-X equivalent |
| Read edit field | `uia_find_all(has_actions=False)` | `GetWindowTextW(edit_hwnd)` | UIA-X works for standard Edit controls |
| Detect modal dialogs | `uia_find_all(roles=['dialog'])` | `EnumWindows + GetClassName` | Win32 more reliable for custom dialogs |
| Get window DPI | — | `GetDpiForWindow` | Issue #11 filed |
| Wait for element | — (issue #14) | Poll + sleep loop | Issue #14 filed |

### 21.3 DPI Scaling (Always Apply)

```
Physical pixels = Logical pixels × 1.75
```

- `uia_inspect` and `uia_find_all` return **physical pixel** coordinates
- `mouse_click(x, y)` takes **physical pixel** coordinates  
- `GetWindowRect` in Python returns **logical** coordinates (divide by 1.75 for UIA comparison, multiply for mouse_click)
- QFRAME physical rect: (0, 0, 2800, 1470) — logical: (0, 0, 1600, 840)

### 21.4 `select_window` Best Practice

Always use `hwnd=` to select Quicken's QFRAME:

```python
# WRONG — picks first window by process name (may be invisible QWFly)
uia_select_window(process_name='qw.exe')

# CORRECT — use HWND discovered via FindWindowW or EnumWindows
qframe_hwnd = find_qframe()  # ctypes FindWindowW('QFRAME', None)
uia_select_window(hwnd=qframe_hwnd)
```

### 21.5 Navigation Patterns (UIA-X-First)

#### Sidebar navigation (active RDP)
```
1. uia_select_window(hwnd=qframe_hwnd)
2. mouse_click(x=physical_x, y=physical_y)   # sidebar account item
3. wait ~1.5s
4. uia_read_display()  # verify title shows account name
```

#### Sidebar navigation (no RDP / fallback)
```python
# Expand sidebar first if needed
u.SendMessageW(expand_btn_hwnd, BM_CLICK, 0, 0)
time.sleep(0.5)
# Navigate to account
u.SendMessageW(account_qwlistviewer_hwnd, BM_CLICK, 0, 0)
time.sleep(1.5)
```

#### Transaction entry (active RDP required)
```
1. mouse_click on new transaction row in txlist
2. type_text('3/28/2026')   # date
3. uia_send_keys('{TAB}')
4. type_text('Test Payee')  # payee
5. uia_send_keys('{TAB}')
6. type_text('100.00')       # amount
7. uia_send_keys('{TAB}')
8. type_text('Food')         # category
9. uia_invoke(name='Save')  OR uia_send_keys('{ENTER}')
```

### 21.6 Remaining UIA-X Gaps (GitHub Issues Filed)

| Issue | Title | Status |
|-------|-------|--------|
| #9 | `select_window` picks wrong window when multiple windows share process_name | Filed |
| #10 | `uia_mouse_click` silent failure on DPI mismatch | Filed |
| #11 | DPI scale factor not exposed by `select_window` / `process_list` | Filed |
| #12 | `uia_invoke` returns ok:true but menu popup doesn't open | Filed |
| #13 | `uia_find_all(roles=['button'])` misses custom-class buttons (ControlType=Pane) | Filed |
| #14 | Missing `wait_for_element` / `wait_for_condition` primitive | Filed |
| #15 | `uia_mouse_click` vs `mouse_click` (SendInput) behave differently on custom controls | Filed |
| #16 | Need `send_message`/`post_message` primitive for Win32 interaction | Filed |

### 21.7 Recommended Automation Stack Priority

1. **UIA-X `mouse_click` (SendInput)** — best general click method when RDP is active  
2. **UIA-X `uia_legacy_invoke`** — for QC_button (pane role) controls without RDP dependency  
3. **Win32 `SendMessageW(BM_CLICK)`** — for QWListViewer sidebar items and headless operation  
4. **Win32 `PostMessageW(WM_COMMAND, id)`** — for menu-equivalent actions (reports, reconcile, etc.)  
5. **Win32 `PostMessageW(WM_KEYDOWN, VK_ESCAPE)`** — dismiss standard `#32770` dialogs  
6. **Win32 `PostMessageW(WM_CLOSE)`** — close `QWinPopup` report windows  
7. **UIA-X `type_text`** — typing text when RDP is active  
8. **Win32 `PostMessageW(WM_CHAR, ch)`** — typing text without RDP (limited reliability)

### 21.8 Known Limitations (No Workaround)

- **Register rows (QWClass_TransactionList)** — completely opaque to UIA; no text, no actions, no tree
- **Investment dialog fields** — custom QWinDlg with unnamed edit combos; must use ctrl_id mapping
- **Edit mode is "sticky"** — once in register edit mode, only hardware input (RDP) can exit it
- **Menus** — Quicken uses custom MSAA menus, not standard Win32 menu; `uia_invoke` doesn't open them visually
- **DPI mismatch** — `uia_mouse_click` (UIA dispatch) silently misses if logical vs physical coords are confused

---

## 22. QWinLightbox Modal Blocker — Recovery Pattern

### 22.1 What Is It?

After certain Quicken wizard flows (activation wizard, mobile sync wizard), a top-level `QWinLightbox`
window persists and calls `EnableWindow(QFRAME, 0)`. This disables all input on QFRAME, causing a
system beep when the user or automation tries to click it.

**Symptom**: Hardware `mouse_click` on QFRAME beeps and does nothing. `IsWindowEnabled(QFRAME)` returns 0.

### 22.2 Detection

```python
import ctypes, ctypes.wintypes as wt
u = ctypes.windll.user32
qframe = ctypes.windll.user32.FindWindowW('QFRAME', None)

# Check QFRAME disabled
if not u.IsWindowEnabled(qframe):
    # Enumerate for QWinLightbox
    lightboxes = []
    def cb(h, _):
        buf = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(h, buf, 64)
        if buf.value == 'QWinLightbox' and u.IsWindowVisible(h):
            lightboxes.append(h)
        return True
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    u.EnumWindows(WNDENUMPROC(cb), 0)
    print(f'Lightboxes: {lightboxes}')
```

### 22.3 Recovery

```python
import ctypes, time
u = ctypes.windll.user32
WM_CLOSE = 0x0010

# Step 1: Dismiss each lightbox
for hwnd in lightboxes:
    u.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    time.sleep(0.3)

# Step 2: Re-enable QFRAME if still disabled
if not u.IsWindowEnabled(qframe):
    u.EnableWindow(qframe, 1)
    time.sleep(0.2)

print(f'QFRAME enabled: {u.IsWindowEnabled(qframe)}')  # should print 1
```

### 22.4 Notes

- First occurrence after sync wizard requires manual `EnableWindow` — auto-recovery not triggered
- Subsequent lightboxes (e.g. after Save + date confirmation) auto-dismiss and QFRAME re-enables itself
- `Ctrl+D` sent while register does not have proper focus resizes QFRAME (MDI restore behavior) — avoid
- UIA-X gap filed as issue #17 (no primitive to detect disabled-window state or dismiss modal overlays)

---

## 23. Delete Transaction Workflow (Phase 7 Verified)

### 23.1 Right-Click Context Menu Method (Reliable)

```
1. Right-click on the transaction row in TxList
   - Physical coords: (center_x, row_y) using DPI × 1.75
   - Context menu appears at logical ~(907, 257, 1267, 864)
2. Screenshot context menu to verify "Delete" position
3. Click "Delete" (≈ physical y=906 for menu at that position)
4. Confirmation dialog #32770 appears:
   "Deleting a reconciled (R) transaction, Continue?"
5. Click No → transaction preserved (recovery test passes)
   OR Click Yes → transaction deleted
```

### 23.2 Shortcut Key

- **Ctrl+D** = Delete (confirmed from context menu)
- **WARNING**: Ctrl+D sent to wrong focus target resizes QFRAME; only use when TxList row is properly selected

### 23.3 Confirmation Dialog Buttons

| Button | Text | Physical coords (approx) |
|--------|------|--------------------------|
| Yes | `&Yes` @ logical (754,550,814,574) | physical (1372, 983) |
| No | `&No` @ logical (830,550,890,574) | physical (1505, 983) |

### 23.4 Context Menu Item Positions (logical y from menu top y=257)

The register context menu spans logical y=257 to y=864 (607px). Key items:
- **Save** — top (~y=272)
- **Split...** — ~y=322 (Ctrl+S)
- **Delete** — ~y=522 (Ctrl+D)
- **New** — ~y=502 (Ctrl+N)
- **Void transaction(s)** — ~y=680

---

| 123 | Ending statement date | 3/28/2026 (today) |
| 100 | Prior balance | 1,234.00 (opening balance) |
| 101 | **Ending balance** | *(empty — user must enter)* |
| 102 | Service charge | *(empty)* |
| 103 | Service charge date | 3/28/2026 |
| 104 | Service charge category | *(empty)* |
| 105 | Interest earned | *(empty)* |
| 106 | Interest earned date | 3/28/2026 |
| 107 | Interest earned category | "Interest Inc" |
| 32767 | OK (→ opens register with cleared transactions) | |
| 32766 | Cancel | |

**Prior balance** is pre-filled with the last reconcile ending balance (or opening balance if never reconciled).  
**Ending balance** is the only required field with no pre-fill — user enters from bank statement.

### Cancel reconciliation safely
```python
# Click Cancel on "Reconcile Details" dialog — no data is changed
cancel_btn = <cid 32766>
user32.PostMessageW(cancel_btn, WM_LBUTTONDOWN, 1, lp_center)
user32.PostMessageW(cancel_btn, WM_LBUTTONUP, 0, lp_center)
time.sleep(0.5)
# OR: user32.SendMessageW(dlg, WM_COMMAND, 32766, 0)
```

### ⚠️ Do NOT click OK on Reconcile Details
Clicking OK opens the full reconciliation register which marks transactions as cleared. Always cancel unless intentionally reconciling.

---

---

## 19. UIA‑X Integration (Active RDP Session Required)

> **Prerequisite**: UIA-X (SendInput-based) only works when the Windows desktop session is **active** (RDP connected or console session). When the session is disconnected, all `uia_mouse_click`, `type_text`, and `uia_send_keys` calls fail silently or produce ACCESS_DENIED. Fall back to Win32 PostMessage/SendMessage in that case.

### DPI Coordinate Mapping
Quicken runs at **175% DPI scaling**. UIA-X uses **physical pixels**; Python `GetWindowRect` returns **logical pixels**.

```python
DPI_SCALE = 1.75
# physical = logical * DPI_SCALE
# logical  = physical / DPI_SCALE

# QFRAME logical:  0,0 → 1600,840
# QFRAME physical: 0,0 → 2800,1470  (UIA-X rects)
```

Always multiply coords from `GetWindowRect` by 1.75 before passing to `uia_mouse_click`.  
Rects returned by `uia_inspect` are already in physical pixels — use them directly.

### UIA‑X Capability Matrix

| Capability | Method | Works? | Notes |
|-----------|--------|--------|-------|
| List processes/windows | `process_list` | ✅ | Always works |
| Select window by HWND | `select_window(hwnd=N)` | ✅ | Use hwnd=2097686 for QFRAME |
| Find elements | `uia_find_all` | ✅ | Finds menus, combos, filters; NOT register rows (custom-drawn) |
| Inspect element tree | `uia_inspect(name=X)` | ✅ | Full tree with physical rects + HWNDs |
| Read display values | `uia_read_display` | ✅ | Reads balance, count, filter values |
| Read single field | `uia_get_text(target)` | ✅ | Works by hwnd, name, automation_id |
| Mouse click (menus/sidebar) | `uia_mouse_click(x,y)` | ✅ **physical coords** | Multiply logical coords × 1.75 |
| Type text in field | `type_text(text)` | ✅ | Sends real keystrokes; Quicken sees them natively |
| Keyboard shortcuts | `uia_send_keys(keys)` | ✅ | `{TAB}`, `{ESC}`, `^a`, etc. |
| Invoke menu items | `uia_invoke(name=X)` | ✅ | Fires do_default_action on menu items |
| Navigate register fields | `{TAB}` via `uia_send_keys` | ✅ | Moves field-by-field through Date→Check#→Payee→Memo→Category→Payment→Amount |
| Navigate sidebar accounts | `uia_mouse_click` at sidebar coords | ✅ | Sidebar QWListViewer responds to SendInput |
| Read QC_button names | `uia_inspect` | ✅ | Visible as "Pane" role with names (Save, More actions, Split) |
| Invoke QC_button (Save, etc.) | `uia_mouse_click` at physical rect | ✅ | Use physical rect center from `uia_inspect` |
| Read register transaction rows | `uia_find_all` | ❌ | Custom-drawn; not exposed via UIA |
| Access Investment dialog fields | `type_text` | ⚠️ **untested** | May work now SendInput is active |

### Sidebar physical coordinates (re-enumerate each session — HWNDs change)
```python
import ctypes, ctypes.wintypes as wt
user32 = ctypes.windll.user32
DPI = 1.75

# Find QWListViewer children of QFRAME that match sidebar y-range (y ≈ 150-280)
def get_sidebar_coords():
    qframe = user32.FindWindowW('QFRAME', None)
    rows = []
    def ecb(h, lp):
        cls = ctypes.create_unicode_buffer(64); user32.GetClassNameW(h, cls, 64)
        if cls.value == 'QWListViewer':
            r = wt.RECT(); user32.GetWindowRect(h, ctypes.byref(r))
            h_height = r.bottom - r.top
            cx = (r.left + r.right) // 2
            cy = (r.top + r.bottom) // 2
            # Sidebar rows: x ≈ 6-242, height > 0, y in 150-280 range
            if 6 <= r.left <= 10 and h_height > 0 and 140 < cy < 290:
                rows.append({'hwnd': h, 'logical_center': (cx, cy),
                              'phys_center': (int(cx*DPI), int(cy*DPI))})
        return True
    user32.EnumChildWindows(qframe, ctypes.WINFUNCTYPE(ctypes.c_bool,
        wt.HWND, wt.LPARAM)(ecb), 0)
    return rows
# Returns rows sorted by y — first=Checking, second=CreditCard, third=Brokerage (approx)
```

### Navigate to an account via UIA-X
```python
# Click the sidebar row at physical coords
# Example session values (re-enumerate each run):
# Checking:    uia_mouse_click(217, 323)
# Credit Card: uia_mouse_click(217, 360)
# Brokerage:   uia_mouse_click(217, 448)

# Or double-click for reliability:
# uia_mouse_click(x, y, double=True)
```

### Enter a transaction via UIA-X (NEW — simpler than Win32)
```python
# Requires active RDP session. Uses real SendInput keystrokes.
# 1. Ensure new-transaction row is selected (cursor at bottom of register)
# 2. Type date
uia.type_text("3/15/2026")
uia.uia_send_keys("{TAB}")      # → Check#
uia.uia_send_keys("{TAB}")      # → Payee
uia.type_text("Grocery Store")
uia.uia_send_keys("{TAB}")      # → Memo (skip)
uia.uia_send_keys("{TAB}")      # → Category
uia.type_text("Food & Dining")
uia.uia_send_keys("{TAB}")      # → Payment
uia.type_text("42.50")
uia.uia_send_keys("{ENTER}")    # → Save transaction
# OR: uia.uia_send_keys("{ESC}") to discard

# Verify saved:
# uia_read_display() → check "2 Transactions" count
```

### Open a report via UIA-X
```python
# 1. Click Reports menu (physical coords)
uia.uia_mouse_click(490, 72)   # Reports button in toolbar (physical)
# 2. Navigate submenu with arrow keys or invoke directly via WM_COMMAND
import ctypes; ctypes.windll.user32.PostMessageW(qframe, 0x0111, report_id, 0)
# (WM_COMMAND is still more reliable for report IDs)
```

### Detect state via UIA-X (use for Phase 7 recovery)
```python
# Read balance — always current:
result = uia.uia_get_text(target={'by':'hwnd','value':263972})
# → {"text": "1,234.00", "source": "value"}

# Read transaction count:
result = uia.uia_get_text(target={'by':'hwnd','value':198482})
# → {"text": "1 Transaction", "source": "name"}

# Check if a dialog is open (find #32770):
elems = uia.uia_find_all(roles=['dialog'])
# If any dialog named 'Delete', 'Reconcile', etc. → handle it

# Check register filter state:
result = uia.uia_read_display()
# Finds filter combo values: "All Dates", "Any Type", "All Transactions"
```

### Strategy: When to use UIA-X vs Win32

| Task | Preferred method | Fallback |
|------|-----------------|----------|
| Navigate to account | UIA-X `uia_mouse_click` (sidebar, physical coords) | Win32 WM_COMMAND 7301 |
| Type text in register | UIA-X `type_text` | Win32 WM_SETTEXT to QREdit |
| Tab between fields | UIA-X `uia_send_keys {TAB}` | Win32 PostMessage VK_TAB to TxList |
| Save transaction | UIA-X `uia_send_keys {ENTER}` | Win32 PostMessage VK_RETURN to QREdit |
| Delete transaction | Win32 PostMessage WM_COMMAND 7106 | — |
| Confirm dialogs | Win32 SendMessage IDYES/IDNO | — |
| Open reports | Win32 PostMessage WM_COMMAND ID | — |
| Read field value | UIA-X `uia_get_text` | Win32 GetWindowText |
| Check balance/count | UIA-X `uia_read_display` | Win32 GetWindowText on Static |
| Detect open dialogs | UIA-X `uia_find_all(roles=['dialog'])` | Win32 EnumWindows #32770 |
| Investment dialog fields | Win32 WM_SETTEXT (reliable) | UIA-X type_text (untested) |

---

*Last updated: UIA-X capability matrix confirmed — full transaction entry, navigation, and state reading via UIA-X with active RDP*

---

## 24. UIA-X MCP Server — Confirmed Live Results (branch: dev/agent-fixes)

> All timings measured against live Quicken Classic Premier on this machine.
> Server run via: `python -m uiax.server` with `UIAX_BACKEND=real`.

### 24.1 Tool Performance (after Win32 fast-path fix)

| Tool | Time | Result |
|------|------|--------|
| `process_list` | 3.9s | 6 visible windows including QFRAME |
| `select_window(process_name="qw.exe")` | 3.2s | ok=True, hwnd=0x401f2 |
| `check_window_state(hwnd)` | 10.5s | ok=True, enabled=True |
| `uia_find_all(roles=["button"])` | **3.2s** | 12 buttons (Win32 path) |
| `uia_find_all(has_actions=True)` | **3.2s** | 12 buttons |
| `uia_invoke(name="File")` | 25.3s | ok=True (UIA fallback path) |

> ⚡ `uia_find_all` was **205 seconds** before the Win32 fast-path fix (64× speedup).

### 24.2 Confirmed Navigation Buttons (Win32 fast-path, QC_button class)

```
HOME                    SPENDING              BILLS & INCOME
PLANNING                MOBILE & WEB          Dashboard
Update now              ACCOUNTS              All Transactions
Banking                 Net Worth             Credit Score
```

All 12 returned as `role=button`, `actions=['click']`, found in **3.2s**.

### 24.3 `select_window` — Updated Guidance

`process_name="qw.exe"` now works reliably (process_manager ranks QFRAME by visible area,
filtering out the 1×1 `QWFly` helper window):

```python
# WORKS — process_manager picks the largest window (QFRAME, not QWFly)
sw = mcp.call("select_window", {"process_name": "qw.exe"})
# Returns: {"ok": True, "window": {"hwnd": 262642, "class_name": "QFRAME",
#           "title": "Quicken Classic Premier - ...", "dpi_scale": 1.0}}
```

### 24.4 Win32 Class → Role Mapping (Quicken-specific)

| Win32 Class | UIA-X role |
|-------------|-----------|
| `QC_button` | `button` |
| `QWComboBox` | `combobox` |
| `QWPanel` | `pane` |
| `QWNavigator` | `pane` |
| `QWListViewer` | `list` |
| `QFRAME` | `window` |
| `QW_MAIN_TOOLBAR` | `toolbar` |
| `QW_BAG_TOOLBAR` | `toolbar` |

### 24.5 Invoke Patterns

| Target | Method | Time | Notes |
|--------|--------|------|-------|
| `uia_invoke({"name": "File"})` | UIA name scan | ~25s | Slow — traverses MSAA bridge |
| `uia_invoke({"hwnd": "0x..."})` | Win32 BM_CLICK | <1s | Fast — direct Win32 message |
| `uia_invoke({"name": "HOME"})` | Win32 fast-path if cached | ~3s | Uses Win32 button lookup |

**Best practice**: prefer `{"hwnd": "0x..."}` targets where HWND is known.

### 24.6 Known Performance Bottlenecks (remaining)

| Operation | Cost | Cause |
|-----------|------|-------|
| `uia_invoke(name="X")` first call | ~25s | UIA `descendants(title=X)` via MSAA bridge |
| `check_window_state` | ~10s | executor + COM thread startup overhead |
| UIA-based element lookup | 10-15s/type | Quicken's MSAA UIA compatibility bridge is O(n) |

**Mitigation**: cache HWNDs from first `uia_find_all` and use `{"hwnd": "0x..."}` for all subsequent invokes.

### 24.7 GitHub Issues — Resolution Status (dev/agent-fixes)

| Issue | Title | Status |
|-------|-------|--------|
| #9 | `select_window` picks wrong window when multiple share process_name | ✅ FIXED — area ranking |
| #10 | `uia_mouse_click` silent failure on DPI mismatch | ✅ FIXED — DPI scale returned |
| #11 | DPI scale factor not exposed | ✅ FIXED — `dpi_scale` in `select_window` response |
| #12 | `uia_invoke` returns ok:true but menu popup doesn't open | ✅ FIXED — 7-strategy invoke chain |
| #13 | `uia_find_all` misses custom-class buttons | ✅ FIXED — Win32 class→role mapping |
| #14 | Missing `wait_for_element` primitive | ✅ FIXED — `uia_wait_for_element` added |
| #15 | Stale element handle not detected | ✅ FIXED — `stale_handle` error code |
| #16 | Need `send_message`/`post_message` primitive | ✅ FIXED — `uia_send_message` added |
| Pagination | `uia_find_all` pagination | ✅ FIXED — `offset`/`limit` params |

### 24.8 Startup Recipe (MCP server against live Quicken)

```bash
# 1. Start server
cd Z:\uiax_checkout\uia-x
set UIAX_BACKEND=real
set UIAX_AUTH=none
set MCP_TRANSPORT=streamable-http
set MCP_PORT=8765
python -m uiax.server

# 2. Connect and select Quicken
select_window(process_name="qw.exe")

# 3. Find buttons fast (Win32 path, ~3s)
uia_find_all(roles=["button"], named_only=True, limit=20)

# 4. Invoke by HWND (fast, <1s)
uia_invoke({"hwnd": "0x401f2"})     # example

# 5. Invoke by name (UIA path, ~25s first time)
uia_invoke({"name": "File"})
```

---

## 25. Full UI Tree (depth=3, probes 5-7 confirmed)

### 25.1 Complete Win32 Window Hierarchy

```
QFRAME "Quicken Classic Premier - ... - [Current View]"
├── QW_BAG_TOOLBAR
│   └── QW_MAIN_TOOLBAR
│       ├── [Static] (various toolbar icons)
│       └── QC_button "More"
├── ToolbarWindow32  (navigation/search toolbar)
│   ├── QC_button  (back/forward nav)
│   ├── [Static]
│   ├── QWPanel "Panel"  (search bar container)
│   │   ├── QWIconDisplay, QC_button, Edit (search input), QWIconDisplay
│   ├── QC_button × 10  (toolbar actions, no text)
│   └── QWComboBox (account selector)
├── ToolbarWindow32  (menu bar — NOTE: menu items are MSAA-only, not Win32 child HWNDs)
├── MDIClient  (main content workspace)
│   ├── QWMDI "Home"       (always present)
│   ├── QWMDI "Spending"   (added when SPENDING clicked)
│   ├── QWMDI "Mobile & Web"
│   └── ...more QWMDI panes per tab
├── HwndWrapper "RenderAppBanner"  (Electron/WebView2 component)
├── HwndWrapper "MCPAppBanner"     (Electron/WebView2 component)
└── QWNavigator  (left sidebar nav)
    ├── QC_button "HOME"
    ├── QC_button "SPENDING"
    ├── QC_button "BILLS & INCOME"
    ├── QC_button "PLANNING"
    ├── QC_button "INVESTING"
    ├── QC_button "PROPERTY & DEBT"
    ├── QC_button "MOBILE & WEB"
    ├── QC_button "REPORTS & GRAPHS"
    ├── [Static] (separator)
    ├── QC_button "Dashboard"
    ├── QC_button × N  (unnamed sidebar items)
    ├── QC_button "Update now"
    ├── QC_button "ACCOUNTS"
    ├── QC_button × N  (account list items — use uia_inspect depth to get names)
    ├── QWNavBtnTray
    │   ├── QWAcctBarHolder  (account bar)
    │   ├── QC_button "Net Worth"
    │   └── QC_button "Credit Score"
    ├── QSideBar "QSideBar"
    │   ├── QC_button, [Static], pane, pane
    │   └── HwndWrapper "LaunchPad"  (Electron/WebView2)
    └── [Static]
```

### 25.2 Navigation — Tab → QWMDI Mapping

Each navigation click creates a **new QWMDI child** in MDIClient (or switches to existing):

| Nav Button | QWMDI Title | Notes |
|-----------|-------------|-------|
| HOME | "Home" | Always present |
| SPENDING | "Spending" | Created on first click |
| BILLS & INCOME | "Bills & Income" | ~15s first load |
| PLANNING | "Planning" | — |
| INVESTING | "Investing" | — |
| MOBILE & WEB | "Mobile & Web" | Always present (WebView) |

**Detection pattern**: After invoking a nav button, check that `uia_inspect(depth=1)`
shows the expected QWMDI title in `MDIClient` children.

### 25.3 Updated Tool Performance (probes 5-7)

| Tool | Time | Notes |
|------|------|-------|
| `uia_find_all(roles=["button"], named_only=True)` | **4.9–6.6s** | Win32 path |
| `uia_inspect(depth=2)` | **2.0–3.6s** | Win32 `_win32_inspect_tree` |
| `uia_inspect(depth=3)` | **2.0s** | Win32 path (no COM at all) |
| `uia_invoke(hwnd=QC_button_hwnd)` | **3.1–8.7s** | UIA/MSAA via `_win32_element_from_hwnd` |
| `uia_invoke(hwnd=native_button_hwnd)` | **<1s** | BM_CLICK direct |

> **Note**: `uia_invoke(hwnd=QC_button)` uses `_win32_element_from_hwnd` (O(1) UIA wrap)
> then tries UIA InvokePattern → MSAA DoDefaultAction. ~3-9s (COM/MSAA path).
> BM_CLICK is only used for native Win32 `button` class controls.

### 25.4 Menu Bar Access

The Quicken menu bar is a **standard Win32 menu** — menu items are NOT separate child
HWNDs. They are MSAA-accessible but only as virtual elements. To access menu items:

```python
# Method 1: UIA name-based invoke (MSAA bridge, ~25s)
uia_invoke(name="File")  # opens File menu

# Method 2: keyboard shortcut (most reliable, <1s)
uia_send_keys(keys="%f")  # Alt+F opens File menu
```

### 25.5 Fixed Bugs (probes 5-7 session)

| Fix | Commit | Impact |
|-----|--------|--------|
| `uia_invoke(hwnd=...)` top-level param | e830b09 | Direct HWND invoke, no name scan |
| `uia_inspect` Win32 fast path | e830b09 | 158s → 3.6s (44× speedup) |
| `uia_inspect` depth as top-level param | e830b09 | `uia_inspect(depth=2)` works directly |
| QC_button invoke uses UIA/MSAA not BM_CLICK | ebdf3cf | Navigation confirmed working |

### 25.6 Best Practice Recipe (updated)

```python
# FAST Quicken automation workflow:

# 1. Connect (one-time)
select_window(process_name="qw.exe")   # ~4s

# 2. Discover all named buttons with HWNDs (one-time scan)
fa = uia_find_all(has_actions=True, named_only=True, include_hwnd=True, limit=50)
# Returns 12+ nav buttons in ~5s. Cache the hwnd values.

# 3. Inspect tree (fast, no COM)
uia_inspect(depth=3)   # ~2s — full structural layout

# 4. Navigate by HWND (no rescan)
uia_invoke(hwnd="0x400a4")   # SPENDING — ~8s (MSAA), then check QWMDI title

# 5. Find account register controls
uia_inspect(target={"hwnd": "0x3018e"}, depth=3)  # MDIClient subtree
```

---

## 26. Transaction Register (All Transactions / Account Register)

### 26.1 Accessing the Register

From the main Quicken window, click the **ACCOUNTS** nav button (QWNavigator area),
then click **All Transactions** to open the full register view.

```python
# Navigate to All Transactions
fa = uia_find_all(has_actions=True, named_only=True, include_hwnd=True, limit=100)
btn_hwnds = {e['name']: e['hwnd'] for e in fa['elements'] if e.get('role')=='button'}

# Click ACCOUNTS first if not in account view
uia_invoke(hwnd=hex(btn_hwnds['ACCOUNTS']))   # ~4s

# Then click All Transactions
uia_invoke(hwnd=hex(btn_hwnds['All Transactions']))   # ~21s (first load)
```

After navigation, window title → `"... - [All Transactions]"`.

### 26.2 TxList Structure

The transaction register is built from these Win32 classes:

```
QWMDI "All Transactions" (MDI pane)
├── [Static] × 3       — header/balance display
│   ├── "6,912.00"     — balance amount (class=Static hwnd varies)
│   └── "Total:"       — label
├── QWClass_TransactionList "TxList"    — main grid (owner-drawn, no UIA children)
│   ├── QWClass_TxToolbar "TxToolbar"  — entry row toolbar
│   │   ├── QC_button "S&ave"          — save current transaction
│   │   ├── QC_button "More actions"   — split, schedule, etc.
│   │   └── QC_button "Split transaction"
│   ├── QREdit "4/10/2026"  — date field (current entry row)
│   ├── QREdit ""            — payee field
│   ├── QREdit ""            — amount/memo field
│   ├── [Static]
│   └── QFBag                — quick-fill bag (auto-complete popup)
│       ├── QC_button × N    — quick-fill suggestions
├── QWScrollBar (vertical)
└── QWScrollBar (horizontal)
```

> **Note**: Existing transactions in the list are **owner-drawn** — no Win32 child HWNDs
> for individual rows. They are accessible via MSAA (slow) or `uia_read_display`.

### 26.3 Filter Controls

Located above the `TxList` (in the QWMDI pane):

| Name | Class | HWND | Description |
|------|-------|------|-------------|
| All accounts | QWComboBox | 0x111a2 | Account filter |
| Last 12 Months | QWComboBox | 0x111a6 | Date range |
| Any Type | QWComboBox | 0x111aa | Transaction type |
| All Transactions | QWComboBox | 0x111ae | View filter |

Use `uia_invoke(hwnd=...)` to click a combo, then inspect the dropdown.

### 26.4 Reading Transaction Fields

The `QREdit` fields (date, payee, amount) in the **entry row** are readable via `uia_get_text`:

```python
# Date field shows today's date in the new-entry row
date_val = uia_get_text(target={"hwnd": "0x1118c"})
# → {"ok": True, "text": "4/10/2026", "source": "value"}

# Payee and amount are empty until focused
payee_val = uia_get_text(target={"hwnd": "0x1118e"})
# → {"ok": True, "text": "", "source": "none"}
```

> The `source` field is `"value"` for QREdit because pywinauto's `get_value()` uses
> `WM_GETTEXT` which works for custom edit classes.

### 26.5 Entering a New Transaction

```python
# 1. Start new entry (goes to blank new row at bottom of register)
#    Either click the empty row, or use Ctrl+Shift+N
uia_send_keys(keys='^+n')    # Ctrl+Shift+N

# 2. After Ctrl+Shift+N, standard 'edit' class fields appear for the new row
#    Find them:
fa = uia_find_all(roles=['edit'], include_hwnd=True, limit=50)
edit_hwnds = [e['hwnd'] for e in fa['elements']]
# Typically 2 standard 'edit' class HWNDs for payee and memo

# 3. Set values via uia_set_value (uses WM_SETTEXT for custom classes)
uia_set_value(target={"hwnd": hex(edit_hwnds[0])}, value="Grocery Store")

# 4. Tab through fields with uia_send_keys
uia_send_keys(keys='{TAB}')   # advance to next field

# 5. Save the transaction
uia_invoke(hwnd=hex(save_button_hwnd))  # "S&ave" QC_button
# OR
uia_send_keys(keys='{ENTER}')   # also saves

# 6. Cancel without saving
uia_send_keys(keys='{ESC}')
```

> **IMPORTANT**: Always `{ESC}` before navigating away if you don't want to save.

### 26.6 Reading Existing Transactions (owner-drawn list)

The transaction rows are owner-drawn — no individual HWNDs. Options:

```python
# Option A: uia_read_display — reads the visual text of the TxList
disp = uia_read_display(target={"hwnd": "0x11184"})  # QWClass_TransactionList hwnd

# Option B: MSAA via legacy_invoke / uia_legacy_invoke (very slow — 25s+)
# Not recommended unless MSAA data is specifically needed

# Option C: Window title shows account balance (Static hwnd near top of QWMDI)
balance_text = uia_get_text(target={"hwnd": "0x11170"})
# → "6,912.00" (total balance shown at top of register)
```

### 26.7 Transaction Register Class Inventory

All new classes added to `_WIN32_CLASS_TO_ROLE` in this session:

| Class | Role | Notes |
|-------|------|-------|
| QREdit | edit | Register entry field (date, payee, amount) |
| QWClass_TransactionList | list | Main transaction grid (owner-drawn) |
| QWClass_TxToolbar | toolbar | Transaction entry toolbar (Save, More, Split) |
| QWScrollBar | scrollbar | Quicken custom scroll bar |
| QWInChild | pane | Generic Quicken child container |
| QWNavBtnTray | toolbar | Account bar nav tray |
| QWAcctBarHolder | pane | Account bar holder |
| QWNavigator | pane | Left sidebar navigator |
| QSideBar | pane | Sidebar |
| QWMDI | pane | MDI content pane |
| MDIfr | pane | MDI frame |

---

## 27. Account Navigation — list_accounts / navigate_to_account

### 27.1 Background

Individual account sidebar buttons (in `qwacctbarholder`) are **owner-drawn** and
cannot be read via `WM_GETTEXT`. However, the register toolbar's "All accounts"
`QWComboBox` exposes all account names via standard `CB_GETLBTEXT`. This is the
reliable path for both listing and navigating to accounts.

### 27.2 Tool Usage

```python
# List all accounts
accounts = list_accounts()
# → {"ok": True, "count": 5, "accounts": [
#      {"name": "All accounts", "combo_index": 0, "combo_hwnd": "0x107f6"},
#      {"name": "All Checking", "combo_index": 1, "combo_hwnd": "0x107f6"},
#      {"name": "All Savings",  "combo_index": 2, "combo_hwnd": "0x107f6"},
#      {"name": "Checking",     "combo_index": 5, "combo_hwnd": "0x107f6"},
#      {"name": "Savings",      "combo_index": 6, "combo_hwnd": "0x107f6"},
#   ]}

# Navigate to a specific account
navigate_to_account("Checking")
# → {"ok": True, "account": "Checking", "combo_index": 5}
```

### 27.3 Pre-conditions

- Must be attached to QFRAME: `select_window(class_name="QFRAME")`
- Must be in a **register view** (SPENDING, BILLS & INCOME, or All Transactions)
  so the toolbar comboboxes are visible. If `list_accounts` fails with
  `ACCOUNT_COMBO_NOT_FOUND`, navigate to SPENDING first:
  ```python
  uia_invoke(target={"class_name": "qc_button", "name": "SPENDING"})
  ```

### 27.4 Combobox items filtered

The combobox includes non-account items that are filtered out:
- `"QCombo_Separator"` — visual separator
- `"Custom..."` — opens a multi-account selection dialog (must NOT select)
- Empty strings

### 27.5 After navigate_to_account

- The register immediately updates to show only transactions for that account
- The combobox label changes to the account name
- Window title may NOT change (it stays `[Spending]` or `[All Transactions]`)
- Balance Static controls update to reflect the account balance
- Use `uia_find_all(named_only=True)` to find the balance Static near the TxList

---

## 28. Reconcile Register — Inline Layout

### 28.1 Structure

In Quicken, reconciliation happens **inline** in the register pane (not a separate
top-level window). When a reconcile is in progress, the "All Transactions" QWMDI
gains additional controls in its header area.

```
QWMDI "All Transactions"  hwnd varies
├── Static (hidden)        — normal register header (hidden during reconcile)
├── [Reconcile header bar] y≈186-218
│   ├── Edit               hwnd=0x10888  — search/filter box
│   ├── QC_button "C"      hwnd=0x1088a  — toggle cleared status on selected row
│   ├── QC_button (unnamed) × 2         — filter/view buttons
│   ├── QWComboBox × 4     hwnd=0x10892-0x1089e  — filter combos
│   └── QC_button "Reset"  hwnd=0x108a2  — reset all cleared marks
├── QWClass_TransactionList "TxList"  y=218-596  — transaction grid
│   └── (owner-drawn rows — no individual HWNDs for existing transactions)
├── Static "Total:"        y≈617
├── Static "1,234.00"      y≈617  — current cleared total
├── Static "Ending Balance:" (hidden unless reconcile balance dialog is open)
├── QWScrollBar (vertical)
└── QWScrollBar (horizontal)
```

### 28.2 Starting Reconcile (WM_COMMAND)

```python
# From any register view, send WM_COMMAND 7203 to QFRAME to open
# "Reconcile Account" dialog.
# IMPORTANT: SendMessage blocks while the dialog is open — use threading.
import threading
WM_COMMAND = 0x0111
def open_reconcile():
    ctypes.windll.user32.SendMessageW(qframe_hwnd, WM_COMMAND, 7203, 0)
t = threading.Thread(target=open_reconcile, daemon=True)
t.start()
time.sleep(1.5)  # dialog should now be open
```

### 28.3 Reconcile Account Selection Dialog (class=#32770)

Opens a "Choose Account" dialog before showing reconcile details:

| Field | Class | cmd_id | Notes |
|-------|-------|--------|-------|
| Account combo | QWComboBox | 102 | CB_GETLBTEXT lists accounts alphabetically |
| OK | QC_button | 32767 | |
| Cancel | QC_button | 32766 | |

### 28.4 Reconcile Details Dialog (class=#32770)

| cid | Field | Notes |
|-----|-------|-------|
| 482 | Statement date | Pre-filled with today |
| 484 | Previous balance | Pre-filled |
| 486 | Ending balance | **Must fill from statement** |
| 488 | Service charge | Optional |
| 492 | Interest earned | Optional |

After filling and clicking OK, the register enters reconcile mode (see 28.1).

### 28.5 Marking Transactions Cleared

The "C" button (hwnd=0x1088a) at `(471,191)` toggles the cleared status of the
**selected** transaction row. Because rows are owner-drawn, there is no direct
HWND to click per row. Workflow:

```python
# 1. Use the Search/filter Edit (hwnd=0x10888) to locate a transaction
uia_set_value(target={"hwnd": "0x10888"}, value="AMAZON")

# 2. Click in the TxList at the row's y-coordinate (requires screenshot + OCR
#    to determine row positions — blocked by issue #20)

# 3. Once a row is selected, click "C" to mark cleared
uia_invoke(target={"hwnd": "0x1088a", "class_name": "qc_button", "name": "C"})
```

> **Blocker (issue #20)**: Individual transaction rows are owner-drawn — no HWNDs.
> The agent cannot programmatically read payee/date/amount from the register.
> This is the primary blocker for autonomous reconcile. Options:
> - Use `uia_read_display` on the TxList HWND (reads visual text)
> - Use MSAA fallback (very slow)
> - Screen-capture + OCR (out of scope for initial implementation)

### 28.6 Reconcile Workflow (current best path for gpt-oss:20b)

```
1. select_window(class_name="QFRAME")
2. navigate_to_account("Checking")
3. [Manual or threading] Open reconcile via WM_COMMAND 7203
4. Fill statement ending balance in Reconcile Details dialog
5. For each statement transaction:
   a. Use search box (Edit at y≈191) to filter by payee/amount
   b. Visually identify matching row (currently requires OCR / uia_read_display)
   c. Click "C" button to mark cleared
6. Compare "Total:" running sum vs statement ending balance
7. Click "Finish Now" (button appears in reconcile header when difference = 0)
```

### 28.7 Balance Static Controls

| Static hwnd (example) | Content |
|------------------------|---------|
| 0x10860 | Current cleared total (e.g., "1,234.00") |
| 0x10868 | Label "Total:" |
| 0x10866 | "Ending Balance:" (hidden — only visible in balance dialog) |
| 0x1086c | Transaction count (e.g., "1 Transaction") |

Read via `uia_get_text(target={"hwnd": "0x10860"})` — note HWNDs change each launch.

---

## 29. QREdit Write Validation (confirmed working)

The `uia_set_value` tool now returns a **read-back dict** after writing:

```python
result = uia_set_value(
    target={"hwnd": "0x107ec"},  # QREdit date field
    value="12/31/2099"
)
# → {"ok": True, "method": "win32_wm_settext", "written": "12/31/2099",
#    "readback": "12/31/2099", "validated": True}
```

Always check `result["validated"]` before proceeding. If `False`, the write was
accepted by Win32 but Quicken's field validation may have reformatted the value
(e.g., date normalisation).

After writing to a QREdit field, send `{TAB}` to confirm entry:
```python
uia_send_keys(keys="{TAB}")
```

To cancel without saving:
```python
uia_send_keys(keys="{ESC}")
```

