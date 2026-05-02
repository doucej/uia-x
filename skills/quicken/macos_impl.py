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
        try:
            from ApplicationServices import (  # noqa: PLC0415
                kAXRoleAttribute, kAXTitleAttribute, kAXOutlineViewRole,
                kAXTableRole, kAXChildrenAttribute
            )
        except ImportError:
            return []
        
        root = _get_ax_root()
        accounts = []
        
        # Search for sidebar/outline view containing accounts
        def find_outline_or_table(element: Any, depth: int = 0) -> list[str]:
            """Recursively search for outline view or table with accounts."""
            if depth > 5:
                return []
            
            role = _get_ax_attribute(element, kAXRoleAttribute)
            found = []
            
            # Check if this is an outline view or table
            if role in (kAXOutlineViewRole, kAXTableRole):
                children = _get_children(element)
                for child in children:
                    child_role = _get_ax_attribute(child, kAXRoleAttribute)
                    # Look for row elements
                    if "row" in str(child_role).lower():
                        title = _get_ax_attribute(child, kAXTitleAttribute)
                        if title:
                            found.append(title)
            
            # Recurse to children
            children = _get_children(element)
            for child in children:
                found.extend(find_outline_or_table(child, depth + 1))
            
            return found
        
        accounts = find_outline_or_table(root)
        # Remove duplicates and filter empty strings
        accounts = list(dict.fromkeys([a for a in accounts if a]))
        
        return accounts
    except TargetNotFoundError as e:
        raise e
    except Exception as e:
        raise UIAError(f"Failed to list accounts: {e}", code="LIST_ACCOUNTS_ERROR")


def navigate_to_account(bridge: Any, account_name: str) -> dict[str, Any]:
    """Navigate to a specific account's register view."""
    try:
        try:
            from ApplicationServices import (  # noqa: PLC0415
                kAXRoleAttribute, kAXTitleAttribute, kAXPressAction,
                kAXChildrenAttribute
            )
        except ImportError:
            return {"ok": False, "error": "ApplicationServices not available", "code": "MISSING_FRAMEWORK"}
        
        root = _get_ax_root()
        
        # Find account row in sidebar
        def find_account_row(element: Any, name: str, depth: int = 0) -> Any:
            """Find account row by name."""
            if depth > 5:
                return None
            
            title = _get_ax_attribute(element, kAXTitleAttribute)
            if title and name.lower() in str(title).lower():
                return element
            
            children = _get_children(element)
            for child in children:
                result = find_account_row(child, name, depth + 1)
                if result:
                    return result
            
            return None
        
        account_row = find_account_row(root, account_name)
        if not account_row:
            return {"ok": False, "error": f"Account '{account_name}' not found", "code": "ACCOUNT_NOT_FOUND"}
        
        # Click account (press action)
        try:
            account_row.performAction_(kAXPressAction)
            time.sleep(0.3)  # Wait for navigation
        except Exception as e:
            return {"ok": False, "error": f"Failed to click account: {e}", "code": "CLICK_FAILED"}
        
        return {"ok": True, "account": account_name}
    except Exception as e:
        return {"ok": False, "error": str(e), "code": "NAV_ERROR"}


def read_register_state(bridge: Any) -> dict[str, Any]:
    """Read current state of the visible transaction register."""
    try:
        try:
            from ApplicationServices import (  # noqa: PLC0415
                kAXRoleAttribute, kAXTitleAttribute, kAXValueAttribute,
                kAXChildrenAttribute
            )
        except ImportError:
            return {"ok": False, "error": "ApplicationServices not available", "code": "MISSING_FRAMEWORK"}
        
        root = _get_ax_root()
        
        # Find account name (look for window title or register header)
        account_name = _get_ax_attribute(root, kAXTitleAttribute)
        if not account_name:
            account_name = ""
        
        # Count transaction rows (traverse to find table/list)
        def count_transactions(element: Any, depth: int = 0) -> int:
            """Count transaction rows in register."""
            if depth > 6:
                return 0
            
            role = _get_ax_attribute(element, kAXRoleAttribute)
            count = 0
            
            # If this is a table role, count rows
            if "table" in str(role).lower() or "outline" in str(role).lower():
                children = _get_children(element)
                for child in children:
                    child_role = _get_ax_attribute(child, kAXRoleAttribute)
                    if "row" in str(child_role).lower():
                        count += 1
            
            # Recurse to children
            children = _get_children(element)
            for child in children:
                count += count_transactions(child, depth + 1)
            
            return count
        
        tx_count = count_transactions(root)
        
        # Try to find balance field
        balance_total = ""
        def find_balance(element: Any, depth: int = 0) -> str:
            """Search for balance value."""
            if depth > 6:
                return ""
            
            value = _get_ax_attribute(element, kAXValueAttribute)
            title = _get_ax_attribute(element, kAXTitleAttribute)
            
            # Look for values that look like currency amounts
            if value and isinstance(value, str) and ("$" in value or "." in value):
                return value
            if title and isinstance(title, str) and ("balance" in title.lower() or "$" in title):
                val = _get_ax_attribute(element, kAXValueAttribute)
                if val:
                    return str(val)
            
            children = _get_children(element)
            for child in children:
                result = find_balance(child, depth + 1)
                if result:
                    return result
            
            return ""
        
        balance_total = find_balance(root)
        
        return {
            "ok": True,
            "account_name": account_name,
            "balance_total": balance_total,
            "tx_count": tx_count,
            "reconcile_active": False,
            "filter_text": "",
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "code": "REGISTER_STATE_ERROR"}


def read_register_rows(
    bridge: Any,
    max_rows: int = 50,
) -> dict[str, Any]:
    """Read transaction rows from the current register."""
    try:
        try:
            from ApplicationServices import (  # noqa: PLC0415
                kAXRoleAttribute, kAXTitleAttribute, kAXValueAttribute,
                kAXChildrenAttribute
            )
        except ImportError:
            return {"ok": False, "error": "ApplicationServices not available", "code": "MISSING_FRAMEWORK"}
        
        root = _get_ax_root()
        rows = []
        
        # Find table and extract rows
        def extract_rows(element: Any, depth: int = 0, count: int = 0) -> tuple[list, int]:
            """Extract transaction rows from table."""
            if depth > 6 or count >= max_rows:
                return [], count
            
            role = _get_ax_attribute(element, kAXRoleAttribute)
            found_rows = []
            
            # If this is a table role, extract rows
            if "table" in str(role).lower() or "outline" in str(role).lower():
                children = _get_children(element)
                for child in children:
                    if count >= max_rows:
                        break
                    child_role = _get_ax_attribute(child, kAXRoleAttribute)
                    if "row" in str(child_role).lower():
                        # Extract row data
                        title = _get_ax_attribute(child, kAXTitleAttribute)
                        value = _get_ax_attribute(child, kAXValueAttribute)
                        row_data = {
                            "index": count,
                            "date": "",
                            "payee": title or "",
                            "category": "",
                            "memo": "",
                            "amount": value or "",
                        }
                        found_rows.append(row_data)
                        count += 1
            
            # Recurse to children
            children = _get_children(element)
            for child in children:
                if count >= max_rows:
                    break
                more_rows, count = extract_rows(child, depth + 1, count)
                found_rows.extend(more_rows)
            
            return found_rows, count
        
        rows, _ = extract_rows(root)
        
        return {
            "ok": True,
            "account": "",
            "rows": rows,
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "code": "READ_ROWS_ERROR"}


def select_register_row(bridge: Any, row_index: int) -> dict[str, Any]:
    """Select a transaction row in the register."""
    try:
        try:
            from ApplicationServices import (  # noqa: PLC0415
                kAXRoleAttribute, kAXPressAction, kAXChildrenAttribute
            )
        except ImportError:
            return {"ok": False, "error": "ApplicationServices not available", "code": "MISSING_FRAMEWORK"}
        
        root = _get_ax_root()
        
        # Find table and select row at index
        def find_and_select_row(element: Any, target_index: int, depth: int = 0, current_index: int = 0) -> tuple[bool, int]:
            """Find row at target_index and click it."""
            if depth > 6:
                return False, current_index
            
            role = _get_ax_attribute(element, kAXRoleAttribute)
            
            if "table" in str(role).lower() or "outline" in str(role).lower():
                children = _get_children(element)
                for child in children:
                    child_role = _get_ax_attribute(child, kAXRoleAttribute)
                    if "row" in str(child_role).lower():
                        if current_index == target_index:
                            try:
                                child.performAction_(kAXPressAction)
                                time.sleep(0.2)
                                return True, current_index
                            except Exception:
                                return False, current_index
                        current_index += 1
            
            children = _get_children(element)
            for child in children:
                found, current_index = find_and_select_row(child, target_index, depth + 1, current_index)
                if found:
                    return True, current_index
            
            return False, current_index
        
        found, _ = find_and_select_row(root, row_index)
        
        if not found:
            return {"ok": False, "error": f"Row {row_index} not found", "code": "ROW_NOT_FOUND"}
        
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e), "code": "SELECT_ROW_ERROR"}


def set_register_filter(bridge: Any, text: str) -> dict[str, Any]:
    """Set search filter in register view."""
    try:
        try:
            from ApplicationServices import (  # noqa: PLC0415
                kAXRoleAttribute, kAXValueAttribute, kAXTextFieldRole,
                kAXChildrenAttribute
            )
        except ImportError:
            return {"ok": False, "error": "ApplicationServices not available", "code": "MISSING_FRAMEWORK"}
        
        root = _get_ax_root()
        
        # Find search/filter text field
        def find_search_field(element: Any, depth: int = 0) -> Any:
            """Find search field in register."""
            if depth > 6:
                return None
            
            role = _get_ax_attribute(element, kAXRoleAttribute)
            if role == kAXTextFieldRole:
                title = _get_ax_attribute(element, kAXTitleAttribute)
                if title and ("search" in str(title).lower() or "filter" in str(title).lower()):
                    return element
            
            children = _get_children(element)
            for child in children:
                result = find_search_field(child, depth + 1)
                if result:
                    return result
            
            return None
        
        search_field = find_search_field(root)
        if not search_field:
            return {"ok": False, "error": "Search field not found", "code": "SEARCH_FIELD_NOT_FOUND"}
        
        try:
            search_field.setAttributeValue_forAttribute_(text, kAXValueAttribute)
            time.sleep(0.2)
        except Exception as e:
            return {"ok": False, "error": f"Failed to set filter: {e}", "code": "SET_FILTER_ERROR"}
        
        return {"ok": True, "filter_text": text}
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
    return {
        "ok": False,
        "error": "Reconcile workflow not yet implemented for macOS",
        "code": "NOT_IMPLEMENTED"
    }


def list_sidebar_accounts(
    bridge: Any,
    resume: bool = False,
    max_seconds: float = 720.0,
    force_rescan: bool = False,
) -> dict[str, Any]:
    """Enumerate all accounts in Quicken sidebar."""
    try:
        # Use list_accounts as fallback
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
        except Exception:
            return {
                "ok": False,
                "accounts": [],
                "scanned": 0,
                "total": 0,
                "done": False,
                "cached": False,
                "error": "Failed to enumerate sidebar accounts",
            }
    except Exception as e:
        return {"ok": False, "error": str(e), "code": "SIDEBAR_ENUM_ERROR"}


def read_screen_text(
    bridge: Any,
    *,
    region: str = "",
) -> dict[str, Any]:
    """Capture text from screen region using macOS OCR."""
    return {
        "ok": False,
        "lines": [],
        "text": "",
        "error": "OCR not yet implemented for macOS",
        "code": "NOT_IMPLEMENTED"
    }


def read_transaction_splits(
    bridge: Any,
    row_index: int | None = None,
) -> dict[str, Any]:
    """Read split dialog details for a transaction."""
    return {
        "ok": False,
        "splits": [],
        "error": "Split dialog reading not yet implemented for macOS",
        "code": "NOT_IMPLEMENTED"
    }


def edit_split_line(
    bridge: Any,
    index: int,
    category: str | None = None,
    memo: str | None = None,
    amount: str | None = None,
    tag: str | None = None,
) -> dict[str, Any]:
    """Edit a split line in the split dialog."""
    return {
        "ok": False,
        "error": "Split editing not yet implemented for macOS",
        "code": "NOT_IMPLEMENTED"
    }


def close_split_dialog(
    bridge: Any,
    save: bool = True,
) -> dict[str, Any]:
    """Close split dialog with save or cancel."""
    return {
        "ok": False,
        "error": "Split dialog close not yet implemented for macOS",
        "code": "NOT_IMPLEMENTED"
    }

