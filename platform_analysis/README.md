# Windows Quicken Skill - Platform Analysis Summary

## Overview
This analysis examines the `dev-agent-fixes` branch Quicken skill for Windows-only assumptions and platform-specific code that would prevent porting to macOS. The Quicken skill is 4774 lines of Windows-specific UI automation code built on top of the cross-platform UIA bridge abstraction.

## Analysis Documents

### [Part 1: Windows-Only APIs](./part1_windows_apis.md)
**Coverage**: ctypes.windll.user32 calls, Win32 constants, DLL imports

**Key findings**:
- 26 direct `ctypes.windll.user32` API calls
- 12 Windows message constants (WM_*, LB_*, etc.)
- 4 custom Quicken window classes (QWinDlg, QWMDI, etc.)
- pywinauto/COM-based UIA dependency

**Abstraction needed**: Platform-agnostic window/element queries with UIA equivalent mappings

---

### [Part 2: OCR & Screen Capture](./part2_ocr_screen_capture.md)
**Coverage**: `read_screen_text()` function and Windows OCR infrastructure

**Key findings**:
- `PIL.ImageGrab` for screenshot (Windows-only)
- `winsdk.windows.media.ocr.OcrEngine` for text extraction (UWP SDK, Windows-only)
- Async event loop pattern for OCR engine
- Dollar sign OCR fix (Quicken-specific artifact)
- 160 lines of tightly coupled Windows OCR logic

**Abstraction needed**: Pluggable OCR backends (Tesseract, Vision Framework, cloud APIs)

---

### [Part 3: Quicken Semantic Abstraction Gaps](./part3_quicken_semantics.md)
**Coverage**: 10 critical Quicken-specific functions that implement application semantics

**Key findings**:
- Modal dialog dismissal (unique Windows workaround)
- Sidebar account enumeration (~1700 lines, ListBox-specific)
- Register state reading (custom controls, not exposed via AX)
- Transaction row reading (owner-drawn grid, requires keyboard navigation)
- Reconcile workflow (app-specific command protocol)
- Account navigation (3 fallback methods, all Windows-dependent)

**Abstraction needed**: Separate `QuickenPlatform` interface for app-specific semantics

---

### [Part 4: Proposed Platform Interface Layer](./part4_interface_layer.md)
**Coverage**: Design for cross-platform abstraction layer

**Key proposals**:
- `UIAPlatform` abstract protocol (element finding, interaction, screen capture)
- `QuickenPlatform` abstract protocol (Quicken-specific operations)
- Platform-specific implementations: `WinAPlatform`, `MacosAPlatform`
- 4-phase migration path with clear success criteria

**Status**: Design-ready, awaiting implementation

---

### [Part 5: What Will NOT Translate to macOS](./part5_no_translate.md)
**Coverage**: Incompatibilities and fundamental gaps

**Key findings**:
- Win32 message protocol has no macOS equivalent (~1100+ lines)
- Owner-drawn controls require screen OCR on both platforms
- Dialog handling strategy differs fundamentally
- ListBox operations use platform-specific message APIs
- Reconcile workflow is app-specific, may not work on macOS
- DPI scaling and coordinate systems differ

**Recommendation**: Don't port, re-implement. Create `QuickenMacOS` with macOS-specific algorithms.

---

## Statistics

| Metric | Value |
|--------|-------|
| Total bridge_ext.py lines | 4774 |
| Windows API calls | 26+ user32 calls |
| Win32 constants | 15+ message/flag constants |
| Custom Quicken classes | 7 window class names |
| Critical Quicken functions | 10 (high complexity) |
| Estimated non-portable code | 1800-2000 lines (~40%) |
| Functions needing re-implementation | 5+ for macOS |
| Portability of foundational operations | HIGH (element finding, I/O) |
| Portability of app semantics | LOW (Quicken-specific) |

---

## Platform Abstraction Needs

### Must Abstract (For Cross-Platform Support)
1. **Window/Element Selection**
   - Current: Uses UIA/AXAPI selectors (works on both)
   - Gap: MSAA role fallback needed for Windows custom controls
   - Solution: Unified role taxonomy + fallback chain

2. **Screen Coordinates**
   - Current: Windows HWND + ClientToScreen; macOS AX frame
   - Gap: DPI scaling differs, coordinate systems differ
   - Solution: Unified `Rect` class with platform-specific converters

3. **Input Synthesis**
   - Current: SendInput/SendMessageW on Windows; CGEvent on macOS
   - Gap: Key code mapping, focus handling differs
   - Solution: Pluggable input backends with unified API

4. **Screen Capture & OCR**
   - Current: ImageGrab + winsdk.OcrEngine on Windows
   - Gap: No OCR infrastructure on macOS
   - Solution: Pluggable capture/OCR backends (Tesseract, Vision Framework)

5. **Dialog Handling**
   - Current: Win32 class enumeration + physical mouse click
   - Gap: macOS modal behavior different
   - Solution: Platform-specific modal dismissal strategies

6. **Window Enumeration**
   - Current: EnumWindows + class name matching
   - Gap: macOS uses list approach, no class names
   - Solution: Abstract window finder with role/bundle filtering

### App-Specific Abstraction (Quicken)
1. **Sidebar Account Discovery**
   - Current: ListBox enumeration with scroll position calculation
   - Gap: macOS Quicken has different sidebar UI
   - Solution: Separate `QuickenMacOS` implementation analyzing actual UI

2. **Register Navigation**
   - Current: QWMDI tab finding + physical clicks
   - Gap: macOS tabs work differently
   - Solution: Refactor to role-based tab finding

3. **Row Reading**
   - Current: Keyboard navigation + GetWindowText on owner-drawn grid
   - Gap: Owner-drawn on both, but extraction differs
   - Solution: Fallback to screen OCR on macOS

4. **Reconcile Workflow**
   - Current: WM_COMMAND 103 + dialog enumeration
   - Gap: No equivalent command protocol on macOS
   - Solution: May need to script Quicken menus or accept feature gap

---

## Recommendations

### For Windows Branch Stability
✅ **Do**: Keep current Windows implementation as-is  
✅ **Do**: Add platform guards to documentation (mark Windows-only functions)  
✅ **Do**: Ensure error handling graceful when features unavailable  
❌ **Don't**: Try to generalize Win32 message operations

### For macOS Port Planning
✅ **Do**: Create separate `QuickenMacOS` class with macOS-specific implementations  
✅ **Do**: Analyze actual macOS Quicken UI before designing sidebar account discovery  
✅ **Do**: Accept feature gaps (reconcile may not work on macOS)  
✅ **Do**: Prioritize core workflows (account nav, register reading)  
✅ **Do**: Use screen OCR as fallback for owner-drawn content  
❌ **Don't**: Attempt 1:1 port of `bridge_ext.py`  
❌ **Don't**: Assume macOS and Windows Quicken have identical UX  
❌ **Don't**: Rely on Win32 message patterns  

### For Cross-Platform Framework
✅ **Do**: Create `UIAPlatform` protocol with platform-specific implementations  
✅ **Do**: Create `QuickenPlatform` protocol for app semantics  
✅ **Do**: Implement pluggable backends for OCR, screen capture, input synthesis  
✅ **Do**: Test cross-platform on Linux + macOS + Windows  
❌ **Don't**: Put platform-specific code in skill layer  
❌ **Don't**: Assume Windows semantics work everywhere  

---

## Next Steps (If Pursuing macOS)

### Phase 1: Framework (2-3 weeks)
- [ ] Create `UIAPlatform` abstract protocol
- [ ] Extract existing bridge methods to protocol
- [ ] Create `QuickenPlatform` abstract protocol
- [ ] Move macOS bridge to implement protocol

### Phase 2: Research (1-2 weeks)
- [ ] Install Quicken for macOS
- [ ] Analyze sidebar UI structure with Accessibility Inspector
- [ ] Analyze register row extraction possibilities
- [ ] Test modal dialog handling
- [ ] Document macOS Quicken UI differences

### Phase 3: Implementation (4-6 weeks)
- [ ] Implement `QuickenMacOS.enumerate_sidebar_accounts()`
- [ ] Implement `QuickenMacOS.navigate_to_account()`
- [ ] Implement `QuickenMacOS.read_register_state()`
- [ ] Implement `QuickenMacOS.read_register_rows()` with OCR fallback
- [ ] Implement `QuickenMacOS.open_reconcile()` or accept gap
- [ ] Add screen capture + OCR backend selection

### Phase 4: Testing & Integration (2-3 weeks)
- [ ] Unit tests for macOS platform backend
- [ ] Integration tests with live macOS Quicken
- [ ] Cross-platform tool testing (Windows vs macOS)
- [ ] Performance benchmarking vs Windows version

---

## Files Generated

```
platform_analysis/
├── part1_windows_apis.md          (Windows API catalog)
├── part2_ocr_screen_capture.md    (Screen/OCR dependencies)
├── part3_quicken_semantics.md     (App-specific functions)
├── part4_interface_layer.md       (Proposed abstraction design)
├── part5_no_translate.md          (Incompatibilities & gaps)
└── README.md                      (This file)
```

All files located in `/Users/doucej/uia-x/platform_analysis/`

---

## Conclusion

The Windows Quicken skill is a sophisticated 4774-line application of Windows-specific UI automation and Quicken semantic knowledge. Approximately **40% of the code is directly Windows-dependent** and cannot be ported without significant redesign.

**Core issue**: Quicken on Windows uses low-level Win32 message passing and proprietary window classes. Quicken on macOS uses native Cocoa APIs with a likely different UI structure.

**Path forward**: Rather than porting `bridge_ext.py`, design a cross-platform abstraction layer (`UIAPlatform` + `QuickenPlatform` protocols) and implement macOS-specific versions that work with Quicken for macOS's actual UI.

The analysis is complete. Implementation requires:
1. Framework design + testing (UIAPlatform protocol)
2. macOS Quicken UI research (understand actual structure)
3. macOS-specific Quicken semantics implementation
4. Cross-platform testing and validation
