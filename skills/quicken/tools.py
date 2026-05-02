"""
Quicken-specific MCP tool definitions.

Registered by :class:`skills.quicken.QuickenSkill` when the skill is loaded.
"""

from __future__ import annotations

from typing import Any, Callable

import sys

from mcp.server.fastmcp import FastMCP

from server.uia_bridge import UIAError


def register(
    mcp: FastMCP,
    get_bridge: Callable[[], Any],
    check_auth: Callable[[str], dict[str, Any] | None],
) -> None:
    """Register all Quicken-specific MCP tools with *mcp*."""
    if sys.platform == "win32":
        try:
            from skills.quicken import windows_impl as _impl  # noqa: PLC0415
        except ImportError as e:
            raise ImportError(
                f"Failed to load Windows Quicken implementation: {e}. "
                "On Windows, install: pip install -e .[windows]"
            ) from e
    elif sys.platform == "darwin":
        try:
            from skills.quicken import macos_impl as _impl  # noqa: PLC0415
        except ImportError as e:
            raise ImportError(
                f"Failed to load macOS Quicken implementation: {e}. "
                "On macOS, install: pip install -e .[macos]"
            ) from e
    else:
        raise NotImplementedError(f"Quicken skill not supported on {sys.platform}")

    @mcp.tool(
        name="list_accounts",
        description=(
            "List all accounts visible in the current register view's account "
            "selector combobox.  Navigate to a register view (e.g. click SPENDING "
            "or ACCOUNTS) before calling this tool so the toolbar combobox is "
            "present.  Returns account names that can be passed to "
            "navigate_to_account.  Windows-only.  [Quicken skill]"
        ),
    )
    def list_accounts_tool(
        api_key: str = "",
    ) -> dict[str, Any]:
        auth_err = check_auth(api_key)
        if auth_err:
            return auth_err
        try:
            bridge = get_bridge()
            accounts = _impl.list_accounts(bridge)
            return {"ok": True, "count": len(accounts), "accounts": accounts}
        except UIAError as exc:
            return {"ok": False, "error": str(exc), "code": exc.code}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc), "code": "UNEXPECTED_ERROR"}

    @mcp.tool(
        name="navigate_to_account",
        description=(
            "Navigate to a specific account's register view.  First checks for an "
            "already-open tab, then tries double-clicking the sidebar, then falls "
            "back to the toolbar combobox.  Sidebar navigation opens the full "
            "account register (with transaction rows); combo navigation filters the "
            "All Transactions view.  Windows-only.  [Quicken skill]"
        ),
    )
    def navigate_to_account_tool(
        account_name: str,
        api_key: str = "",
    ) -> dict[str, Any]:
        auth_err = check_auth(api_key)
        if auth_err:
            return auth_err
        try:
            bridge = get_bridge()
            return _impl.navigate_to_account(bridge, account_name)
        except UIAError as exc:
            return {"ok": False, "error": str(exc), "code": exc.code}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc), "code": "UNEXPECTED_ERROR"}

    @mcp.tool(
        name="read_register_state",
        description=(
            "Read the current state of the visible transaction register: account "
            "name, balance total, transaction count, whether a reconcile is active, "
            "and the current search/filter text.  Does not require access to "
            "individual transaction rows.  Windows-only.  [Quicken skill]"
        ),
    )
    def read_register_state_tool(
        api_key: str = "",
    ) -> dict[str, Any]:
        auth_err = check_auth(api_key)
        if auth_err:
            return auth_err
        try:
            bridge = get_bridge()
            return _impl.read_register_state(bridge)
        except UIAError as exc:
            return {"ok": False, "error": str(exc), "code": exc.code}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc), "code": "UNEXPECTED_ERROR"}

    @mcp.tool(
        name="read_register_rows",
        description=(
            "Read individual transaction rows from the visible register.  Returns "
            "an array of {date, payee, check_num, category, memo, payment, deposit, "
            "balance} objects for each transaction.  'payment' means money out "
            "(withdrawal, charge); 'deposit' means money in (deposit, card payment). "
            "Uses keyboard navigation (Ctrl+Home, Tab, Down) so it "
            "works with Quicken's owner-drawn grid.  Navigate to the desired account "
            "first with navigate_to_account.  Optionally limit the number of rows "
            "with max_rows (default 50).  Windows-only.  [Quicken skill]"
        ),
    )
    def read_register_rows_tool(
        max_rows: int = 50,
        api_key: str = "",
    ) -> dict[str, Any]:
        auth_err = check_auth(api_key)
        if auth_err:
            return auth_err
        try:
            bridge = get_bridge()
            return _impl.read_register_rows(bridge, max_rows=max_rows)
        except UIAError as exc:
            return {"ok": False, "error": str(exc), "code": exc.code}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc), "code": "UNEXPECTED_ERROR"}

    @mcp.tool(
        name="set_register_filter",
        description=(
            "Type a search term into the register search/filter box and return the "
            "resulting transaction count.  Use this to narrow the register to "
            "transactions matching a payee, amount, or date.  Pass an empty string "
            "to clear the filter.  Works in both normal and reconcile register views. "
            "Windows-only.  [Quicken skill]"
        ),
    )
    def set_register_filter_tool(
        text: str,
        api_key: str = "",
    ) -> dict[str, Any]:
        auth_err = check_auth(api_key)
        if auth_err:
            return auth_err
        try:
            bridge = get_bridge()
            return _impl.set_register_filter(bridge, text)
        except UIAError as exc:
            return {"ok": False, "error": str(exc), "code": exc.code}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc), "code": "UNEXPECTED_ERROR"}

    @mcp.tool(
        name="open_reconcile",
        description=(
            "Open the Quicken reconcile dialog for an account and enter statement "
            "details.  Sends WM_COMMAND 7203 to QFRAME, handles the 'Choose "
            "Reconcile Account' dialog, selects the account, then fills in the "
            "'Reconcile Details' dialog with the statement date and ending balance.  "
            "After this call returns ok=true the register switches to reconcile "
            "mode — use read_register_state to check reconcile_active=true.  "
            "Dates must be in MM/DD/YYYY format (e.g. '03/31/2026').  "
            "Balances are plain numbers or comma-formatted (e.g. '1234.56' or "
            "'1,234.56').  "
            "Windows-only.  [Quicken skill]"
        ),
    )
    def open_reconcile_tool(
        account_name: str,
        statement_date: str,
        ending_balance: str,
        service_charge: str = "",
        service_date: str = "",
        interest_earned: str = "",
        interest_date: str = "",
        timeout_ms: int = 5000,
        api_key: str = "",
    ) -> dict[str, Any]:
        auth_err = check_auth(api_key)
        if auth_err:
            return auth_err
        try:
            bridge = get_bridge()
            return _impl.open_reconcile(
                bridge,
                account_name=account_name,
                statement_date=statement_date,
                ending_balance=ending_balance,
                service_charge=service_charge,
                service_date=service_date,
                interest_earned=interest_earned,
                interest_date=interest_date,
                timeout_ms=timeout_ms,
            )
        except UIAError as exc:
            return {"ok": False, "error": str(exc), "code": exc.code}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc), "code": "UNEXPECTED_ERROR"}

    @mcp.tool(
        name="list_sidebar_accounts",
        description=(
            "Discover all accounts in the Quicken sidebar by scrolling through "
            "and clicking visible items.  Uses a scroll-sweep approach that is "
            "reliable for all account types including investments.  "
            "Returns {ok, accounts:[{name,section}], scanned, total, done, cached}. "
            "The first call runs a full scan (up to max_seconds; default 720s). "
            "Subsequent calls return cached results instantly — set force_rescan=true "
            "to discard the cache and re-scan.  "
            "Windows-only.  [Quicken skill]"
        ),
    )
    def list_sidebar_accounts_tool(
        api_key: str = "",
        resume: bool = False,
        max_seconds: float = 720.0,
        force_rescan: bool = False,
    ) -> dict[str, Any]:
        auth_err = check_auth(api_key)
        if auth_err:
            return auth_err
        try:
            bridge = get_bridge()
            result = _impl.list_sidebar_accounts(
                bridge, resume=resume, max_seconds=max_seconds,
                force_rescan=force_rescan,
            )
            return result
        except UIAError as exc:
            return {"ok": False, "error": str(exc), "code": exc.code}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc), "code": "UNEXPECTED_ERROR"}

    @mcp.tool(
        name="read_screen_text",
        description=(
            "Read visible text from the active Quicken window using server-side "
            "Windows OCR.  Returns structured text lines with x/y positions.  "
            "This is essential for reading content from custom-drawn controls "
            "that do not expose text via standard Win32 or UIA APIs, such as "
            "investment portfolio values, owner-drawn sidebar items, and "
            "QWHtmlView content.  The calling model does NOT need vision "
            "capabilities — all OCR is performed server-side and only text is "
            "returned.  Optionally pass a region as 'left,top,right,bottom' "
            "in screen coordinates to scope the capture; if omitted, the "
            "active MDI child window is used.  Windows-only.  [Quicken skill]"
        ),
    )
    def read_screen_text_tool(
        api_key: str = "",
        region: str = "",
    ) -> dict[str, Any]:
        auth_err = check_auth(api_key)
        if auth_err:
            return auth_err
        try:
            bridge = get_bridge()
            return _impl.read_screen_text(bridge, region=region)
        except UIAError as exc:
            return {"ok": False, "error": str(exc), "code": exc.code}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc), "code": "UNEXPECTED_ERROR"}

    @mcp.tool(
        name="select_register_row",
        description=(
            "Select a specific transaction row in the visible register by "
            "0-based row_index (0 = most recent transaction).  Clicks the "
            "row directly via its screen rectangle — no keyboard required.  "
            "Call this before read_transaction_splits to choose which "
            "transaction to inspect.  Windows-only.  [Quicken skill]"
        ),
    )
    def select_register_row_tool(
        row_index: int = 0,
        api_key: str = "",
    ) -> dict[str, Any]:
        auth_err = check_auth(api_key)
        if auth_err:
            return auth_err
        try:
            bridge = get_bridge()
            return _impl.select_register_row(bridge, row_index=row_index)
        except UIAError as exc:
            return {"ok": False, "error": str(exc), "code": exc.code}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc), "code": "UNEXPECTED_ERROR"}

    @mcp.tool(
        name="read_transaction_splits",
        description=(
            "Open the split editor for a transaction and read all split lines.  "
            "If row_index is given (0 = most recent), that row is selected first; "
            "otherwise uses the currently-selected row.  "
            "Returns a list of splits: [{index, category, memo, amount}].  "
            "The split editor is left OPEN after this call — use "
            "edit_split_line to change values, then close_split_dialog to save "
            "or cancel.  "
            "Windows-only.  [Quicken skill]"
        ),
    )
    def read_transaction_splits_tool(
        row_index: int | None = None,
        api_key: str = "",
    ) -> dict[str, Any]:
        auth_err = check_auth(api_key)
        if auth_err:
            return auth_err
        try:
            bridge = get_bridge()
            return _impl.read_transaction_splits(bridge, row_index=row_index)
        except UIAError as exc:
            return {"ok": False, "error": str(exc), "code": exc.code}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc), "code": "UNEXPECTED_ERROR"}

    @mcp.tool(
        name="edit_split_line",
        description=(
            "Edit one split line in the currently-open split editor.  "
            "Uses WM_SETTEXT + EN_CHANGE injection — no focus change, no "
            "keyboard input.  A readback verifies each write.  "
            "read_transaction_splits must be called first to open the editor.  "
            "index: 0-based split row.  Pass only the fields you want to change; "
            "omit (or pass null) to leave unchanged.  "
            "Windows-only.  [Quicken skill]"
        ),
    )
    def edit_split_line_tool(
        index: int,
        category: str | None = None,
        memo: str | None = None,
        amount: str | None = None,
        tag: str | None = None,
        api_key: str = "",
    ) -> dict[str, Any]:
        auth_err = check_auth(api_key)
        if auth_err:
            return auth_err
        try:
            bridge = get_bridge()
            return _impl.edit_split_line(
                bridge, index=index, category=category, memo=memo, amount=amount, tag=tag,
            )
        except UIAError as exc:
            return {"ok": False, "error": str(exc), "code": exc.code}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc), "code": "UNEXPECTED_ERROR"}

    @mcp.tool(
        name="close_split_dialog",
        description=(
            "Close the currently-open split editor.  "
            "save=true (default) commits changes (clicks OK/Done/Enter button).  "
            "save=false discards changes (clicks Cancel).  "
            "Uses BM_CLICK on the button HWND — no focus required.  "
            "Windows-only.  [Quicken skill]"
        ),
    )
    def close_split_dialog_tool(
        save: bool = True,
        api_key: str = "",
    ) -> dict[str, Any]:
        auth_err = check_auth(api_key)
        if auth_err:
            return auth_err
        try:
            bridge = get_bridge()
            return _impl.close_split_dialog(bridge, save=save)
        except UIAError as exc:
            return {"ok": False, "error": str(exc), "code": exc.code}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc), "code": "UNEXPECTED_ERROR"}
