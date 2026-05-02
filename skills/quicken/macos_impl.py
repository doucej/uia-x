"""
macOS Quicken skill — pure ctypes AXAPI implementation.

No PyObjC required. Loads ApplicationServices.framework directly via ctypes.
Requires the calling process (Terminal / MCP server) to have Accessibility
permission:  System Settings > Privacy & Security > Accessibility

Public API mirrors windows_impl.py signatures exactly so tools.py routes
darwin calls here without modification.

Discovered AX hierarchy (live Quicken, PID 1160, macOS Sonoma):
  AXApplication
    AXWindow
      AXSplitGroup                              outer_children[0..n]
        [0] AXSplitGroup  (sidebar panel)
              AXScrollArea > AXOutline > AXRow[]   ← account list
        [1..n] main content elements (layout varies by account type)

Banking register (e.g. DCU Checking):
  [3] AXGroup  ← filter bar, account name/balance
  [4] AXScrollArea > AXTable  ← transactions (8 cols/row)

Investment register (e.g. Fidelity PAS):
  [7] AXStaticText desc='Account Name'
  [2] AXStaticText desc='Balance'
  [15] AXGroup > AXScrollArea > AXTable  ← transactions (9 cols/row)
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import subprocess
import time
from typing import Any

from server.uia_bridge import UIAError, TargetNotFoundError

# ---------------------------------------------------------------------------
# Framework setup (lazy, cached at module level)
# ---------------------------------------------------------------------------

_AX: ctypes.CDLL | None = None
_CF: ctypes.CDLL | None = None
_CG: ctypes.CDLL | None = None

kCFStringEncodingUTF8: int = 0x08000100
kAXErrorSuccess: int = 0
kCFNumberDoubleType: int = 13
kAXValueCGRectType: int = 3
kCGKeyCode_Tab: int = 48
kCGKeyCode_Return: int = 36
kCGKeyCode_Escape: int = 53
kCGEventFlagMaskCommand: int = 0x100000
c_void_p = ctypes.c_void_p


class _CGPoint(ctypes.Structure):
    """CGPoint struct for CGEventCreateMouseEvent."""
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]

_OCR_SCRIPT = os.path.join(os.path.dirname(__file__), "ocr_region.swift")


def _load_frameworks() -> None:
    """Load macOS frameworks needed for AX access (idempotent)."""
    global _AX, _CF, _CG
    if _AX is not None:
        return

    _CF = ctypes.cdll.LoadLibrary(
        "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
    )
    _AX = ctypes.cdll.LoadLibrary(
        "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
    )
    _CG = ctypes.cdll.LoadLibrary(
        "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
    )

    # CoreFoundation types
    _CF.CFStringCreateWithCString.argtypes = [c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    _CF.CFStringCreateWithCString.restype = c_void_p
    _CF.CFStringGetCString.argtypes = [c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
    _CF.CFStringGetCString.restype = ctypes.c_bool
    _CF.CFRelease.argtypes = [c_void_p]
    _CF.CFRelease.restype = None
    _CF.CFGetTypeID.argtypes = [c_void_p]
    _CF.CFGetTypeID.restype = ctypes.c_ulong
    _CF.CFStringGetTypeID.restype = ctypes.c_ulong
    _CF.CFArrayGetCount.argtypes = [c_void_p]
    _CF.CFArrayGetCount.restype = ctypes.c_long
    _CF.CFArrayGetValueAtIndex.argtypes = [c_void_p, ctypes.c_long]
    _CF.CFArrayGetValueAtIndex.restype = c_void_p
    _CF.CFArrayCreate.argtypes = [c_void_p, ctypes.POINTER(c_void_p), ctypes.c_long, c_void_p]
    _CF.CFArrayCreate.restype = c_void_p
    _CF.CFNumberCreate.restype = c_void_p
    _CF.CFNumberCreate.argtypes = [c_void_p, ctypes.c_int, c_void_p]

    # AXUIElement
    _AX.AXUIElementCreateApplication.argtypes = [ctypes.c_int32]
    _AX.AXUIElementCreateApplication.restype = c_void_p
    _AX.AXUIElementCreateSystemWide.restype = c_void_p
    _AX.AXUIElementCreateSystemWide.argtypes = []
    _AX.AXUIElementCopyAttributeValue.argtypes = [c_void_p, c_void_p, ctypes.POINTER(c_void_p)]
    _AX.AXUIElementCopyAttributeValue.restype = ctypes.c_int
    _AX.AXUIElementSetAttributeValue.argtypes = [c_void_p, c_void_p, c_void_p]
    _AX.AXUIElementSetAttributeValue.restype = ctypes.c_int
    _AX.AXUIElementPerformAction.argtypes = [c_void_p, c_void_p]
    _AX.AXUIElementPerformAction.restype = ctypes.c_int
    _AX.AXIsProcessTrusted.restype = ctypes.c_bool
    _AX.AXValueGetValue.restype = ctypes.c_bool
    _AX.AXValueGetValue.argtypes = [c_void_p, ctypes.c_int, c_void_p]

    # CoreGraphics CGEvent
    _CG.CGEventCreateKeyboardEvent.restype = c_void_p
    _CG.CGEventCreateKeyboardEvent.argtypes = [c_void_p, ctypes.c_uint16, ctypes.c_bool]
    _CG.CGEventPost.restype = None
    _CG.CGEventPost.argtypes = [ctypes.c_uint32, c_void_p]
    _CG.CGEventSetFlags.restype = None
    _CG.CGEventSetFlags.argtypes = [c_void_p, ctypes.c_uint64]
    _CG.CGEventKeyboardSetUnicodeString.restype = None
    _CG.CGEventKeyboardSetUnicodeString.argtypes = [
        c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_uint16)
    ]
    _CG.CGEventCreateMouseEvent.restype = c_void_p
    _CG.CGEventCreateMouseEvent.argtypes = [c_void_p, ctypes.c_uint32, _CGPoint, ctypes.c_uint32]


# ---------------------------------------------------------------------------
# Low-level AX helpers
# ---------------------------------------------------------------------------

def _cfstr(s: str) -> c_void_p:
    """Create a CFString from Python str. Caller must CFRelease."""
    return _CF.CFStringCreateWithCString(None, s.encode("utf-8"), kCFStringEncodingUTF8)


def _to_str(cfval: c_void_p) -> str | None:
    """Convert a CFStringRef to Python str, or None if not a CFString."""
    if not cfval:
        return None
    try:
        if _CF.CFGetTypeID(cfval) != _CF.CFStringGetTypeID():
            return None
        buf = ctypes.create_string_buffer(4096)
        if _CF.CFStringGetCString(cfval, buf, 4096, kCFStringEncodingUTF8):
            return buf.value.decode("utf-8", errors="replace")
    except Exception:
        pass
    return None


def _ax_attr(el: c_void_p, name: str) -> c_void_p | None:
    """Return raw CFTypeRef for AX attribute, or None on error."""
    k = _cfstr(name)
    val = c_void_p(0)
    err = _AX.AXUIElementCopyAttributeValue(el, k, ctypes.byref(val))
    _CF.CFRelease(k)
    return val if err == kAXErrorSuccess and val else None


def _ax_str(el: c_void_p, name: str) -> str | None:
    """Return AX attribute as Python str, or None."""
    return _to_str(_ax_attr(el, name))


def _ax_children(el: c_void_p, attr: str = "AXChildren") -> list[c_void_p]:
    """Return an AX array attribute (default AXChildren) as a list of c_void_p."""
    k = _cfstr(attr)
    val = c_void_p(0)
    err = _AX.AXUIElementCopyAttributeValue(el, k, ctypes.byref(val))
    _CF.CFRelease(k)
    if err != kAXErrorSuccess or not val:
        return []
    n = _CF.CFArrayGetCount(val)
    return [c_void_p(_CF.CFArrayGetValueAtIndex(val, i)) for i in range(n)]


def _ax_set_selected_rows(container: c_void_p, row: c_void_p) -> int:
    """Set AXSelectedRows on container to [row]. Returns AX error code."""
    row_arr = (c_void_p * 1)(row)
    cf_arr = _CF.CFArrayCreate(None, row_arr, 1, None)
    k = _cfstr("AXSelectedRows")
    err = _AX.AXUIElementSetAttributeValue(container, k, cf_arr)
    _CF.CFRelease(k)
    _CF.CFRelease(cf_arr)
    return err


def _ax_set_value(el: c_void_p, text: str) -> int:
    """Set AXValue on element to given string. Returns AX error code."""
    cf_val = _cfstr(text)
    k = _cfstr("AXValue")
    err = _AX.AXUIElementSetAttributeValue(el, k, cf_val)
    _CF.CFRelease(k)
    _CF.CFRelease(cf_val)
    return err


# ---------------------------------------------------------------------------
# New low-level helpers: frame, numeric attributes, key events, OCR
# ---------------------------------------------------------------------------

def _ax_get_frame(el: c_void_p) -> tuple[float, float, float, float] | None:
    """Return AXFrame as (x, y, w, h), or None if not available."""
    val = _ax_attr(el, "AXFrame")
    if val is None:
        return None
    r = (ctypes.c_double * 4)()
    ok = _AX.AXValueGetValue(val, kAXValueCGRectType, r)
    _CF.CFRelease(val)
    return (r[0], r[1], r[2], r[3]) if ok else None


def _ax_set_num(el: c_void_p, attr: str, val_f: float) -> int:
    """Set a numeric AX attribute using CFNumber (double). Returns error code."""
    v = ctypes.c_double(val_f)
    cfnum = _CF.CFNumberCreate(None, kCFNumberDoubleType, ctypes.byref(v))
    k = _cfstr(attr)
    err = _AX.AXUIElementSetAttributeValue(el, k, cfnum)
    _CF.CFRelease(k)
    _CF.CFRelease(cfnum)
    return err


def _ax_press_action(el: c_void_p) -> int:
    """Perform AXPress action on an element. Returns error code."""
    k = _cfstr("AXPress")
    err = _AX.AXUIElementPerformAction(el, k)
    _CF.CFRelease(k)
    return err


def _mouse_click(x: float, y: float) -> None:
    """Send a synthetic left-click at screen position (x, y) via CGEvent."""
    _load_frameworks()
    pt = _CGPoint(x, y)
    kDown = ctypes.c_uint32(1)  # kCGEventLeftMouseDown
    kUp = ctypes.c_uint32(2)    # kCGEventLeftMouseUp
    kLeft = ctypes.c_uint32(0)  # kCGMouseButtonLeft
    for event_type in (kDown, kUp):
        ev = _CG.CGEventCreateMouseEvent(None, event_type, pt, kLeft)
        _CG.CGEventPost(ctypes.c_uint32(0), ev)
        _CF.CFRelease(ev)
        time.sleep(0.05)


def _activate_quicken() -> None:
    """Bring Quicken to the foreground. Required before menu/button interactions."""
    try:
        subprocess.run(
            ["osascript", "-e", 'tell application "Quicken" to activate'],
            timeout=3,
            capture_output=True,
        )
    except subprocess.TimeoutExpired:
        pass  # Quicken may be unresponsive; continue anyway
    time.sleep(0.2)


def _send_key(keycode: int, modifiers: int = 0) -> None:
    """Send a synthetic key event (down + up) via CoreGraphics."""
    _load_frameworks()
    for down in (True, False):
        ev = _CG.CGEventCreateKeyboardEvent(None, keycode, down)
        if modifiers:
            _CG.CGEventSetFlags(ev, modifiers)
        _CG.CGEventPost(0, ev)
        _CF.CFRelease(ev)
        if down:
            time.sleep(0.02)


def _type_text(text: str) -> None:
    """Type Unicode text via CGEvent keyboard string injection."""
    _load_frameworks()
    for ch in text:
        encoded = ch.encode("utf-16-le")
        buf = (ctypes.c_uint16 * 1)(int.from_bytes(encoded, "little"))
        for down in (True, False):
            ev = _CG.CGEventCreateKeyboardEvent(None, 0, down)
            _CG.CGEventKeyboardSetUnicodeString(ev, 1, buf)
            _CG.CGEventPost(0, ev)
            _CF.CFRelease(ev)
            if down:
                time.sleep(0.01)


def _scroll_table_to_row(scroll_bar: c_void_p, total_rows: int, idx: int) -> None:
    """Scroll the register table so that row idx becomes visible."""
    if total_rows > 0:
        _ax_set_num(scroll_bar, "AXValue", idx / total_rows)
        time.sleep(0.35)


def _ocr_region(x: int, y: int, w: int, h: int) -> list[str]:
    """
    OCR a screen region using Vision framework via the ocr_region.swift script.
    Returns a list of recognized text strings (one per text observation, top-to-bottom).
    Returns empty list on any error.
    """
    if not os.path.exists(_OCR_SCRIPT):
        return []
    try:
        result = subprocess.run(
            ["swift", _OCR_SCRIPT, str(x), str(y), str(w), str(h)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return []
        data = json.loads(result.stdout.strip())
        return data.get("lines", [])
    except Exception:
        return []


def _parse_split_lines(ocr_lines: list[str]) -> list[dict[str, Any]]:
    """
    Parse raw OCR text lines from an expanded split row into structured dicts.
    Each split line typically contains: Category  [Tag]  Memo  Amount
    The amount is usually the last token matching a currency pattern.
    """
    splits = []
    for line in ocr_lines:
        line = line.strip()
        if not line:
            continue
        # Try to extract amount from end of line
        parts = line.rsplit(None, 1)
        amount = ""
        rest = line
        if len(parts) == 2:
            candidate = parts[1].lstrip("-").replace(",", "").replace("$", "")
            if re.match(r"^\d+(\.\d{2})?$", candidate):
                amount = parts[1]
                rest = parts[0].strip()
        splits.append(
            {
                "index": len(splits),
                "category": rest,
                "tag": "",
                "memo": "",
                "amount": amount,
            }
        )
    return splits


# ---------------------------------------------------------------------------
# Quicken process / window navigation
# ---------------------------------------------------------------------------

def _find_quicken_pid() -> int:
    """Return Quicken PID. Raises TargetNotFoundError if not running."""
    try:
        result = subprocess.run(
            ["pgrep", "-x", "Quicken"],
            capture_output=True, text=True, timeout=5,
        )
        pids = result.stdout.strip().split()
        if not pids:
            raise TargetNotFoundError("Quicken is not running")
        return int(pids[0])
    except (subprocess.TimeoutExpired, ValueError) as e:
        raise TargetNotFoundError(f"Failed to find Quicken process: {e}")


def _get_root() -> c_void_p:
    """Return AXUIElement for the Quicken application."""
    _load_frameworks()
    if not _AX.AXIsProcessTrusted():
        raise UIAError(
            "Accessibility permission not granted. Enable Terminal in "
            "System Settings > Privacy & Security > Accessibility.",
            code="AX_NOT_TRUSTED",
        )
    pid = _find_quicken_pid()
    return _AX.AXUIElementCreateApplication(pid)


def _get_outer_children(root: c_void_p) -> list[c_void_p]:
    """
    Return the children of the top-level AXSplitGroup:
      [0] = sidebar SplitGroup
      [1..n] = main content elements (layout varies by account type)

    Uses AXMainWindow to avoid picking up tooltip (AXHelpTag) or dialog windows.
    """
    window = _ax_attr(root, "AXMainWindow")
    if window is None:
        raise UIAError("Quicken main window not found", code="NO_WINDOW")
    win_children = _ax_children(window)
    if not win_children:
        raise UIAError("Window has no children", code="EMPTY_WINDOW")
    return _ax_children(win_children[0])


# ---------------------------------------------------------------------------
# Sidebar helpers
# ---------------------------------------------------------------------------

def _get_sidebar_outline(outer_children: list[c_void_p]) -> c_void_p | None:
    """Find the AXOutline inside the sidebar panel (outer_children[0])."""
    if not outer_children:
        return None
    sidebar = outer_children[0]  # AXSplitGroup sidebar
    for child in _ax_children(sidebar):
        if _ax_str(child, "AXRole") == "AXScrollArea":
            for gc in _ax_children(child):
                if _ax_str(gc, "AXRole") == "AXOutline":
                    return gc
    return None


def _classify_row(row: c_void_p) -> str:
    """
    Classify a sidebar AXRow.
    Returns 'account', 'group_header', 'sub_header', or 'other'.
    """
    cells = _ax_children(row)
    if not cells:
        return "other"
    cell_children = _ax_children(cells[0])
    texts = [c for c in cell_children if _ax_str(c, "AXRole") == "AXStaticText"]
    checkboxes = [c for c in cell_children if _ax_str(c, "AXRole") == "AXCheckBox"]
    if len(texts) == 2 and not checkboxes:
        return "account"
    if len(texts) >= 2 and checkboxes:
        return "group_header"
    if len(texts) == 1:
        return "sub_header"
    return "other"


def _scan_sidebar_accounts(outline: c_void_p) -> list[dict[str, Any]]:
    """Walk AXOutline rows and return list of {name, balance} dicts."""
    accounts: list[dict[str, Any]] = []
    for row in _ax_children(outline):
        if _classify_row(row) != "account":
            continue
        cells = _ax_children(row)
        texts = [c for c in _ax_children(cells[0]) if _ax_str(c, "AXRole") == "AXStaticText"]
        name = _ax_str(texts[0], "AXValue") or ""
        balance = _ax_str(texts[1], "AXValue") or ""
        if name:
            accounts.append({"name": name, "balance": balance})
    return accounts


def _find_sidebar_row(outline: c_void_p, account_name: str) -> c_void_p | None:
    """
    Find sidebar AXRow whose account name matches account_name.
    Tries exact match first, then substring match.
    """
    needle = account_name.lower().strip()
    best: c_void_p | None = None
    best_score = -1

    for row in _ax_children(outline):
        if _classify_row(row) != "account":
            continue
        cells = _ax_children(row)
        texts = [c for c in _ax_children(cells[0]) if _ax_str(c, "AXRole") == "AXStaticText"]
        name = (_ax_str(texts[0], "AXValue") or "").lower().strip()
        if name == needle:
            return row
        if needle in name or name in needle:
            score = len(name) - abs(len(name) - len(needle))
            if score > best_score:
                best_score = score
                best = row

    return best


# ---------------------------------------------------------------------------
# Main content area helpers
# ---------------------------------------------------------------------------

def _get_register_info(main_children: list[c_void_p]) -> dict[str, str]:
    """
    Extract account_name, balance, item_count_text from main content area.
    Handles both banking (elements in AXGroup) and investment (flat) layouts.

    Banking:    desc='Account Name' + desc='Balance' in AXGroup child
    Investment: desc='Account Name' flat; balance = first bare $-value AXStaticText
    """
    info: dict[str, str] = {
        "account_name": "",
        "balance": "",
        "item_count_text": "",
    }

    def check_el(el: c_void_p) -> None:
        if _ax_str(el, "AXRole") != "AXStaticText":
            return
        desc = _ax_str(el, "AXDescription") or ""
        val = _ax_str(el, "AXValue") or ""
        if desc == "Account Name" and val and not info["account_name"]:
            info["account_name"] = val
        elif desc == "Balance" and val and not info["balance"]:
            info["balance"] = val
        elif not desc and val and not info["item_count_text"] and re.match(r"^\d+\s+items", val):
            info["item_count_text"] = val

    for child in main_children:
        check_el(child)
        if _ax_str(child, "AXRole") == "AXGroup":
            for gc in _ax_children(child):
                check_el(gc)

    # Fallback for investment accounts: no desc='Balance'; use first bare $-value static text
    if not info["balance"]:
        for child in main_children:
            if _ax_str(child, "AXRole") != "AXStaticText":
                continue
            val = _ax_str(child, "AXValue") or ""
            desc = _ax_str(child, "AXDescription") or ""
            if not desc and val.startswith("$"):
                info["balance"] = val
                break

    return info


def _find_transaction_table(main_children: list[c_void_p]) -> c_void_p | None:
    """
    Locate the AXTable containing register rows in the main content area.

    Banking layout:   AXScrollArea > AXTable  (direct child)
    Investment layout: AXGroup > AXScrollArea > AXTable
    """
    for child in main_children:
        role = _ax_str(child, "AXRole") or ""
        if role == "AXScrollArea":
            for gc in _ax_children(child):
                if _ax_str(gc, "AXRole") == "AXTable":
                    return gc
        elif role == "AXGroup":
            for gc in _ax_children(child):
                if _ax_str(gc, "AXRole") == "AXScrollArea":
                    for ggc in _ax_children(gc):
                        if _ax_str(ggc, "AXRole") == "AXTable":
                            return ggc
    return None


def _find_search_field(main_children: list[c_void_p]) -> c_void_p | None:
    """Find the search/filter AXTextField in the register header."""
    for child in main_children:
        if _ax_str(child, "AXRole") == "AXTextField":
            return child
        if _ax_str(child, "AXRole") == "AXGroup":
            for gc in _ax_children(child):
                if _ax_str(gc, "AXRole") == "AXTextField":
                    return gc
    return None


# ---------------------------------------------------------------------------
# Transaction row parsing
# ---------------------------------------------------------------------------

def _parse_tx_row(ax_row: c_void_p) -> dict[str, Any] | None:
    """
    Parse one AXRow into a transaction dict.

    Cell structures observed live:
    n=8 checking:    [flag_btn, date, cleared, payee, cat_img, split_btn, amount, balance]
    n=7 credit card: [flag_btn, date, payee, category, split_btn, amount, balance]
    n=9 investment:  [flag_btn, date, action, security, memo, split_btn, debit, credit, balance]
    """
    cells = _ax_children(ax_row)
    vals = [_ax_str(c, "AXValue") or "" for c in cells]

    if not any(vals):
        return None

    n = len(vals)
    if n == 8:
        # Checking/savings: cleared col at [2], cat icon (AXImage) at [4], split btn at [5]
        return {
            "date": vals[1],
            "payee": vals[3],
            "amount": vals[6],
            "balance": vals[7],
        }
    elif n == 7:
        # Credit card: no cleared col; payee at [2], category text at [3], split btn at [4]
        return {
            "date": vals[1],
            "payee": vals[2],
            "category": vals[3],
            "amount": vals[5],
            "balance": vals[6],
        }
    elif n >= 9:
        # Investment: action at [2], security at [3], memo at [4], debit/credit at [6]/[7]
        return {
            "date": vals[1],
            "action": vals[2],
            "security": vals[3],
            "memo": vals[4],
            "debit": vals[6],
            "credit": vals[7],
            "balance": vals[8] if n > 8 else "",
        }
    elif n >= 5:
        # Fallback for unknown layouts
        return {
            "date": vals[1] if n > 1 else "",
            "payee": vals[2] if n > 2 else "",
            "amount": vals[-2] if n > 2 else "",
            "balance": vals[-1] if n > 1 else "",
        }
    return None


# ---------------------------------------------------------------------------
# Public API — must match windows_impl.py signatures
# ---------------------------------------------------------------------------

def list_accounts(bridge: Any) -> list[dict[str, Any]]:
    """Return list of {name, balance} dicts for all sidebar accounts."""
    root = _get_root()
    outer = _get_outer_children(root)
    outline = _get_sidebar_outline(outer)
    if outline is None:
        raise UIAError("Sidebar outline not found", code="SIDEBAR_NOT_FOUND")
    return _scan_sidebar_accounts(outline)


def list_sidebar_accounts(
    bridge: Any,
    resume: bool = False,
    max_seconds: float = 720.0,
    force_rescan: bool = False,
) -> dict[str, Any]:
    """Enumerate all Quicken sidebar accounts."""
    try:
        accounts = list_accounts(bridge)
        return {
            "ok": True,
            "accounts": accounts,
            "scanned": len(accounts),
            "total": len(accounts),
            "done": True,
            "cached": False,
        }
    except (UIAError, TargetNotFoundError) as e:
        return {"ok": False, "error": str(e), "code": getattr(e, "code", "ERROR")}
    except Exception as e:
        return {"ok": False, "error": str(e), "code": "SIDEBAR_ENUM_ERROR"}


def navigate_to_account(bridge: Any, account_name: str) -> dict[str, Any]:
    """Navigate to an account by selecting its sidebar row via AXSelectedRows."""
    try:
        root = _get_root()
        outer = _get_outer_children(root)
        outline = _get_sidebar_outline(outer)
        if outline is None:
            return {"ok": False, "error": "Sidebar outline not found", "code": "SIDEBAR_NOT_FOUND"}

        row = _find_sidebar_row(outline, account_name)
        if row is None:
            return {
                "ok": False,
                "error": f"Account '{account_name}' not found in sidebar",
                "code": "ACCOUNT_NOT_FOUND",
            }

        err = _ax_set_selected_rows(outline, row)
        if err != kAXErrorSuccess:
            return {
                "ok": False,
                "error": f"AXSelectedRows failed (err={err})",
                "code": "NAV_FAILED",
            }

        time.sleep(0.5)  # let the register view update
        return {"ok": True, "account": account_name}

    except (UIAError, TargetNotFoundError) as e:
        return {"ok": False, "error": str(e), "code": getattr(e, "code", "ERROR")}
    except Exception as e:
        return {"ok": False, "error": str(e), "code": "NAV_ERROR"}


def read_register_state(bridge: Any) -> dict[str, Any]:
    """Read current account name, balance, item count, and filter state."""
    try:
        root = _get_root()
        outer = _get_outer_children(root)
        main = outer[1:]  # skip sidebar[0]

        info = _get_register_info(main)

        tx_count = 0
        m = re.match(r"^(\d+)\s+items", info.get("item_count_text", ""))
        if m:
            tx_count = int(m.group(1))

        sf = _find_search_field(main)
        filter_text = (_ax_str(sf, "AXValue") or "") if sf else ""

        return {
            "ok": True,
            "account_name": info["account_name"],
            "balance_total": info["balance"],
            "tx_count": tx_count,
            "item_count_text": info["item_count_text"],
            "reconcile_active": False,
            "filter_text": filter_text,
        }
    except (UIAError, TargetNotFoundError) as e:
        return {"ok": False, "error": str(e), "code": getattr(e, "code", "ERROR")}
    except Exception as e:
        return {"ok": False, "error": str(e), "code": "REGISTER_STATE_ERROR"}


def read_register_rows(
    bridge: Any,
    max_rows: int = 50,
) -> dict[str, Any]:
    """Read up to max_rows transaction rows from the current register."""
    try:
        root = _get_root()
        outer = _get_outer_children(root)
        main = outer[1:]

        table = _find_transaction_table(main)
        if table is None:
            return {"ok": False, "error": "Transaction table not found", "code": "TABLE_NOT_FOUND"}

        all_rows = _ax_children(table)
        parsed: list[dict[str, Any]] = []
        for i, ax_row in enumerate(all_rows):
            if len(parsed) >= max_rows:
                break
            tx = _parse_tx_row(ax_row)
            if tx:
                tx["index"] = i
                parsed.append(tx)

        info = _get_register_info(main)
        return {
            "ok": True,
            "account": info.get("account_name", ""),
            "rows": parsed,
            "total_in_view": len(all_rows),
        }
    except (UIAError, TargetNotFoundError) as e:
        return {"ok": False, "error": str(e), "code": getattr(e, "code", "ERROR")}
    except Exception as e:
        return {"ok": False, "error": str(e), "code": "READ_ROWS_ERROR"}


def select_register_row(bridge: Any, row_index: int) -> dict[str, Any]:
    """Select a transaction row (0-based) via AXSelectedRows."""
    try:
        root = _get_root()
        outer = _get_outer_children(root)
        main = outer[1:]

        table = _find_transaction_table(main)
        if table is None:
            return {"ok": False, "error": "Transaction table not found", "code": "TABLE_NOT_FOUND"}

        rows = _ax_children(table)
        if row_index >= len(rows):
            return {
                "ok": False,
                "error": f"Row {row_index} out of range ({len(rows)} rows)",
                "code": "ROW_OUT_OF_RANGE",
            }

        err = _ax_set_selected_rows(table, rows[row_index])
        if err != kAXErrorSuccess:
            return {
                "ok": False,
                "error": f"AXSelectedRows on table failed (err={err})",
                "code": "SELECT_FAILED",
            }

        time.sleep(0.2)
        return {"ok": True, "row_index": row_index}

    except (UIAError, TargetNotFoundError) as e:
        return {"ok": False, "error": str(e), "code": getattr(e, "code", "ERROR")}
    except Exception as e:
        return {"ok": False, "error": str(e), "code": "SELECT_ROW_ERROR"}


def set_register_filter(bridge: Any, text: str) -> dict[str, Any]:
    """Set the search filter text field in the register view."""
    try:
        root = _get_root()
        outer = _get_outer_children(root)
        main = outer[1:]

        sf = _find_search_field(main)
        if sf is None:
            return {
                "ok": False,
                "error": "Search field not found",
                "code": "SEARCH_FIELD_NOT_FOUND",
            }

        err = _ax_set_value(sf, text)
        if err != kAXErrorSuccess:
            return {
                "ok": False,
                "error": f"Failed to set filter value (err={err})",
                "code": "SET_FILTER_FAILED",
            }

        time.sleep(0.3)
        return {"ok": True, "filter_text": text}

    except (UIAError, TargetNotFoundError) as e:
        return {"ok": False, "error": str(e), "code": getattr(e, "code", "ERROR")}
    except Exception as e:
        return {"ok": False, "error": str(e), "code": "FILTER_ERROR"}


# ---------------------------------------------------------------------------
# Reconcile and split management
# ---------------------------------------------------------------------------

def open_reconcile(
    bridge: Any,
    account_name: str,
    statement_date: str,
    ending_balance: str,
    service_charge: str = "",
    service_date: str = "",
    interest_earned: str = "",
    interest_date: str = "",
    timeout_ms: int = 5000,
) -> dict[str, Any]:
    """
    Open the reconcile dialog for account_name and advance to the transaction list.

    Navigates to account_name, opens Accounts > Reconcile Account…, fills the
    ending balance in Step 1, and presses Next.  If an in-progress reconciliation
    is detected (Step 2 already showing), it returns immediately with step=2 and
    resumed=True.

    macOS note: statement_date, service_charge, service_date, interest_earned, and
    interest_date are not supported in the Quicken for Mac reconcile UI — they are
    accepted for API compatibility but silently ignored.
    """
    try:
        nav = navigate_to_account(bridge, account_name)
        if not nav.get("ok"):
            return nav

        _activate_quicken()
        root = _get_root()

        # Open Accounts menu (index 6) → Reconcile Account…
        mb = _ax_attr(root, "AXMenuBar")
        if mb is None:
            return {"ok": False, "error": "Menu bar not found", "code": "NO_MENUBAR"}
        mb_kids = _ax_children(mb)
        if len(mb_kids) < 7:
            return {"ok": False, "error": "Accounts menu not found", "code": "NO_MENU"}
        accts_menu = mb_kids[6]
        _ax_press_action(accts_menu)
        time.sleep(0.35)
        menu_children = _ax_children(accts_menu)
        if not menu_children:
            return {"ok": False, "error": "Accounts menu did not open", "code": "MENU_OPEN_FAILED"}
        items = _ax_children(menu_children[0])
        rec_item = next(
            (
                it for it in items
                if "Reconcile" in (_ax_str(it, "AXTitle") or "")
                and "History" not in (_ax_str(it, "AXTitle") or "")
            ),
            None,
        )
        if rec_item is None:
            return {
                "ok": False,
                "error": "Reconcile Account menu item not found",
                "code": "MENU_ITEM_NOT_FOUND",
            }
        _ax_press_action(rec_item)

        # Wait for the reconcile dialog window
        deadline = time.time() + timeout_ms / 1000.0
        rec_win = None
        while time.time() < deadline:
            wins = _ax_children(root, "AXWindows")
            for w in wins:
                if (_ax_str(w, "AXTitle") or "").startswith("Reconcile:"):
                    rec_win = w
                    break
            if rec_win:
                break
            time.sleep(0.2)

        if rec_win is None:
            return {
                "ok": False,
                "error": "Reconcile dialog did not appear within timeout",
                "code": "TIMEOUT",
            }

        win_title = _ax_str(rec_win, "AXTitle") or f"Reconcile: {account_name}"
        kids = _ax_children(rec_win)
        group = kids[0]
        gkids = _ax_children(group)

        has_next = any(_ax_str(g, "AXTitle") == "Next" for g in gkids)
        has_finish = any(_ax_str(g, "AXTitle") == "Finish" for g in gkids)

        if has_finish and not has_next:
            # Already on Step 2 — in-progress reconciliation was resumed
            return {
                "ok": True,
                "window": win_title,
                "step": 2,
                "resumed": True,
                "note": (
                    "Resumed in-progress reconciliation; "
                    "ending_balance not re-entered on macOS"
                ),
            }

        # Step 1: fill ending balance field
        eb_field = next(
            (g for g in gkids if _ax_str(g, "AXDescription") == "Statement Ending Balance"),
            gkids[11] if len(gkids) > 11 else None,
        )
        if eb_field is not None:
            _ax_set_value(eb_field, ending_balance)

        # Click Next
        next_btn = next((g for g in gkids if _ax_str(g, "AXTitle") == "Next"), None)
        if next_btn is None:
            return {
                "ok": False,
                "error": "Next button not found in reconcile Step 1",
                "code": "UI_CHANGED",
            }
        _ax_press_action(next_btn)
        time.sleep(0.8)

        return {"ok": True, "window": win_title, "step": 2, "resumed": False}

    except (UIAError, TargetNotFoundError) as e:
        return {"ok": False, "error": str(e), "code": getattr(e, "code", "ERROR")}
    except Exception as e:
        return {"ok": False, "error": str(e), "code": "RECONCILE_ERROR"}


def read_screen_text(bridge: Any, *, region: str = "") -> dict[str, Any]:
    """
    Capture visible text from the screen (or a sub-region) via macOS Vision OCR.

    region format: "x,y,w,h" in screen points (optional; defaults to full screen).
    """
    try:
        import subprocess as _sp
        # Parse optional region
        x, y, w, h = 0, 0, 0, 0
        if region:
            parts = [p.strip() for p in region.split(",")]
            if len(parts) == 4:
                x, y, w, h = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])

        if w <= 0 or h <= 0:
            # Full primary display bounds
            _load_frameworks()
            _CG.CGMainDisplayID.restype = ctypes.c_uint32
            _CG.CGDisplayPixelsWide.restype = ctypes.c_ulong
            _CG.CGDisplayPixelsHigh.restype = ctypes.c_ulong
            _CG.CGMainDisplayID.argtypes = []
            _CG.CGDisplayPixelsWide.argtypes = [ctypes.c_uint32]
            _CG.CGDisplayPixelsHigh.argtypes = [ctypes.c_uint32]
            did = _CG.CGMainDisplayID()
            w = int(_CG.CGDisplayPixelsWide(did))
            h = int(_CG.CGDisplayPixelsHigh(did))

        lines = _ocr_region(x, y, w, h)
        return {"ok": True, "lines": lines, "text": "\n".join(lines)}

    except (UIAError, TargetNotFoundError) as e:
        return {"ok": False, "lines": [], "text": "", "error": str(e), "code": getattr(e, "code", "ERROR")}
    except Exception as e:
        return {"ok": False, "lines": [], "text": "", "error": str(e), "code": "OCR_ERROR"}


def read_transaction_splits(bridge: Any, row_index: int | None = None) -> dict[str, Any]:
    """
    Read split line details for the currently selected (or specified) transaction.

    For a split transaction (cell[4] role = AXImage), the function:
      1. Scrolls the row into view.
      2. Clicks the split-expand button (cell[5]) to open the inline split editor.
      3. Screenshots the expanded sub-row area.
      4. Runs Vision OCR (via ocr_region.swift) to extract split lines.

    Returns:
      {
        "ok": True,
        "kind": "split" | "single",
        "count": int,
        "splits": [{"index", "category", "tag", "memo", "amount"}, ...],
        "header": {"date", "payee", "amount"},
      }

    Note: split sub-lines are not exposed in the AX tree; OCR is the only
    available read path on macOS.
    """
    try:
        _load_frameworks()
        root = _get_root()
        outer = _get_outer_children(root)
        main = outer[1:]

        table = _find_transaction_table(main)
        if table is None:
            return {"ok": False, "error": "Transaction table not found", "code": "NO_TABLE"}

        rows = _ax_children(table)
        if not rows:
            return {"ok": False, "error": "No rows in table", "code": "NO_ROWS"}

        # Resolve target row
        if row_index is None:
            k = _cfstr("AXSelectedRows")
            v = c_void_p(0)
            err = _AX.AXUIElementCopyAttributeValue(table, k, ctypes.byref(v))
            _CF.CFRelease(k)
            if err != kAXErrorSuccess or not v:
                return {"ok": False, "error": "No row selected", "code": "NO_SELECTION"}
            sel_count = _CF.CFArrayGetCount(v)
            if sel_count == 0:
                return {"ok": False, "error": "No row selected", "code": "NO_SELECTION"}
            target_row = c_void_p(_CF.CFArrayGetValueAtIndex(v, 0))
            _CF.CFRelease(v)
            # Find its index in the rows list
            target_idx = next(
                (i for i, r in enumerate(rows) if r.value == target_row.value), None
            )
        else:
            if row_index < 0 or row_index >= len(rows):
                return {
                    "ok": False,
                    "error": f"Row index {row_index} out of range ({len(rows)} rows)",
                    "code": "INVALID_INDEX",
                }
            target_row = rows[row_index]
            target_idx = row_index

        cells = _ax_children(target_row)
        if not cells:
            return {"ok": False, "error": "Row has no cells", "code": "EMPTY_ROW"}

        # Check for split indicator: cell[4] is AXImage for split rows
        is_split = len(cells) >= 5 and (_ax_str(cells[4], "AXRole") or "") == "AXImage"
        vals = [_ax_str(c, "AXValue") or "" for c in cells]
        header = {
            "date": vals[1] if len(vals) > 1 else "",
            "payee": vals[3] if len(vals) > 3 else "",
            "amount": vals[6] if len(vals) > 6 else "",
        }

        if not is_split:
            return {"ok": True, "kind": "single", "count": 0, "splits": [], "header": header}

        # Scroll row into view
        scroll_area = next(
            (el for el in main if (_ax_str(el, "AXRole") or "") == "AXScrollArea"), None
        )
        if scroll_area and target_idx is not None:
            sb = next(
                (k for k in _ax_children(scroll_area) if (_ax_str(k, "AXRole") or "") == "AXScrollBar"),
                None,
            )
            if sb:
                _scroll_table_to_row(sb, len(rows), target_idx)

        # Expand the split view by clicking cell[5] (split open button)
        # AXPress is not supported on these cells; CGEvent mouse click is required.
        if len(cells) >= 6:
            _activate_quicken()
            frame5 = _ax_get_frame(cells[5])
            if frame5:
                cx = frame5[0] + frame5[2] / 2.0
                cy = frame5[1] + frame5[3] / 2.0
                _mouse_click(cx, cy)
            else:
                # Fallback: click center of the row
                row_frame = _ax_get_frame(target_row)
                if row_frame:
                    _mouse_click(row_frame[0] + row_frame[2] / 2.0,
                                 row_frame[1] + row_frame[3] / 2.0)
            time.sleep(0.7)
            # Refresh cell references after expansion
            cells = _ax_children(target_row)

        # Get the expanded row frame for OCR
        frame = _ax_get_frame(target_row)
        if frame is None:
            return {
                "ok": True,
                "kind": "split",
                "count": 0,
                "splits": [],
                "header": header,
                "note": "Could not get row frame for OCR",
            }

        x, y, w, h = frame
        # Skip the 34px header row, OCR the split content below
        split_y = int(y) + 34
        split_h = int(h) - 34
        if split_h <= 4:
            return {
                "ok": True,
                "kind": "split",
                "count": 0,
                "splits": [],
                "header": header,
                "note": "Split row not expanded or too small for OCR",
            }

        ocr_lines = _ocr_region(int(x), split_y, int(w), split_h)
        splits = _parse_split_lines(ocr_lines)

        return {
            "ok": True,
            "kind": "split",
            "count": len(splits),
            "splits": splits,
            "header": header,
        }

    except (UIAError, TargetNotFoundError) as e:
        return {"ok": False, "error": str(e), "code": getattr(e, "code", "ERROR")}
    except Exception as e:
        return {"ok": False, "error": str(e), "code": "SPLIT_READ_ERROR"}


def edit_split_line(
    bridge: Any,
    index: int,
    category: str | None = None,
    memo: str | None = None,
    amount: str | None = None,
    tag: str | None = None,
) -> dict[str, Any]:
    """
    Edit a split line by keyboard Tab navigation within the inline split editor.

    Quicken's inline split editor does not expose split sub-elements in the AX tree.
    This function navigates by sending Tab keystrokes to reach the correct field and
    typing the new value.

    Column order inside each split line: Category → Tag → Memo → Amount
    Pressing Cmd+E first ensures the row is in edit mode.

    Note: AXFocusedUIElement does not track focus within Quicken's custom split editor,
    so field targeting is based on a fixed tab count.  Use close_split_dialog(save=True)
    afterwards to commit changes.
    """
    try:
        _activate_quicken()

        # Enter edit mode (Cmd+E)
        _send_key(14, kCGEventFlagMaskCommand)  # E key
        time.sleep(0.5)

        # Tab navigation: 3 header fields (date, check#, payee) then per-split fields
        HEADER_TABS = 3
        FIELDS_PER_SPLIT = 4  # category, tag, memo, amount
        base_tab = HEADER_TABS + index * FIELDS_PER_SPLIT

        for _ in range(base_tab):
            _send_key(kCGKeyCode_Tab)
            time.sleep(0.08)

        # Write fields in column order
        field_values = [
            ("category", category),
            ("tag", tag),
            ("memo", memo),
            ("amount", amount),
        ]
        wrote: list[str] = []
        for field_name, value in field_values:
            if value is not None:
                # Select all existing text, then type new value
                _send_key(0x00, kCGEventFlagMaskCommand)  # Cmd+A (select all)
                time.sleep(0.05)
                _type_text(value)
                time.sleep(0.05)
                wrote.append(field_name)
            _send_key(kCGKeyCode_Tab)
            time.sleep(0.08)

        return {
            "ok": True,
            "index": index,
            "fields_written": wrote,
            "note": (
                "Split line edited via keyboard Tab navigation; "
                "call close_split_dialog(save=True) to commit."
            ),
        }

    except (UIAError, TargetNotFoundError) as e:
        return {"ok": False, "error": str(e), "code": getattr(e, "code", "ERROR")}
    except Exception as e:
        return {"ok": False, "error": str(e), "code": "EDIT_ERROR"}


def close_split_dialog(bridge: Any, save: bool = True) -> dict[str, Any]:
    """
    Commit or discard split/transaction edits in the inline editor.

    save=True  → press Return to save changes.
    save=False → press Escape to discard.
    """
    try:
        _activate_quicken()
        if save:
            _send_key(kCGKeyCode_Return)
        else:
            _send_key(kCGKeyCode_Escape)
        time.sleep(0.3)
        return {"ok": True, "saved": save}

    except (UIAError, TargetNotFoundError) as e:
        return {"ok": False, "error": str(e), "code": getattr(e, "code", "ERROR")}
    except Exception as e:
        return {"ok": False, "error": str(e), "code": "CLOSE_ERROR"}
