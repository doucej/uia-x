"""
macOS AXAPI backend – Node model and tree traversal.

Wraps AXUIElement objects in a stable :class:`Node` representation
that can be serialised, cached, and compared across inspections.  This is
the data layer; the :mod:`bridge` module builds the MCP-compatible
``UIABridge`` on top.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from uiax.backends.macos.util import (
    ax_action_names,
    ax_attribute,
    axapi_available,
    get_children,
    get_description,
    get_frame,
    get_role,
    get_selected_text,
    get_title,
    get_value,
    make_element_id,
    require_axapi,
    role_name,
    state_names,
)

# ---------------------------------------------------------------------------
# Node data class
# ---------------------------------------------------------------------------


@dataclass
class Node:
    """
    Stable internal representation of a macOS AXUIElement.

    Mirrors the fields exposed by the Windows and Linux backends so that
    the abstraction layer is platform-independent.
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
    selected_text: str | None = None
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
        if self.selected_text is not None:
            d["selected_text"] = self.selected_text
        if include_children:
            d["children"] = self.children
        return d


# ---------------------------------------------------------------------------
# Build a Node from a live AXUIElement
# ---------------------------------------------------------------------------


def node_from_element(element: Any) -> Node:
    """
    Create a :class:`Node` from a live AXUIElement.

    The resulting Node stores the AXUIElement in ``backend_data``
    so the bridge can use it for actions.
    """
    child_ids: list[str] = []
    try:
        for child in get_children(element):
            child_ids.append(make_element_id(child))
    except Exception:
        pass

    # Determine name: prefer AXTitle, fall back to AXDescription
    name = get_title(element) or get_description(element)

    return Node(
        id=make_element_id(element),
        name=name,
        role=role_name(element),
        states=state_names(element),
        rect=get_frame(element),
        children=child_ids,
        description=get_description(element),
        actions=ax_action_names(element),
        text=get_value(element),
        value=get_value(element),
        selected_text=get_selected_text(element),
        backend_data=element,
    )


# ---------------------------------------------------------------------------
# Tree-building helpers
# ---------------------------------------------------------------------------


def build_element_dict(element: Any, depth: int = 3) -> dict[str, Any]:
    """
    Recursively serialise an AXUIElement into a dict tree.

    Mirrors ``build_element_dict`` from the Linux backend and
    ``_element_to_dict`` from the Windows backend.

    Parameters
    ----------
    element : AXUIElement
        Root element to serialise.
    depth : int
        How many levels of children to expand.
    """
    node = node_from_element(element)
    result = node.to_dict(include_children=False)

    children: list[dict[str, Any]] = []
    if depth > 0:
        try:
            for child in get_children(element):
                children.append(build_element_dict(child, depth - 1))
        except Exception:
            pass
    result["children"] = children
    return result


# ---------------------------------------------------------------------------
# Find element by criteria
# ---------------------------------------------------------------------------


def find_element(
    root: Any,
    *,
    by: str = "name",
    value: str = "",
    index: int = 0,
) -> Any:
    """
    Locate an AXUIElement descendant matching the given selector.

    Parameters
    ----------
    root : AXUIElement
        Subtree root to search within.
    by : str
        Selector strategy: ``"name"``, ``"role"``, ``"description"``,
        ``"name_substring"``, ``"title"``, or ``"path"``.
    value : str
        Value to match against.
    index : int
        Zero-based index among matches.

    Returns
    -------
    AXUIElement
        The matched element.

    Raises
    ------
    LookupError
        If no match is found.
    """
    if by == "path":
        return _find_by_path(root, value)

    predicate_map: dict[str, Any] = {
        "name": lambda el: (get_title(el) or get_description(el)) == value,
        "title": lambda el: get_title(el) == value,
        "role": lambda el: role_name(el) == value.lower(),
        "description": lambda el: get_description(el) == value,
        "name_substring": lambda el: value.lower() in (
            get_title(el) or get_description(el) or ""
        ).lower(),
        "automation_id": lambda el: (
            ax_attribute(el, "AXIdentifier") or ""
        ) == value,
        "control_type": lambda el: role_name(el) == value.lower().replace("_", " "),
        "value": lambda el: (get_value(el) or "") == value,
    }

    predicate = predicate_map.get(by)
    if predicate is None:
        raise ValueError(f"Unknown selector strategy: {by!r}")

    matches = _collect_matches(root, predicate)
    if not matches:
        raise LookupError(f"No element matched by={by!r} value={value!r}")
    try:
        return matches[index]
    except IndexError:
        raise LookupError(
            f"Only {len(matches)} match(es) for by={by!r} value={value!r}, "
            f"but index={index} was requested."
        ) from None


def _find_by_path(root: Any, path: str) -> Any:
    """Navigate a ``/``-separated path of element titles."""
    parts = [p.strip() for p in path.split("/") if p.strip()]
    current = root
    for part in parts:
        found = False
        try:
            for child in get_children(current):
                title = get_title(child) or get_description(child)
                if title == part:
                    current = child
                    found = True
                    break
        except Exception:
            pass
        if not found:
            raise LookupError(
                f"Path segment {part!r} not found under "
                f"{get_title(current) or '(untitled)'!r}"
            )
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
            children = get_children(node)
            # Push children in reverse order so index 0 is visited first
            for child in reversed(children):
                stack.append(child)
        except Exception:
            pass
    return matches
