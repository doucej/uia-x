"""
macOS AXAPI utility helpers for the macOS accessibility backend.

Provides low-level wrappers around ApplicationServices / AXAPI via PyObjC to:
  - check accessibility trust status
  - enumerate accessible objects via AXUIElement
  - extract roles, subroles, titles, values, descriptions, and bounding rects
  - generate stable element identifiers from the AX hierarchy path
  - translate key names to macOS virtual key codes
  - synthesise keystrokes via Quartz CGEvent
  - synthesise mouse clicks via Quartz CGEvent
"""

from __future__ import annotations

import hashlib
import re
import time
from typing import Any

# ---------------------------------------------------------------------------
# Optional imports – guard so tests / CI can import on any platform.
# ---------------------------------------------------------------------------

_AXAPI_IMPORT_ERROR: str | None = None
_AXAPI_AVAILABLE = False

try:
    from ApplicationServices import (  # type: ignore[import-untyped]
        AXIsProcessTrusted,
        AXUIElementCreateApplication,
        AXUIElementCreateSystemWide,
        AXUIElementCopyAttributeNames,
        AXUIElementCopyAttributeValue,
        AXUIElementCopyActionNames,
        AXUIElementIsAttributeSettable,
        AXUIElementPerformAction,
        AXUIElementSetAttributeValue,
        kAXErrorSuccess,
    )
    from CoreFoundation import CFRange  # type: ignore[import-untyped]
    import Quartz  # type: ignore[import-untyped]

    _AXAPI_AVAILABLE = True
except Exception as _exc:  # noqa: BLE001
    _AXAPI_IMPORT_ERROR = f"{type(_exc).__name__}: {_exc}"


def require_axapi() -> None:
    """Raise a clear error if AXAPI / PyObjC bindings are not available."""
    if not _AXAPI_AVAILABLE:
        detail = f" ({_AXAPI_IMPORT_ERROR})" if _AXAPI_IMPORT_ERROR else ""
        raise RuntimeError(
            f"PyObjC (ApplicationServices) is not installed or the "
            f"accessibility API is unreachable.{detail}"
        )


def axapi_available() -> bool:
    """Return True if AXAPI / PyObjC was imported successfully."""
    return _AXAPI_AVAILABLE


def is_trusted() -> bool:
    """
    Return True if the current process is trusted for accessibility.

    On macOS, the user must grant accessibility permission to the Python
    interpreter (or Terminal.app / iTerm2) via System Preferences →
    Privacy & Security → Accessibility.
    """
    require_axapi()
    return bool(AXIsProcessTrusted())


# ---------------------------------------------------------------------------
# AXUIElement attribute helpers
# ---------------------------------------------------------------------------


def ax_attribute(element: Any, attribute: str) -> Any:
    """
    Retrieve a single AX attribute from an AXUIElement.

    Returns ``None`` if the attribute does not exist or an error occurs.
    """
    require_axapi()
    err, value = AXUIElementCopyAttributeValue(element, attribute, None)
    if err == kAXErrorSuccess:
        return value
    return None


def ax_attribute_names(element: Any) -> list[str]:
    """Return the list of attribute names supported by *element*."""
    require_axapi()
    err, names = AXUIElementCopyAttributeNames(element, None)
    if err == kAXErrorSuccess and names:
        return list(names)
    return []


def ax_action_names(element: Any) -> list[str]:
    """Return the list of action names supported by *element*."""
    require_axapi()
    err, names = AXUIElementCopyActionNames(element, None)
    if err == kAXErrorSuccess and names:
        return list(names)
    return []


def ax_is_settable(element: Any, attribute: str) -> bool:
    """Return True if *attribute* is settable on *element*."""
    require_axapi()
    err, settable = AXUIElementIsAttributeSettable(element, attribute, None)
    if err == kAXErrorSuccess:
        return bool(settable)
    return False


def ax_set_attribute(element: Any, attribute: str, value: Any) -> bool:
    """
    Set an AX attribute on *element*.

    Returns True on success.
    """
    require_axapi()
    err = AXUIElementSetAttributeValue(element, attribute, value)
    return err == kAXErrorSuccess


def ax_perform_action(element: Any, action: str) -> bool:
    """
    Perform an accessibility action on *element*.

    Returns True on success.
    """
    require_axapi()
    err = AXUIElementPerformAction(element, action)
    return err == kAXErrorSuccess


# ---------------------------------------------------------------------------
# High-level attribute accessors
# ---------------------------------------------------------------------------


def get_role(element: Any) -> str:
    """Return the AXRole string (e.g. 'AXButton', 'AXWindow')."""
    role = ax_attribute(element, "AXRole")
    return str(role) if role else "unknown"


def get_subrole(element: Any) -> str:
    """Return the AXSubrole string (or empty)."""
    subrole = ax_attribute(element, "AXSubrole")
    return str(subrole) if subrole else ""


def get_title(element: Any) -> str:
    """Return the AXTitle (window/element title)."""
    title = ax_attribute(element, "AXTitle")
    return str(title) if title else ""


def get_description(element: Any) -> str:
    """Return the AXDescription."""
    desc = ax_attribute(element, "AXDescription")
    return str(desc) if desc else ""


def get_value(element: Any) -> str | None:
    """Return AXValue as a string, or None if absent."""
    val = ax_attribute(element, "AXValue")
    if val is not None:
        return str(val)
    return None


def get_selected_text(element: Any) -> str | None:
    """Return AXSelectedText, or None."""
    txt = ax_attribute(element, "AXSelectedText")
    if txt is not None:
        return str(txt)
    return None


def get_children(element: Any) -> list[Any]:
    """Return the list of AXUIElement children."""
    children = ax_attribute(element, "AXChildren")
    if children is not None:
        return list(children)
    return []


def _parse_axvalue_repr(val: Any) -> dict[str, float] | None:
    """
    Parse numeric fields from an AXValue's string representation.

    PyObjC's ``AXValueGetValue`` wrapper can fail on some macOS versions.
    As a fallback we regex-parse the repr, which looks like:

    - ``{value = x:356.000000 y:114.000000 type = kAXValueCGPointType}``
    - ``{value = w:230.000000 h:408.000000 type = kAXValueCGSizeType}``
    - ``{value = x:356.0 y:114.0 w:230.0 h:408.0 type = kAXValueCGRectType}``
    """
    s = str(val)
    pairs = re.findall(r"([xywh]):([0-9]+(?:\.[0-9]+)?)", s)
    if not pairs:
        return None
    return {k: float(v) for k, v in pairs}


def get_frame(element: Any) -> dict[str, int]:
    """
    Return ``{left, top, right, bottom}`` from AXFrame / AXPosition + AXSize.

    AXFrame is an AXValue of type CGRect on some elements.  Falls back to
    AXPosition + AXSize if AXFrame is unavailable.  If the PyObjC
    ``AXValueGetValue`` wrapper fails (known on some macOS/PyObjC combos),
    falls back to parsing the AXValue string representation.
    """
    require_axapi()

    # Try AXPosition + AXSize (most reliable)
    pos = ax_attribute(element, "AXPosition")
    size = ax_attribute(element, "AXSize")

    if pos is not None and size is not None:
        # Attempt 1: proper AXValueGetValue extraction
        try:
            import Quartz  # type: ignore[import-untyped]

            (ok_pos, point) = Quartz.AXValueGetValue(
                pos, Quartz.kAXValueTypeCGPoint, None
            )
            (ok_size, sz) = Quartz.AXValueGetValue(
                size, Quartz.kAXValueTypeCGSize, None
            )
            if ok_pos and ok_size:
                x = int(point.x)
                y = int(point.y)
                w = int(sz.width)
                h = int(sz.height)
                return {"left": x, "top": y, "right": x + w, "bottom": y + h}
        except Exception:
            pass

        # Attempt 2: parse string representations
        pos_d = _parse_axvalue_repr(pos)
        size_d = _parse_axvalue_repr(size)
        if pos_d and size_d and "x" in pos_d and "y" in pos_d and "w" in size_d and "h" in size_d:
            x = int(pos_d["x"])
            y = int(pos_d["y"])
            w = int(size_d["w"])
            h = int(size_d["h"])
            return {"left": x, "top": y, "right": x + w, "bottom": y + h}

    # Try AXFrame directly (CGRect)
    frame = ax_attribute(element, "AXFrame")
    if frame is not None:
        fd = _parse_axvalue_repr(frame)
        if fd and "x" in fd and "y" in fd and "w" in fd and "h" in fd:
            x = int(fd["x"])
            y = int(fd["y"])
            w = int(fd["w"])
            h = int(fd["h"])
            return {"left": x, "top": y, "right": x + w, "bottom": y + h}

    return {"left": 0, "top": 0, "right": 0, "bottom": 0}


def get_enabled(element: Any) -> bool:
    """Return True if the element is enabled (AXEnabled)."""
    val = ax_attribute(element, "AXEnabled")
    if val is not None:
        return bool(val)
    return True  # Default to enabled if attribute missing


def get_focused(element: Any) -> bool:
    """Return True if the element has focus (AXFocused)."""
    val = ax_attribute(element, "AXFocused")
    return bool(val) if val is not None else False


def get_selected(element: Any) -> bool:
    """Return True if the element is selected (AXSelected)."""
    val = ax_attribute(element, "AXSelected")
    return bool(val) if val is not None else False


def get_selected_children(element: Any) -> list[Any]:
    """Return the list of currently selected child elements."""
    children = ax_attribute(element, "AXSelectedChildren")
    if children is not None:
        return list(children)
    return []


# ---------------------------------------------------------------------------
# State computation
# ---------------------------------------------------------------------------


def state_names(element: Any) -> list[str]:
    """
    Compute a list of human-readable state strings for an AXUIElement.

    Mirrors the state list produced by the Linux backend.
    """
    states: list[str] = []
    if get_enabled(element):
        states.append("enabled")
    else:
        states.append("disabled")
    if get_focused(element):
        states.append("focused")
    if get_selected(element):
        states.append("selected")

    # Check additional boolean attributes
    for attr, state_true, state_false in [
        ("AXExpanded", "expanded", "collapsed"),
        ("AXMinimized", "minimized", None),
        ("AXMain", "main", None),
        ("AXFrontmost", "active", None),
    ]:
        val = ax_attribute(element, attr)
        if val is not None:
            if val:
                states.append(state_true)
            elif state_false:
                states.append(state_false)

    return states


# ---------------------------------------------------------------------------
# Role mapping – normalise AXRole strings to human-readable names
# ---------------------------------------------------------------------------

_ROLE_MAP: dict[str, str] = {
    "AXApplication": "application",
    "AXWindow": "window",
    "AXSheet": "sheet",
    "AXDrawer": "drawer",
    "AXGrowArea": "grow area",
    "AXButton": "button",
    "AXRadioButton": "radio button",
    "AXCheckBox": "check box",
    "AXPopUpButton": "pop up button",
    "AXMenuButton": "menu button",
    "AXStaticText": "text",
    "AXTextField": "text field",
    "AXTextArea": "text area",
    "AXScrollArea": "scroll area",
    "AXScrollBar": "scroll bar",
    "AXSlider": "slider",
    "AXSplitter": "splitter",
    "AXToolbar": "toolbar",
    "AXGroup": "group",
    "AXList": "list",
    "AXTable": "table",
    "AXRow": "row",
    "AXColumn": "column",
    "AXCell": "cell",
    "AXOutline": "outline",
    "AXBrowser": "browser",
    "AXTabGroup": "tab group",
    "AXTab": "tab",
    "AXSplitGroup": "split group",
    "AXImage": "image",
    "AXLink": "link",
    "AXMenuBar": "menu bar",
    "AXMenu": "menu",
    "AXMenuItem": "menu item",
    "AXValueIndicator": "value indicator",
    "AXComboBox": "combo box",
    "AXDisclosureTriangle": "disclosure triangle",
    "AXProgressIndicator": "progress indicator",
    "AXBusyIndicator": "busy indicator",
    "AXRelevanceIndicator": "relevance indicator",
    "AXColorWell": "color well",
    "AXRuler": "ruler",
    "AXRulerMarker": "ruler marker",
    "AXGrid": "grid",
    "AXLevelIndicator": "level indicator",
    "AXLayoutArea": "layout area",
    "AXLayoutItem": "layout item",
    "AXHandle": "handle",
    "AXPopover": "popover",
    "AXWebArea": "web area",
    "AXHeading": "heading",
    "AXUnknown": "unknown",
}


def role_name(element: Any) -> str:
    """
    Return a normalised, human-readable role string.

    Maps AXRole values like ``'AXButton'`` to ``'button'``.  Falls back to
    stripping the ``AX`` prefix and lowering if the role is not in the map.
    """
    raw = get_role(element)
    if raw in _ROLE_MAP:
        return _ROLE_MAP[raw]
    # Fallback: strip AX prefix and lower-case
    if raw.startswith("AX"):
        return raw[2:].lower()
    return raw.lower()


# ---------------------------------------------------------------------------
# Element ID generation
# ---------------------------------------------------------------------------


def _element_path(element: Any) -> str:
    """
    Build a stable path string from the AXUIElement hierarchy.

    Walks up the parent chain using AXParent, collecting role + title +
    child-index at each level to form a repeatable path.
    """
    parts: list[str] = []
    current = element
    depth = 0
    max_depth = 50  # Safety limit

    while current is not None and depth < max_depth:
        role = get_role(current)
        title = get_title(current) or get_description(current)

        # Compute index among siblings
        idx = 0
        parent = ax_attribute(current, "AXParent")
        if parent is not None:
            siblings = get_children(parent)
            for i, sib in enumerate(siblings):
                # Compare by identity (CFEqual) – pyobjc handles this
                if sib == current:
                    idx = i
                    break

        parts.append(f"{role}:{title}:{idx}")
        current = parent
        depth += 1

    parts.reverse()
    return "/".join(parts)


def make_element_id(element: Any) -> str:
    """
    Generate a stable, short ID for an AXUIElement.

    Uses a SHA-1 hash of the element path so callers get a fixed-length
    opaque identifier usable as a dictionary key.
    """
    path = _element_path(element)
    return hashlib.sha1(path.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Application / window enumeration
# ---------------------------------------------------------------------------


def get_system_wide_element() -> Any:
    """Return the system-wide AXUIElement."""
    require_axapi()
    return AXUIElementCreateSystemWide()


def get_running_apps() -> list[dict[str, Any]]:
    """
    Enumerate running GUI applications via NSWorkspace.

    Returns a list of dicts with ``pid``, ``name``, and ``bundle_id``.
    """
    require_axapi()
    from AppKit import NSWorkspace  # type: ignore[import-untyped]

    ws = NSWorkspace.sharedWorkspace()
    result: list[dict[str, Any]] = []
    for app in ws.runningApplications():
        # Filter to regular GUI apps (activationPolicy == 0 means NSApplicationActivationPolicyRegular)
        if app.activationPolicy() == 0:
            result.append({
                "pid": app.processIdentifier(),
                "name": app.localizedName() or "",
                "bundle_id": app.bundleIdentifier() or "",
            })
    return result


def get_app_element(pid: int) -> Any:
    """Create an AXUIElement for the application with the given PID."""
    require_axapi()
    return AXUIElementCreateApplication(pid)


def get_app_windows(pid: int) -> list[Any]:
    """Return the list of AXWindow elements for an application by PID."""
    app = get_app_element(pid)
    windows = ax_attribute(app, "AXWindows")
    if windows is not None:
        return list(windows)
    return []


def list_all_windows() -> list[dict[str, Any]]:
    """
    Enumerate all top-level windows across all running GUI applications.

    Returns a list of dicts with window metadata suitable for the
    ``list_windows`` MCP tool.
    """
    results: list[dict[str, Any]] = []
    for app_info in get_running_apps():
        pid = app_info["pid"]
        try:
            for win in get_app_windows(pid):
                title = get_title(win) or ""
                raw_role = get_role(win)
                rect = get_frame(win)
                states = state_names(win)

                # Generate a stable handle
                eid = make_element_id(win)
                hwnd = int(eid[:8], 16) & 0xFFFFFFFF

                results.append({
                    "hwnd": hwnd,
                    "hwnd_hex": hex(hwnd),
                    "title": title,
                    "class_name": role_name(win),
                    "pid": pid,
                    "process_name": app_info["name"],
                    "bundle_id": app_info.get("bundle_id", ""),
                    "visible": "minimized" not in states,
                    "rect": rect,
                    "_ax_element": win,
                    "_app_pid": pid,
                })
        except Exception:
            continue
    return results


# ---------------------------------------------------------------------------
# Keystroke synthesis via Quartz CGEvent
# ---------------------------------------------------------------------------

# Map from common key names (pywinauto / SendKeys-style) to macOS virtual
# key codes.  See Events.h / Carbon HIToolbox.
_KEYCODE_MAP: dict[str, int] = {
    "RETURN": 36,
    "ENTER": 36,
    "TAB": 48,
    "SPACE": 49,
    "DELETE": 51,
    "BACKSPACE": 51,
    "ESCAPE": 53,
    "ESC": 53,
    "COMMAND": 55,
    "CMD": 55,
    "SHIFT": 56,
    "CAPSLOCK": 57,
    "OPTION": 58,
    "ALT": 58,
    "CONTROL": 59,
    "CTRL": 59,
    "RIGHT_SHIFT": 60,
    "RIGHT_OPTION": 61,
    "RIGHT_CONTROL": 62,
    "FN": 63,
    "F1": 122, "F2": 120, "F3": 99, "F4": 118,
    "F5": 96, "F6": 97, "F7": 98, "F8": 100,
    "F9": 101, "F10": 109, "F11": 103, "F12": 111,
    "F13": 105, "F14": 107, "F15": 113,
    "HOME": 115,
    "END": 119,
    "PGUP": 116,
    "PAGEUP": 116,
    "PGDN": 121,
    "PAGEDOWN": 121,
    "LEFT": 123,
    "RIGHT": 124,
    "DOWN": 125,
    "UP": 126,
    "DEL": 117,       # Forward delete
    "INSERT": 114,     # Help key on Mac keyboards
    "INS": 114,
    "PRINTSCREEN": 105,
    "PRTSC": 105,
    "NUMLOCK": 71,
    "SCROLLLOCK": 107,
}

# Map from printable ASCII characters to virtual keycodes (US QWERTY layout)
_CHAR_KEYCODE_MAP: dict[str, tuple[int, bool]] = {
    "a": (0, False), "b": (11, False), "c": (8, False), "d": (2, False),
    "e": (14, False), "f": (3, False), "g": (5, False), "h": (4, False),
    "i": (34, False), "j": (38, False), "k": (40, False), "l": (37, False),
    "m": (46, False), "n": (45, False), "o": (31, False), "p": (35, False),
    "q": (12, False), "r": (15, False), "s": (1, False), "t": (17, False),
    "u": (32, False), "v": (9, False), "w": (13, False), "x": (7, False),
    "y": (16, False), "z": (6, False),
    "A": (0, True), "B": (11, True), "C": (8, True), "D": (2, True),
    "E": (14, True), "F": (3, True), "G": (5, True), "H": (4, True),
    "I": (34, True), "J": (38, True), "K": (40, True), "L": (37, True),
    "M": (46, True), "N": (45, True), "O": (31, True), "P": (35, True),
    "Q": (12, True), "R": (15, True), "S": (1, True), "T": (17, True),
    "U": (32, True), "V": (9, True), "W": (13, True), "X": (7, True),
    "Y": (16, True), "Z": (6, True),
    "0": (29, False), "1": (18, False), "2": (19, False), "3": (20, False),
    "4": (21, False), "5": (23, False), "6": (22, False), "7": (26, False),
    "8": (28, False), "9": (25, False),
    " ": (49, False),
    "-": (27, False), "=": (24, False), "[": (33, False), "]": (30, False),
    "\\": (42, False), ";": (41, False), "'": (39, False), ",": (43, False),
    ".": (47, False), "/": (44, False), "`": (50, False),
    "!": (18, True), "@": (19, True), "#": (20, True), "$": (21, True),
    "%": (23, True), "^": (22, True), "&": (26, True), "*": (28, True),
    "(": (25, True), ")": (29, True),
    "_": (27, True), "+": (24, True), "{": (33, True), "}": (30, True),
    "|": (42, True), ":": (41, True), '"': (39, True), "<": (43, True),
    ">": (47, True), "?": (44, True), "~": (50, True),
}

# Modifier prefixes in pywinauto / SendKeys notation
_MODIFIER_FLAGS: dict[str, int] = {}


def _init_modifier_flags() -> None:
    """Populate modifier flag mappings (deferred to avoid import at module level)."""
    global _MODIFIER_FLAGS
    if _MODIFIER_FLAGS:
        return
    try:
        import Quartz  # type: ignore[import-untyped]
        _MODIFIER_FLAGS.update({
            "^": Quartz.kCGEventFlagMaskControl,
            "+": Quartz.kCGEventFlagMaskShift,
            "%": Quartz.kCGEventFlagMaskAlternate,
            "@": Quartz.kCGEventFlagMaskCommand,
        })
    except Exception:
        _MODIFIER_FLAGS.update({
            "^": 1 << 18,   # kCGEventFlagMaskControl
            "+": 1 << 17,   # kCGEventFlagMaskShift
            "%": 1 << 19,   # kCGEventFlagMaskAlternate
            "@": 1 << 20,   # kCGEventFlagMaskCommand
        })


def send_keys_quartz(keys: str) -> None:
    """
    Send keystrokes via Quartz CGEvent.

    Interprets a simplified subset of pywinauto / SendKeys notation:
      - Plain characters are typed literally.
      - ``{KEY}`` sends a named special key (e.g. ``{ENTER}``).
      - ``^``, ``+``, ``%`` act as modifier prefixes (Ctrl, Shift, Alt)
        for the next character or ``{KEY}`` group.
      - ``@`` acts as a Command modifier prefix (macOS-specific).
    """
    require_axapi()
    _init_modifier_flags()
    import Quartz as Q  # type: ignore[import-untyped]

    source = Q.CGEventSourceCreate(Q.kCGEventSourceStateHIDSystemState)

    i = 0
    length = len(keys)
    held_flags = 0

    while i < length:
        ch = keys[i]

        # Modifier prefix
        if ch in _MODIFIER_FLAGS:
            held_flags |= _MODIFIER_FLAGS[ch]
            i += 1
            continue

        # Special key in braces: {KEY}
        if ch == "{":
            end = keys.find("}", i + 1)
            if end == -1:
                _type_char_quartz(source, ch, held_flags)
                held_flags = 0
                i += 1
                continue
            key_name = keys[i + 1 : end].upper()
            keycode = _KEYCODE_MAP.get(key_name)
            if keycode is not None:
                _send_keycode(source, keycode, held_flags)
            held_flags = 0
            i = end + 1
            continue

        # Literal character
        _type_char_quartz(source, ch, held_flags)
        held_flags = 0
        i += 1


def _send_keycode(source: Any, keycode: int, flags: int = 0) -> None:
    """Send a key-down + key-up for the given virtual keycode."""
    import Quartz as Q  # type: ignore[import-untyped]

    key_down = Q.CGEventCreateKeyboardEvent(source, keycode, True)
    key_up = Q.CGEventCreateKeyboardEvent(source, keycode, False)
    if flags:
        Q.CGEventSetFlags(key_down, flags | Q.CGEventGetFlags(key_down))
        Q.CGEventSetFlags(key_up, flags | Q.CGEventGetFlags(key_up))
    Q.CGEventPost(Q.kCGHIDEventTap, key_down)
    Q.CGEventPost(Q.kCGHIDEventTap, key_up)
    time.sleep(0.01)  # Small delay for event processing


def _type_char_quartz(source: Any, ch: str, flags: int = 0) -> None:
    """Type a single character via CGEvent."""
    import Quartz as Q  # type: ignore[import-untyped]

    entry = _CHAR_KEYCODE_MAP.get(ch)
    if entry:
        keycode, needs_shift = entry
        effective_flags = flags
        if needs_shift:
            effective_flags |= Q.kCGEventFlagMaskShift
        _send_keycode(source, keycode, effective_flags)
    else:
        # Fall back to Unicode string event
        keycode = 0
        key_down = Q.CGEventCreateKeyboardEvent(source, keycode, True)
        Q.CGEventKeyboardSetUnicodeString(key_down, len(ch), ch)
        if flags:
            Q.CGEventSetFlags(key_down, flags | Q.CGEventGetFlags(key_down))
        key_up = Q.CGEventCreateKeyboardEvent(source, keycode, False)
        Q.CGEventPost(Q.kCGHIDEventTap, key_down)
        Q.CGEventPost(Q.kCGHIDEventTap, key_up)
        time.sleep(0.01)


def type_text_quartz(text: str) -> None:
    """
    Type plain text literally via Quartz CGEvent, character by character.

    Unlike :func:`send_keys_quartz`, no special encoding is needed — spaces,
    punctuation, and symbols are all typed as-is.  ``\\n`` is sent as Return
    (keycode 36).
    """
    require_axapi()
    _init_modifier_flags()
    import Quartz as Q  # type: ignore[import-untyped]

    _RETURN_KEYCODE = 36

    source = Q.CGEventSourceCreate(Q.kCGEventSourceStateHIDSystemState)
    for ch in text:
        if ch == "\n":
            _send_keycode(source, _RETURN_KEYCODE, 0)
        elif ch == "\t":
            _send_keycode(source, 48, 0)  # Tab keycode
        else:
            _type_char_quartz(source, ch, 0)


# ---------------------------------------------------------------------------
# Mouse helpers via Quartz CGEvent
# ---------------------------------------------------------------------------


def mouse_click_quartz(
    x: int,
    y: int,
    double: bool = False,
    button: str = "left",
) -> None:
    """
    Click at absolute screen coordinates using Quartz CGEvent.

    Parameters
    ----------
    x, y : int
        Screen coordinates (pixels, origin at top-left).
    double : bool
        If True, send a double-click.
    button : str
        ``"left"`` (default), ``"right"``, or ``"middle"``.
    """
    require_axapi()
    import Quartz as Q  # type: ignore[import-untyped]

    point = Q.CGPointMake(float(x), float(y))
    source = Q.CGEventSourceCreate(Q.kCGEventSourceStateHIDSystemState)

    button_map = {
        "left": (Q.kCGEventLeftMouseDown, Q.kCGEventLeftMouseUp, Q.kCGMouseButtonLeft),
        "right": (Q.kCGEventRightMouseDown, Q.kCGEventRightMouseUp, Q.kCGMouseButtonRight),
        "middle": (Q.kCGEventOtherMouseDown, Q.kCGEventOtherMouseUp, Q.kCGMouseButtonCenter),
    }

    down_type, up_type, btn = button_map.get(button, button_map["left"])

    click_count = 2 if double else 1

    for click_num in range(1, click_count + 1):
        down = Q.CGEventCreateMouseEvent(source, down_type, point, btn)
        up = Q.CGEventCreateMouseEvent(source, up_type, point, btn)
        Q.CGEventSetIntegerValueField(down, Q.kCGMouseEventClickState, click_num)
        Q.CGEventSetIntegerValueField(up, Q.kCGMouseEventClickState, click_num)
        Q.CGEventPost(Q.kCGHIDEventTap, down)
        Q.CGEventPost(Q.kCGHIDEventTap, up)
        time.sleep(0.05)


# ---------------------------------------------------------------------------
# Bounding rect helper (alias for get_frame, for Linux-compat naming)
# ---------------------------------------------------------------------------

bounding_rect = get_frame
