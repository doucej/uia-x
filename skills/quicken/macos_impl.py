"""
macOS Quicken skill implementation using AXAPI (Accessibility Framework).

Mirrors the Windows implementation (windows_impl.py) but uses PyObjC and
AXAPI for accessibility instead of Win32 ctypes.

This module exposes the same public functions that tools.py expects:
- list_accounts
- navigate_to_account
- read_register_state
- read_register_rows
- set_register_filter
- open_reconcile
- list_sidebar_accounts
- read_screen_text
- select_register_row
- read_transaction_splits
- edit_split_line
- close_split_dialog
"""

from __future__ import annotations

import subprocess
import time
from typing import Any

from server.uia_bridge import UIAError, TargetNotFoundError


# ---------------------------------------------------------------------------
# macOS Quicken Discovery & Connection
# ---------------------------------------------------------------------------

def _find_quicken_pid() -> int:
    """Find Quicken for macOS process ID using pgrep."""
    try:
        result = subprocess.run(
            ["pgrep", "-x", "Quicken"],
            capture_output=True,
            text=True,
            timeout=5
        )
        pids = result.stdout.strip().split()
        if not pids:
            raise TargetNotFoundError("Quicken is not running")
        return int(pids[0])
    except (subprocess.TimeoutExpired, ValueError) as e:
        raise TargetNotFoundError(f"Failed to find Quicken process: {e}")


def _get_ax_root() -> Any:
    """Get the root AXUIElement for Quicken application."""
    try:
        from ApplicationServices import AXUIElementCreateApplication  # noqa: PLC0415
    except ImportError:
        raise UIAError(
            "PyObjC ApplicationServices not available. Install: pip install pyobjc-framework-ApplicationServices",
            code="DEPENDENCY_MISSING"
        )
    
    pid = _find_quicken_pid()
    return AXUIElementCreateApplication(pid)


def _get_ax_attribute(element: Any, attr: str) -> Any:
    """Get an AX attribute from an element, returning None on error."""
    try:
        from ApplicationServices import AXUIElementCopyAttributeValue, kAXErrorSuccess  # noqa: PLC0415
        err, value = AXUIElementCopyAttributeValue(element, attr, None)
        if err == kAXErrorSuccess:
            return value
    except Exception:
        pass
    return None


def _get_children(element: Any) -> list[Any]:
    """Get children of an AX element."""
    try:
        from ApplicationServices import kAXChildrenAttribute  # noqa: PLC0415
        children = _get_ax_attribute(element, kAXChildrenAttribute)
        return children if children else []
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Public API Functions
# ---------------------------------------------------------------------------

def list_accounts(bridge: Any) -> list[dict[str, Any]]:
    """List all accounts visible in Quicken sidebar or toolbar."""
    try:
        root = _get_ax_root()
        # TODO: Implement account list extraction
        # For now, return empty list as placeholder
        return []
    except TargetNotFoundError as e:
        raise e
    except Exception as e:
        raise UIAError(f"Failed to list accounts: {e}", code="LIST_ACCOUNTS_ERROR")


def navigate_to_account(bridge: Any, account_name: str) -> dict[str, Any]:
    """Navigate to a specific account's register view."""
    try:
        root = _get_ax_root()
        # TODO: Implement account navigation
        return {"ok": False, "error": "Not implemented", "code": "NOT_IMPLEMENTED"}
    except Exception as e:
        return {"ok": False, "error": str(e), "code": "NAV_ERROR"}


def read_register_state(bridge: Any) -> dict[str, Any]:
    """Read current state of the visible transaction register."""
    try:
        root = _get_ax_root()
        # TODO: Implement register state reading
        return {
            "ok": False,
            "account_name": "",
            "balance_total": "",
            "tx_count": 0,
            "reconcile_active": False,
            "filter_text": "",
            "error": "Not implemented",
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "code": "REGISTER_STATE_ERROR"}


def read_register_rows(
    bridge: Any,
    max_rows: int = 50,
) -> dict[str, Any]:
    """Read transaction rows from the current register."""
    try:
        root = _get_ax_root()
        # TODO: Implement row reading
        return {
            "ok": False,
            "account": "",
            "rows": [],
            "error": "Not implemented",
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "code": "READ_ROWS_ERROR"}


def set_register_filter(bridge: Any, text: str) -> dict[str, Any]:
    """Set search filter in register view."""
    try:
        root = _get_ax_root()
        # TODO: Implement filter setting
        return {"ok": False, "error": "Not implemented", "code": "NOT_IMPLEMENTED"}
    except Exception as e:
        return {"ok": False, "error": str(e), "code": "FILTER_ERROR"}


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
    """Open reconcile dialog and enter statement details."""
    try:
        root = _get_ax_root()
        # TODO: Implement reconcile workflow
        return {"ok": False, "error": "Not implemented", "code": "NOT_IMPLEMENTED"}
    except Exception as e:
        return {"ok": False, "error": str(e), "code": "RECONCILE_ERROR"}


def list_sidebar_accounts(
    bridge: Any,
    resume: bool = False,
    max_seconds: float = 720.0,
    force_rescan: bool = False,
) -> dict[str, Any]:
    """Enumerate all accounts in Quicken sidebar."""
    try:
        root = _get_ax_root()
        # TODO: Implement sidebar account enumeration
        return {
            "ok": False,
            "accounts": [],
            "scanned": 0,
            "total": 0,
            "done": False,
            "cached": False,
            "error": "Not implemented",
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "code": "SIDEBAR_ENUM_ERROR"}


def read_screen_text(
    bridge: Any,
    *,
    region: str = "",
) -> dict[str, Any]:
    """Capture text from screen region using macOS OCR."""
    try:
        root = _get_ax_root()
        # TODO: Implement screen capture + OCR (Tesseract or Vision Framework)
        return {
            "ok": False,
            "lines": [],
            "text": "",
            "error": "Not implemented",
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "code": "OCR_ERROR"}


def select_register_row(
    bridge: Any,
    row_index: int = 0,
) -> dict[str, Any]:
    """Select a transaction row by index."""
    try:
        root = _get_ax_root()
        # TODO: Implement row selection
        return {"ok": False, "error": "Not implemented", "code": "NOT_IMPLEMENTED"}
    except Exception as e:
        return {"ok": False, "error": str(e), "code": "SELECT_ROW_ERROR"}


def read_transaction_splits(
    bridge: Any,
    row_index: int | None = None,
) -> dict[str, Any]:
    """Read split dialog details for a transaction."""
    try:
        root = _get_ax_root()
        # TODO: Implement split dialog reading
        return {
            "ok": False,
            "splits": [],
            "error": "Not implemented",
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "code": "READ_SPLITS_ERROR"}


def edit_split_line(
    bridge: Any,
    index: int,
    category: str | None = None,
    memo: str | None = None,
    amount: str | None = None,
    tag: str | None = None,
) -> dict[str, Any]:
    """Edit a split line in the split dialog."""
    try:
        root = _get_ax_root()
        # TODO: Implement split editing
        return {"ok": False, "error": "Not implemented", "code": "NOT_IMPLEMENTED"}
    except Exception as e:
        return {"ok": False, "error": str(e), "code": "EDIT_SPLIT_ERROR"}


def close_split_dialog(
    bridge: Any,
    save: bool = True,
) -> dict[str, Any]:
    """Close split dialog with save or cancel."""
    try:
        root = _get_ax_root()
        # TODO: Implement split dialog close
        return {"ok": False, "error": "Not implemented", "code": "NOT_IMPLEMENTED"}
    except Exception as e:
        return {"ok": False, "error": str(e), "code": "CLOSE_SPLIT_ERROR"}

