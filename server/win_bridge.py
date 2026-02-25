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
    from pywinauto import Desktop
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
_META_KEYS = {"by", "value", "index", "depth"}

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
    """
    _require_pywinauto()
    from server.process_manager import get_process_manager  # noqa: PLC0415

    pm = get_process_manager()
    attached = pm.attached
    if attached is None:
        raise TargetNotFoundError("Use select_window to attach to a target first.")

    desktop = Desktop(backend="uia")

    # Try by HWND first (most precise)
    try:
        wins = desktop.windows(handle=attached.hwnd)
        if wins:
            return wins[0]
    except Exception:
        pass

    # Fallback: by class name
    if attached.class_name:
        wins = desktop.windows(class_name=attached.class_name)
        if wins:
            return wins[0]

    # Fallback: by title
    if attached.title:
        wins = desktop.windows(title_re=f".*{attached.title}.*")
        if wins:
            return wins[0]

    raise TargetNotFoundError(
        f"Window hwnd={hex(attached.hwnd)} title={attached.title!r} is no longer available."
    )


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
        all_desc = root.descendants()
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

    if by in _MSAA_STRATEGIES:
        all_desc = root.descendants()
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

    kwarg_name, fallback_pred = attr_map[by]
    try:
        matches = root.descendants(**{kwarg_name: value})
    except Exception:
        matches = []
    if not matches:
        for elem in root.descendants():
            try:
                if fallback_pred(elem, value):
                    matches.append(elem)
            except Exception:
                pass
    if not matches and by == "name":
        for elem in root.descendants():
            if _matches_msaa(elem, "legacy_name", value):
                matches.append(elem)
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

    def inspect(self, target: dict[str, Any]) -> dict[str, Any]:
        root = _attach_target()
        selector = {k: v for k, v in target.items() if k != "depth"} if target else {}
        depth = int(target.get("depth", _DEPTH_DEFAULT)) if target else _DEPTH_DEFAULT
        element = _find_element(root, selector)
        return _element_to_dict(element, depth)

    def invoke(self, target: dict[str, Any]) -> None:
        root = _attach_target()
        element = _find_element(root, target)
        try:
            element.invoke()
            return
        except Exception:
            pass
        try:
            element.toggle()
            return
        except Exception:
            pass
        try:
            element.click_input()
            return
        except Exception as exc:
            raise PatternNotSupportedError(
                "Invoke/Toggle", element.window_text()
            ) from exc

    def set_value(self, target: dict[str, Any], value: str) -> None:
        root = _attach_target()
        element = _find_element(root, target)
        try:
            element.set_edit_text(value)
            return
        except Exception:
            pass
        try:
            iface = element.iface_value
            iface.SetValue(value)
            return
        except Exception as exc:
            raise PatternNotSupportedError(
                "Value", element.window_text()
            ) from exc

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
    ) -> None:
        """Click at absolute screen coordinates using pywinauto.mouse."""
        _require_pywinauto()
        from pywinauto import mouse  # noqa: PLC0415

        coords = (x, y)
        if double:
            mouse.double_click(button=button, coords=coords)
        else:
            mouse.click(button=button, coords=coords)

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
