# Quicken Automation Agent Skill Guide

This document captures everything an LLM agent needs to navigate Quicken Classic
Premier and perform data-quality tasks such as transaction deduplication and manual
transaction entry (e.g. ESPP/RSU from PDF brokerage statements).

## Primary Use Cases

1. **Transaction deduplication** — find duplicate transactions (same date/amount/payee),
   delete the extras directly in the UI.
2. **Manual transaction entry** — given external data (PDF statements, CSV), enter
   transactions field-by-field into an account register that has no download capability
   (e.g. ESPP/RSU brokerage accounts).
3. **Reconciliation** — compare register totals against a known balance and resolve
   discrepancies.

All three require **reading and writing transaction fields directly in the register UI**,
not via file export/import.

---

## 1. Available Tools

| Tool | Purpose |
|------|---------|
| `uia_inspect(target, depth)` | Read the UIA element tree. Returns name, control_type, class_name, rect, value, patterns, msaa fields. |
| `uia_invoke(target)` | Click/activate a UIA element that supports InvokePattern. |
| `uia_legacy_invoke(target)` | Activate an element via MSAA `DoDefaultAction` — use for owner-drawn controls. |
| `uia_mouse_click(x, y, double, button)` | Click at absolute screen coordinates. Use `double=true` to open register rows or accounts. Coordinates come from `rect` fields in `uia_inspect`. |
| `uia_send_keys(keys)` | Send keystrokes. Uses pywinauto/SendKeys notation: `{TAB}`, `{ESC}`, `{ENTER}`, `^c` (Ctrl+C), `%f` (Alt+F), `+{TAB}` (Shift+Tab), etc. |
| `uia_set_value(target, value)` | Set the value of an Edit or ComboBox field. |

### Target selector fields

```json
{ "by": "name",          "value": "Example Bank Checking" }
{ "by": "class_name",    "value": "QWClass_TransactionList" }
{ "by": "control_type",  "value": "MenuItem" }
{ "by": "automation_id", "value": "7611" }
{ "by": "hwnd",          "value": "2230922" }
{ "by": "legacy_name",   "value": "Save" }
{ "by": "legacy_role",   "value": "43" }
{ "by": "child_id",      "value": "2" }
```

Add `"depth": N` (1–8) to any target to control how deep `uia_inspect` recurses.

---

## 2. Application Window Hierarchy

```
QFRAME  "Quicken Classic Premier - my_finances - [<current view>]"
├── QW_BAG_TOOLBAR / QW_MAIN_TOOLBAR   (icon toolbar)
├── ToolbarWindow32                     (menu bar — see §4)
│   ├── MenuItem "File"
│   ├── MenuItem "Edit"
│   ├── MenuItem "View"
│   ├── MenuItem "Tools"
│   ├── MenuItem "Mobile && Web"
│   ├── MenuItem "Reports"
│   └── MenuItem "Help"
├── MDIClient  "Workspace"
│   ├── QWMDI  "Home"             (dashboard, always present)
│   └── QWMDI  "<Account Name>"  (one per open register, e.g. "Example Bank Checking")
│       ├── QWClass_TransactionList  "TxList"  (the register grid)
│       │   ├── QWClass_TxToolbar  (Save / More actions / Split — only when a row is open)
│       │   ├── QREdit  aid="3"   (date field of the currently-selected row — ONLY field exposed)
│       │   ├── QWScrollBar  (vertical)
│       │   └── QWScrollBar  (horizontal)
│       ├── Static  "Current Balance:"  + Static  "<amount>"
│       ├── Static  "Ending Balance:"   + Static  "<amount>"
│       ├── Static  "Online Balance:"   + Static  "<amount>"
│       ├── QWComboBox  aid="452"  "All Dates"     (date filter)
│       ├── QWComboBox  aid="454"  "Any Type"      (type filter)
│       └── QWComboBox  aid="451"  "All Transactions" (status filter)
├── QWNavigator                         (tab strip: HOME / SPENDING / BILLS / etc.)
└── QWAcctBarHolder  aid="1000"         (account sidebar — completely owner-drawn, NO UIA children)
```

**Key limitation**: `QWAcctBarHolder` exposes zero UIA/MSAA children. Account items in the
sidebar cannot be read; you can only click them by coordinate within its rect `(11,222)–(424,1739)`.

---

## 3. The Account Register

### Opening an account

Preferred: use the **Account List dialog** (reliable item list via `ListItem` controls):
```
Ctrl+A  →  opens Account List dialog (class QWinPopup)
           Filter to "Banking" via button with aid="9801"
           Double-click the account name ListItem to open its register
```

Alternative: if the register is already open as a QWMDI window, bring it to focus:
```python
uia_invoke({"by": "name", "value": "Example Bank Checking"})
```

### Register row structure — what is and isn't accessible

Transaction rows are **fully owner-drawn** (GDI painted). The grid itself exposes nothing.

However, Quicken uses **a single reusable `QREdit` widget** (aid=`3`, hwnd=`2359874`)
that gets repositioned and repopulated as focus moves through the inline edit form.
This means you can read any field value by:
1. Clicking the row to open the inline edit form
2. Clicking or Tabbing to the desired field
3. Reading `QREdit.value` via `uia_inspect({"by": "automation_id", "value": "3"})`

### Complete tab order for the banking register edit form

Each TAB advances through the edit form fields. The `QREdit` widget (aid=`3` for text fields,
aid=`5` for amount fields) repositions to each field in turn. Both `QREdit` instances share
`class_name: "QREdit"` but have different `hwnd` values.

The QFBag (calendar or calculator picklist button) moves alongside the active field as a
suffix widget — it **does NOT consume TABs**.

| Tab stop | aid | Description | Example value |
|----------|-----|-------------|---------------|
| 0 — click | `3` | **Date** (x ≈ 453–749 in register) | `"2/17/2026"` |
| TAB 1 | `3` | **Check # / Num** (x ≈ 782–833, narrow) | `""` / `"1234"` |
| TAB 2 | `3` | **Payee** (x ≈ 866–924) | `"N/A"` / `"Acme Corp"` |
| TAB 3 | `3` | **Clr / Status?** (x ≈ 929–987) | `""` |
| TAB 4 | `3` | **Category** (x ≈ 992–1022, with QFBag cat-picker aid=1124 + aid=1128) | `"Education"` |
| TAB 5 | `3` | **Tag** (x ≈ 1055–1113, no QFBag) | `""` |
| TAB 6 | `5` | **Payment/Charge** amount (x ≈ 1153–1246, QFBag = calculator) | `"20.00"` |
| TAB 7 | `5` | **Deposit/Credit** amount (x ≈ 1244–1337, QFBag = calculator) | `"-20.00"` |
| TAB 8 | — | **Save** button (TxToolbar slides into viewport) | — |
| TAB 9 | — | **More actions** button | — |
| TAB 10 | — | **Split transaction** button | — |
| TAB 11 | — | wraps back to **Date** | — |

Notes:
- The TxToolbar (`QWClass_TxToolbar`) floats to the right of the currently-focused field.
  When focus is on the Save button, the entire toolbar slides into the visible viewport at x≈1234–1369.
- Fields with QFBag have a category-picker button (aid=1124) — TAB **skips** over QFBag.
- A second QFBag button (aid=1128) appears in the Category field — it may be a subcategory picker.
- The "Memo" field on Line 2 of the edit form does **not** appear in this tab order (only 1 row
  is visible in the compact view). Memo may require expanding the row or is only visible in the
  full "two-line" register mode.

**Balance totals** (always visible, no row selection needed):
- `Static` named `"<amount>"` → Current Balance
- `Static` named `"<amount>"` → Ending Balance
- `Static` named `"<amount>"` → Online Balance

### Reading a transaction's fields (tab-walk pattern)

```python
# 1. Double-click the row to open it
uia_mouse_click(x=600, y=<row_y>, double=True)

# 2. Click the date column to focus the Date field
uia_mouse_click(x=540, y=<row_y>)
date_val = uia_inspect({"by": "automation_id", "value": "3"})["element"]["value"]

# 3. Tab forward to each subsequent field, reading after each
for step in range(1, 8):
    uia_send_keys("{TAB}")
    result = uia_inspect({"by": "automation_id", "value": "3"})
    if not result["ok"]:
        # aid=3 not visible — try aid=5 (amount widget)
        result = uia_inspect({"by": "automation_id", "value": "5"})
    rect = result["element"]["rect"]
    value = result["element"]["value"]
    print(f"Tab {step}: x={rect['left']}–{rect['right']} value={value!r}")

# 4. Escape when done (do NOT press Enter/Save unless you intend to modify)
uia_send_keys("{ESC}")
```

**Important**: tabbing through reads only — as long as no field value is changed,
`{ESC}` will cancel without saving. Confirm nothing changed if autosave is a concern.

### Writing / editing a transaction field

```python
# 1. Open the row, tab to the field you want to change
uia_mouse_click(x=600, y=<row_y>)
# ... tab to target field ...

# 2. Set the new value
uia_set_value({"by": "automation_id", "value": "3"}, "2/21/2026")
# or just type it:
uia_send_keys("2/21/2026")

# 3. Save
uia_send_keys("{ENTER}")
# or click the Save button in the toolbar:
uia_invoke({"by": "name", "value": "Save"})
```

### Deleting a transaction

```python
# 1. Click row to select it (single click — don't need to open edit form)
uia_mouse_click(x=600, y=<row_y>)

# 2. Delete key (will show confirmation dialog)
uia_send_keys("{DELETE}")

# 3. Confirm the dialog — it's a standard #32770 dialog with a "Yes"/"OK" button
uia_invoke({"by": "name", "value": "Yes"})   # or "OK" depending on Quicken version
```

### Entering a new transaction

```python
# 1. Press Ctrl+N or click the empty "new transaction" row at the bottom of the register
uia_send_keys("^n")

# 2. The blank edit form opens — tab through and fill each field:
uia_send_keys("2/21/2026{TAB}")          # Date
uia_send_keys("1234{TAB}")              # Check number (or {TAB} to skip)
uia_send_keys("Acme Corp ESPP{TAB}")     # Payee
uia_send_keys("{TAB}")                  # skip Deposit (payment transaction)
uia_send_keys("500.00{TAB}")            # Payment amount
uia_send_keys("Investments:ESPP{TAB}") # Category
uia_send_keys("Q4 2025 purchase{TAB}") # Memo

# 3. Save
uia_send_keys("{ENTER}")
```

### Finding a specific transaction

Use the search box (Edit control, aid=`482`, rect top≈385) to filter by payee:
```python
uia_set_value({"by": "automation_id", "value": "482"}, "Acme Corp")
# Register filters to matching rows; read the visible QREdit date to confirm
```
Clear it to restore full register: `uia_set_value({"by": "automation_id", "value": "482"}, "")`

---

## 3a. Split Transaction Dialog

A transaction can have multiple category lines ("splits"). Open the splits panel with the
**Split transaction** button (aid=`103`, `QC_button`, visible in `QWClass_TxToolbar` when a row is open).

### Opening the splits dialog

The Split button is only in the viewport when the TxToolbar has scrolled left (happens when
the Save button gets focus, at Tab 8). Reliable method:

```python
# Option A: Tab to the Split button (10 TABs from Date field) then click
for _ in range(10):
    uia_send_keys("{TAB}")
# TxToolbar is now at x≈1234–1369; Split button rect≈(1321,600)–(1358,632)
uia_mouse_click(x=1339, y=616)

# Option B: Click by absolute coordinate when toolbar is in viewport
# Check UIA for current Split button rect first:
toolbar = uia_inspect({"by": "class_name", "value": "QWClass_TxToolbar"})
# Then click mid-point of its "Split transaction" child
```

### Split Transaction dialog structure

```
class_name: "QWinDlg"  name: "Split Transaction"
rect: left≈837, top≈462, right≈2043, bottom≈1372
│
├── Static "Split Transaction"  aid="32702"         — title
├── Static (help text)          aid="65535"
│
├── QWListViewer  aid="200"   rect (891,632)–(1971,1138)
│   └── ListBox  aid="1"  hwnd≈...              — contains all split lines
│       ├── Static aid="8"                      — floating row buttons (above list, y≈690–720)
│       │   ├── QC_button "Next"  aid="211"     — advance to next split line
│       │   └── QC_button "Edit " aid="212"     — (purpose TBD)
│       ├── Edit  aid="1002"  class="Edit"      — ACTIVE split Category (x≈991–1318)
│       │   QFBag aid="2020"                    — picker button(s) for category
│       │     ├── QC_button aid="1124"          — open category-picker dropdown
│       │     └── QC_button aid="1128"          — subcategory picker?
│       ├── Edit  aid="1004"  class="Edit"      — ACTIVE split Amount (x≈1374–1756)
│       │
│       └── ListItem (child_id 1..N)            — one per split line; owner-drawn rows
│               SelectionItemPattern + LegacyIAccessiblePattern
│               default_action: "Double Click"  → focuses that split line
│               Row height: ~30px; first visible row at y≈660
│
├── QC_button "Add Lines"   aid="233"           — add more split lines
├── QC_button "Clear All"   aid="239"           — clear all splits
├── QC_button "Allocate"    aid="240"  (disabled when only 1 split)
├── QC_button "Adjust"      aid="201"  (disabled when Remainder=0)
│
├── Static "Split Total:"   aid="241"           — sum of all split amounts
├── Static <value>          aid="242"           — e.g. "20.00"
├── Static "Remainder:"     aid="258"           — Transaction Total – Split Total
├── Static <value>          aid="203"           — e.g. "0.00"
├── Static "Transaction Total:" aid="259"
├── Static <value>          aid="204"           — e.g. "20.00"
│
├── CheckBox "Save this to Memorized Payee List"  aid="322"  (checked by default)
├── QC_button "OK"          aid="32767"         — save splits and close dialog
└── QC_button "Cancel"      aid="32766"         — discard changes
```

### Reading split line values

Each split line in the ListBox is a `ListItem` (owner-drawn). When a split line has focus,
the `Edit` fields (aid=`1002` for Category, aid=`1004` for Amount) appear in the ListBox
and are readable:

```python
# Open splits dialog (see above)
# Click the desired split line's row by coordinate:
uia_mouse_click(x=1150, y=<split_row_y>)      # focus Category field
cat = uia_inspect({"by": "automation_id", "value": "1002"})["element"]["value"]

uia_mouse_click(x=1500, y=<split_row_y>)      # focus Amount field
amt = uia_inspect({"by": "automation_id", "value": "1004"})["element"]["value"]
```

The split list starts at y≈660 (first row) and each row is ~30px tall.
So split line N is at y ≈ 660 + (N-1) * 30.

Read all split lines in a loop:
```python
split_rows = []
for i in range(max_splits):
    row_y = 660 + i * 30
    # Skip rows past the list boundary (bottom≈1136)
    if row_y > 1136:
        break
    # Click Category field
    uia_mouse_click(x=1150, y=row_y)
    r = uia_inspect({"by": "automation_id", "value": "1002"})
    if not r["ok"] or r["element"]["value"] == "":
        break  # empty row = no more splits
    cat = r["element"]["value"]
    # Click Amount field
    uia_mouse_click(x=1500, y=row_y)
    amt_r = uia_inspect({"by": "automation_id", "value": "1004"})
    amt = amt_r["element"]["value"] if amt_r["ok"] else ""
    split_rows.append({"category": cat, "amount": amt})
```

### Writing split values

```python
# Click the Category cell on the row you want to edit
uia_mouse_click(x=1150, y=row_y)
uia_send_keys("Salary:Gross Pay")              # type the category
# Or:
uia_set_value({"by": "automation_id", "value": "1002"}, "Salary:Gross Pay")

# Click the Amount cell on the same row
uia_mouse_click(x=1500, y=row_y)
uia_set_value({"by": "automation_id", "value": "1004"}, "2500.00")

# After all lines are entered, read the Remainder to verify it's 0.00
remainder_el = uia_inspect({"by": "automation_id", "value": "203"})
assert remainder_el["element"]["name"] == "0.00", "Splits don't sum to transaction total"

# Click OK to save
uia_invoke({"by": "automation_id", "value": "32767"})   # OK button
# Back in the register — click Save to commit the transaction
uia_legacy_invoke({"by": "name", "value": "Save"})
```

### Adding new split lines

```python
# Click "Add Lines" to add more rows
uia_invoke({"by": "automation_id", "value": "233"})     # Add Lines button
```

### Checking totals

The dialog shows three read-only totals in `Static` elements:
- `aid="242"` → Split Total (sum of all Amount cells)
- `aid="203"` → Remainder = Transaction Total − Split Total (should be 0.00 when balanced)
- `aid="204"` → Transaction Total (fixed; equals the register transaction amount)

---

## 3b. Investment Account Register

For accounts like ESPP/RSU that lack automatic download, the register has additional
transaction types. The edit form will have different fields:

| Transaction type | Fields |
|-----------------|--------|
| Buy / Sell | Date, Security, Shares, Price, Commission, Account |
| Div / Int | Date, Security, Amount, Account |
| ReinvDiv | Date, Security, Shares, Price, Amount |
| MiscExp / MiscInc | Date, Description, Amount |

Navigation to investment accounts follows the same pattern (Ctrl+A → filter to
"Investing" → double-click account). The tab order through the investment edit form
needs to be mapped (see §8 TODO).

---

## 4. Menu Bar Navigation

The menu bar items are exposed as `MenuItem` UIA controls inside the second `ToolbarWindow32`
(rect starting at y≈54).

### Opening a menu

```python
# By UIA invoke:
uia_invoke({"by": "name", "value": "File"})

# By Alt+key shortcut (more reliable):
uia_send_keys({"keys": "%f"})   # Alt+F = File
uia_send_keys({"keys": "%e"})   # Alt+E = Edit
uia_send_keys({"keys": "%t"})   # Alt+T = Tools
uia_send_keys({"keys": "%r"})   # Alt+R = Reports
```

The dropdown popup is a native Win32 `#32768` window — **not** visible in the UIA tree.
Navigate it entirely with keyboard after opening.

### File menu items (child IDs for MSAA, keyboard positions)

| Item | cid | DOWN count from top |
|------|-----|---------------------|
| New Quicken File... | 1 | 0 |
| Open Quicken File... | 2 | 1 |
| *(separator)* | 3 | — |
| Copy or Backup File... | 4 | 2 |
| View/Restore Backups... | 5 | 3 |
| Validate and Repair File... | 6 | 4 |
| *(separator)* | 7 | — |
| Show this file on my computer | 8 | 5 |
| Find Quicken Files... | 9 | 6 |
| *(separator)* | 10 | — |
| Set Password (file) | 11 | 7 |
| Set Password (transactions) | 12 | 8 |
| **File Import** | 13 | 9 |
| **File Export** ▶ | 14 | 10 |
| *(separator)* | 15 | — |
| Printer Setup | 16 | 11 |
| Print Checks... | 17 | 12 |
| Print \<Account\>... | 18 | 13 |
| Exit | 24 | 19 |

File Export submenu items (open with RIGHT after highlighting item 14):
1. `Quicken Interchange Format (.QIF) File...`
2. `Quicken Transfer Format (.QXF) File...`
3. `Export Tax Data in TurboTax-compatible format (TXF)`
4. `Export Tax Data in TXJ format`

### Full QIF export key sequence

```python
uia_send_keys("%f")                                          # Alt+F opens File menu
uia_send_keys("{DOWN}{DOWN}{DOWN}{DOWN}{DOWN}{DOWN}{DOWN}{DOWN}{DOWN}{DOWN}")  # 10 DOWNs to File Export
uia_send_keys("{RIGHT}")                                     # open submenu
uia_send_keys("{ENTER}")                                     # select QIF
# → QIF export dialog appears (QWinPopup / #32770)
# Fill in: account selection, date range, output filename
# Then click Export / OK
```

---

## 5. Transaction Deduplication Workflow

All steps happen directly in the register UI.

### Scanning for duplicates

Rows are owner-drawn and not enumerable, but you can walk them by clicking each one and
reading fields through the reusable `QREdit` widget (tab-walk pattern, see §3).

Efficient approach — use the search box to narrow to a suspected payee first:
```python
# Filter register to a specific payee
uia_set_value({"by": "automation_id", "value": "482"}, "Acme Corp NSO")
# Now walk only the filtered rows
```

For each visible row:
1. Click row → read date (QREdit value)
2. Tab once → read payee
3. Tab once → read amount
4. Press Esc (no changes made)
5. Compare against previous row — if date + payee + amount match, it's a duplicate

### Deleting a duplicate

```python
# Single-click the row to select it (don't open edit form)
uia_mouse_click(x=600, y=<row_y>)

# Delete and confirm
uia_send_keys("{DELETE}")
uia_invoke({"by": "name", "value": "Yes"})   # confirmation dialog button
```

### Verifying

After deletes, check that **Ending Balance** (`Static` in the register footer) matches
the expected value. Re-scan the filtered rows to confirm no duplicates remain.

### Tips

- Use the **date filter** (ComboBox aid=`452`) to narrow to a specific date range first.
- After deleting, rows shift up — rescan from the current y position rather than
  continuing with a stale offset.
- If unsure which copy to keep, prefer the one with a **Memo** or **Category** already
  filled in (read those fields via the tab-walk before deciding).

---

## 6. ESPP / RSU Transaction Entry Workflow

For investment accounts with no automatic download, an agent can enter transactions
directly from a PDF brokerage statement.

### Open the account

```python
uia_send_keys("^a")                                     # Ctrl+A = Account List
# filter to Investing accounts, double-click the ESPP/RSU account
```

### Enter a Buy transaction

```python
uia_send_keys("^n")                                     # new transaction
# Tab order for investment register needs mapping (see §9 TODO)
# Approximate sequence:
uia_send_keys("11/15/2025{TAB}")                        # Date
uia_send_keys("Buy{TAB}")                               # Action type
uia_send_keys("ESPP{TAB}")                              # Security name
uia_send_keys("42.567{TAB}")                            # Shares
uia_send_keys("138.22{TAB}")                            # Price per share
uia_send_keys("0.00{TAB}")                              # Commission
uia_send_keys("{ENTER}")                                # Save
```

### Verify

After entry, click the row to read back the date and tab to shares/price to confirm
the values were saved correctly before moving to the next transaction.

---

## 6. Known Control Classes Reference

| Class | Description | UIA accessible? |
|-------|-------------|-----------------|
| `QFRAME` | Main application window | ✅ root |
| `QWMDI` / `QWinPopup` | MDI child / floating dialog | ✅ children inspectable |
| `QWClass_TransactionList` | Register transaction grid | ⚠️ rows owner-drawn |
| `QREdit` | Active edit field in register | ✅ `value` readable |
| `QWComboBox` | Dropdown filter (date/type/status) | ✅ `value` readable |
| `QWAcctBarHolder` | Left sidebar account list | ❌ no UIA/MSAA children |
| `QWListViewer` / `ListBox` | Account list in sidebar/dialogs | ✅ via MSAA `accName` |
| `QC_button` | Quicken custom button control | ⚠️ MSAA `name` readable |
| `QWClass_TxToolbar` | Save/Split toolbar in edit form | ✅ children named |
| `ToolbarWindow32` | Win32 toolbar (menu bar row) | ✅ MenuItem children |
| `#32768` | Native Win32 popup menu | ❌ not in UIA tree; read via MSAA `accName[cid]` |
| `#32770` | Standard Win32 dialog | ✅ UIA fully accessible |

---

## 7. Useful Keyboard Shortcuts

| Keys | Action |
|------|--------|
| `Ctrl+A` | Open Account List dialog |
| `Ctrl+O` | Open Quicken File... |
| `Ctrl+P` | Print current register |
| `Ctrl+W` or `Ctrl+F4` | Close current MDI window |
| `Ctrl+Z` | Undo last action |
| `Delete` | Delete selected transaction (when row selected in register) |
| `Ctrl+F` or search box | Find/filter transactions |
| `Alt+F` / `Alt+E` / `Alt+T` / `Alt+R` | Open File / Edit / Tools / Reports menu |
| `F10` | Activate menu bar |
| `Esc` | Cancel / close menu / close edit form |
| `Tab` / `Shift+Tab` | Move between fields in open edit form |

---

## 8. Common Pitfalls

- **Multiple windows open on second monitor**: Security Detail View (INTC), Write Checks, etc.
  may linger. Close them with `uia_invoke({"by": "name", "value": "<dialog title>"})` then
  `uia_send_keys("^{F4}")` before doing sensitive navigation.

- **Clicking the sidebar**: `QWAcctBarHolder` coords are `(11,222)–(424,1739)`. Account items
  are ~30px tall painted vertically. Without known y-offsets, use Account List dialog (Ctrl+A)
  instead — it has proper ListItem UIA elements.

- **Menu popup not in UIA tree**: After `uia_invoke` or `%f`, the dropdown is a `#32768` window.
  Navigate it only with `uia_send_keys` (DOWN/ENTER/RIGHT/ESC). Do NOT try to `uia_inspect`
  the popup — it won't appear. Use the Win32 MSAA enumeration in a terminal if you need to
  verify items.

- **Register rows only expose one field at a time via QREdit**: The `QREdit` (aid=`3`) shows
  whichever register field currently has keyboard focus. It is NOT limited to date — you can read
  any field by clicking or tabbing to it. See the complete tab order table in §3 above.

- **HWNDs change between sessions**: All hwnd values are session-specific and will differ on
  restart. Always look them up via `uia_inspect` before using `{"by": "hwnd", ...}`.

- **Split button out of viewport**: The `QWClass_TxToolbar` containing the Split button floats
  off-screen to the right when tabs 1-7 are active. TAB to the Save button (TAB 8 from Date)
  to bring it into the visible viewport at x~1234-1369.

- **Split dialog Category/Amount fields change with focus**: Only the currently-focused split
  line exposes its `Edit` fields (aid=1002 for Category, 1004 for Amount). Click each row
  by y-coordinate to focus it before reading/writing.

- **Remainder must be 0.00 to close splits cleanly**: If Remainder (aid=`203`) != `"0.00"`,
  Quicken will show a warning when you click OK. Verify the sum before closing.

---

## 9. Remaining Unknowns / TODO

- [ ] **Payee field identity**: TAB 2 value was `"N/A"` -- is this Payee, Clr, or Action?
      Need to test on a transaction with a real payee name.
- [ ] **Memo field location**: The compact register edit form does not expose Memo in the
      tab walk. It may require "two-line mode" or a different register view setting.
      Also: does the split dialog expose a per-split Memo field (aid=1005 or similar)?
- [ ] **Investment register tab order**: Needs a live scan on an investment account.
- [ ] **Paycheck split reconciliation workflow**: Document end-to-end steps --
      search Example Bank Checking for employer paycheck -> open splits -> read each split amount ->
      compare with Fidelity contribution transaction amount.
- [ ] **Clearing/reconciliation status field**: TAB 3 (x~929-987, value `""`) identity
      unconfirmed -- may be Clr (cleared/reconciled flag).
- [ ] **TxToolbar "More actions" menu**: What commands does this expose?
      Navigate with ENTER or SPACE, then Down/Enter to select an item.
