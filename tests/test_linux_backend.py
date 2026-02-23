"""
Unit tests for the Linux AT-SPI2 backend.

These tests exercise the Node model, utility functions, and bridge logic
using mock AT-SPI objects — no live accessibility bus required.  They run
on any platform (including CI).

Integration tests that require a live AT-SPI bus and a running application
are in ``test_linux_integration.py``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from uiax.backends.linux.atspi_backend import (
    Node,
    build_element_dict,
    node_from_accessible,
)
from uiax.backends.linux.util import (
    _parse_keys_to_xdotool,
    bounding_rect,
    get_actions,
    get_description,
    get_text_content,
    get_value,
    make_element_id,
    role_name,
    set_text_content,
    state_names,
)


# ---------------------------------------------------------------------------
# Mock AT-SPI accessible objects
# ---------------------------------------------------------------------------


class _MockState:
    """Simulates a pyatspi state enum value."""

    def __init__(self, nick: str) -> None:
        self.value_nick = nick

    def __str__(self) -> str:
        return f"STATE_{self.value_nick.upper()}"


class _MockStateSet:
    """Simulates pyatspi StateSet."""

    def __init__(self, states: list[str]) -> None:
        self._states = [_MockState(s) for s in states]

    def getStates(self) -> list[_MockState]:
        return list(self._states)

    def contains(self, state: Any) -> bool:
        return any(s.value_nick == str(state) for s in self._states)


class _MockRole:
    """Simulates a pyatspi role enum value."""

    def __init__(self, nick: str) -> None:
        self.value_nick = nick

    def __str__(self) -> str:
        return f"ROLE_{self.value_nick.upper().replace(' ', '_')}"


class _MockExtents:
    """Simulates an AT-SPI bounding box."""

    def __init__(self, x: int, y: int, width: int, height: int) -> None:
        self.x = x
        self.y = y
        self.width = width
        self.height = height


class _MockComponent:
    """Simulates the AT-SPI Component interface."""

    def __init__(self, x: int = 10, y: int = 20, w: int = 300, h: int = 200) -> None:
        self._ext = _MockExtents(x, y, w, h)

    def getExtents(self, coord_type: Any) -> _MockExtents:
        return self._ext

    def grabFocus(self) -> bool:
        return True


class _MockAction:
    """Simulates the AT-SPI Action interface."""

    def __init__(self, names: list[str]) -> None:
        self._names = names
        self.nActions = len(names)
        self.invoked: list[int] = []

    def getName(self, index: int) -> str:
        return self._names[index]

    def doAction(self, index: int) -> bool:
        self.invoked.append(index)
        return True


class _MockText:
    """Simulates the AT-SPI Text interface."""

    def __init__(self, text: str = "") -> None:
        self._text = text
        self.characterCount = len(text)

    def getText(self, start: int, end: int) -> str:
        return self._text[start:end]


class _MockEditableText:
    """Simulates the AT-SPI EditableText interface."""

    def __init__(self, text: str = "") -> None:
        self._text = text

    def setTextContents(self, text: str) -> bool:
        self._text = text
        return True

    def deleteText(self, start: int, end: int) -> bool:
        self._text = self._text[:start] + self._text[end:]
        return True

    def insertText(self, pos: int, text: str, length: int) -> bool:
        self._text = self._text[:pos] + text + self._text[pos:]
        return True


class _MockValue:
    """Simulates the AT-SPI Value interface."""

    def __init__(self, val: float = 0.0) -> None:
        self.currentValue = val


class MockAccessible:
    """
    A lightweight mock of a pyatspi.Accessible object.

    Supports the subset of the AT-SPI API used by the backend.
    """

    def __init__(
        self,
        name: str = "",
        role: str = "frame",
        states: list[str] | None = None,
        children: list["MockAccessible"] | None = None,
        parent: "MockAccessible | None" = None,
        actions: list[str] | None = None,
        text: str | None = None,
        editable: bool = False,
        value: float | None = None,
        rect: tuple[int, int, int, int] = (10, 20, 300, 200),
        description: str = "",
        attributes: list[str] | None = None,
    ) -> None:
        self.name = name
        self._role = _MockRole(role)
        self._states = _MockStateSet(states or [])
        self.children_list = children or []
        self.childCount = len(self.children_list)
        self.parent = parent
        self._actions = _MockAction(actions or [])
        self._text = _MockText(text) if text is not None else None
        self._editable_text = _MockEditableText(text or "") if editable else None
        self._value = _MockValue(value) if value is not None else None
        self._component = _MockComponent(*rect)
        self.description = description
        self._attributes = attributes or []

        # Set parent references on children
        for child in self.children_list:
            child.parent = self

    def getRole(self) -> _MockRole:
        return self._role

    def getState(self) -> _MockStateSet:
        return self._states

    def getChildAtIndex(self, idx: int) -> "MockAccessible | None":
        if 0 <= idx < len(self.children_list):
            return self.children_list[idx]
        return None

    def getIndexInParent(self) -> int:
        if self.parent is not None:
            for i, child in enumerate(self.parent.children_list):
                if child is self:
                    return i
        return 0

    def queryComponent(self) -> _MockComponent:
        return self._component

    def queryAction(self) -> _MockAction:
        return self._actions

    def queryText(self) -> Any:
        if self._text is None:
            raise NotImplementedError("No Text interface")
        return self._text

    def queryEditableText(self) -> Any:
        if self._editable_text is None:
            raise NotImplementedError("No EditableText interface")
        return self._editable_text

    def queryValue(self) -> Any:
        if self._value is None:
            raise NotImplementedError("No Value interface")
        return self._value

    def getAttributes(self) -> list[str]:
        return self._attributes


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_notepad_tree() -> MockAccessible:
    """Build a Notepad-like mock accessible tree."""
    save_btn = MockAccessible(
        name="Save",
        role="push button",
        actions=["click"],
    )
    open_btn = MockAccessible(
        name="Open",
        role="push button",
        actions=["click"],
    )
    toolbar = MockAccessible(
        name="Toolbar",
        role="tool bar",
        children=[save_btn, open_btn],
    )
    editor = MockAccessible(
        name="Editor",
        role="text",
        text="Hello world",
        editable=True,
        states=["editable", "focusable"],
    )
    status = MockAccessible(
        name="Status Bar",
        role="status bar",
        text="Ready",
    )
    root = MockAccessible(
        name="Untitled - Notepad",
        role="frame",
        states=["active", "visible", "showing"],
        children=[toolbar, editor, status],
    )
    return root


@pytest.fixture()
def notepad_tree() -> MockAccessible:
    return _make_notepad_tree()


@pytest.fixture()
def notepad_root() -> MockAccessible:
    return _make_notepad_tree()


# ---------------------------------------------------------------------------
# Node model tests
# ---------------------------------------------------------------------------


class TestNode:
    def test_node_basic_fields(self):
        node = Node(
            id="abc123",
            name="Test Button",
            role="push button",
            states=["focusable", "visible"],
            rect={"left": 10, "top": 20, "right": 110, "bottom": 50},
            children=["child1", "child2"],
        )
        assert node.id == "abc123"
        assert node.name == "Test Button"
        assert node.role == "push button"
        assert len(node.states) == 2
        assert len(node.children) == 2

    def test_node_to_dict(self):
        node = Node(
            id="abc123",
            name="Test Button",
            role="push button",
            states=["focusable"],
            rect={"left": 0, "top": 0, "right": 100, "bottom": 30},
            children=["child1"],
            text="Hello",
            value="42",
        )
        d = node.to_dict()
        assert d["id"] == "abc123"
        assert d["name"] == "Test Button"
        assert d["text"] == "Hello"
        assert d["value"] == "42"
        assert d["children"] == ["child1"]

    def test_node_to_dict_omits_none_text(self):
        node = Node(id="x", name="", role="frame")
        d = node.to_dict()
        assert "text" not in d
        assert "value" not in d

    def test_node_to_dict_without_children(self):
        node = Node(id="x", name="", role="frame", children=["c1"])
        d = node.to_dict(include_children=False)
        assert "children" not in d


# ---------------------------------------------------------------------------
# Utility function tests
# ---------------------------------------------------------------------------


class TestUtils:
    def test_role_name(self, notepad_root: MockAccessible):
        assert role_name(notepad_root) == "frame"

    def test_state_names(self, notepad_root: MockAccessible):
        states = state_names(notepad_root)
        assert "active" in states
        assert "visible" in states

    def test_bounding_rect(self, notepad_root: MockAccessible):
        rect = bounding_rect(notepad_root)
        assert rect["left"] == 10
        assert rect["top"] == 20
        assert rect["right"] == 310  # 10 + 300
        assert rect["bottom"] == 220  # 20 + 200

    def test_get_description(self, notepad_root: MockAccessible):
        # No description set → empty string
        assert get_description(notepad_root) == ""

    def test_get_description_with_value(self):
        acc = MockAccessible(name="test", description="A description")
        assert get_description(acc) == "A description"

    def test_get_text_content(self, notepad_root: MockAccessible):
        # Root has no Text interface
        assert get_text_content(notepad_root) is None

    def test_get_text_content_with_text(self):
        acc = MockAccessible(name="editor", text="Hello world")
        assert get_text_content(acc) == "Hello world"

    def test_get_value(self):
        acc = MockAccessible(name="slider", value=42.0)
        assert get_value(acc) == "42.0"

    def test_get_value_none(self, notepad_root: MockAccessible):
        assert get_value(notepad_root) is None

    def test_get_actions(self):
        acc = MockAccessible(name="btn", actions=["click", "activate"])
        assert get_actions(acc) == ["click", "activate"]

    def test_get_actions_empty(self, notepad_root: MockAccessible):
        assert get_actions(notepad_root) == []

    def test_make_element_id_stable(self, notepad_root: MockAccessible):
        id1 = make_element_id(notepad_root)
        id2 = make_element_id(notepad_root)
        assert id1 == id2
        assert len(id1) == 16

    def test_make_element_id_differs_for_different_elements(self):
        a = MockAccessible(name="A", role="button")
        b = MockAccessible(name="B", role="button")
        assert make_element_id(a) != make_element_id(b)

    def test_set_text_content(self):
        acc = MockAccessible(name="editor", text="old", editable=True)
        assert set_text_content(acc, "new text") is True
        assert acc._editable_text._text == "new text"

    def test_set_text_content_not_editable(self, notepad_root: MockAccessible):
        assert set_text_content(notepad_root, "text") is False


# ---------------------------------------------------------------------------
# AT-SPI backend (tree building) tests
# ---------------------------------------------------------------------------


class TestAtspiBackend:
    def test_node_from_accessible(self, notepad_root: MockAccessible):
        node = node_from_accessible(notepad_root)
        assert node.name == "Untitled - Notepad"
        assert node.role == "frame"
        assert len(node.children) == 3  # toolbar, editor, status
        assert node.backend_data is notepad_root

    def test_build_element_dict_depth_zero(self, notepad_root: MockAccessible):
        d = build_element_dict(notepad_root, depth=0)
        assert d["name"] == "Untitled - Notepad"
        assert d["children"] == []

    def test_build_element_dict_depth_one(self, notepad_root: MockAccessible):
        d = build_element_dict(notepad_root, depth=1)
        assert d["name"] == "Untitled - Notepad"
        assert len(d["children"]) == 3
        # Children at depth 1 should have no grandchildren expanded
        for child in d["children"]:
            assert child["children"] == []

    def test_build_element_dict_full_depth(self, notepad_root: MockAccessible):
        d = build_element_dict(notepad_root, depth=3)
        assert d["name"] == "Untitled - Notepad"
        toolbar = d["children"][0]
        assert toolbar["name"] == "Toolbar"
        assert len(toolbar["children"]) == 2
        assert toolbar["children"][0]["name"] == "Save"

    def test_build_element_dict_includes_text(self):
        acc = MockAccessible(name="editor", role="text", text="Hello")
        d = build_element_dict(acc, depth=0)
        assert d["text"] == "Hello"

    def test_build_element_dict_includes_value(self):
        acc = MockAccessible(name="slider", role="slider", value=50.0)
        d = build_element_dict(acc, depth=0)
        assert d["value"] == "50.0"


# ---------------------------------------------------------------------------
# XDotool key translation tests
# ---------------------------------------------------------------------------


class TestKeyTranslation:
    def test_plain_text(self):
        tokens = _parse_keys_to_xdotool("hello")
        # Each character becomes: type --clearmodifiers <char>
        assert tokens.count("type") == 5

    def test_special_key(self):
        tokens = _parse_keys_to_xdotool("{ENTER}")
        assert "key" in tokens
        assert "Return" in tokens

    def test_modifier_plus_key(self):
        tokens = _parse_keys_to_xdotool("^c")
        assert "key" in tokens
        assert "ctrl+c" in tokens

    def test_shift_modifier(self):
        tokens = _parse_keys_to_xdotool("+{TAB}")
        assert "key" in tokens
        assert "shift+Tab" in tokens

    def test_alt_modifier(self):
        tokens = _parse_keys_to_xdotool("%{F4}")
        assert "key" in tokens
        assert "alt+F4" in tokens

    def test_mixed_input(self):
        tokens = _parse_keys_to_xdotool("abc{ENTER}")
        # 3 chars + 1 special key
        assert tokens.count("type") == 3
        assert "Return" in tokens


# ---------------------------------------------------------------------------
# find_accessible tests (using mock objects)
# ---------------------------------------------------------------------------


class TestFindAccessible:
    """Test find_accessible with mock AT-SPI objects."""

    def test_find_by_name(self, notepad_root: MockAccessible):
        """find_accessible with by='name' uses DFS matching."""
        from uiax.backends.linux.atspi_backend import find_accessible

        # Patch require_atspi to skip the import check
        with patch("uiax.backends.linux.atspi_backend.require_atspi"):
            result = find_accessible(notepad_root, by="name", value="Save")
            assert result.name == "Save"

    def test_find_by_role(self, notepad_root: MockAccessible):
        from uiax.backends.linux.atspi_backend import find_accessible

        with patch("uiax.backends.linux.atspi_backend.require_atspi"):
            result = find_accessible(notepad_root, by="role", value="tool bar")
            assert result.name == "Toolbar"

    def test_find_by_name_substring(self, notepad_root: MockAccessible):
        from uiax.backends.linux.atspi_backend import find_accessible

        with patch("uiax.backends.linux.atspi_backend.require_atspi"):
            result = find_accessible(
                notepad_root, by="name_substring", value="notepad"
            )
            assert result.name == "Untitled - Notepad"

    def test_find_by_path(self, notepad_root: MockAccessible):
        from uiax.backends.linux.atspi_backend import find_accessible

        with patch("uiax.backends.linux.atspi_backend.require_atspi"):
            result = find_accessible(
                notepad_root, by="path", value="Toolbar/Save"
            )
            assert result.name == "Save"

    def test_find_not_found_raises(self, notepad_root: MockAccessible):
        from uiax.backends.linux.atspi_backend import find_accessible

        with patch("uiax.backends.linux.atspi_backend.require_atspi"):
            with pytest.raises(LookupError, match="No accessible matched"):
                find_accessible(notepad_root, by="name", value="NonExistent")

    def test_find_by_index(self, notepad_root: MockAccessible):
        """Multiple matches with index selection."""
        from uiax.backends.linux.atspi_backend import find_accessible

        with patch("uiax.backends.linux.atspi_backend.require_atspi"):
            # "push button" role matches both Save and Open
            result = find_accessible(
                notepad_root, by="role", value="push button", index=1
            )
            assert result.name == "Open"

    def test_find_invalid_strategy(self, notepad_root: MockAccessible):
        from uiax.backends.linux.atspi_backend import find_accessible

        with patch("uiax.backends.linux.atspi_backend.require_atspi"):
            with pytest.raises(ValueError, match="Unknown selector"):
                find_accessible(notepad_root, by="invalid_strategy", value="x")
