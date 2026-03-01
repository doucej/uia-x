"""
AT-SPI2 utility helpers for the Linux accessibility backend.

Provides low-level wrappers around python3-pyatspi / D-Bus to:
  - connect to the session accessibility bus
  - enumerate accessible objects
  - extract roles, names, descriptions, states, and bounding rectangles
  - generate stable element identifiers from the AT-SPI path
  - translate AT-SPI key-sym names to/from human-readable strings
  - synthesise keystrokes via AT-SPI or XTest fallback
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from typing import Any

# ---------------------------------------------------------------------------
# Optional imports – guard so tests/CI can import the module even without
# a running accessibility bus.
# ---------------------------------------------------------------------------

_ATSPI_IMPORT_ERROR: str | None = None
_ATSPI_AVAILABLE = False

try:
    import pyatspi  # type: ignore[import-untyped]

    _ATSPI_AVAILABLE = True
except Exception as _exc:  # noqa: BLE001
    _ATSPI_IMPORT_ERROR = f"{type(_exc).__name__}: {_exc}"


def require_atspi() -> None:
    """Raise a clear error if AT-SPI2 bindings are not available."""
    if not _ATSPI_AVAILABLE:
        detail = f" ({_ATSPI_IMPORT_ERROR})" if _ATSPI_IMPORT_ERROR else ""
        raise RuntimeError(
            f"python3-pyatspi is not installed or the accessibility bus is "
            f"unreachable.{detail}"
        )


def atspi_available() -> bool:
    """Return True if pyatspi was imported successfully."""
    return _ATSPI_AVAILABLE


# ---------------------------------------------------------------------------
# Element ID generation
# ---------------------------------------------------------------------------


def _accessible_path(acc: Any) -> str:
    """
    Build a stable path string from the AT-SPI accessible hierarchy.

    The path is formed from the accessible's application name, role, name,
    and its index among siblings at each level – giving a repeatable
    identifier that survives across inspections within the same session.
    """
    parts: list[str] = []
    current = acc
    while current is not None:
        try:
            role = current.getRole().value_nick if hasattr(current.getRole(), "value_nick") else str(current.getRole())
        except Exception:
            role = "unknown"
        try:
            name = current.name or ""
        except Exception:
            name = ""
        try:
            idx = current.getIndexInParent()
        except Exception:
            idx = 0
        parts.append(f"{role}:{name}:{idx}")
        try:
            current = current.parent
        except Exception:
            break
    parts.reverse()
    return "/".join(parts)


def make_element_id(acc: Any) -> str:
    """
    Generate a stable, short ID for an AT-SPI accessible object.

    Uses a SHA-1 hash of the accessible path so callers get a fixed-length
    opaque identifier that can be used as a dictionary key.
    """
    path = _accessible_path(acc)
    return hashlib.sha1(path.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Role / state helpers
# ---------------------------------------------------------------------------


def role_name(acc: Any) -> str:
    """Return a human-readable role string for an accessible."""
    try:
        r = acc.getRole()
        # pyatspi roles have a value_nick property for GEnum types
        if hasattr(r, "value_nick"):
            return str(r.value_nick).replace("-", " ")
        return str(r).rsplit(".", 1)[-1].replace("ROLE_", "").replace("_", " ").lower()
    except Exception:
        return "unknown"


def state_names(acc: Any) -> list[str]:
    """Return a list of human-readable state strings for an accessible."""
    try:
        state_set = acc.getState()
        states: list[str] = []
        for st in state_set.getStates():
            if hasattr(st, "value_nick"):
                states.append(str(st.value_nick).replace("-", " "))
            else:
                val = str(st).rsplit(".", 1)[-1].replace("STATE_", "").replace("_", " ").lower()
                states.append(val)
        return states
    except Exception:
        return []


def bounding_rect(acc: Any) -> dict[str, int]:
    """
    Return ``{left, top, right, bottom}`` from the Component interface.

    Returns an empty dict if the accessible has no Component interface.
    """
    try:
        comp = acc.queryComponent()
        # pyatspi.DESKTOP_COORDS == 0; use the constant if available,
        # otherwise fall back to the raw int.
        try:
            coord_type = pyatspi.DESKTOP_COORDS
        except Exception:
            coord_type = 0
        ext = comp.getExtents(coord_type)
        return {
            "left": ext.x,
            "top": ext.y,
            "right": ext.x + ext.width,
            "bottom": ext.y + ext.height,
        }
    except Exception:
        return {}


def get_description(acc: Any) -> str:
    """Return the accessible description (or empty string)."""
    try:
        return acc.description or ""
    except Exception:
        return ""


def get_text_content(acc: Any) -> str | None:
    """
    Retrieve the full text content from the Text interface.

    Returns None if the accessible has no Text interface.
    """
    try:
        ti = acc.queryText()
        return ti.getText(0, ti.characterCount)
    except Exception:
        return None


def get_value(acc: Any) -> str | None:
    """
    Retrieve the current value from the Value interface.

    Returns a string representation, or None if unavailable.
    """
    try:
        vi = acc.queryValue()
        return str(vi.currentValue)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Action helpers
# ---------------------------------------------------------------------------


def get_actions(acc: Any) -> list[str]:
    """Return the list of action names supported by the accessible."""
    try:
        ai = acc.queryAction()
        return [ai.getName(i) for i in range(ai.nActions)]
    except Exception:
        return []


def do_action(acc: Any, action_index: int = 0) -> bool:
    """
    Invoke an action by index (default: first / default action).

    Returns True on success, False on failure.
    """
    try:
        ai = acc.queryAction()
        return bool(ai.doAction(action_index))
    except Exception:
        return False


def do_action_by_name(acc: Any, name: str) -> bool:
    """Invoke the first action whose name matches *name* (case-insensitive)."""
    try:
        ai = acc.queryAction()
        for i in range(ai.nActions):
            if ai.getName(i).lower() == name.lower():
                return bool(ai.doAction(i))
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Selection helpers
# ---------------------------------------------------------------------------


def get_selected_children(acc: Any) -> list[Any]:
    """Return the list of currently selected child accessibles."""
    try:
        sel = acc.querySelection()
        return [sel.getSelectedChild(i) for i in range(sel.nSelectedChildren)]
    except Exception:
        return []


def select_child(acc: Any, child_index: int) -> bool:
    """Select a child by index via the Selection interface."""
    try:
        sel = acc.querySelection()
        return bool(sel.selectChild(child_index))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Text mutation
# ---------------------------------------------------------------------------


def set_text_content(acc: Any, text: str) -> bool:
    """
    Replace the full text of an editable accessible via EditableText.

    Falls back to clearing + inserting if ``setTextContents`` is unavailable.
    """
    try:
        eti = acc.queryEditableText()
        # Best path: setTextContents (supported by GTK, Qt)
        try:
            eti.setTextContents(text)
            return True
        except Exception:
            pass
        # Fallback: delete all, then insert
        ti = acc.queryText()
        length = ti.characterCount
        if length > 0:
            eti.deleteText(0, length)
        eti.insertText(0, text, len(text))
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Keystroke synthesis
# ---------------------------------------------------------------------------

# Map from common key names (pywinauto / SendKeys-style) to AT-SPI keysym
# names.  AT-SPI's generateKeyboardEvent expects X11 keysym names.
_KEY_MAP: dict[str, str] = {
    "ENTER": "Return",
    "RETURN": "Return",
    "TAB": "Tab",
    "ESC": "Escape",
    "ESCAPE": "Escape",
    "BACKSPACE": "BackSpace",
    "DELETE": "Delete",
    "DEL": "Delete",
    "INSERT": "Insert",
    "INS": "Insert",
    "HOME": "Home",
    "END": "End",
    "PGUP": "Prior",
    "PGDN": "Next",
    "PAGEUP": "Prior",
    "PAGEDOWN": "Next",
    "UP": "Up",
    "DOWN": "Down",
    "LEFT": "Left",
    "RIGHT": "Right",
    "SPACE": "space",
    "F1": "F1", "F2": "F2", "F3": "F3", "F4": "F4",
    "F5": "F5", "F6": "F6", "F7": "F7", "F8": "F8",
    "F9": "F9", "F10": "F10", "F11": "F11", "F12": "F12",
    "CAPSLOCK": "Caps_Lock",
    "NUMLOCK": "Num_Lock",
    "SCROLLLOCK": "Scroll_Lock",
    "PRINTSCREEN": "Print",
    "PRTSC": "Print",
    "PAUSE": "Pause",
    "BREAK": "Break",
}

# Modifier keys in pywinauto notation: ^ = Ctrl, + = Shift, % = Alt
_MODIFIER_MAP: dict[str, tuple[str, str]] = {
    "^": ("Control_L", "control"),
    "+": ("Shift_L", "shift"),
    "%": ("Alt_L", "alt"),
}


def send_keys_atspi(keys: str) -> None:
    """
    Send keystrokes via AT-SPI ``generateKeyboardEvent``.

    Interprets a simplified subset of pywinauto / SendKeys notation:
      - Plain characters are typed literally.
      - ``{KEY}`` sends a named special key (e.g. ``{ENTER}``).
      - ``^``, ``+``, ``%`` act as modifier prefixes (Ctrl, Shift, Alt)
        for the next character or ``{KEY}`` group.

    Falls back to ``xdotool`` if AT-SPI key generation is unavailable.
    """
    require_atspi()
    _send_keys_via_atspi(keys)


def _send_keys_via_atspi(keys: str) -> None:
    """Core implementation using pyatspi.Registry.generateKeyboardEvent."""
    import pyatspi  # type: ignore[import-untyped]

    reg = pyatspi.Registry

    i = 0
    length = len(keys)
    held_modifiers: list[str] = []

    while i < length:
        ch = keys[i]

        # Modifier prefix
        if ch in _MODIFIER_MAP:
            keysym_name, _ = _MODIFIER_MAP[ch]
            reg.generateKeyboardEvent(
                pyatspi.Registry.generateKeyboardEvent.__func__  # type: ignore
                if False else 0,
                keysym_name,
                pyatspi.KEY_PRESSRELEASE if False else pyatspi.KEY_PRESS,
            )
            # Actually: pyatspi expects (keycode, keysym_string, synth_type)
            # For press:
            reg.generateKeyboardEvent(0, keysym_name, pyatspi.KEY_PRESS)
            held_modifiers.append(keysym_name)
            i += 1
            continue

        # Special key in braces: {KEY}
        if ch == "{":
            end = keys.find("}", i + 1)
            if end == -1:
                # Malformed – treat as literal
                _type_char(ch)
                i += 1
                continue
            key_name = keys[i + 1 : end].upper()
            keysym = _KEY_MAP.get(key_name, key_name)
            reg.generateKeyboardEvent(0, keysym, pyatspi.KEY_SYM)
            # Release any held modifiers
            for mod in reversed(held_modifiers):
                reg.generateKeyboardEvent(0, mod, pyatspi.KEY_RELEASE)
            held_modifiers.clear()
            i = end + 1
            continue

        # Literal character
        _type_char(ch)

        # Release any held modifiers
        for mod in reversed(held_modifiers):
            reg.generateKeyboardEvent(0, mod, pyatspi.KEY_RELEASE)
        held_modifiers.clear()

        i += 1

    # Safety: release any remaining modifiers
    for mod in reversed(held_modifiers):
        reg.generateKeyboardEvent(0, mod, pyatspi.KEY_RELEASE)


def _type_char(ch: str) -> None:
    """Type a single character via AT-SPI KEY_STRING."""
    import pyatspi  # type: ignore[import-untyped]

    pyatspi.Registry.generateKeyboardEvent(0, ch, pyatspi.KEY_STRING)


def focus_window(title: str) -> bool:
    """
    Bring a window with the given title into keyboard focus.

    Tries ``wmctrl -a <title>`` first (reliable under most Xorg/XWayland
    compositors).  Falls back to ``xdotool search --name <title>
    windowfocus`` if wmctrl is unavailable.  Returns True if either
    command succeeded, False if neither tool is installed or the window
    was not found.
    """
    import time  # noqa: PLC0415
    display = os.environ.get("DISPLAY", ":0")
    env = {**os.environ, "DISPLAY": display}
    for cmd in (
        ["wmctrl", "-a", title],
        ["xdotool", "search", "--name", title, "windowfocus", "--sync"],
    ):
        try:
            result = subprocess.run(
                cmd, env=env, capture_output=True, timeout=3
            )
            if result.returncode == 0:
                time.sleep(0.15)  # give the WM time to complete the focus
                return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return False


def send_keys_xdotool(keys: str) -> None:
    """
    Fallback keystroke injection via xdotool.

    Translates the pywinauto/SendKeys notation into xdotool commands.
    Requires ``xdotool`` to be installed.
    """
    # Build an xdotool command sequence
    tokens = _parse_keys_to_xdotool(keys)
    if tokens:
        subprocess.run(["xdotool"] + tokens, check=True)


def type_text_atspi(text: str) -> None:
    """
    Type plain text literally via AT-SPI, character by character.

    Unlike :func:`send_keys_atspi`, no special encoding is needed — spaces,
    punctuation, and symbols are all typed as-is.  ``\\n`` is sent as Enter.
    """
    require_atspi()
    for ch in text:
        if ch == "\n":
            _send_keys_via_atspi("{ENTER}")
        elif ch == "\t":
            _send_keys_via_atspi("{TAB}")
        else:
            _type_char(ch)


def type_text_xdotool(text: str) -> None:
    """
    Type plain text literally via ``xdotool type``.

    Uses ``xdotool type --clearmodifiers -- <text>`` which handles all
    characters (including spaces and punctuation) without special encoding.
    ``\\n`` is sent as a Return key press before/after splitting lines.
    """
    if not text:
        return
    # xdotool type handles multi-line text well with --clearmodifiers
    subprocess.run(
        ["xdotool", "type", "--clearmodifiers", "--delay", "20", "--", text],
        check=False,
    )


def _parse_keys_to_xdotool(keys: str) -> list[str]:
    """Convert SendKeys notation into xdotool argument tokens.

    xdotool supports chained subcommands in a single invocation, e.g.::

        xdotool key Escape type --clearmodifiers "6*7" key Return

    The key rule: consecutive plain (unmodified) characters must be batched
    into a *single* ``type --clearmodifiers <text>`` subcommand.  Emitting
    one ``type`` per character causes xdotool to treat subsequent ``type``
    tokens as text arguments of the first ``type`` subcommand.
    """
    _XDOTOOL_MAP: dict[str, str] = {
        "ENTER": "Return", "RETURN": "Return", "TAB": "Tab",
        "ESC": "Escape", "ESCAPE": "Escape", "BACKSPACE": "BackSpace",
        "DELETE": "Delete", "INSERT": "Insert", "HOME": "Home",
        "END": "End", "PGUP": "Prior", "PGDN": "Next",
        "UP": "Up", "DOWN": "Down", "LEFT": "Left", "RIGHT": "Right",
        "SPACE": "space",
        "F1": "F1", "F2": "F2", "F3": "F3", "F4": "F4",
        "F5": "F5", "F6": "F6", "F7": "F7", "F8": "F8",
        "F9": "F9", "F10": "F10", "F11": "F11", "F12": "F12",
    }
    _MOD_XDOTOOL: dict[str, str] = {
        "^": "ctrl", "+": "shift", "%": "alt",
    }

    result: list[str] = []
    i = 0
    length = len(keys)
    modifiers: list[str] = []
    # Buffer for consecutive plain characters – flushed as one "type" call
    plain_buf: list[str] = []

    def _flush_plain() -> None:
        if plain_buf:
            result.extend(["type", "--clearmodifiers", "".join(plain_buf)])
            plain_buf.clear()

    while i < length:
        ch = keys[i]

        if ch in _MOD_XDOTOOL:
            _flush_plain()
            modifiers.append(_MOD_XDOTOOL[ch])
            i += 1
            continue

        if ch == "{":
            end = keys.find("}", i + 1)
            if end == -1:
                plain_buf.append(ch)
                i += 1
                continue
            _flush_plain()
            key_name = keys[i + 1 : end].upper()
            xkey = _XDOTOOL_MAP.get(key_name, key_name)
            combo = "+".join(modifiers + [xkey])
            result.extend(["key", combo])
            modifiers.clear()
            i = end + 1
            continue

        # Literal character
        if modifiers:
            _flush_plain()
            combo = "+".join(modifiers + [ch])
            result.extend(["key", combo])
            modifiers.clear()
        else:
            plain_buf.append(ch)
        i += 1

    _flush_plain()
    return result


# ---------------------------------------------------------------------------
# Mouse helpers (via XTest / xdotool)
# ---------------------------------------------------------------------------


def mouse_click_atspi(
    x: int,
    y: int,
    double: bool = False,
    button: str = "left",
) -> None:
    """
    Click at absolute screen coordinates.

    Attempts AT-SPI mouse event generation first, falls back to xdotool.
    """
    button_num = {"left": 1, "middle": 2, "right": 3}.get(button, 1)

    # Try pyatspi first
    try:
        require_atspi()
        import pyatspi  # type: ignore[import-untyped]

        reg = pyatspi.Registry
        reg.generateMouseEvent(x, y, f"b{button_num}c")
        if double:
            reg.generateMouseEvent(x, y, f"b{button_num}c")
        return
    except Exception:
        pass

    # Fallback: xdotool
    _mouse_click_xdotool(x, y, double=double, button_num=button_num)


def _mouse_click_xdotool(
    x: int,
    y: int,
    *,
    double: bool = False,
    button_num: int = 1,
) -> None:
    """Click via xdotool."""
    cmd = ["xdotool", "mousemove", str(x), str(y), "click"]
    if double:
        cmd.extend(["--repeat", "2", "--delay", "50"])
    cmd.append(str(button_num))
    subprocess.run(cmd, check=True)
