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
# Server instantiation
# ---------------------------------------------------------------------------

_transport = os.environ.get("MCP_TRANSPORT", "stdio").lower()  # stdio | sse | streamable-http
_host = os.environ.get("MCP_HOST", "0.0.0.0")
_port = int(os.environ.get("MCP_PORT", "8000"))

mcp = FastMCP(
    "uiax-automation",
    instructions=(
        "UI Automation MCP server (Linux AT-SPI2 / Windows UIA). "
        "Workflow: "
        "(1) call process_list to find the target application window, "
        "(2) call select_window to attach to it, "
        "(3) call uia_find_all to get a FLAT LIST of every named/interactive "
        "element in the window — this is the most reliable way to discover "
        "buttons, inputs, and controls, especially in GTK4/Electron apps where "
        "uia_inspect at a fixed depth returns an empty or truncated tree, "
        "(4) call uia_invoke with {'by': 'name', 'value': '<element name>'} to "
        "click a button or activate a control discovered via uia_find_all. "
        "Only fall back to uia_inspect when you need structural/hierarchical data "
        "for a specific subtree. "
        "NEVER use send_keys or type_text to drive buttons that are visible in "
        "uia_find_all — always prefer uia_invoke on named elements. "
        "Use send_keys / uia_send_keys only for keyboard shortcuts and "
        "special-key sequences (Ctrl+S, Alt+F4, arrow keys, etc.). "
        "Use type_text only for typing into text input fields."
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
    return {
        "hwnd": w.hwnd,
        "hwnd_hex": hex(w.hwnd),
        "title": w.title,
        "class_name": w.class_name,
        "pid": w.pid,
        "process_name": w.process_name,
        "visible": w.visible,
        "rect": w.rect,
    }


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
        "The selected window becomes the target for all subsequent UIA calls."
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
        return {"ok": True, "window": _window_to_dict(win)}
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
        "Returns a JSON snapshot of the matched element and its children up to "
        "'depth' levels (default 3). "
        "NOTE: GTK4 and Electron apps nest widgets 10-15 levels deep, so the "
        "root tree at depth=3 will appear empty or show only structural panels. "
        "If the tree looks shallow or empty, switch to uia_find_all instead — "
        "it walks the full tree regardless of depth and returns all named elements "
        "as a flat list. Use uia_inspect only when you need hierarchical structure "
        "for a specific known subtree."
    ),
)
def uia_inspect(
    target: dict[str, Any] = {},  # noqa: B006
    api_key: str = "",
) -> dict[str, Any]:
    """
    Inspect a UI element in the target window.

    Parameters
    ----------
    target : dict
        Selector describing which element to inspect.  Supported keys:

        ``by``    – selector strategy: ``"name"``, ``"automation_id"``,
                    ``"control_type"``, ``"class_name"``, ``"path"``,
                    ``"legacy_name"``, ``"legacy_role"``, ``"child_id"``,
                    ``"hwnd"``
        ``value`` – value for the chosen strategy
        ``depth`` – how many levels of children to expand (default 3)
        ``index`` – zero-based index for multiple matches (default 0)

        Pass an empty dict ``{}`` to return the root window.

    Returns
    -------
    dict
        ``{"ok": true, "element": {...}}`` on success or
        ``{"ok": false, "error": "...", "code": "..."}`` on failure.
    """
    auth_err = _check_auth(api_key)
    if auth_err:
        return auth_err
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
        "Each entry has 'name', 'role', and 'actions' fields. "
        "Use the 'name' values directly with uia_invoke to click buttons. "
        "Filter by role with roles=['button'] or include display labels with "
        "has_actions=false to read the current value shown on screen."
    ),
)
def uia_find_all(
    roles: list[str] = [],  # noqa: B006
    has_actions: bool = True,
    named_only: bool = True,
    target: dict[str, Any] = {},  # noqa: B006  # optional subtree root
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

    Returns
    -------
    dict
        ``{"ok": true, "count": N, "elements": [{"name": ..., "role": ...,
        "actions": [...], "text": ..., "value": ...}, ...]}``
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
        return {"ok": True, "count": len(items), "elements": items}
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
        "Invoke (click / activate) a UI Automation element in the target window. "
        "The element must support the Invoke or Toggle pattern."
    ),
)
def uia_invoke(
    target: dict[str, Any],
    api_key: str = "",
) -> dict[str, Any]:
    """
    Invoke a UI element (e.g. press a button).

    Parameters
    ----------
    target : dict
        Selector describing which element to invoke.
    """
    auth_err = _check_auth(api_key)
    if auth_err:
        return auth_err
    try:
        bridge = _get_bridge()
        bridge.invoke(target)
        return {"ok": True}
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
        "button: 'left' (default), 'right', or 'middle'."
    ),
)
def uia_mouse_click(
    x: int,
    y: int,
    double: bool = False,
    button: str = "left",
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
# Tool: uia_get_text
# ===================================================================


@mcp.tool(
    name="uia_get_text",
    description=(
        "Return the human-readable text of a single UI element without "
        "dumping the full tree.  "
        "Tries, in order: the UIA/AXAPI/AT-SPI value (ValuePattern / "
        "AXValue / Value interface), then the accessible name, then "
        "platform-specific text content.  "
        "Returns the first non-empty result together with a 'source' field "
        "that identifies which property it came from.  "
        "Useful for reading display labels such as a calculator result, "
        "status bar text, or the current value of any control."
    ),
)
def uia_get_text(
    target: dict[str, Any],
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
        ``child_id``).  Pass ``{}`` to read the root window.

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
        text, source = bridge.get_text(target)
        return {"ok": True, "text": text, "source": source}
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
