# macOS Quicken Skill Implementation Plan

**Status**: Framework in place, beginning AX hierarchy exploration
**Quicken Running**: Yes (PID 1160)
**System**: macOS (Silicon Mac)

---

## Phase 1: UI Structure Discovery (NOW)

### Objective
Map Quicken for macOS's AX hierarchy to understand:
1. Main window structure
2. Sidebar layout and account list
3. Register/transaction view structure
4. Split dialog structure
5. Navigation elements (tabs, buttons, combos)

### Steps
1. Open Accessibility Inspector (`/System/Library/Accessibility/Accessibility Inspector.app`)
2. Connect to Quicken PID 1160
3. Map the following UI elements:
   - Application window (AXWindow role)
   - Sidebar container (likely AXOutlineView or AXGroup)
   - Account list items (likely AXStaticText or AXRow)
   - Main register/transaction view (likely AXTable or owner-drawn)
   - Filter/search box (likely AXTextField)
   - Buttons and controls

### Key Questions to Answer
- Q: Does macOS Quicken expose account list via AX?  
  A: ??  (Expected: Partially - may need to find via sidebar structure)

- Q: Is the transaction register accessible via AXTable?  
  A: ??  (Expected: Partially - may be owner-drawn, need OCR fallback)

- Q: How does split dialog appear?  
  A: ??  (Expected: Modal dialog with AXTextField for fields)

- Q: Where are the main navigation controls?  
  A: ??  (Expected: Sidebar or tabs)

---

## Phase 2: Core Functionality Implementation

### 2.1 Account Discovery (list_accounts, list_sidebar_accounts)
**Priority**: HIGH  
**Complexity**: MEDIUM  
**Windows Version Size**: ~1200 lines  
**Expected macOS Size**: ~300-400 lines

**Algorithm**:
```
1. Get Quicken root element (AXUIElementCreateApplication)
2. Find sidebar container by role search (AXOutlineView or similar)
3. Get sidebar children
4. Filter for account item elements (AXStaticText or AXRow)
5. Extract name from each item
6. Return list of account names
```

**Implementation Points**:
- Use `_get_children()` helper to traverse AX hierarchy
- Search by role: AXOutlineView, AXTable, AXGroup, AXButton
- Extract text via AXTitle or AXValue attributes
- Cache results in module-level dict (like Windows version)

---

### 2.2 Account Navigation (navigate_to_account)
**Priority**: HIGH  
**Complexity**: HIGH  
**Windows Version Size**: ~800 lines  
**Expected macOS Size**: ~400-500 lines

**Algorithm**:
```
1. Find account in sidebar by name (fuzzy match)
2. Click sidebar account (AXPress action) or use keyboard navigation
3. Wait for register to load (check for transaction rows)
4. Verify account name changed
5. Return success or error
```

**Implementation Points**:
- Use AXPerformAction("AXPress") for clicking
- May need fallback to keyboard navigation (arrow keys)
- Implement timeout/retry logic
- Compare current account name before/after

---

### 2.3 Register State Reading (read_register_state)
**Priority**: HIGH  
**Complexity**: MEDIUM  
**Windows Version Size**: ~300 lines  
**Expected macOS Size**: ~200-300 lines

**Algorithm**:
```
1. Find current account name label (AXStaticText or title)
2. Find balance field (search for "$" or specific control)
3. Count transaction rows in register
4. Check for reconcile indicator (button state or label)
5. Extract search/filter text from filter box
6. Return state dict
```

**Implementation Points**:
- May need to use screen OCR for balance (owner-drawn?)
- Transaction count via AXTable rowCount or child enumeration
- Reconcile mode via indicator element visibility/state

---

### 2.4 Register Row Reading (read_register_rows, select_register_row)
**Priority**: HIGH  
**Complexity**: HIGH  
**Windows Version Size**: ~500 lines  
**Expected macOS Size**: ~400-600 lines (with OCR fallback)

**Algorithm**:
```
1. Find transaction table/register view
2. For each row (up to max_rows):
   a. Get row element (AXRow or AXTableRow)
   b. Extract columns: date, payee, category, amount, memo
   c. May need keyboard nav (arrow keys) to scroll through
   d. Parse text or use OCR if owner-drawn
3. Return array of transaction dicts
```

**Implementation Points**:
- If table accessible: iterate AXChildren and extract column values
- If owner-drawn: use keyboard navigation + screen OCR
- Column positions may differ from Windows version
- Need to handle pagination/virtual scrolling

---

### 2.5 Split Dialog Operations (read_transaction_splits, edit_split_line, close_split_dialog)
**Priority**: MEDIUM  
**Complexity**: HIGH  
**Windows Version Size**: ~600 lines  
**Expected macOS Size**: ~500-700 lines

**Algorithm**:
```
For read_transaction_splits:
1. Double-click transaction row to open split dialog
2. Find split list/table in dialog
3. For each split:
   a. Extract category, tag, memo, amount columns
   b. Return as array of split dicts

For edit_split_line:
1. Find split line by index
2. Click category/memo/amount field
3. Set value via AXValue or type via keyboard
4. Handle "New Tag" dialog if tag is new

For close_split_dialog:
1. Find OK/Save button (or Cancel)
2. Click it via AXPress or keyboard Enter/Escape
```

**Implementation Points**:
- May need physical coordinates for double-click (CGEventMouseClick)
- Fields may be editable text fields (AXTextField) or custom controls
- New Tag dialog needs modal handling
- Return to register after close

---

### 2.6 Additional Tools
**Priority**: LOWER  
**Complexity**: VARIED

#### set_register_filter
- Find filter search box (AXTextField)
- Set value via AXValue = text or type_text_quartz()
- Return resulting transaction count

#### open_reconcile
- Find "Reconcile" menu or button
- Open reconcile dialog (native Cocoa modal)
- Fill in date/balance fields
- Return status dict

#### read_screen_text
- Capture screen region using Quartz CGWindowListCreateImage
- Run Tesseract OCR (binary available via homebrew)
- Parse output to structured format

---

## Implementation Sequence

### Week 1-2: Foundation & Discovery
- [ ] Use Accessibility Inspector to map Quicken UI
- [ ] Implement `list_accounts` (simple sidebar enumeration)
- [ ] Implement `navigate_to_account` (basic version, no retries)
- [ ] Test with live Quicken

### Week 2-3: Core Data Reading
- [ ] Implement `read_register_state`
- [ ] Implement `read_register_rows` (basic, no OCR)
- [ ] Implement `select_register_row`
- [ ] Test data extraction

### Week 3-4: Split Dialog & Advanced
- [ ] Implement `read_transaction_splits`
- [ ] Implement `edit_split_line` and `close_split_dialog`
- [ ] Implement `set_register_filter`
- [ ] Test editing workflows

### Week 4-5: Refinement & OCR
- [ ] Implement `read_screen_text` with Tesseract
- [ ] Implement `open_reconcile`
- [ ] Handle edge cases and error conditions
- [ ] Cross-platform testing (compare Windows vs macOS behavior)

---

## Known Challenges

### 1. Owner-Drawn Controls
**Issue**: macOS Quicken may use owner-drawn controls for register/portfolio that don't expose text via AX  
**Solution**: Fall back to screen capture + Tesseract OCR

### 2. Modal Dialogs
**Issue**: Modal dialogs block accessibility navigation  
**Solution**: Use AXPerformAction for buttons, keyboard navigation for focus

### 3. Coordinate System
**Issue**: Window coordinates differ from screen coordinates  
**Solution**: Use AX frame coordinates which are relative to screen

### 4. Keyboard vs Mouse
**Issue**: Some operations may require keyboard (arrow nav) vs mouse (click)  
**Solution**: Implement both pathways with fallback

### 5. UI Differences
**Issue**: macOS Quicken UI likely differs from Windows version  
**Solution**: Don't assume 1:1 UI mapping; discover actual structure

---

## Testing Strategy

### Manual Testing
1. Start Quicken with known accounts/transactions
2. Call each tool via MCP server
3. Verify output matches expected structure
4. Compare with Windows version results

### Automated Testing
1. Create test fixtures with known data
2. Mock AX elements for unit testing
3. Integration tests against live Quicken (if available)

### Cross-Platform Testing
1. Run same operations on Windows and macOS
2. Compare output (accounting for UI differences)
3. Verify same tool API works on both platforms

---

## Success Criteria

- [ ] `list_accounts` returns accounts from sidebar
- [ ] `navigate_to_account` changes register to correct account
- [ ] `read_register_state` returns account name, balance, tx count
- [ ] `read_register_rows` extracts transaction details
- [ ] `read_transaction_splits` shows split dialog details
- [ ] `edit_split_line` successfully edits and saves split
- [ ] `set_register_filter` narrows transaction list
- [ ] `read_screen_text` extracts visible text via OCR
- [ ] All tools match Windows tool signatures and response formats

---

## Resources

### AX Framework Documentation
- ApplicationServices framework
- AXUIElement API reference
- AXAPI Python bindings (PyObjC)

### Quicken for macOS
- Running on this system with test data
- UI structure discoverable via Accessibility Inspector

### External Tools
- Tesseract OCR for screen text extraction
- Quartz for screen capture and mouse events
- PyObjC for accessibility framework access

