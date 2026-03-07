"""
AT-SPI2 backend – Node model and tree traversal.

Wraps AT-SPI accessible objects in a stable :class:`Node` representation
that can be serialised, cached, and compared across inspections.  This is
the data layer; the :mod:`bridge` module builds the MCP-compatible
``UIABridge`` on top.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from uiax.backends.linux.util import (
    atspi_available,
    bounding_rect,
    get_actions,
    get_description,
    get_text_content,
    get_value,
    make_element_id,
    require_atspi,
    role_name,
    state_names,
)

# ---------------------------------------------------------------------------
# Node data class
# ---------------------------------------------------------------------------

# AT-SPI roles that indicate an element is interactive (a control the user
# can activate), as opposed to a passive display element (label, text, etc.).
# When multiple elements share the same accessible name, interactive ones are
# preferred by find_accessible so that uia_invoke(name='=') hits the button
# rather than a history label also named '='.
_INTERACTIVE_ROLES = frozenset({
    "button", "push button", "toggle button", "check box", "radio button",
    "combo box", "list item", "menu item", "menu", "spin button",
    "slider", "scroll bar", "entry", "password text", "text",
    "tree item", "table cell", "page tab", "link",
})


@dataclass
class Node:
    """
    Stable internal representation of an AT-SPI accessible element.

    Mirrors the fields exposed by the Windows backend so that the
    abstraction layer is platform-independent.
    """

    id: str
    name: str
    role: str
    states: list[str] = field(default_factory=list)
    rect: dict[str, int] = field(
        default_factory=lambda: {"left": 0, "top": 0, "right": 0, "bottom": 0}
    )
    children: list[str] = field(default_factory=list)
    description: str = ""
    actions: list[str] = field(default_factory=list)
    text: str | None = None
    value: str | None = None
    backend_data: Any = None

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self, *, include_children: bool = True) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        d: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "states": self.states,
            "rect": self.rect,
            "description": self.description,
            "actions": self.actions,
        }
        if self.text is not None:
            d["text"] = self.text
        if self.value is not None:
            d["value"] = self.value
        if include_children:
            d["children"] = self.children
        return d


# ---------------------------------------------------------------------------
# Build a Node from a live AT-SPI accessible
# ---------------------------------------------------------------------------


def node_from_accessible(acc: Any) -> Node:
    """
    Create a :class:`Node` from a live ``pyatspi.Accessible`` object.

    The resulting Node stores the pyatspi accessible in ``backend_data``
    so the bridge can use it for actions.
    """
    child_ids: list[str] = []
    try:
        for i in range(acc.childCount):
            child = acc.getChildAtIndex(i)
            if child is not None:
                child_ids.append(make_element_id(child))
    except Exception:
        pass

    return Node(
        id=make_element_id(acc),
        name=acc.name or "",
        role=role_name(acc),
        states=state_names(acc),
        rect=bounding_rect(acc),
        children=child_ids,
        description=get_description(acc),
        actions=get_actions(acc),
        text=get_text_content(acc),
        value=get_value(acc),
        backend_data=acc,
    )


# ---------------------------------------------------------------------------
# Tree-building helpers
# ---------------------------------------------------------------------------


def build_element_dict(acc: Any, depth: int = 3) -> dict[str, Any]:
    """
    Recursively serialise an AT-SPI accessible into a dict tree.

    Mirrors ``_element_to_dict`` from the Windows backend.

    Parameters
    ----------
    acc : pyatspi.Accessible
        Root accessible to serialise.
    depth : int
        How many levels of children to expand.
    """
    node = node_from_accessible(acc)
    result = node.to_dict(include_children=False)

    children: list[dict[str, Any]] = []
    if depth > 0:
        try:
            for i in range(acc.childCount):
                child = acc.getChildAtIndex(i)
                if child is not None:
                    children.append(build_element_dict(child, depth - 1))
        except Exception:
            pass
    result["children"] = children
    return result


# ---------------------------------------------------------------------------
# Desktop enumeration
# ---------------------------------------------------------------------------


def get_desktop() -> Any:
    """Return the AT-SPI desktop object (index 0)."""
    require_atspi()
    import pyatspi  # type: ignore[import-untyped]

    return pyatspi.Registry.getDesktop(0)


def list_applications() -> list[Any]:
    """Return all applications registered on the accessibility bus."""
    desktop = get_desktop()
    apps: list[Any] = []
    for i in range(desktop.childCount):
        app = desktop.getChildAtIndex(i)
        if app is not None:
            apps.append(app)
    return apps


def list_top_level_windows() -> list[Any]:
    """
    Return all top-level window accessibles across all applications.

    A "top-level window" is an accessible with role ``ROLE_FRAME``,
    ``ROLE_WINDOW``, or ``ROLE_DIALOG`` that is a direct child of an
    application.
    """
    require_atspi()
    import pyatspi  # type: ignore[import-untyped]

    _WINDOW_ROLES = {
        pyatspi.ROLE_FRAME,
        pyatspi.ROLE_WINDOW,
        pyatspi.ROLE_DIALOG,
    }
    windows: list[Any] = []
    for app in list_applications():
        try:
            for i in range(app.childCount):
                child = app.getChildAtIndex(i)
                if child is not None:
                    try:
                        if child.getRole() in _WINDOW_ROLES:
                            windows.append(child)
                    except Exception:
                        pass
        except Exception:
            pass
    return windows


# ---------------------------------------------------------------------------
# Find element by criteria
# ---------------------------------------------------------------------------


def find_accessible(
    root: Any,
    *,
    by: str = "name",
    value: str = "",
    index: int = 0,
    role_filter: str = "",
    prefer_interactive: bool = False,
) -> Any:
    """
    Locate an accessible descendant matching the given selector.

    Parameters
    ----------
    root : pyatspi.Accessible
        Subtree root to search within.
    by : str
        Selector strategy: ``"name"``, ``"role"``, ``"description"``,
        ``"name_substring"``, or ``"path"``.
    value : str
        Value to match against.
    index : int
        Zero-based index among matches.
    role_filter : str
        Optional additional role constraint.  When non-empty, only elements
        whose AT-SPI role matches this string (case-insensitive) are counted
        toward *index*.  Useful when multiple element types share a name
        (e.g. a digit button ``'8'`` and a history label ``'8'``).
    prefer_interactive : bool
        When True and no *role_filter* is given, elements with interactive
        roles (button, check box, menu item, …) are sorted before passive
        display elements (label, static text, …) so that ``index=0`` returns
        the actionable control rather than a same-named content label.

    Returns
    -------
    pyatspi.Accessible
        The matched accessible.

    Raises
    ------
    LookupError
        If no match is found.
    """
    require_atspi()

    if by == "path":
        return _find_by_path(root, value)

    predicate_map: dict[str, Any] = {
        "name": lambda acc: (acc.name or "") == value,
        "role": lambda acc: role_name(acc) == value.lower(),
        "description": lambda acc: get_description(acc) == value,
        "name_substring": lambda acc: value.lower() in (acc.name or "").lower(),
        "automation_id": lambda acc: _get_atspi_id(acc) == value,
        "control_type": lambda acc: role_name(acc) == value.lower().replace("_", " "),
    }

    predicate = predicate_map.get(by)
    if predicate is None:
        raise ValueError(f"Unknown selector strategy: {by!r}")

    if role_filter:
        role_constraint = role_filter.lower()
        base_pred = predicate
        predicate = lambda acc: base_pred(acc) and role_name(acc) == role_constraint  # noqa: E731

    matches = _collect_matches(root, predicate)
    if not matches:
        detail = f" role={role_filter!r}" if role_filter else ""
        raise LookupError(f"No accessible matched by={by!r} value={value!r}{detail}")

    # Re-order so interactive elements come first (stable sort preserves DFS
    # order within each tier), but only when the caller hasn't already applied
    # an explicit role constraint.
    if prefer_interactive and not role_filter:
        matches.sort(key=lambda acc: 0 if role_name(acc) in _INTERACTIVE_ROLES else 1)

    try:
        return matches[index]
    except IndexError:
        detail = f" role={role_filter!r}" if role_filter else ""
        raise LookupError(
            f"Only {len(matches)} match(es) for by={by!r} value={value!r}{detail}, "
            f"but index={index} was requested."
        ) from None


def _find_by_path(root: Any, path: str) -> Any:
    """Navigate a ``/``-separated path of accessible names."""
    parts = [p.strip() for p in path.split("/") if p.strip()]
    current = root
    for part in parts:
        found = False
        try:
            for i in range(current.childCount):
                child = current.getChildAtIndex(i)
                if child is not None and (child.name or "") == part:
                    current = child
                    found = True
                    break
        except Exception:
            pass
        if not found:
            raise LookupError(f"Path segment {part!r} not found under {current.name!r}")
    return current


def _collect_matches(root: Any, predicate: Any) -> list[Any]:
    """DFS-collect all descendants that satisfy *predicate*."""
    matches: list[Any] = []
    stack = [root]
    while stack:
        node = stack.pop()
        try:
            if predicate(node):
                matches.append(node)
        except Exception:
            pass
        try:
            # Push children in reverse order so index 0 is visited first
            for i in range(node.childCount - 1, -1, -1):
                child = node.getChildAtIndex(i)
                if child is not None:
                    stack.append(child)
        except Exception:
            pass
    return matches


def _get_atspi_id(acc: Any) -> str:
    """
    Attempt to retrieve a toolkit-specific automation ID.

    GTK widgets expose an accessible ID via attributes; Qt uses
    ``objectName``.  Falls back to empty string.
    """
    try:
        attrs = acc.getAttributes()
        if attrs:
            attr_dict = dict(a.split(":", 1) for a in attrs if ":" in a)
            # GTK: "id", Qt: "objectName"
            return attr_dict.get("id", attr_dict.get("objectName", ""))
    except Exception:
        pass
    return ""
