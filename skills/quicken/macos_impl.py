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

kCFStringEncodingUTF8: int = 0x08000100
kAXErrorSuccess: int = 0
c_void_p = ctypes.c_void_p


def _load_frameworks() -> None:
    """Load macOS frameworks needed for AX access (idempotent)."""
    global _AX, _CF
    if _AX is not None:
        return

    _CF = ctypes.cdll.LoadLibrary(
        "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
    )
    _AX = ctypes.cdll.LoadLibrary(
        "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
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

    # AXUIElement
    _AX.AXUIElementCreateApplication.argtypes = [ctypes.c_int32]
    _AX.AXUIElementCreateApplication.restype = c_void_p
    _AX.AXUIElementCopyAttributeValue.argtypes = [c_void_p, c_void_p, ctypes.POINTER(c_void_p)]
    _AX.AXUIElementCopyAttributeValue.restype = ctypes.c_int
    _AX.AXUIElementSetAttributeValue.argtypes = [c_void_p, c_void_p, c_void_p]
    _AX.AXUIElementSetAttributeValue.restype = ctypes.c_int
    _AX.AXUIElementPerformAction.argtypes = [c_void_p, c_void_p]
    _AX.AXUIElementPerformAction.restype = ctypes.c_int
    _AX.AXIsProcessTrusted.restype = ctypes.c_bool


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

    Uses AXWindows (not AXChildren) to get the actual window element.
    """
    wins = _ax_children(root, "AXWindows")
    if not wins:
        raise UIAError("Quicken has no windows", code="NO_WINDOW")
    window = wins[0]
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
# Not yet implemented on macOS
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
    """Open reconcile dialog. (macOS: not yet implemented.)"""
    return {
        "ok": False,
        "error": "Reconcile workflow not yet implemented for macOS",
        "code": "NOT_IMPLEMENTED",
    }


def read_screen_text(bridge: Any, *, region: str = "") -> dict[str, Any]:
    """Capture text via macOS OCR. (Not yet implemented.)"""
    return {
        "ok": False,
        "lines": [],
        "text": "",
        "error": "Screen OCR not yet implemented for macOS",
        "code": "NOT_IMPLEMENTED",
    }


def read_transaction_splits(bridge: Any, row_index: int | None = None) -> dict[str, Any]:
    """Read split dialog details. (Not yet implemented.)"""
    return {
        "ok": False,
        "splits": [],
        "error": "Split dialog reading not yet implemented for macOS",
        "code": "NOT_IMPLEMENTED",
    }


def edit_split_line(
    bridge: Any,
    index: int,
    category: str | None = None,
    memo: str | None = None,
    amount: str | None = None,
    tag: str | None = None,
) -> dict[str, Any]:
    """Edit a split line. (Not yet implemented.)"""
    return {
        "ok": False,
        "error": "Split editing not yet implemented for macOS",
        "code": "NOT_IMPLEMENTED",
    }


def close_split_dialog(bridge: Any, save: bool = True) -> dict[str, Any]:
    """Close split dialog. (Not yet implemented.)"""
    return {
        "ok": False,
        "error": "Split dialog close not yet implemented for macOS",
        "code": "NOT_IMPLEMENTED",
    }
