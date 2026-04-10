"""
Main MCP server entry point – cross-platform UI Automation over MCP.

Use the ``process_list`` and ``select_window`` tools to choose a target window,
then use the UIA tools to inspect and interact with it.

Environment variables
---------------------
UIAX_BACKEND      Backend: ``real`` (auto-detect, default), ``mock``, ``linux``,
                  ``macos``.  Legacy alias: ``UIA_BACKEND``.
UIAX_AUTH         Auth mode: ``apikey`` (default) or ``none``.
                  Legacy alias: ``UIA_X_AUTH``.
UIAX_API_KEY      Pin a specific API key (printed on startup; skips on-disk keygen).
                  Deprecated alias: ``UIA_X_API_KEY``.
MCP_TRANSPORT     Transport: ``stdio`` (default), ``sse``, ``streamable-http``.
MCP_HOST          Bind address for HTTP transports (default ``0.0.0.0``).
MCP_PORT          Port for HTTP transports (default ``8000``).

Usage
-----
    python -m uiax.server
    UIAX_BACKEND=mock python -m uiax.server
    UIAX_AUTH=none MCP_TRANSPORT=streamable-http python -m uiax.server

Key rotation
------------
    uiax-server --reset-key   # delete stored hash and generate a new key
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import functools
import os
import sys
import argparse
from typing import Any

from mcp.server.fastmcp import FastMCP

from server.uia_bridge import get_bridge, UIAError
from server.auth import require_auth, NoAuthProvider, set_auth_provider, BearerAuthMiddleware, init_auth, delete_key_file
from server.process_manager import (
    get_process_manager,
    WindowInfo,
)

# ---------------------------------------------------------------------------
# Bridge thread pool — keeps COM/pywinauto calls off the asyncio event loop.
#
# FastMCP calls sync tool functions *directly* in the event loop, which blocks
# the server entirely while a slow UIA query runs.  We patch
# FuncMetadata.call_fn_with_arg_validation to route sync tools through a
# single-threaded ThreadPoolExecutor.  max_workers=1 satisfies COM's
# Single-Threaded Apartment requirement: all pywinauto objects are created and
# used inside the same thread.
# ---------------------------------------------------------------------------

def _init_bridge_thread() -> None:
    """Initialise COM for the bridge worker thread (Windows only, no-op elsewhere)."""
    try:
        import comtypes  # noqa: PLC0415
        comtypes.CoInitialize()
    except Exception:
        pass


_bridge_executor: concurrent.futures.ThreadPoolExecutor | None = None


def _get_bridge_executor() -> concurrent.futures.ThreadPoolExecutor:
    global _bridge_executor
    if _bridge_executor is None:
        _bridge_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1,
            initializer=_init_bridge_thread,
            thread_name_prefix="uiax-bridge",
        )
    return _bridge_executor


# Patch FastMCP so sync tool functions run in the bridge thread pool instead
# of blocking the asyncio event loop.
from mcp.server.fastmcp.utilities.func_metadata import FuncMetadata as _FuncMetadata  # noqa: E402


async def _threaded_call_fn(
    self,
    fn,
    fn_is_async: bool,
    arguments_to_validate: dict,
    arguments_to_pass_directly: dict | None,
):
    """Run sync MCP tools in the bridge thread pool (avoids event-loop blocking)."""
    arguments_pre_parsed = self.pre_parse_json(arguments_to_validate)
    arguments_parsed_model = self.arg_model.model_validate(arguments_pre_parsed)
    arguments_parsed_dict = arguments_parsed_model.model_dump_one_level()
    arguments_parsed_dict |= arguments_to_pass_directly or {}
    if fn_is_async:
        return await fn(**arguments_parsed_dict)
    loop = asyncio.get_running_loop()
    wrapped = functools.partial(fn, **arguments_parsed_dict)
    return await loop.run_in_executor(_get_bridge_executor(), wrapped)


_FuncMetadata.call_fn_with_arg_validation = _threaded_call_fn

# ---------------------------------------------------------------------------
# Server instantiation
# ---------------------------------------------------------------------------

_transport = os.environ.get("MCP_TRANSPORT", "stdio").lower()  # stdio | sse | streamable-http
_host = os.environ.get("MCP_HOST", "0.0.0.0")
_port = int(os.environ.get("MCP_PORT", "8000"))

mcp = FastMCP(
    "uiax-automation",
    instructions=(
        "UI Automation MCP server (Linux AT-SPI2 / Windows UIA). "
        "Standard workflow:\n"
        "1. process_list — find the target application window\n"
        "2. select_window — attach to it by hwnd or window_title\n"
        "3. uia_find_all — get every named/interactive element as a flat list "
        "(ALWAYS do this before invoking; GTK4/Electron trees are too deep for "
        "uia_inspect at default depth)\n"
        "4. uia_invoke(name='Button Name') — click a button found in step 3\n"
        "5. uia_find_all(has_actions=False) — re-run to read display labels/values\n"
        "\n"
        "Key rules:\n"
        "- uia_invoke takes name='...' directly: uia_invoke(name='7') NOT "
        "uia_invoke(target={'by':'name','value':'7'})\n"
        "- NEVER use send_keys/type_text to click buttons visible in uia_find_all\n"
        "- send_keys is ONLY for keyboard shortcuts (Ctrl+S, Alt+F4, arrow keys)\n"
        "- type_text is ONLY for typing into text input fields"
    ),
    host=_host,
    port=_port,
)

# ---------------------------------------------------------------------------
# Bridge (lazy-initialised)
# ---------------------------------------------------------------------------

_bridge = None


def _get_bridge():
    global _bridge
    if _bridge is None:
        backend = (
            os.environ.get("UIAX_BACKEND", "")
            or os.environ.get("UIA_BACKEND", "real")
        ).lower()
        _bridge = get_bridge(backend)
    return _bridge


# ---------------------------------------------------------------------------
# Authentication helper
# ---------------------------------------------------------------------------


def _check_auth(api_key: str = "") -> dict[str, Any] | None:
    """
    Validate the API key.  Returns an error dict if auth fails, else None.
    """
    try:
        require_auth(api_key)
        return None
    except UIAError as exc:
        return {"ok": False, "error": str(exc), "code": exc.code}


# ---------------------------------------------------------------------------
# Helper: serialise WindowInfo
# ---------------------------------------------------------------------------


def _window_to_dict(w: WindowInfo) -> dict[str, Any]:
    d: dict[str, Any] = {
        "hwnd": w.hwnd,
        "hwnd_hex": hex(w.hwnd),
        "title": w.title,
        "class_name": w.class_name,
        "pid": w.pid,
        "process_name": w.process_name,
        "visible": w.visible,
        "rect": w.rect,
    }
    if w.dpi_scale is not None:
        d["dpi_scale"] = w.dpi_scale
        rect = w.rect
        if isinstance(rect, dict):
            lw = rect.get("right", 0) - rect.get("left", 0)
            lh = rect.get("bottom", 0) - rect.get("top", 0)
        elif rect and hasattr(rect, "__len__") and len(rect) == 4:
            lw = rect[2] - rect[0]
            lh = rect[3] - rect[1]
        else:
            lw = lh = 0
        d["logical_size"] = [lw, lh]
        d["physical_size"] = [
            round(lw * w.dpi_scale),
            round(lh * w.dpi_scale),
        ]
    return d


# ===================================================================
# Tool: process_list
# ===================================================================


@mcp.tool(
    name="process_list",
    description=(
        "List running processes and their top-level windows. "
        "Returns an array of window descriptors. Use this to discover "
        "available automation targets before calling select_window."
    ),
)
def process_list(
    api_key: str = "",
    visible_only: bool = True,
) -> dict[str, Any]:
    """
    Enumerate top-level windows.

    Parameters
    ----------
    api_key : str
        API key for authentication.
    visible_only : bool
        If True (default), only return visible windows.

    Returns
    -------
    dict
        ``{"ok": true, "windows": [...]}``
    """
    auth_err = _check_auth(api_key)
    if auth_err:
        return auth_err
    try:
        pm = get_process_manager()
        windows = pm.list_windows(visible_only=visible_only)
        return {
            "ok": True,
            "windows": [_window_to_dict(w) for w in windows],
            "count": len(windows),
        }
    except UIAError as exc:
        return {"ok": False, "error": str(exc), "code": exc.code}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "code": "UNEXPECTED_ERROR"}


# ===================================================================
# Tool: select_window
# ===================================================================


@mcp.tool(
    name="select_window",
    description=(
        "Attach to a specific window as the active automation target. "
        "Provide at least one criterion: pid, process_name, window_title, "
        "class_name, or hwnd.  Multiple criteria act as an AND filter. "
        "The selected window becomes the target for all subsequent UIA calls. "
        "On success, a 'context' field may be present listing the currently-focused element "
        "and any active toggle buttons (e.g. checked, pressed) — useful for detecting "
        "whether a mode like 'second function' (x²) is active before you start. "
        "Example: select_window(process_name='gnome-calculator') "
        "Example: select_window(window_title='Calculator')"
    ),
)
def select_window(
    api_key: str = "",
    pid: int | None = None,
    process_name: str | None = None,
    window_title: str | None = None,
    class_name: str | None = None,
    hwnd: int | None = None,
) -> dict[str, Any]:
    """
    Attach to a process/window.

    Returns
    -------
    dict
        ``{"ok": true, "window": {...}}`` on success.
    """
    auth_err = _check_auth(api_key)
    if auth_err:
        return auth_err
    try:
        pm = get_process_manager()
        win = pm.attach(
            pid=pid,
            process_name=process_name,
            window_title=window_title,
            class_name=class_name,
            hwnd=hwnd,
        )
        result: dict[str, Any] = {"ok": True, "window": _window_to_dict(win)}
        return result
    except UIAError as exc:
        return {"ok": False, "error": str(exc), "code": exc.code}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "code": "UNEXPECTED_ERROR"}


# ===================================================================
# Tool: uia_inspect
# ===================================================================


@mcp.tool(
    name="uia_inspect",
    description=(
        "Inspect the UI Automation element tree of the active target window. "
        "Pass name='ElementName' to inspect a specific element, or omit all "
        "parameters to get the root window at depth=3. "
        "NOTE: GTK4 and Electron apps nest widgets 10-15 levels deep, so the "
        "root tree at depth=3 will appear empty or show only structural panels. "
        "If the tree looks shallow or empty, use uia_find_all instead."
    ),
)
def uia_inspect(
    name: str = "",
    target: dict[str, Any] = {},  # noqa: B006
    depth: int = 3,
    api_key: str = "",
) -> dict[str, Any]:
    """
    Inspect a UI element in the target window.

    Parameters
    ----------
    name : str
        Shortcut: find an element by exact name and inspect it.
        Equivalent to target={"by": "name", "value": name}.
    target : dict
        Full selector dict (used when name is not set).  Supported keys:

        ``by``    – selector strategy: ``"name"``, ``"role"``, ``"name_substring"``,
                    ``"automation_id"``, ``"control_type"``, ``"class_name"``,
                    ``"path"``, ``"legacy_name"``, ``"legacy_role"``, ``"hwnd"``
        ``value`` – value for the chosen strategy
        ``depth`` – how many levels of children to expand (overridden by top-level depth param)
        ``index`` – zero-based index for multiple matches (default 0)

        Pass an empty dict ``{}`` to return the root window.
    depth : int
        How many levels of children to expand (default 3). Overrides target["depth"].
    """
    auth_err = _check_auth(api_key)
    if auth_err:
        return auth_err
    if name and not target:
        target = {"by": "name", "value": name}
    # Inject depth into target dict (top-level param takes precedence)
    target = {**target, "depth": depth} if target else {"depth": depth}
    try:
        bridge = _get_bridge()
        element = bridge.inspect(target)
        return {"ok": True, "element": element}
    except UIAError as exc:
        return {"ok": False, "error": str(exc), "code": exc.code}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "code": "UNEXPECTED_ERROR"}


# ===================================================================
# Tool: uia_find_all
# ===================================================================


@mcp.tool(
    name="uia_find_all",
    description=(
        "RECOMMENDED FIRST STEP after select_window. "
        "Returns a flat list of every named/interactive element in the window, "
        "regardless of how deeply nested it is. "
        "Essential for GTK4 and Electron apps where uia_inspect at a fixed depth "
        "returns an empty tree. "
        "Each entry has 'index', 'name', 'role', 'actions', and optionally 'states', "
        "'focused', 'text', 'value' fields. "
        "IMPORTANT: 'index' is the per-name ordinal — e.g. if two buttons are both "
        "named '×', the first has index=0 and the second index=1. "
        "Use index directly in uia_invoke: uia_invoke(target={'by':'name','value':'x','index':1}) "
        "selects the second element named 'x'. "
        "The 'states' list reveals toggle state (e.g. ['checked'] means active). "
        "ALWAYS check states after select_window — if a toggle button is 'checked' "
        "(e.g. x\u00b2 / second-function), operators like \u00d7 will be remapped to "
        "exponentiation. Deactivate it before calculating. "
        "Example: uia_find_all() lists all interactive elements. "
        "Example: uia_find_all(has_actions=false) includes display labels to read values. "
        "Example: uia_find_all(roles=['push button','toggle button']) shows only buttons. "
        "Filter by role with roles=['button'] or include display labels with "
        "has_actions=false to read the current value shown on screen. "
        "Use name_contains='save' to search by name (case-insensitive substring). "
        "Results are paginated: limit (default 50) and offset control paging. "
        "Response includes total, count, offset, has_more for navigation."
    ),
)
def uia_find_all(
    roles: list[str] = [],  # noqa: B006
    has_actions: bool = True,
    named_only: bool = True,
    target: dict[str, Any] = {},  # noqa: B006  # optional subtree root
    name_contains: str = "",
    limit: int = 50,
    offset: int = 0,
    api_key: str = "",
) -> dict[str, Any]:
    """
    Discover all interactive UI elements in the current window.

    Parameters
    ----------
    roles : list[str]
        Restrict results to these AT-SPI/UIA roles, e.g.
        ``["push button", "check box", "text"]``.  Empty list (default)
        returns all matching elements regardless of role.
    has_actions : bool
        When True (default) only elements with at least one invokable
        action (click, activate, …) are returned.
    named_only : bool
        When True (default) skip elements without a name.
    target : dict
        Optional selector for a subtree root — same format as uia_inspect.
        Omit (or pass ``{}``) to search the whole window.
    name_contains : str
        Case-insensitive substring filter on element names.  Only elements
        whose name contains this string are returned.  Empty string
        (default) disables the filter.
    limit : int
        Maximum number of elements to return per page (default 50).
    offset : int
        Number of matching elements to skip (default 0).  Use with *limit*
        to paginate through large result sets.

    Returns
    -------
    dict
        ``{"ok": true, "total": M, "count": N, "offset": 0,
        "has_more": false, "elements": [...]}``

        *total* is the full number of matching elements; *count* is how
        many are in this page; *has_more* indicates whether further pages
        exist.
    """
    auth_err = _check_auth(api_key)
    if auth_err:
        return auth_err
    try:
        bridge = _get_bridge()
        items = bridge.find_all({
            "roles": roles,
            "has_actions": has_actions,
            "named_only": named_only,
            "root": target or None,
        })
        # Server-side name search
        if name_contains:
            _q = name_contains.lower()
            items = [e for e in items if _q in e.get("name", "").lower()]
        total = len(items)
        # Pagination
        page = items[offset : offset + limit]
        return {
            "ok": True,
            "total": total,
            "count": len(page),
            "offset": offset,
            "has_more": offset + limit < total,
            "elements": page,
        }
    except UIAError as exc:
        return {"ok": False, "error": str(exc), "code": exc.code}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "code": "UNEXPECTED_ERROR"}


# ===================================================================
# Tool: uia_invoke
# ===================================================================


@mcp.tool(
    name="uia_invoke",
    description=(
        "Click or activate a UI element by name or HWND. "
        "Use name='Button Name' with the exact name from uia_find_all. "
        "Use hwnd='0x1234' (the hex hwnd from uia_find_all include_hwnd=True) for the fastest path. "
        "Example: uia_invoke(name='7') clicks the '7' button. "
        "Example: uia_invoke(hwnd='0x40164') clicks by window handle (fastest, no scan). "
        "Example: uia_invoke(name='=') presses equals and returns the result when read_after=true. "
        "Set read_after=true to read the focused element's text immediately after invoking "
        "(useful for getting calculator results, updated labels, etc.). "
        "IMPORTANT: When an app has history panels, lists, or other text that shares a name "
        "with an interactive button, always add role to the target to avoid hitting the wrong element. "
        "Example: uia_invoke(target={'by':'name','value':'8','role':'button'}) ensures the digit "
        "button is pressed, not a history label also named '8'. "
        "The 'role' from uia_find_all output can be passed directly in the target dict. "
        "To pick by ordinal, use index: target={'by':'name','value':'×','index':1} picks the second."
    ),
)
def uia_invoke(
    name: str = "",
    target: dict[str, Any] = {},  # noqa: B006
    hwnd: str = "",
    read_after: bool = False,
    api_key: str = "",
) -> dict[str, Any]:
    """
    Invoke a UI element (e.g. press a button).

    Parameters
    ----------
    name : str
        Shortcut: invoke the element with this exact name.
        Equivalent to target={"by": "name", "value": name}.
        Use names from uia_find_all output.
    target : dict
        Full selector dict (used when name is not set).
    hwnd : str
        Hex HWND string (e.g. '0x401f2') from uia_find_all include_hwnd=True.
        Fastest path — no element scan required. Takes precedence over name.
    read_after : bool
        When True, read the focused element's text after invoking and include
        it in the response as ``after_text`` / ``after_source``.  Useful after
        pressing = or Enter to capture the calculator result in one round-trip.
    """
    auth_err = _check_auth(api_key)
    if auth_err:
        return auth_err
    if hwnd and not target:
        target = {"hwnd": hwnd}
    if name and not target:
        target = {"by": "name", "value": name}
    if not target:
        return {"ok": False, "error": "Provide name='...' or target={...}", "code": "INVALID_ARGS"}
    try:
        bridge = _get_bridge()
        bridge.invoke(target)
        result: dict[str, Any] = {"ok": True}
        if read_after:
            try:
                after_text, after_source = bridge.get_text(None)
                result["after_text"] = after_text
                result["after_source"] = after_source
            except Exception:  # noqa: BLE001
                pass
        return result
    except UIAError as exc:
        return {"ok": False, "error": str(exc), "code": exc.code}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "code": "UNEXPECTED_ERROR"}


# ===================================================================
# Tool: uia_set_value
# ===================================================================


@mcp.tool(
    name="uia_set_value",
    description=(
        "Set the value of a UI Automation element in the target window. "
        "The element must support the Value pattern (e.g. text fields, "
        "date pickers, combo boxes)."
    ),
)
def uia_set_value(
    target: dict[str, Any],
    value: str,
    api_key: str = "",
) -> dict[str, Any]:
    """
    Set the value of a UI element.

    Parameters
    ----------
    target : dict
        Selector describing which element to change.
    value : str
        New value to assign.
    """
    auth_err = _check_auth(api_key)
    if auth_err:
        return auth_err
    try:
        bridge = _get_bridge()
        bridge.set_value(target, value)
        return {"ok": True}
    except UIAError as exc:
        return {"ok": False, "error": str(exc), "code": exc.code}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "code": "UNEXPECTED_ERROR"}


# ===================================================================
# Tool: uia_send_keys  (V1 name: send_keys)
# ===================================================================


_SEND_KEYS_NOTATION = (
    "Key sequence in pywinauto / SendKeys notation.  "
    "Use type_text instead for plain prose — send_keys is for shortcuts and navigation.  "
    "Notation reference: "
    "plain letters/digits are typed as-is; "
    "spaces are typed literally (no escaping needed); "
    "special keys use braces: {ENTER} {TAB} {ESC} {BACKSPACE} {DELETE} "
    "{HOME} {END} {UP} {DOWN} {LEFT} {RIGHT} {F1}-{F12} {PGUP} {PGDN}; "
    "modifier prefixes (applied to the NEXT key only): "
    "^ = Ctrl (e.g. ^s = Ctrl+S, ^a = Ctrl+A, ^z = Ctrl+Z), "
    "+ = Shift (e.g. +{F10} = Shift+F10), "
    "% = Alt (e.g. %{F4} = Alt+F4, %f = Alt+F to open File menu); "
    "repeat a key with {key N}: e.g. {TAB 3} = Tab x3; "
    "to type ~ ^ + % ( ) { } literally wrap in braces: {^} {+} {%} {~} "
    "{(} {)} {{} {}}.  "
    "Examples: '^s' saves, '^{HOME}' goes to start, '%f' opens File menu, "
    "'%{F4}' closes the window."
)


@mcp.tool(
    name="uia_send_keys",
    description=(
        "Send keyboard shortcuts or special-key sequences to the target window. "
        "For typing plain text content use the type_text tool instead. "
        + _SEND_KEYS_NOTATION
    ),
)
def uia_send_keys(
    keys: str,
    target: dict[str, Any] = {},  # noqa: B006
    api_key: str = "",
) -> dict[str, Any]:
    """
    Send keystrokes to the target window.

    Parameters
    ----------
    keys : str
        Key sequence in pywinauto / SendKeys notation.
    target : dict, optional
        Element selector to focus before sending keys.  Pass ``{}``
        to send to whatever is currently focused.
    """
    auth_err = _check_auth(api_key)
    if auth_err:
        return auth_err
    try:
        bridge = _get_bridge()
        bridge.send_keys(keys, target or None)
        return {"ok": True}
    except UIAError as exc:
        return {"ok": False, "error": str(exc), "code": exc.code}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "code": "UNEXPECTED_ERROR"}


# ===================================================================
# Tool: uia_legacy_invoke
# ===================================================================


@mcp.tool(
    name="uia_legacy_invoke",
    description=(
        "Invoke a UI element via LegacyIAccessiblePattern.DoDefaultAction (MSAA). "
        "Use this for owner-drawn controls that are invisible to the standard "
        "UIA InvokePattern.  Supports selectors: by=legacy_name, legacy_role, "
        "child_id, hwnd — as well as all standard UIA selectors."
    ),
)
def uia_legacy_invoke(
    target: dict[str, Any],
    api_key: str = "",
) -> dict[str, Any]:
    """
    Invoke a UI element via LegacyIAccessiblePattern.DoDefaultAction.

    Parameters
    ----------
    target : dict
        Selector.  Supports standard UIA and MSAA selectors.
    """
    auth_err = _check_auth(api_key)
    if auth_err:
        return auth_err
    try:
        bridge = _get_bridge()
        bridge.legacy_invoke(target)
        return {"ok": True}
    except UIAError as exc:
        return {"ok": False, "error": str(exc), "code": exc.code}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "code": "UNEXPECTED_ERROR"}


# ===================================================================
# Tool: uia_mouse_click
# ===================================================================


@mcp.tool(
    name="uia_mouse_click",
    description=(
        "Click at absolute screen coordinates. "
        "Use double=true for double-clicks.  Coordinates come from the 'rect' "
        "field returned by uia_inspect.  "
        "button: 'left' (default), 'right', or 'middle'.  "
        "force_sendinput: always dispatch via raw SendInput (no pywinauto "
        "SetCursorPos path).  Use when the target window filters out synthetic "
        "mouse events that set the cursor position first."
    ),
)
def uia_mouse_click(
    x: int,
    y: int,
    double: bool = False,
    button: str = "left",
    force_sendinput: bool = False,
    api_key: str = "",
) -> dict[str, Any]:
    """
    Click at absolute screen coordinates.

    Parameters
    ----------
    x, y : int
        Screen coordinates.
    double : bool
        True for double-click.
    button : str
        'left', 'right', or 'middle'.
    force_sendinput : bool
        Forward to the bridge's ``force_sendinput`` parameter (see bridge docs).
    """
    auth_err = _check_auth(api_key)
    if auth_err:
        return auth_err
    try:
        bridge = _get_bridge()
        bridge.mouse_click(x, y, double=double, button=button, force_sendinput=force_sendinput)
        return {"ok": True}
    except UIAError as exc:
        return {"ok": False, "error": str(exc), "code": exc.code}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "code": "UNEXPECTED_ERROR"}


# ===================================================================
# Tool: send_keys  (standalone keystroke injection)
# ===================================================================


@mcp.tool(
    name="send_keys",
    description=(
        "Inject keyboard shortcuts or special-key sequences via Windows SendInput "
        "(lower-level alternative to uia_send_keys — no UIA target required). "
        "For typing plain text content use the type_text tool instead. "
        + _SEND_KEYS_NOTATION
    ),
)
def send_keys_tool(
    keys: str,
    api_key: str = "",
) -> dict[str, Any]:
    """
    Send keystrokes via SendInput.

    Parameters
    ----------
    keys : str
        Key sequence in pywinauto / SendKeys notation.
    """
    auth_err = _check_auth(api_key)
    if auth_err:
        return auth_err
    try:
        bridge = _get_bridge()
        bridge.send_keys(keys)
        return {"ok": True}
    except UIAError as exc:
        return {"ok": False, "error": str(exc), "code": exc.code}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "code": "UNEXPECTED_ERROR"}


# ===================================================================
# Tool: type_text  (plain-text typing, no SendKeys encoding needed)
# ===================================================================


@mcp.tool(
    name="type_text",
    description=(
        "Type plain text into the focused window or element. "
        "All characters are typed literally — spaces, punctuation (!, @, #, etc.), "
        "and symbols require NO special encoding. "
        "Newlines (\\n) are sent as Enter key presses. "
        "Use this to type text content; use send_keys / uia_send_keys for "
        "keyboard shortcuts and navigation (Ctrl+S, Alt+F4, arrow keys, etc.)."
    ),
)
def type_text_tool(
    text: str,
    target: dict[str, Any] = {},  # noqa: B006
    api_key: str = "",
) -> dict[str, Any]:
    """
    Type plain text into the target window.

    Parameters
    ----------
    text : str
        The plain text to type.  All characters are sent literally.
    target : dict, optional
        Element selector to focus before typing.  Pass ``{}`` to send to
        whatever is currently focused.
    """
    auth_err = _check_auth(api_key)
    if auth_err:
        return auth_err
    try:
        bridge = _get_bridge()
        bridge.type_text(text, target or None)
        return {"ok": True}
    except UIAError as exc:
        return {"ok": False, "error": str(exc), "code": exc.code}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "code": "UNEXPECTED_ERROR"}


# ===================================================================
# Tool: mouse_click  (standalone mouse click)
# ===================================================================


@mcp.tool(
    name="mouse_click",
    description=(
        "Click at absolute screen coordinates via Windows SendInput API. "
        "This is a lower-level alternative to uia_mouse_click. "
        "button: 'left' (default), 'right', or 'middle'."
    ),
)
def mouse_click_tool(
    x: int,
    y: int,
    double: bool = False,
    button: str = "left",
    api_key: str = "",
) -> dict[str, Any]:
    """
    Click at absolute screen coordinates.
    """
    auth_err = _check_auth(api_key)
    if auth_err:
        return auth_err
    try:
        bridge = _get_bridge()
        bridge.mouse_click(x, y, double=double, button=button)
        return {"ok": True}
    except UIAError as exc:
        return {"ok": False, "error": str(exc), "code": exc.code}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "code": "UNEXPECTED_ERROR"}


# ===================================================================
# ===================================================================
# Tool: uia_read_display
# ===================================================================


@mcp.tool(
    name="uia_read_display",
    description=(
        "Read all visible text/values currently shown in the window without specifying a target. "
        "Returns display-like elements (labels, text areas, result fields) that have non-empty "
        "text or value content. Focused elements appear first. "
        "Use this after invoking buttons to see what changed on screen. "
        "Example: uia_read_display() after pressing = returns the calculator result. "
        "More convenient than uia_get_text when you don't know the element name."
    ),
)
def uia_read_display(
    api_key: str = "",
) -> dict[str, Any]:
    """
    Read all display text currently visible in the attached window.

    Returns elements that have non-empty ``text`` or ``value`` fields, sorted
    so that the focused element appears first.  Useful for reading a calculator
    result, status label, or any dynamic display area without knowing its name.

    Returns
    -------
    dict
        ``{"ok": true, "count": N, "elements": [{"name":..., "role":...,
        "text":..., "focused": true, ...}, ...]}``
    """
    auth_err = _check_auth(api_key)
    if auth_err:
        return auth_err
    try:
        bridge = _get_bridge()
        all_items = bridge.find_all({
            "has_actions": False,
            "named_only": False,
        })
        # Keep only elements with actual content
        candidates = [
            item for item in all_items
            if item.get("text") or item.get("value")
        ]

        def _rank(item: dict) -> int:
            if item.get("focused"):
                return 0
            role = item.get("role", "")
            if role in ("label", "text", "static text", "static", "entry", "editable text"):
                return 1
            return 2

        candidates.sort(key=_rank)
        return {"ok": True, "count": len(candidates), "elements": candidates[:10]}
    except UIAError as exc:
        return {"ok": False, "error": str(exc), "code": exc.code}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "code": "UNEXPECTED_ERROR"}


# ===================================================================
# Tool: uia_get_text
# ===================================================================


@mcp.tool(
    name="uia_get_text",
    description=(
        "Return the human-readable text of a UI element without dumping the full tree. "
        "When called with no target (or target={}), reads the currently-focused element "
        "— ideal for capturing a calculator result after pressing =. "
        "Example: uia_get_text() → {\"text\": \"5040\", \"source\": \"text\"} "
        "Example: uia_get_text(target={\"by\":\"name\",\"value\":\"Display\"}) reads a named element. "
        "Tries, in order: AT-SPI/UIA text content, value interface, then accessible name. "
        "Returns the first non-empty result together with a 'source' field "
        "that identifies which property it came from."
    ),
)
def uia_get_text(
    target: dict[str, Any] = {},  # noqa: B006
    api_key: str = "",
) -> dict[str, Any]:
    """
    Get the text of a UI element.

    Parameters
    ----------
    target : dict
        Selector describing which element to read.  Accepts all standard
        ``by`` strategies (``name``, ``automation_id``, ``control_type``,
        ``class_name``, ``path``, ``hwnd``, ``legacy_name``, ``legacy_role``,
        ``child_id``).  Pass ``{}`` or omit to read the currently-focused element.

    Returns
    -------
    dict
        ``{"ok": true, "text": "...", "source": "value"|"name"|"text"|
        "description"|"msaa_value"|"msaa_name"|"none"}``

        ``source`` tells the caller *which* accessibility property the text
        came from, which is helpful when parsing app-specific prefixes (e.g.
        Windows Calculator returns ``"Display is 56"`` from ``source="name"``
        rather than a bare ``"56"``).
    """
    auth_err = _check_auth(api_key)
    if auth_err:
        return auth_err
    try:
        bridge = _get_bridge()
        text, source = bridge.get_text(target or None)
        return {"ok": True, "text": text, "source": source}
    except UIAError as exc:
        return {"ok": False, "error": str(exc), "code": exc.code}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "code": "UNEXPECTED_ERROR"}


# ===================================================================
# Tool: wait_for_element  (#14)
# ===================================================================


@mcp.tool(
    name="wait_for_element",
    description=(
        "Poll the UI tree until a named element appears (or disappears). "
        "Returns as soon as the condition is met, or after timeout_ms with ok=false. "
        "Use present=true (default) to wait for an element to appear, "
        "present=false to wait for it to vanish (e.g. dismiss confirmation dialogs). "
        "Example: wait_for_element(name='Save') waits up to 5 s for a Save button. "
        "Example: wait_for_element(name='Please wait…', present=false) waits for a "
        "loading spinner to disappear."
    ),
)
def wait_for_element(
    name: str,
    present: bool = True,
    timeout_ms: int = 5000,
    poll_ms: int = 250,
    api_key: str = "",
) -> dict[str, Any]:
    """
    Poll until an element appears or disappears in the UI tree.

    Parameters
    ----------
    name : str
        Exact name of the element to wait for.
    present : bool
        ``True`` (default) — wait until the element is present.
        ``False`` — wait until the element is gone.
    timeout_ms : int
        Maximum wait time in milliseconds (default 5000).
    poll_ms : int
        Polling interval in milliseconds (default 250).
    """
    import time  # noqa: PLC0415

    auth_err = _check_auth(api_key)
    if auth_err:
        return auth_err
    try:
        bridge = _get_bridge()
        deadline = time.monotonic() + timeout_ms / 1000.0
        interval = poll_ms / 1000.0
        while True:
            items = bridge.find_all({"has_actions": False, "named_only": True})
            found = any(i.get("name") == name for i in items)
            if found == present:
                return {
                    "ok": True,
                    "found": found,
                    "name": name,
                    "elapsed_ms": round((time.monotonic() - (deadline - timeout_ms / 1000.0)) * 1000),
                }
            if time.monotonic() >= deadline:
                return {
                    "ok": False,
                    "found": found,
                    "name": name,
                    "error": f"Timed out after {timeout_ms} ms waiting for element "
                    f"{'to appear' if present else 'to disappear'}",
                    "code": "TIMEOUT",
                }
            time.sleep(interval)
    except UIAError as exc:
        return {"ok": False, "error": str(exc), "code": exc.code}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "code": "UNEXPECTED_ERROR"}


# ===================================================================
# Tool: uia_send_message  (#16)
# ===================================================================


@mcp.tool(
    name="uia_send_message",
    description=(
        "Send a raw Win32 message to a window handle. "
        "Use for controls that only respond to WM_LBUTTONDOWN/UP, BM_CLICK, "
        "or similar low-level messages (e.g. Quicken menu items that ignore "
        "InvokePattern).  "
        "Common message constants: BM_CLICK=0xF5, WM_CLOSE=0x0010, "
        "WM_COMMAND=0x0111, WM_LBUTTONDOWN=0x0201, WM_LBUTTONUP=0x0202.  "
        "sync=true (default) uses SendMessageW; sync=false uses PostMessageW. "
        "Only available on Windows; returns ok=false with code=NOT_SUPPORTED "
        "on other platforms."
    ),
)
def uia_send_message(
    hwnd: int,
    message: int,
    wparam: int = 0,
    lparam: int = 0,
    sync: bool = True,
    api_key: str = "",
) -> dict[str, Any]:
    """
    Send or post a Win32 message to a window handle.

    Parameters
    ----------
    hwnd : int
        Target window handle (e.g. from uia_inspect's ``hwnd`` field).
    message : int
        Windows message constant.
    wparam, lparam : int
        Message parameters (default 0).
    sync : bool
        True → SendMessageW (blocking).  False → PostMessageW (async).
    """
    auth_err = _check_auth(api_key)
    if auth_err:
        return auth_err
    try:
        bridge = _get_bridge()
        ret = bridge.send_win32_message(hwnd, message, wparam, lparam, sync=sync)
        return {"ok": True, "return_value": ret}
    except UIAError as exc:
        return {"ok": False, "error": str(exc), "code": exc.code}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "code": "UNEXPECTED_ERROR"}


# ===================================================================
# Tool: check_window_state  (#17)
# ===================================================================


@mcp.tool(
    name="check_window_state",
    description=(
        "Check whether the target window is currently enabled (not blocked by a "
        "modal overlay or dialog).  "
        "Returns enabled=true when the window is interactive.  "
        "When enabled=false, the response includes a blocking_windows list "
        "identifying the same-process windows that may be responsible "
        "(e.g. Quicken's QWinLightbox or an unsaved-changes dialog).  "
        "Use this before invoking menu items to detect modal states and avoid "
        "silent failures.  "
        "Only available on Windows."
    ),
)
def check_window_state(
    hwnd: int,
    api_key: str = "",
) -> dict[str, Any]:
    """
    Check whether *hwnd* is enabled and list any blocking overlays.

    Parameters
    ----------
    hwnd : int
        The handle of the window to check (from process_list or select_window).
    """
    auth_err = _check_auth(api_key)
    if auth_err:
        return auth_err
    try:
        bridge = _get_bridge()
        result = bridge.get_window_enabled_state(hwnd)
        return {"ok": True, **result}
    except UIAError as exc:
        return {"ok": False, "error": str(exc), "code": exc.code}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "code": "UNEXPECTED_ERROR"}


# ===================================================================
# Tool: dismiss_modal_overlay  (#17)
# ===================================================================


@mcp.tool(
    name="dismiss_modal_overlay",
    description=(
        "Close any modal overlay windows blocking the target window and "
        "re-enable it if it was left disabled.  "
        "Handles the pattern where a wizard or background dialog calls "
        "EnableWindow(parent, 0) and then fails to re-enable it after closing. "
        "Sends WM_CLOSE to each blocking same-process window, then calls "
        "EnableWindow(target, 1) if the window is still disabled.  "
        "Returns a list of dismissed windows and whether the target was "
        "explicitly re-enabled.  "
        "Only available on Windows."
    ),
)
def dismiss_modal_overlay_tool(
    hwnd: int,
    api_key: str = "",
) -> dict[str, Any]:
    """
    Dismiss modal overlays blocking *hwnd* and re-enable it.

    Parameters
    ----------
    hwnd : int
        The handle of the blocked parent window.
    """
    auth_err = _check_auth(api_key)
    if auth_err:
        return auth_err
    try:
        bridge = _get_bridge()
        result = bridge.dismiss_modal_overlay(hwnd)
        return {"ok": True, **result}
    except UIAError as exc:
        return {"ok": False, "error": str(exc), "code": exc.code}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "code": "UNEXPECTED_ERROR"}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="uia-x MCP server")
    parser.add_argument(
        "--reset-key",
        action="store_true",
        help="Delete the stored API key hash and generate a new key on startup.",
    )
    # parse_known_args so MCP-client-injected arguments don't cause a hard error.
    args, _ = parser.parse_known_args()

    backend = (
        os.environ.get("UIAX_BACKEND", "")
        or os.environ.get("UIA_BACKEND", "real")
    ).lower()
    auth_mode = (
        os.environ.get("UIAX_AUTH", "")
        or os.environ.get("UIA_X_AUTH", "apikey")
    ).lower()
    transport = os.environ.get("MCP_TRANSPORT", "stdio").lower()
    host = os.environ.get("MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("MCP_PORT", "8000"))

    # Early warning on Linux if AT-SPI2 bindings are not importable.
    if sys.platform.startswith("linux") and backend in ("real", "linux"):
        try:
            import pyatspi  # noqa: F401
        except ImportError:
            print(
                "[uiax] WARNING: python3-pyatspi not found.\n"
                "[uiax] Linux AT-SPI2 automation requires the system package:\n"
                "[uiax]   sudo apt install python3-pyatspi gir1.2-atspi-2.0 at-spi2-core\n"
                "[uiax] If running in a venv, either:\n"
                "[uiax]   a) Create the venv with --system-site-packages, or\n"
                "[uiax]   b) Use the system Python directly (python3 -m uiax.server)",
                file=sys.stderr,
            )

    if args.reset_key:
        deleted = delete_key_file()
        if deleted:
            print("[uia-x] Stored API key hash deleted; a new key will be generated.", file=sys.stdout)
        else:
            print("[uia-x] --reset-key: no stored key found; a new key will be generated.", file=sys.stdout)

    # Eagerly initialise the auth provider so the API key is printed to stdout
    # *before* the HTTP server emits its own log lines.
    init_auth()

    print(
        f"[uiax] starting server "
        f"(backend={backend}, auth={auth_mode}, transport={transport}"
        + (f", http://{host}:{port}" if transport != "stdio" else "")
        + ")",
        file=sys.stderr,
    )

    if transport == "stdio":
        # stdio mode — no HTTP, no Bearer middleware needed
        mcp.run(transport="stdio")
    else:
        # HTTP modes (sse / streamable-http) — wrap with Bearer auth
        import anyio

        async def _run_http() -> None:
            import uvicorn

            if transport == "sse":
                starlette_app = mcp.sse_app()
            else:
                starlette_app = mcp.streamable_http_app()

            # Wrap with Bearer auth middleware so clients can authenticate
            # via  Authorization: Bearer <api-key>  HTTP header.
            app = BearerAuthMiddleware(starlette_app)

            config = uvicorn.Config(app, host=host, port=port, log_level="info")
            server = uvicorn.Server(config)
            await server.serve()

        anyio.run(_run_http)


if __name__ == "__main__":
    main()
