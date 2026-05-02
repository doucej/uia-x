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


# ---------------------------------------------------------------------------
# Public API Functions - All stubs for now
# ---------------------------------------------------------------------------

def list_accounts(bridge: Any) -> list[dict[str, Any]]:
    """List all accounts visible in Quicken sidebar or toolbar."""
    raise NotImplementedError("macOS Quicken: list_accounts not yet implemented")


def navigate_to_account(bridge: Any, account_name: str) -> dict[str, Any]:
    """Navigate to a specific account's register view."""
    raise NotImplementedError("macOS Quicken: navigate_to_account not yet implemented")


def read_register_state(bridge: Any) -> dict[str, Any]:
    """Read current state of the visible transaction register."""
    raise NotImplementedError("macOS Quicken: read_register_state not yet implemented")


def read_register_rows(
    bridge: Any,
    max_rows: int = 50,
) -> dict[str, Any]:
    """Read transaction rows from the current register."""
    raise NotImplementedError("macOS Quicken: read_register_rows not yet implemented")


def set_register_filter(bridge: Any, text: str) -> dict[str, Any]:
    """Set search filter in register view."""
    raise NotImplementedError("macOS Quicken: set_register_filter not yet implemented")


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
    raise NotImplementedError("macOS Quicken: open_reconcile not yet implemented")


def list_sidebar_accounts(
    bridge: Any,
    resume: bool = False,
    max_seconds: float = 720.0,
    force_rescan: bool = False,
) -> dict[str, Any]:
    """Enumerate all accounts in Quicken sidebar."""
    raise NotImplementedError("macOS Quicken: list_sidebar_accounts not yet implemented")


def read_screen_text(
    bridge: Any,
    *,
    region: str = "",
) -> dict[str, Any]:
    """Capture text from screen region using macOS OCR."""
    raise NotImplementedError("macOS Quicken: read_screen_text not yet implemented")


def select_register_row(
    bridge: Any,
    row_index: int = 0,
) -> dict[str, Any]:
    """Select a transaction row by index."""
    raise NotImplementedError("macOS Quicken: select_register_row not yet implemented")


def read_transaction_splits(
    bridge: Any,
    row_index: int | None = None,
) -> dict[str, Any]:
    """Read split dialog details for a transaction."""
    raise NotImplementedError("macOS Quicken: read_transaction_splits not yet implemented")


def edit_split_line(
    bridge: Any,
    index: int,
    category: str | None = None,
    memo: str | None = None,
    amount: str | None = None,
    tag: str | None = None,
) -> dict[str, Any]:
    """Edit a split line in the split dialog."""
    raise NotImplementedError("macOS Quicken: edit_split_line not yet implemented")


def close_split_dialog(
    bridge: Any,
    save: bool = True,
) -> dict[str, Any]:
    """Close split dialog with save or cancel."""
    raise NotImplementedError("macOS Quicken: close_split_dialog not yet implemented")
