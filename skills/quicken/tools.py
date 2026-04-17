"""
Quicken-specific MCP tool definitions.

Registered by :class:`skills.quicken.QuickenSkill` when the skill is loaded.
"""

from __future__ import annotations

from typing import Any, Callable

from mcp.server.fastmcp import FastMCP

from server.uia_bridge import UIAError
from skills.quicken import bridge_ext


def register(
    mcp: FastMCP,
    get_bridge: Callable[[], Any],
    check_auth: Callable[[str], dict[str, Any] | None],
) -> None:
    """Register all Quicken-specific MCP tools with *mcp*."""

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
            accounts = bridge_ext.list_accounts(bridge)
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
            return bridge_ext.navigate_to_account(bridge, account_name)
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
            return bridge_ext.read_register_state(bridge)
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
            return bridge_ext.read_register_rows(bridge, max_rows=max_rows)
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
            return bridge_ext.set_register_filter(bridge, text)
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
            return bridge_ext.open_reconcile(
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
            "Discover all accounts in the Quicken sidebar by physically clicking "
            "each item and reading the resulting window title.  Investment accounts "
            "take 2-7 seconds each, so the scan is time-bounded: each call processes "
            "items for up to max_seconds (default 20s) then returns with done=False. "
            "Call repeatedly with resume=True until done=True to get all accounts. "
            "The 'accounts' list accumulates across calls. "
            "Example: call with resume=False, then keep calling with resume=True "
            "until done=True. "
            "Returns {ok, accounts:[{name,section}], scanned, total, done}. "
            "Windows-only.  [Quicken skill]"
        ),
    )
    def list_sidebar_accounts_tool(
        api_key: str = "",
        resume: bool = False,
        max_seconds: float = 20.0,
    ) -> dict[str, Any]:
        auth_err = check_auth(api_key)
        if auth_err:
            return auth_err
        try:
            bridge = get_bridge()
            return bridge_ext.list_sidebar_accounts(bridge, resume=resume,
                                                    max_seconds=max_seconds)
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
            return bridge_ext.read_screen_text(bridge, region=region)
        except UIAError as exc:
            return {"ok": False, "error": str(exc), "code": exc.code}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc), "code": "UNEXPECTED_ERROR"}
