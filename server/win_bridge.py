"""
Windows UI Automation bridge via pywinauto.

V2: generalised – attaches to whatever window is selected by the process
manager rather than hard-coding Quicken.  Falls back gracefully with
structured errors when the target is not running or a targeted element
cannot be found.

MSAA / LegacyIAccessible support
---------------------------------
All elements returned by ``_element_to_dict()`` include an optional ``msaa``
sub-dict populated from LegacyIAccessiblePattern when available.  The selector
engine supports four additional *by* strategies:

  * legacy_name  – matches MSAA accName
  * legacy_role  – matches MSAA role constant (int or string)
  * child_id     – CHILDID value reported by LegacyIAccessiblePattern
  * hwnd         – Windows HWND (hex string or int accepted)
"""

from __future__ import annotations

from typing import Any

from server.uia_bridge import (
    UIABridge,
    ElementNotFoundError,
    PatternNotSupportedError,
    TargetNotFoundError,
    UIAError,
)

_PYWINAUTO_IMPORT_ERROR: str | None = None
try:
    from pywinauto.controls.uiawrapper import UIAWrapper

    _PYWINAUTO_AVAILABLE = True
except Exception as _e:  # noqa: BLE001
    _PYWINAUTO_AVAILABLE = False
    _PYWINAUTO_IMPORT_ERROR = f"{type(_e).__name__}: {_e}"

_DEPTH_DEFAULT = 3

_STATE_SELECTED = 0x0002
_STATE_FOCUSED = 0x0004
_STATE_CHECKED = 0x0010

_ROLE_LABEL: dict[int, str] = {
    0x01: "title bar",
    0x02: "menu bar",
    0x04: "scroll bar",
    0x09: "window",
    0x0A: "client",
    0x0C: "menu item",
    0x15: "pane",
    0x16: "tool bar",
    0x17: "status bar",
    0x21: "list",
    0x22: "list item",
    0x23: "outline",
    0x24: "outline item",
    0x25: "page tab",
    0x26: "combo box",
    0x29: "text",
    0x2A: "editable text",
    0x2B: "push button",
    0x2C: "check button",
    0x2D: "radio button",
    0x36: "data item",
}

_MSAA_STRATEGIES = {"legacy_name", "legacy_role", "child_id", "hwnd"}

# All valid 'by' strategy names — also accepted as flat shorthand keys.
_SELECTOR_STRATEGIES = {
    "automation_id", "name", "control_type", "class_name", "path",
} | _MSAA_STRATEGIES

# Keys that carry plumbing metadata, not selector values.
_META_KEYS = {"by", "value", "index", "depth", "role"}

# Windows control types (friendly_class_name()) that represent interactive controls.
_WIN_INTERACTIVE_ROLES = frozenset({
    "button", "checkbox", "radiobutton", "combobox", "listitem",
    "menuitem", "edit", "richedit", "richedit20", "treeitem",
    "tabitem", "hyperlink", "slider", "spinner", "toolbar",
    "calendar", "timepicker",
})

# MSAA ROLE_SYSTEM_PUSHBUTTON = 43 (0x2B). Controls with this MSAA role
# should match a roles=["button"] filter even when their UIA ControlType is
# Pane or Custom (e.g. Quicken's QC_button class).
_MSAA_PUSHBUTTON_ROLE = 0x2B
_BUTTON_ROLE_ALIASES = frozenset({
    "button", "push button", "pushbutton", "toolbarbutton",
})

# UIA control types for interactive elements.  Querying by individual control
# type creates a COM PropertyCondition, so the UIA provider filters results
# server-side — vastly faster than root.descendants() with no condition.
_INTERACTIVE_CONTROL_TYPES: tuple[str, ...] = (
    "Button", "MenuItem", "CheckBox", "RadioButton",
    "Edit", "ComboBox", "ListItem", "TreeItem",
    "Tab", "TabItem", "Hyperlink", "Slider", "Spinner",
    "SplitButton", "Menu", "ToolBar",
)
_DISPLAY_CONTROL_TYPES: tuple[str, ...] = (
    "Text", "Image", "Document", "DataItem",
    "Header", "HeaderItem", "Custom",
)


def _fetch_typed_descendants(root, include_display: bool = False):
    """Return all interactive (and optionally display) descendants using
    per-type COM property conditions instead of a single unfiltered FindAll.

    Each call to root.descendants(control_type=ct) issues a
    FindAll(TreeScope.Subtree, PropertyCondition(ControlType, ct)) to the UIA
    provider, which is O(matching_elements) not O(all_elements).
    """
    types = _INTERACTIVE_CONTROL_TYPES
    if include_display:
        types = types + _DISPLAY_CONTROL_TYPES
    results: list = []
    for ct in types:
        try:
            results.extend(root.descendants(control_type=ct))
        except Exception:
            pass
    return results


# ---------------------------------------------------------------------------
# Win32-native fast element enumeration (bypasses UIA COM layer entirely)
# ---------------------------------------------------------------------------

# Maps Win32 class names (lowercase) to UIA-like role strings.
_WIN32_CLASS_TO_ROLE: dict[str, str] = {
    # Standard Win32 controls
    "button": "button",
    "edit": "edit",
    "static": "text",
    "listbox": "list",
    "combobox": "combobox",
    "comboboxex32": "combobox",
    "scrollbar": "scrollbar",
    "syslistview32": "list",
    "listview": "list",
    "systreeview32": "tree",
    "treeview": "tree",
    "systabcontrol32": "tab",
    "tabcontrol": "tab",
    "toolbarwindow32": "toolbar",
    "rebar": "toolbar",
    "msctls_statusbar32": "statusbar",
    "msctls_trackbar32": "slider",
    "msctls_updown32": "spinner",
    "msctls_progress32": "progressbar",
    "richedit20w": "edit",
    "richedit20a": "edit",
    "richedit50w": "edit",
    "rich edit": "edit",
    "sysmonthcal32": "calendar",
    "sysdatetimepick32": "timepicker",
    "tooltips_class32": "tooltip",
    "sysipaddress32": "edit",
    "sysanimate32": "image",
    "syspager": "toolbar",
    "header": "header",
    "sysheader32": "header",
    # Quicken-specific custom window classes (observed via EnumChildWindows)
    "qc_button": "button",
    "qwcombobox": "combobox",
    "qwpanel": "pane",
    "qwiconDisplay".lower(): "image",
    "qw_bag_toolbar": "toolbar",
    "qw_main_toolbar": "toolbar",
    "qwmenubar": "menubar",
    "qwlistbox": "list",
    "qwedit": "edit",
    # Quicken transaction register classes
    "qredit": "edit",                          # register entry field (date, payee, amount)
    "qwclass_transactionlist": "list",         # main transaction grid
    "qwclass_txtoolbar": "toolbar",            # transaction toolbar (Save, More actions, Split)
    "qwscrollbar": "scrollbar",               # Quicken custom scrollbar
    "qwinchild": "pane",                       # generic Quicken child container
    "qwnavbtntray": "toolbar",                 # account bar nav tray
    "qwacctbarholder": "pane",                 # account bar holder
    "qwnavigator": "pane",                     # left sidebar navigator
    "qsidebar": "pane",                        # sidebar
    "qwmdi": "pane",                           # MDI content pane
    "mdifr": "pane",                           # MDI frame
}

# Win32 class names that have interactive actions (lowercase).
_WIN32_INTERACTIVE_CLASSES: frozenset[str] = frozenset({
    # Standard Win32
    "button", "edit", "listbox", "combobox", "comboboxex32",
    "syslistview32", "listview", "systreeview32", "treeview",
    "systabcontrol32", "tabcontrol", "toolbarwindow32",
    "msctls_trackbar32", "msctls_updown32",
    "richedit20w", "richedit20a", "richedit50w", "rich edit",
    "sysmonthcal32", "sysdatetimepick32", "sysipaddress32",
    # Quicken custom
    "qc_button", "qwcombobox", "qwlistbox", "qwedit", "qwmenubar",
    # Quicken transaction register
    "qredit", "qwclass_transactionlist", "qwcombobox",
})

# GWL_STYLE / WS_TABSTOP: controls with this style are keyboard-navigable.
_WS_TABSTOP = 0x00010000
_GWL_STYLE = -16


def _win32_fast_find_all(
    hwnd: int,
    named_only: bool = True,
    must_have_actions: bool = True,
    roles_filter: list[str] | None = None,
    limit: int = 0,
) -> list[dict[str, Any]]:
    """Enumerate all child windows using Win32 EnumChildWindows.

    This bypasses the UIA COM layer entirely, making it ~100× faster than
    ``root.descendants(control_type=X)`` for legacy Win32 apps like Quicken
    whose UIA provider is bridged through MSAA (slow COM round-trips).

    Returns dicts compatible with the ``find_all`` response schema, including
    ``hwnd`` / ``hwnd_hex`` fields so callers can later invoke elements by
    handle rather than re-scanning the tree.
    """
    import ctypes  # noqa: PLC0415
    import ctypes.wintypes  # noqa: PLC0415

    user32 = ctypes.windll.user32
    _roles_set = set(roles_filter) if roles_filter else None

    results: list[dict[str, Any]] = []
    name_counts: dict[str, int] = {}

    # WM_GETTEXT with abort-if-hung timeout avoids blocking on slow/unresponsive
    # child windows (e.g. Quicken custom controls with busy message pumps).
    _WM_GETTEXT = 0x000D
    _SMTO_ABORTIFHUNG = 0x0002
    _TEXT_BUFSIZE = 512

    EnumChildProc = ctypes.WINFUNCTYPE(  # noqa: N806
        ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
    )

    # Pre-compute: does the roles filter include any "button" aliases?
    _want_buttons = _roles_set is not None and any(
        r in _BUTTON_ROLE_ALIASES for r in _roles_set
    )

    def _enum_cb(child_hwnd: int, _: int) -> bool:  # noqa: ANN001
        try:
            if not user32.IsWindowVisible(child_hwnd):
                return True

            # --- class name (kernel data, no message sent) ---
            cls_buf = ctypes.create_unicode_buffer(128)
            user32.GetClassNameW(child_hwnd, cls_buf, 128)
            cls = cls_buf.value.lower()

            role = _WIN32_CLASS_TO_ROLE.get(cls, "custom")

            # --- role filter (early exit before any SendMessage calls) ---
            if _roles_set and role not in _roles_set:
                # "button" aliases also match qc_button and similar custom buttons
                if not (_want_buttons and role == "button"):
                    return True

            # --- window text via SendMessageTimeout (non-blocking, 50 ms cap) ---
            tbuf = ctypes.create_unicode_buffer(_TEXT_BUFSIZE)
            msg_result = ctypes.c_size_t(0)
            ret = user32.SendMessageTimeoutW(
                child_hwnd,
                _WM_GETTEXT,
                _TEXT_BUFSIZE,
                tbuf,
                _SMTO_ABORTIFHUNG,
                50,
                ctypes.byref(msg_result),
            )
            text = tbuf.value if (ret and msg_result.value > 0) else ""

            if named_only and not text:
                return True

            # --- bounding rect (kernel data, no message sent) ---
            rc = ctypes.wintypes.RECT()
            user32.GetWindowRect(child_hwnd, ctypes.byref(rc))
            if rc.right - rc.left <= 0 or rc.bottom - rc.top <= 0:
                return True
            rect = {"left": rc.left, "top": rc.top, "right": rc.right, "bottom": rc.bottom}

            enabled = bool(user32.IsWindowEnabled(child_hwnd))

            # --- interactivity: class membership OR WS_TABSTOP style ---
            is_interactive = cls in _WIN32_INTERACTIVE_CLASSES
            if not is_interactive:
                style = user32.GetWindowLongW(child_hwnd, _GWL_STYLE)
                is_interactive = bool(style & _WS_TABSTOP)

            # --- derive actions from class ---
            actions: list[str] = []
            if is_interactive:
                if cls in ("button", "qc_button"):
                    actions = ["click"]
                elif cls in ("edit", "richedit20w", "richedit20a", "richedit50w",
                             "rich edit", "sysipaddress32", "qwedit", "qredit"):
                    actions = ["type"]
                elif cls in ("listbox", "combobox", "comboboxex32", "qwcombobox",
                             "qwlistbox", "syslistview32", "listview",
                             "systreeview32", "treeview",
                             "systabcontrol32", "tabcontrol"):
                    actions = ["select"]
                elif cls in ("qwmenubar",):
                    actions = ["click"]
                else:
                    actions = ["click"]

            if must_have_actions and not actions:
                return True

            per_name_idx = name_counts.get(text, 0)
            name_counts[text] = per_name_idx + 1

            d: dict[str, Any] = {
                "index": per_name_idx,
                "name": text,
                "role": role,
                "class_name": cls,
                "hwnd": child_hwnd,
                "hwnd_hex": hex(child_hwnd),
                "enabled": enabled,
                "rect": rect,
                "actions": actions,
            }
            if text:
                d["text"] = text
            results.append(d)
            # Honour limit: returning False stops EnumChildWindows early
            if limit > 0 and len(results) >= limit:
                return False
        except Exception:  # noqa: BLE001
            pass
        return True

    cb = EnumChildProc(_enum_cb)
    user32.EnumChildWindows(hwnd, cb, 0)
    return results


def _win32_element_from_hwnd(hwnd: int):
    """Wrap a raw Win32 HWND as a pywinauto UIAWrapper without scanning descendants.

    Uses pywinauto's internal IUIA singleton and UIAElementInfo so no desktop
    enumeration is needed — this is O(1).
    """
    try:
        import comtypes  # noqa: PLC0415
        comtypes.CoInitialize()
        from pywinauto.uia_defines import IUIA  # noqa: PLC0415
        from pywinauto.controls.uiawrapper import UIAWrapper  # noqa: PLC0415
        from pywinauto.uia_element_info import UIAElementInfo  # noqa: PLC0415

        raw_elem = IUIA().iuia.ElementFromHandle(hwnd)
        return UIAWrapper(UIAElementInfo(raw_elem))
    except Exception:  # noqa: BLE001
        return None


def _win32_inspect_tree(hwnd: int, depth: int) -> dict[str, Any]:
    """Build an element tree using pure Win32 APIs (no COM/MSAA).

    ~100× faster than UIA-based ``_element_to_dict`` for legacy Win32 apps
    like Quicken whose UIA tree is bridged through slow MSAA.  Children are
    enumerated via ``GetWindow(GW_CHILD/GW_HWNDNEXT)`` and text is read with
    ``SendMessageTimeoutW`` (50 ms cap) to avoid blocking on busy controls.
    """
    import ctypes  # noqa: PLC0415
    import ctypes.wintypes  # noqa: PLC0415

    user32 = ctypes.windll.user32
    _GW_CHILD = 5
    _GW_HWNDNEXT = 2
    _WM_GETTEXT = 0x000D
    _SMTO_ABORTIFHUNG = 0x0002

    cls_buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, cls_buf, 256)
    cls = cls_buf.value
    cls_lower = cls.lower()

    txt_buf = ctypes.create_unicode_buffer(512)
    result_len = ctypes.c_ulong(0)
    user32.SendMessageTimeoutW(
        hwnd, _WM_GETTEXT, 512, txt_buf,
        _SMTO_ABORTIFHUNG, 50, ctypes.byref(result_len),
    )
    name = txt_buf.value

    rc = ctypes.wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rc))

    is_enabled = bool(user32.IsWindowEnabled(hwnd))
    role = _WIN32_CLASS_TO_ROLE.get(cls, _WIN32_CLASS_TO_ROLE.get(cls_lower, cls or "custom"))

    actions: list[str] = []
    if cls_lower in _WIN32_INTERACTIVE_CLASSES:
        if cls_lower in ("button", "qc_button", "toolbarwindow32", "qwmenubar"):
            actions = ["click"]
        elif cls_lower in ("edit", "richedit20w", "richedit20a", "richedit50w",
                           "rich edit", "sysipaddress32", "qwedit"):
            actions = ["type"]
        elif cls_lower in ("combobox", "comboboxex32", "qwcombobox", "listbox",
                           "qwlistbox", "syslistview32", "listview",
                           "systreeview32", "treeview", "systabcontrol32", "tabcontrol"):
            actions = ["select"]
        else:
            actions = ["click"]

    node: dict[str, Any] = {
        "name": name,
        "control_type": role,
        "class_name": cls,
        "hwnd": hwnd,
        "hwnd_hex": hex(hwnd),
        "enabled": is_enabled,
        "rect": {"left": rc.left, "top": rc.top, "right": rc.right, "bottom": rc.bottom},
        "children": [],
    }
    if actions:
        node["actions"] = actions

    if depth > 0:
        child = user32.GetWindow(hwnd, _GW_CHILD)
        while child:
            try:
                node["children"].append(_win32_inspect_tree(child, depth - 1))
            except Exception:  # noqa: BLE001
                pass
            child = user32.GetWindow(child, _GW_HWNDNEXT)

    return node

# ---------------------------------------------------------------------------
# SendKeys helpers
# ---------------------------------------------------------------------------

# Characters that have special meaning in pywinauto SendKeys notation and must
# be escaped with braces when the caller wants them typed literally.
_SENDKEYS_ESCAPE = str.maketrans({
    "~": "{~}",   # Enter
    "^": "{^}",   # Ctrl modifier
    "+": "{+}",   # Shift modifier
    "%": "{%}",   # Alt modifier
    "(": "{(}",
    ")": "{)}",
    "{": "{{}",
    "}": "{}}",
})


def _escape_text_for_send_keys(text: str) -> str:
    """Escape all pywinauto SendKeys special characters so *text* is typed literally."""
    return text.translate(_SENDKEYS_ESCAPE)


def _require_pywinauto() -> None:
    if not _PYWINAUTO_AVAILABLE:
        detail = f" ({_PYWINAUTO_IMPORT_ERROR})" if _PYWINAUTO_IMPORT_ERROR else ""
        raise UIAError(
            f"pywinauto is not installed or this is not a Windows system.{detail}",
            code="PYWINAUTO_UNAVAILABLE",
        )


# ---------------------------------------------------------------------------
# Attach to the currently selected window via ProcessManager
# ---------------------------------------------------------------------------


def _attach_target():
    """
    Return the pywinauto wrapper for the currently-attached window.

    Uses the ``ProcessManager`` singleton to determine the target HWND.
    Connects directly via HWND to avoid slow full-desktop enumeration.
    """
    _require_pywinauto()
    from server.process_manager import get_process_manager  # noqa: PLC0415
    import pywinauto  # noqa: PLC0415

    # Ensure COM is initialised for this thread (safe to call multiple times).
    try:
        import comtypes  # noqa: PLC0415
        comtypes.CoInitialize()
    except Exception:
        pass

    pm = get_process_manager()
    attached = pm.attached
    if attached is None:
        raise TargetNotFoundError("Use select_window to attach to a target first.")

    # Connect directly by handle — avoids enumerating every window on the desktop.
    try:
        app = pywinauto.Application(backend="uia").connect(handle=attached.hwnd)
        return app.window(handle=attached.hwnd)
    except Exception as exc:
        raise TargetNotFoundError(
            f"Window hwnd={hex(attached.hwnd)} title={attached.title!r} "
            f"is no longer available: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# MSAA helpers
# ---------------------------------------------------------------------------


def _msaa_props(element) -> dict[str, Any]:
    try:
        raw = element.legacy_properties()
    except Exception:
        return {}
    if not raw:
        return {}
    role = raw.get("Role", 0) or 0
    state = raw.get("State", 0) or 0
    child_id = raw.get("ChildId", 0) or 0
    msaa: dict[str, Any] = {}
    name = raw.get("Name") or ""
    if name:
        msaa["name"] = name
    if role:
        msaa["role"] = role
        msaa["role_text"] = _ROLE_LABEL.get(role, f"role_0x{role:02x}")
    description = raw.get("Description") or ""
    if description:
        msaa["description"] = description
    default_action = raw.get("DefaultAction") or ""
    if default_action:
        msaa["default_action"] = default_action
    legacy_value = raw.get("Value") or ""
    if legacy_value:
        msaa["value"] = legacy_value
    if state:
        msaa["state"] = state
        msaa["selected"] = bool(state & _STATE_SELECTED)
        msaa["focused"] = bool(state & _STATE_FOCUSED)
        msaa["checked"] = bool(state & _STATE_CHECKED)
    if child_id:
        msaa["child_id"] = child_id
    try:
        hwnd = element.handle
        if hwnd:
            msaa["hwnd"] = hwnd
    except Exception:
        pass
    return msaa


def _element_to_dict(element, depth: int) -> dict[str, Any]:
    ctrl_type = ""
    try:
        ctrl_type = element.friendly_class_name()
    except Exception:
        pass
    name = ""
    try:
        name = element.window_text()
    except Exception:
        pass
    auto_id = ""
    try:
        auto_id = element.automation_id()
    except Exception:
        pass
    class_name = ""
    try:
        class_name = element.class_name()
    except Exception:
        pass
    rect: dict[str, int] = {}
    try:
        r = element.rectangle()
        rect = {"left": r.left, "top": r.top, "right": r.right, "bottom": r.bottom}
    except Exception:
        pass
    enabled = None
    try:
        enabled = element.is_enabled()
    except Exception:
        pass
    patterns: list[str] = []
    try:
        patterns = [p for p in element.iface_patterns if p is not None]
    except Exception:
        pass
    value: str | None = None
    try:
        value = element.iface_value.CurrentValue
    except Exception:
        pass
    if value is None:
        try:
            value = element.get_value()
        except Exception:
            pass
    try:
        if element.iface_selection_item.CurrentIsSelected:
            if "SelectionItemPattern" not in patterns:
                patterns.append("SelectionItemPattern")
    except Exception:
        pass
    node: dict[str, Any] = {
        "name": name,
        "control_type": ctrl_type,
        "automation_id": auto_id,
        "class_name": class_name,
        "enabled": enabled,
        "rect": rect,
        "patterns": patterns,
        "children": [],
    }
    if value is not None:
        node["value"] = value
    msaa = _msaa_props(element)
    if msaa:
        node["msaa"] = msaa
        if msaa.get("default_action") and "LegacyIAccessiblePattern" not in patterns:
            patterns.append("LegacyIAccessiblePattern")
        if msaa.get("selected") and "SelectionItemPattern" not in patterns:
            patterns.append("SelectionItemPattern")
    if depth > 0:
        try:
            for child in element.children():
                node["children"].append(_element_to_dict(child, depth - 1))
        except Exception:
            pass
    return node


# ---------------------------------------------------------------------------
# MSAA matching
# ---------------------------------------------------------------------------


def _matches_msaa(element, by: str, value: str) -> bool:
    try:
        raw = element.legacy_properties() or {}
    except Exception:
        return False
    if by == "legacy_name":
        return (raw.get("Name") or "") == value
    if by == "legacy_role":
        role = raw.get("Role") or 0
        try:
            return int(role) == int(value)
        except (TypeError, ValueError):
            return False
    if by == "child_id":
        cid = raw.get("ChildId") or 0
        try:
            return int(cid) == int(value)
        except (TypeError, ValueError):
            return False
    if by == "hwnd":
        try:
            hwnd = element.handle
            target_hwnd = (
                int(value, 16)
                if isinstance(value, str) and value.startswith("0x")
                else int(value)
            )
            return int(hwnd) == target_hwnd
        except Exception:
            return False
    return False


# ---------------------------------------------------------------------------
# Element finder
# ---------------------------------------------------------------------------


def _find_element(root, target: dict[str, Any]):
    if not target:
        return root

    # ------------------------------------------------------------------
    # Normalise shorthand form  {"automation_id": "okBtn"}  into
    # the canonical form        {"by": "automation_id", "value": "okBtn"}
    # and reject unknown keys so a typo never silently matches the wrong
    # element (e.g. the old default 'name=""' fallback could invoke the
    # Minimize button).
    # ------------------------------------------------------------------
    if "by" not in target:
        extra_keys = {k for k in target if k not in _META_KEYS}
        unknown = extra_keys - _SELECTOR_STRATEGIES
        if unknown:
            raise UIAError(
                f"Unrecognised target key(s): {sorted(unknown)!r}. "
                "Use {\"by\": \"<strategy>\", \"value\": \"<val>\"} "
                "or a shorthand like {\"automation_id\": \"myButton\"}.",
                code="INVALID_SELECTOR",
            )
        known = extra_keys & _SELECTOR_STRATEGIES
        if len(known) > 1:
            raise UIAError(
                f"Ambiguous shorthand: multiple selector keys {sorted(known)!r}. "
                "Use the explicit {\"by\": \"<strategy>\", \"value\": \"<val>\"} form.",
                code="INVALID_SELECTOR",
            )
        if known:
            shorthand_by = next(iter(known))
            target = {
                "by": shorthand_by,
                "value": str(target[shorthand_by]),
                "index": target.get("index", 0),
            }

    by = target.get("by", "name")
    value = target.get("value", "")
    index = int(target.get("index", 0))

    if by == "path":
        parts = [p.strip() for p in value.split("/") if p.strip()]
        current = root
        for part in parts:
            children = current.children(title=part)
            if not children:
                raise ElementNotFoundError(target)
            current = children[0]
        return current

    if by == "automation_id":
        # Fast path: create a COM property condition for automation_id.
        try:
            matches = root.descendants(auto_id=value)
        except Exception:
            matches = []
        if not matches:
            # Fallback: iterate typed descendants (auto_id pywinauto kwarg may vary)
            all_desc = _fetch_typed_descendants(root, include_display=True)
            matches = []
            for elem in all_desc:
                try:
                    if elem.automation_id() == value:
                        matches.append(elem)
                except Exception:
                    pass
        if not matches:
            raise ElementNotFoundError(target)
        try:
            return matches[index]
        except IndexError:
            raise ElementNotFoundError(target) from None

    if by == "hwnd":
        # Fast path: wrap the HWND directly without scanning descendants (O(1)).
        _require_pywinauto()
        try:
            target_hwnd = (
                int(value, 16)
                if isinstance(value, str) and value.startswith("0x")
                else int(value)
            )
        except (ValueError, TypeError) as exc:
            raise UIAError(f"Invalid hwnd value: {value!r}", code="INVALID_SELECTOR") from exc
        elem = _win32_element_from_hwnd(target_hwnd)
        if elem is not None:
            return elem
        # Fallback: scan descendants
        all_desc = _fetch_typed_descendants(root, include_display=True)
        for candidate in all_desc:
            if _matches_msaa(candidate, "hwnd", value):
                return candidate
        raise ElementNotFoundError(target)

    if by in _MSAA_STRATEGIES:
        # MSAA attributes cannot be filtered at the COM level; iterate typed descendants.
        all_desc = _fetch_typed_descendants(root, include_display=True)
        matches = []
        for elem in all_desc:
            if _matches_msaa(elem, by, value):
                matches.append(elem)
        if not matches:
            raise ElementNotFoundError(target)
        try:
            return matches[index]
        except IndexError:
            raise ElementNotFoundError(target) from None

    attr_map = {
        "name": ("title", lambda e, v: e.window_text() == v),
        "control_type": ("control_type", lambda e, v: e.friendly_class_name() == v),
        "class_name": ("class_name", lambda e, v: e.class_name() == v),
    }
    if by not in attr_map:
        raise UIAError(f"Unknown selector strategy: {by!r}", code="INVALID_SELECTOR")

    role_filter = str(target.get("role", "")).lower().replace(" ", "")
    prefer_interactive = (by == "name" and not role_filter)

    kwarg_name, fallback_pred = attr_map[by]
    try:
        matches = root.descendants(**{kwarg_name: value})
    except Exception:
        matches = []
    if not matches:
        for elem in _fetch_typed_descendants(root, include_display=True):
            try:
                if fallback_pred(elem, value):
                    matches.append(elem)
            except Exception:
                pass
    if not matches and by == "name":
        for elem in _fetch_typed_descendants(root, include_display=True):
            if _matches_msaa(elem, "legacy_name", value):
                matches.append(elem)
    if matches and role_filter:
        def _win_role(e) -> str:
            try:
                return e.friendly_class_name().lower().replace(" ", "")
            except Exception:
                return ""
        matches = [m for m in matches if _win_role(m) == role_filter]
    elif matches and prefer_interactive:
        def _win_role_key(e) -> int:
            try:
                return 0 if e.friendly_class_name().lower().replace(" ", "") in _WIN_INTERACTIVE_ROLES else 1
            except Exception:
                return 1
        matches.sort(key=_win_role_key)
    if not matches:
        raise ElementNotFoundError(target)
    try:
        return matches[index]
    except IndexError:
        raise ElementNotFoundError(target) from None


# ---------------------------------------------------------------------------
# WinUIABridge
# ---------------------------------------------------------------------------


class WinUIABridge(UIABridge):
    """Live Windows UIA bridge using pywinauto (UIA + MSAA).⁠"""


    def find_all(self, filter: dict[str, Any]) -> list[dict[str, Any]]:  # noqa: A002
        """Return a flat list of every named/interactive element in the window.

        Strategy
        --------
        1. **Win32-native fast path** (when root has an hwnd key, or no root):
           Use ``EnumChildWindows`` to enumerate child HWNDs starting from the
           given HWND — bypasses UIA COM entirely, ~100× faster for legacy Win32
           apps like Quicken.  Works for both full-window and scoped sub-tree roots.

        2. **UIA/MSAA fallback**: used when a root sub-element is specified
           (the Win32 path only knows the top-level HWND) or when the Win32
           path returns no results (pure UIA / modern apps).
        """
        named_only = bool(filter.get("named_only", True))
        must_have_actions = bool(filter.get("has_actions", True))
        roles_filter = [r.lower() for r in (filter.get("roles") or [])]
        root_target = filter.get("root") or {}
        limit = int(filter.get("limit") or 0)

        # ------------------------------------------------------------------
        # Win32-native fast path — works for both full-window and scoped-HWND
        # roots.  EnumChildWindows accepts any HWND as its starting point so
        # we can scope the search to a sub-tree without any UIA COM overhead.
        # ------------------------------------------------------------------
        from server.process_manager import get_process_manager  # noqa: PLC0415
        pm = get_process_manager()
        if pm.attached:
            # Determine which HWND to enumerate from
            root_hwnd: int | None = None
            if not root_target:
                root_hwnd = pm.attached.hwnd
            elif "hwnd" in root_target:
                # Caller scoped the search to a specific sub-window
                try:
                    raw = root_target["hwnd"]
                    root_hwnd = int(raw, 16) if isinstance(raw, str) else int(raw)
                except (ValueError, TypeError):
                    pass
            if root_hwnd is not None:
                try:
                    win32_results = _win32_fast_find_all(
                        root_hwnd,
                        named_only=named_only,
                        must_have_actions=must_have_actions,
                        roles_filter=roles_filter or None,
                        limit=limit,
                    )
                    if win32_results:
                        return win32_results
                except Exception:  # noqa: BLE001
                    pass

        # ------------------------------------------------------------------
        # UIA / MSAA fallback (required when root has no hwnd, or modern apps)
        # ------------------------------------------------------------------
        root = _attach_target()
        if root_target:
            root = _find_element(root, root_target)

        # Fetch elements using per-type COM property conditions — much faster than
        # root.descendants() with no filter, which returns every element in the tree.
        all_elements = _fetch_typed_descendants(root, include_display=not must_have_actions)

        results: list[dict[str, Any]] = []
        name_counts: dict[str, int] = {}

        for elem in all_elements:
            name = ""
            try:
                name = elem.window_text() or ""
            except Exception:
                pass

            role = ""
            try:
                role = elem.friendly_class_name().lower()
            except Exception:
                pass

            # Actions: derive from supported UIA patterns
            actions: list[str] = []
            try:
                patterns = [p for p in (elem.iface_patterns or []) if p is not None]
            except Exception:
                patterns = []
            if "InvokePattern" in patterns:
                actions.append("click")
            if "TogglePattern" in patterns:
                actions.append("toggle")
            if "ExpandCollapsePattern" in patterns:
                actions.append("expand")
            if "SelectionItemPattern" in patterns:
                actions.append("select")
            # Owner-drawn / MSAA-only controls
            if not actions:
                try:
                    raw = elem.legacy_properties() or {}
                    if raw.get("DefaultAction"):
                        actions.append("do default action")
                except Exception:
                    pass

            # Apply filters
            if named_only and not name:
                continue

            # Role filter: exact match OR widened button matching.
            # roles=["button"] also matches Pane/Custom elements that are
            # functionally buttons (MSAA ROLE_SYSTEM_PUSHBUTTON or InvokePattern).
            if roles_filter:
                role_match = role in roles_filter
                if not role_match and any(rf in _BUTTON_ROLE_ALIASES for rf in roles_filter):
                    # Check MSAA role
                    try:
                        _raw_msaa = elem.legacy_properties() or {}
                        if (_raw_msaa.get("Role") or 0) == _MSAA_PUSHBUTTON_ROLE:
                            role_match = True
                    except Exception:
                        pass
                    # Also match non-button UIA types that have InvokePattern
                    if not role_match and "InvokePattern" in patterns and role in (
                        "pane", "custom", "group", "image", "hyperlink",
                    ):
                        role_match = True
                if not role_match:
                    continue

            if must_have_actions and not actions:
                continue

            # Value (ValuePattern / editable fields)
            value = None
            try:
                v = elem.iface_value.CurrentValue
                if v is not None:
                    value = str(v)
            except Exception:
                pass

            # States + focused
            states: list[str] = []
            focused = False
            try:
                raw = elem.legacy_properties() or {}
                state = raw.get("State", 0) or 0
                if state & _STATE_FOCUSED:
                    focused = True
                if state & _STATE_SELECTED:
                    states.append("selected")
                if state & _STATE_CHECKED:
                    states.append("checked")
            except Exception:
                pass
            try:
                if elem.has_keyboard_focus():
                    focused = True
            except Exception:
                pass
            if focused:
                states.append("focused")

            per_name_idx = name_counts.get(name, 0)
            name_counts[name] = per_name_idx + 1

            d: dict[str, Any] = {
                "index": per_name_idx,
                "name": name,
                "role": role,
                "actions": actions,
            }
            if value is not None:
                d["value"] = value
            if name:
                d["text"] = name
            if states:
                d["states"] = states
            if focused:
                d["focused"] = True
            # Expose MSAA role so callers can filter independently of UIA type
            try:
                _msaa_r = elem.legacy_properties() or {}
                _msaa_role_int = _msaa_r.get("Role") or 0
                if _msaa_role_int:
                    d["msaa_role"] = _msaa_role_int
                    d["msaa_role_text"] = _ROLE_LABEL.get(_msaa_role_int, f"role_0x{_msaa_role_int:02x}")
            except Exception:
                pass
            results.append(d)

        return results

    def inspect(self, target: dict[str, Any]) -> dict[str, Any]:
        root = _attach_target()
        selector = {k: v for k, v in target.items() if k != "depth"} if target else {}
        depth = int(target.get("depth", _DEPTH_DEFAULT)) if target else _DEPTH_DEFAULT
        element = _find_element(root, selector)
        # Fast path: use Win32 tree enumeration when the element has a HWND.
        # This is ~100× faster than UIA for legacy Win32 apps like Quicken
        # whose UIA tree is bridged through slow MSAA.
        try:
            hwnd = element.handle
            if hwnd and hwnd > 0:
                return _win32_inspect_tree(hwnd, depth)
        except Exception:  # noqa: BLE001
            pass
        return _element_to_dict(element, depth)

    def invoke(self, target: dict[str, Any]) -> None:
        # ------------------------------------------------------------------
        # Fast path: if the target specifies an HWND directly, try Win32
        # BM_CLICK before falling through to the slow UIA element search.
        # ------------------------------------------------------------------
        _raw_hwnd: int | None = None
        if "hwnd" in target and "by" not in target:
            try:
                _v = target["hwnd"]
                _raw_hwnd = int(_v, 16) if isinstance(_v, str) and _v.startswith("0x") else int(_v)
            except (ValueError, TypeError):
                pass
        elif target.get("by") == "hwnd":
            try:
                _v2 = target.get("value", "0")
                _raw_hwnd = int(_v2, 16) if isinstance(_v2, str) and _v2.startswith("0x") else int(_v2)
            except (ValueError, TypeError):
                pass
        if _raw_hwnd is not None:
            import ctypes  # noqa: PLC0415
            import ctypes.wintypes  # noqa: PLC0415
            _u32 = ctypes.windll.user32
            try:
                _cls_buf = ctypes.create_unicode_buffer(256)
                _u32.GetClassNameW(_raw_hwnd, _cls_buf, 256)
                _cls = _cls_buf.value.lower()
                _orig_cls = _cls_buf.value
            except Exception:
                _cls = ""
                _orig_cls = ""
            if _cls in ("button",) or not _cls:
                # Native Win32 BUTTON class responds to BM_CLICK (0x00F5).
                if _u32.SendMessageW(_raw_hwnd, 0x00F5, 0, 0) == 0 or not _cls:
                    _u32.SetFocus(_raw_hwnd)
                    _u32.SendMessageW(_raw_hwnd, 0x00F5, 0, 0)
                return
            # For custom/non-standard classes (e.g. QC_button, QWComboBox):
            # 1) Fast O(1) UIA wrap → UIA invoke or MSAA DoDefaultAction (proven
            #    to work for Quicken's QC_button navigation buttons).
            _elem = _win32_element_from_hwnd(_raw_hwnd)
            if _elem is not None:
                try:
                    _elem.invoke()
                    return
                except Exception:  # noqa: BLE001
                    pass
                try:
                    _elem.iface_legacy_iaccessible.DoDefaultAction()
                    return
                except Exception:  # noqa: BLE001
                    pass
            # 2) Last resort: synthesise WM_LBUTTONDOWN/WM_LBUTTONUP at client center.
            _cr = ctypes.wintypes.RECT()
            _u32.GetClientRect(_raw_hwnd, ctypes.byref(_cr))
            _cx = (_cr.right - _cr.left) // 2
            _cy = (_cr.bottom - _cr.top) // 2
            _lp = ctypes.c_long((_cy << 16) | (_cx & 0xFFFF)).value
            _u32.SetFocus(_raw_hwnd)
            _u32.SendMessageW(_raw_hwnd, 0x0201, 0x0001, _lp)  # WM_LBUTTONDOWN
            _u32.SendMessageW(_raw_hwnd, 0x0202, 0, _lp)       # WM_LBUTTONUP
            return
        # ------------------------------------------------------------------
        # Standard UIA / MSAA path
        # ------------------------------------------------------------------
        root = _attach_target()
        element = _find_element(root, target)
        # Strategy 1: UIA InvokePattern
        try:
            element.invoke()
            return
        except Exception:
            pass
        # Strategy 2: UIA TogglePattern
        try:
            element.toggle()
            return
        except Exception:
            pass
        # Strategy 3: UIA ExpandCollapsePattern (menus/dropdowns)
        try:
            element.expand()
            return
        except Exception:
            pass
        # Strategy 4: MSAA LegacyIAccessiblePattern.DoDefaultAction
        # Handles apps (e.g. Quicken QWMenuBar) that only respond to MSAA.
        try:
            element.iface_legacy_iaccessible.DoDefaultAction()
            return
        except Exception:
            pass
        # Strategy 5: SelectionItemPattern.Select
        try:
            element.iface_selection_item.Select()
            return
        except Exception:
            pass
        # Strategy 6: pywinauto click_input
        try:
            element.click_input()
            return
        except Exception:
            pass
        # Strategy 7: raw coordinate click at element's bounding-box centre.
        # Works for Quicken QWMenuBar items that ignore all UIA patterns but
        # respond to physical WM_LBUTTONDOWN at the correct screen coordinates.
        try:
            _require_pywinauto()
            from pywinauto import mouse  # noqa: PLC0415
            r = element.rectangle()
            cx = (r.left + r.right) // 2
            cy = (r.top + r.bottom) // 2
            mouse.click(coords=(cx, cy))
            return
        except Exception as exc:
            raise PatternNotSupportedError(
                "Invoke/Toggle/Expand/Click", element.window_text()
            ) from exc

    def set_value(self, target: dict[str, Any], value: str) -> None:
        import ctypes  # noqa: PLC0415

        root = _attach_target()
        element = _find_element(root, target)

        # 1. pywinauto set_edit_text (uses WM_SETTEXT for HwndWrappers)
        try:
            element.set_edit_text(value)
            return
        except Exception:
            pass

        # 2. UIA ValuePattern SetValue (works for native UIA edit controls)
        try:
            iface = element.iface_value
            iface.SetValue(value)
            return
        except Exception:
            pass

        # 3. Win32 WM_SETTEXT fallback for custom classes (QREdit, QWEdit, etc.)
        WM_SETTEXT = 0x000C
        SMTO_ABORTIFHUNG = 0x0002
        hwnd = getattr(element, "handle", None)
        if hwnd:
            buf = ctypes.create_unicode_buffer(value)
            result = ctypes.windll.user32.SendMessageTimeoutW(
                hwnd, WM_SETTEXT, 0, buf, SMTO_ABORTIFHUNG, 200, None
            )
            if result:
                return

        raise PatternNotSupportedError("Value", element.window_text())

    def send_keys(self, keys: str, target: dict[str, Any] | None = None) -> None:
        _require_pywinauto()
        from pywinauto import keyboard  # noqa: PLC0415

        if target:
            root = _attach_target()
            element = _find_element(root, target)
            try:
                element.set_focus()
            except Exception:
                pass
        else:
            root = _attach_target()
            try:
                root.set_focus()
            except Exception:
                pass
        keyboard.send_keys(keys, pause=0.05, with_spaces=True, with_newlines=True, with_tabs=True)

    def type_text(self, text: str, target: dict[str, Any] | None = None) -> None:
        """Type *text* literally, auto-escaping all SendKeys special characters."""
        _require_pywinauto()
        from pywinauto import keyboard  # noqa: PLC0415

        if target:
            root = _attach_target()
            element = _find_element(root, target)
            try:
                element.set_focus()
            except Exception:
                pass
        else:
            root = _attach_target()
            try:
                root.set_focus()
            except Exception:
                pass
        escaped = _escape_text_for_send_keys(text)
        keyboard.send_keys(escaped, pause=0.05, with_spaces=True, with_newlines=True, with_tabs=True)

    def legacy_invoke(self, target: dict[str, Any]) -> None:
        """Invoke via MSAA DoDefaultAction / Win32 WM_LBUTTONDBLCLK."""
        import ctypes  # noqa: PLC0415

        WM_LBUTTONDOWN = 0x0201
        WM_LBUTTONUP = 0x0202
        WM_LBUTTONDBLCLK = 0x0203

        def _makelparam(x: int, y: int) -> int:
            return ((y & 0xFFFF) << 16) | (x & 0xFFFF)

        root = _attach_target()
        element = _find_element(root, target)

        default_action = ""
        try:
            raw = element.legacy_properties() or {}
            default_action = (raw.get("DefaultAction") or "").lower()
        except Exception:
            pass

        if "double" in default_action:
            # Strategy 1: Win32 SendMessage
            try:
                parent = element.parent()
                parent_hwnd = parent.handle
                if parent_hwnd:
                    item_rect = element.rectangle()
                    parent_rect = parent.rectangle()
                    cx = (item_rect.left + item_rect.right) // 2 - parent_rect.left
                    cy = (item_rect.top + item_rect.bottom) // 2 - parent_rect.top
                    lp = _makelparam(cx, cy)
                    u32 = ctypes.windll.user32
                    u32.SetForegroundWindow(parent_hwnd)
                    u32.SetFocus(parent_hwnd)
                    u32.SendMessageW(parent_hwnd, WM_LBUTTONDOWN, 1, lp)
                    u32.SendMessageW(parent_hwnd, WM_LBUTTONUP, 0, lp)
                    u32.SendMessageW(parent_hwnd, WM_LBUTTONDBLCLK, 1, lp)
                    u32.SendMessageW(parent_hwnd, WM_LBUTTONUP, 0, lp)
                    return
            except Exception:
                pass
            # Strategy 2: pywinauto double_click_input
            try:
                element.double_click_input()
                return
            except Exception:
                pass

        # Strategy 3: LegacyIAccessiblePattern.DoDefaultAction
        try:
            element.iface_legacy_iaccessible.DoDefaultAction()
            return
        except Exception:
            pass
        # Strategy 4: SelectionItemPattern.Select
        try:
            element.iface_selection_item.Select()
            return
        except Exception:
            pass
        # Strategy 5: InvokePattern
        try:
            element.invoke()
            return
        except Exception:
            pass
        # Strategy 6: Single click fallback
        try:
            element.click_input()
            return
        except Exception as exc:
            raise PatternNotSupportedError(
                "LegacyIAccessible/DefaultAction", element.window_text()
            ) from exc

    def mouse_click(
        self,
        x: int,
        y: int,
        double: bool = False,
        button: str = "left",
        force_sendinput: bool = False,
    ) -> None:
        """Click at absolute screen coordinates using pywinauto.mouse (SendInput).

        Parameters
        ----------
        force_sendinput : bool
            Deprecated no-op — the current implementation already uses
            SendInput under the hood.  Retained for forward compatibility
            with callers that explicitly request raw input dispatch.
        """
        _require_pywinauto()
        from pywinauto import mouse  # noqa: PLC0415

        coords = (x, y)
        if double:
            mouse.double_click(button=button, coords=coords)
        else:
            mouse.click(button=button, coords=coords)

    def send_win32_message(
        self,
        hwnd: int,
        message: int,
        wparam: int = 0,
        lparam: int = 0,
        sync: bool = True,
    ) -> int:
        """Send or post a Win32 message to a window handle.

        Parameters
        ----------
        hwnd : int
            Target window handle.
        message : int
            Windows message constant (e.g. ``0xF5`` for BM_CLICK,
            ``0x0010`` for WM_CLOSE, ``0x0111`` for WM_COMMAND).
        wparam, lparam : int
            Message parameters (default 0).
        sync : bool
            ``True`` (default) → ``SendMessageW`` — blocks until the
            target processes the message.  Use for buttons and dialogs.
            ``False`` → ``PostMessageW`` — fire-and-forget.  Use to
            dismiss modals asynchronously.

        Returns
        -------
        int
            Return value from ``SendMessageW``, or 1 on success for
            ``PostMessageW``.
        """
        import ctypes  # noqa: PLC0415

        user32 = ctypes.windll.user32
        if sync:
            return user32.SendMessageW(hwnd, message, wparam, lparam)
        user32.PostMessageW(hwnd, message, wparam, lparam)
        return 1

    def get_window_enabled_state(self, hwnd: int) -> dict[str, Any]:
        """Check whether *hwnd* is enabled and identify any blocking overlays.

        Returns a dict with ``enabled`` (bool) and, when disabled, a
        ``blocking_windows`` list of same-process windows that may be
        responsible (e.g. Quicken's QWinLightbox).
        """
        import ctypes  # noqa: PLC0415
        import ctypes.wintypes  # noqa: PLC0415

        user32 = ctypes.windll.user32
        enabled = bool(user32.IsWindowEnabled(hwnd))
        result: dict[str, Any] = {"hwnd": hwnd, "enabled": enabled}

        if not enabled:
            pid = ctypes.wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            target_pid = pid.value

            blocking: list[dict[str, Any]] = []

            def _enum_cb(h: int, _lp: Any) -> bool:
                if h == hwnd:
                    return True
                p = ctypes.wintypes.DWORD()
                user32.GetWindowThreadProcessId(h, ctypes.byref(p))
                if p.value != target_pid:
                    return True
                if not user32.IsWindowVisible(h):
                    return True
                cls_buf = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(h, cls_buf, 256)
                tlen = user32.GetWindowTextLengthW(h)
                tbuf = ctypes.create_unicode_buffer(tlen + 1)
                user32.GetWindowTextW(h, tbuf, tlen + 1)
                blocking.append({
                    "hwnd": h,
                    "class_name": cls_buf.value,
                    "title": tbuf.value,
                })
                return True

            WNDENUMPROC = ctypes.WINFUNCTYPE(
                ctypes.c_bool, ctypes.c_int, ctypes.POINTER(ctypes.c_int)
            )
            user32.EnumWindows(WNDENUMPROC(_enum_cb), 0)
            if blocking:
                result["blocking_windows"] = blocking

        return result

    def dismiss_modal_overlay(self, target_hwnd: int) -> dict[str, Any]:
        """Close overlay windows blocking *target_hwnd* and re-enable it.

        Handles the QWinLightbox (and similar) pattern where a wizard or
        background dialog calls ``EnableWindow(parent, 0)`` and then fails
        to re-enable it after closing.

        Steps
        -----
        1. Check ``IsWindowEnabled(target_hwnd)`` — exit early if enabled.
        2. Enumerate all visible same-process windows.
        3. Send ``WM_CLOSE`` to each overlay.
        4. If target is still disabled after closing overlays, call
           ``EnableWindow(target_hwnd, 1)`` directly.

        Returns
        -------
        dict
            ``{"ok": True, "dismissed": [...], "re_enabled": bool}``
        """
        import ctypes  # noqa: PLC0415
        import ctypes.wintypes  # noqa: PLC0415

        WM_CLOSE = 0x0010  # noqa: N806
        user32 = ctypes.windll.user32

        if bool(user32.IsWindowEnabled(target_hwnd)):
            return {
                "ok": True,
                "target_hwnd": target_hwnd,
                "enabled": True,
                "dismissed": [],
                "re_enabled": False,
            }

        pid = ctypes.wintypes.DWORD()
        user32.GetWindowThreadProcessId(target_hwnd, ctypes.byref(pid))
        target_pid = pid.value

        overlays: list[int] = []

        def _enum_cb(h: int, _lp: Any) -> bool:
            if h == target_hwnd:
                return True
            p = ctypes.wintypes.DWORD()
            user32.GetWindowThreadProcessId(h, ctypes.byref(p))
            if p.value != target_pid:
                return True
            overlays.append(h)
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(
            ctypes.c_bool, ctypes.c_int, ctypes.POINTER(ctypes.c_int)
        )
        user32.EnumWindows(WNDENUMPROC(_enum_cb), 0)

        dismissed: list[dict[str, Any]] = []
        for h in overlays:
            cls_buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(h, cls_buf, 256)
            user32.SendMessageW(h, WM_CLOSE, 0, 0)
            dismissed.append({"hwnd": h, "class_name": cls_buf.value})

        re_enabled = False
        if not bool(user32.IsWindowEnabled(target_hwnd)):
            user32.EnableWindow(target_hwnd, 1)
            re_enabled = True

        return {
            "ok": True,
            "target_hwnd": target_hwnd,
            "enabled": True,
            "dismissed": dismissed,
            "re_enabled": re_enabled,
        }

    def get_text(self, target: dict[str, Any]) -> tuple[str, str]:
        """
        Return the human-readable text of an element and the source field.

        Priority
        --------
        1. UIA ``ValuePattern`` (``iface_value.CurrentValue`` /
           ``get_value()``) — the programmatic value for editable and
           display elements.  Empty string is treated as *absent*.
        2. UIA ``window_text()`` — the accessible name (e.g. UWP Calculator
           exposes ``"Display is 56"`` here when ValuePattern is absent).
        3. MSAA ``Value`` from ``legacy_properties()``.
        4. MSAA ``Name`` from ``legacy_properties()``.
        """
        root = _attach_target()
        element = _find_element(root, target)

        # 1. UIA Value pattern
        uia_value: str | None = None
        try:
            uia_value = element.iface_value.CurrentValue
        except Exception:
            pass
        if uia_value is None:
            try:
                uia_value = element.get_value()
            except Exception:
                pass
        if uia_value is not None and str(uia_value).strip():
            return str(uia_value), "value"

        # 2. Accessible name (window_text)
        name = ""
        try:
            name = element.window_text()
        except Exception:
            pass
        if name.strip():
            return name, "name"

        # 3. MSAA legacy value / name
        try:
            raw = element.legacy_properties() or {}
            msaa_val = (raw.get("Value") or "").strip()
            if msaa_val:
                return msaa_val, "msaa_value"
            msaa_name = (raw.get("Name") or "").strip()
            if msaa_name:
                return msaa_name, "msaa_name"
        except Exception:
            pass

        return "", "none"

    # ---------------------------------------------------------------------------
    # Account navigation (Quicken-specific, Win32 combobox path)
    # ---------------------------------------------------------------------------

    _SKIP_ACCT_ITEMS = frozenset({"custom...", "qcombo_separator", ""})

    def list_accounts(self) -> list[dict[str, Any]]:
        """
        Return all accounts visible in the 'All accounts' register combobox.

        Strategy
        --------
        1. Find all ``qwcombobox`` controls in the current window.
        2. Locate the one named "All accounts" (or the leftmost one that
           contains non-date, non-type items).
        3. Read its item list via ``CB_GETLBTEXT``.
        4. Filter out separators, "Custom...", and empty strings.

        Returns
        -------
        list of dict
            Each entry has ``{"name": str, "combo_index": int}``.

        Raises
        ------
        UIAError
            If no account combobox can be located.
        """
        CB_GETCOUNT    = 0x0146
        CB_GETLBTEXT   = 0x0148
        CB_GETLBTEXTLEN= 0x0149
        WM_GETTEXT     = 0x000D

        import ctypes  # noqa: PLC0415

        user32 = ctypes.windll.user32

        from server.process_manager import get_process_manager  # noqa: PLC0415
        pm = get_process_manager()
        if not pm.attached:
            raise TargetNotFoundError("Use select_window to attach to a target first.")

        # Find all qwcombobox controls (platform-native: fast Win32 path)
        all_els = self.find_all({
            "roles": [], "has_actions": False, "named_only": False, "root": None,
        })
        combos = [e for e in all_els if e.get("class_name", "").lower() == "qwcombobox"]

        # Prefer one explicitly named "All accounts"
        def _hwnd_int(e: dict) -> int:
            raw = e.get("hwnd", 0)
            return int(raw, 16) if isinstance(raw, str) else int(raw)

        def _get_items(h: int) -> list[str]:
            count = user32.SendMessageW(h, CB_GETCOUNT, 0, 0)
            out: list[str] = []
            for i in range(min(count, 500)):
                tlen = user32.SendMessageW(h, CB_GETLBTEXTLEN, i, 0)
                if tlen <= 0:
                    out.append("")
                    continue
                buf = ctypes.create_unicode_buffer(tlen + 1)
                user32.SendMessageW(h, CB_GETLBTEXT, i, buf)
                out.append(buf.value)
            return out

        acct_combo_h: int | None = None
        for cb in combos:
            nm = (cb.get("name") or "").lower()
            if "account" in nm:
                acct_combo_h = _hwnd_int(cb)
                break

        # Fallback: use leftmost combo that has non-date items
        if acct_combo_h is None:
            import ctypes.wintypes  # noqa: PLC0415
            def _combo_x(e: dict) -> int:
                r = ctypes.wintypes.RECT()
                user32.GetWindowRect(_hwnd_int(e), ctypes.byref(r))
                return r.left
            for cb in sorted(combos, key=_combo_x):
                items = _get_items(_hwnd_int(cb))
                non_temporal = [
                    it for it in items if it and not any(
                        x in it.lower() for x in (
                            "month", "year", "week", "day", "today",
                            "quarter", "type", "income", "expense",
                            "transfer", "all date",
                        )
                    )
                ]
                if len(non_temporal) > 1:
                    acct_combo_h = _hwnd_int(cb)
                    break

        if acct_combo_h is None:
            raise UIAError(
                "No account combobox found. Navigate to a register view (e.g. SPENDING) first.",
                code="ACCOUNT_COMBO_NOT_FOUND",
            )

        raw_items = _get_items(acct_combo_h)
        result = []
        for idx, name in enumerate(raw_items):
            if name.lower() in self._SKIP_ACCT_ITEMS:
                continue
            result.append({"name": name, "combo_index": idx, "combo_hwnd": hex(acct_combo_h)})
        return result

    def navigate_to_account(self, account_name: str) -> dict[str, Any]:
        """
        Navigate the register view to a specific account.

        Selects *account_name* in the "All accounts" toolbar combobox using
        ``CB_SETCURSEL`` + ``WM_COMMAND(CBN_SELCHANGE)`` — no mouse interaction
        required.

        Parameters
        ----------
        account_name : str
            Exact account name as returned by ``list_accounts()``.
            Comparison is case-insensitive.

        Returns
        -------
        dict
            ``{"ok": True, "account": str, "combo_index": int}``

        Raises
        ------
        UIAError
            ``ACCOUNT_NOT_FOUND`` if the account name is not in the list.
            ``ACCOUNT_COMBO_NOT_FOUND`` if no combobox can be located.
        """
        import ctypes  # noqa: PLC0415

        CB_SETCURSEL  = 0x014E
        CBN_SELCHANGE = 1
        WM_COMMAND    = 0x0111

        user32 = ctypes.windll.user32

        accounts = self.list_accounts()
        name_lower = account_name.lower()
        match = next(
            (a for a in accounts if a["name"].lower() == name_lower),
            None,
        )
        if match is None:
            available = [a["name"] for a in accounts]
            raise UIAError(
                f"Account {account_name!r} not found. Available: {available}",
                code="ACCOUNT_NOT_FOUND",
            )

        combo_h = int(match["combo_hwnd"], 16)
        idx = match["combo_index"]

        user32.SendMessageW(combo_h, CB_SETCURSEL, idx, 0)
        parent = user32.GetParent(combo_h)
        ctrl_id = user32.GetDlgCtrlID(combo_h)
        wparam = (CBN_SELCHANGE << 16) | (ctrl_id & 0xFFFF)
        user32.SendMessageW(parent, WM_COMMAND, wparam, combo_h)

        return {"ok": True, "account": match["name"], "combo_index": idx}
