"""
Unit tests for the macOS AXAPI backend.

These tests exercise the Node model, utility functions, and bridge logic
using mock AXUIElement objects — no live accessibility API required.  They
run on any platform (including CI).

Integration tests that require a live macOS session and Calculator.app
are in ``test_macos_integration.py``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from uiax.backends.macos.axapi_backend import (
    Node,
    build_element_dict,
    find_element,
    node_from_element,
)


# ---------------------------------------------------------------------------
# Mock AXUIElement objects
# ---------------------------------------------------------------------------


class MockAXUIElement:
    """
    A lightweight mock of a macOS AXUIElement for unit testing.

    Stores attributes in a dictionary which ``ax_attribute`` reads from,
    and supports the same interface the backend expects.
    """

    def __init__(
        self,
        *,
        role: str = "AXWindow",
        subrole: str = "",
        title: str = "",
        description: str = "",
        value: str | None = None,
        enabled: bool = True,
        focused: bool = False,
        selected: bool = False,
        children: list["MockAXUIElement"] | None = None,
        parent: "MockAXUIElement | None" = None,
        actions: list[str] | None = None,
        position: tuple[float, float] = (10.0, 20.0),
        size: tuple[float, float] = (300.0, 200.0),
        identifier: str = "",
        selected_text: str | None = None,
    ) -> None:
        self._children = children or []
        self._parent = parent
        self._actions = actions or []

        # Build the attribute dict that ax_attribute will read
        self._attrs: dict[str, Any] = {
            "AXRole": role,
            "AXTitle": title,
            "AXDescription": description,
            "AXEnabled": enabled,
            "AXFocused": focused,
            "AXSelected": selected,
            "AXChildren": self._children,
            "AXParent": parent,
        }
        if subrole:
            self._attrs["AXSubrole"] = subrole
        if value is not None:
            self._attrs["AXValue"] = value
        if identifier:
            self._attrs["AXIdentifier"] = identifier
        if selected_text is not None:
            self._attrs["AXSelectedText"] = selected_text

        # Position and Size are AXValue-wrapped CGPoint / CGSize in real AXAPI.
        # We use a mock object with .x, .y / .width, .height for the
        # get_frame extraction path.
        self._position = _MockCGPoint(*position)
        self._size = _MockCGSize(*size)
        self._attrs["AXPosition"] = _MockAXValue("point", self._position)
        self._attrs["AXSize"] = _MockAXValue("size", self._size)

        # Set parent references on children
        for child in self._children:
            child._attrs["AXParent"] = self
            child._parent = self

    def __eq__(self, other: Any) -> bool:
        return self is other

    def __hash__(self) -> int:
        return id(self)


class _MockCGPoint:
    def __init__(self, x: float = 0.0, y: float = 0.0) -> None:
        self.x = x
        self.y = y


class _MockCGSize:
    def __init__(self, width: float = 0.0, height: float = 0.0) -> None:
        self.width = width
        self.height = height


class _MockAXValue:
    """Simulate an AXValueRef wrapping a CGPoint or CGSize."""

    def __init__(self, kind: str, value: Any) -> None:
        self.kind = kind
        self.value = value


# ---------------------------------------------------------------------------
# Patching helpers – intercept AXAPI calls to use our mock objects
# ---------------------------------------------------------------------------

_UTIL_MODULE = "uiax.backends.macos.util"


def _mock_ax_attribute(element: Any, attribute: str) -> Any:
    """Drop-in replacement for ``ax_attribute`` using MockAXUIElement."""
    if isinstance(element, MockAXUIElement):
        return element._attrs.get(attribute)
    return None


def _mock_ax_action_names(element: Any) -> list[str]:
    """Drop-in replacement for ``ax_action_names``."""
    if isinstance(element, MockAXUIElement):
        return list(element._actions)
    return []


def _mock_ax_perform_action(element: Any, action: str) -> bool:
    if isinstance(element, MockAXUIElement):
        return action in element._actions
    return False


def _mock_ax_set_attribute(element: Any, attribute: str, value: Any) -> bool:
    if isinstance(element, MockAXUIElement):
        element._attrs[attribute] = value
        return True
    return False


def _mock_get_frame(element: Any) -> dict[str, int]:
    """Mock get_frame using stored position/size."""
    if isinstance(element, MockAXUIElement):
        pos = element._position
        sz = element._size
        return {
            "left": int(pos.x),
            "top": int(pos.y),
            "right": int(pos.x + sz.width),
            "bottom": int(pos.y + sz.height),
        }
    return {"left": 0, "top": 0, "right": 0, "bottom": 0}


def _mock_get_children(element: Any) -> list[Any]:
    if isinstance(element, MockAXUIElement):
        return list(element._children)
    return []


def _mock_get_title(element: Any) -> str:
    if isinstance(element, MockAXUIElement):
        return element._attrs.get("AXTitle", "") or ""
    return ""


def _mock_get_description(element: Any) -> str:
    if isinstance(element, MockAXUIElement):
        return element._attrs.get("AXDescription", "") or ""
    return ""


def _mock_get_role(element: Any) -> str:
    if isinstance(element, MockAXUIElement):
        return element._attrs.get("AXRole", "AXUnknown")
    return "AXUnknown"


def _mock_get_value(element: Any) -> str | None:
    if isinstance(element, MockAXUIElement):
        v = element._attrs.get("AXValue")
        return str(v) if v is not None else None
    return None


def _mock_get_selected_text(element: Any) -> str | None:
    if isinstance(element, MockAXUIElement):
        v = element._attrs.get("AXSelectedText")
        return str(v) if v is not None else None
    return None


def _mock_role_name(element: Any) -> str:
    role = _mock_get_role(element)
    from uiax.backends.macos.util import _ROLE_MAP

    if role in _ROLE_MAP:
        return _ROLE_MAP[role]
    if role.startswith("AX"):
        return role[2:].lower()
    return role.lower()


def _mock_state_names(element: Any) -> list[str]:
    states: list[str] = []
    if isinstance(element, MockAXUIElement):
        if element._attrs.get("AXEnabled", True):
            states.append("enabled")
        else:
            states.append("disabled")
        if element._attrs.get("AXFocused", False):
            states.append("focused")
        if element._attrs.get("AXSelected", False):
            states.append("selected")
    return states


def _mock_make_element_id(element: Any) -> str:
    """Generate a stable ID by walking the mock parent chain."""
    parts: list[str] = []
    current = element
    depth = 0
    while current is not None and depth < 50:
        if isinstance(current, MockAXUIElement):
            role = current._attrs.get("AXRole", "unknown")
            title = current._attrs.get("AXTitle", "")
            parent = current._attrs.get("AXParent")
            idx = 0
            if parent is not None and isinstance(parent, MockAXUIElement):
                for i, sib in enumerate(parent._children):
                    if sib is current:
                        idx = i
                        break
            parts.append(f"{role}:{title}:{idx}")
            current = parent
        else:
            break
        depth += 1
    parts.reverse()
    return hashlib.sha1("/".join(parts).encode()).hexdigest()[:16]


@pytest.fixture(autouse=True)
def _patch_axapi():
    """Patch all AXAPI-dependent functions so tests run without PyObjC."""
    patches = [
        patch(f"{_UTIL_MODULE}.require_axapi"),
        patch(f"{_UTIL_MODULE}.ax_attribute", side_effect=_mock_ax_attribute),
        patch(f"{_UTIL_MODULE}.ax_action_names", side_effect=_mock_ax_action_names),
        patch(f"{_UTIL_MODULE}.ax_perform_action", side_effect=_mock_ax_perform_action),
        patch(f"{_UTIL_MODULE}.ax_set_attribute", side_effect=_mock_ax_set_attribute),
        patch(f"{_UTIL_MODULE}.get_frame", side_effect=_mock_get_frame),
        patch(f"{_UTIL_MODULE}.get_children", side_effect=_mock_get_children),
        patch(f"{_UTIL_MODULE}.get_title", side_effect=_mock_get_title),
        patch(f"{_UTIL_MODULE}.get_description", side_effect=_mock_get_description),
        patch(f"{_UTIL_MODULE}.get_role", side_effect=_mock_get_role),
        patch(f"{_UTIL_MODULE}.get_value", side_effect=_mock_get_value),
        patch(f"{_UTIL_MODULE}.get_selected_text", side_effect=_mock_get_selected_text),
        patch(f"{_UTIL_MODULE}.role_name", side_effect=_mock_role_name),
        patch(f"{_UTIL_MODULE}.state_names", side_effect=_mock_state_names),
        patch(f"{_UTIL_MODULE}.make_element_id", side_effect=_mock_make_element_id),
        # Also patch the backend module's imported copies
        patch("uiax.backends.macos.axapi_backend.require_axapi"),
        patch("uiax.backends.macos.axapi_backend.ax_action_names", side_effect=_mock_ax_action_names),
        patch("uiax.backends.macos.axapi_backend.ax_attribute", side_effect=_mock_ax_attribute),
        patch("uiax.backends.macos.axapi_backend.get_children", side_effect=_mock_get_children),
        patch("uiax.backends.macos.axapi_backend.get_description", side_effect=_mock_get_description),
        patch("uiax.backends.macos.axapi_backend.get_frame", side_effect=_mock_get_frame),
        patch("uiax.backends.macos.axapi_backend.get_role", side_effect=_mock_get_role),
        patch("uiax.backends.macos.axapi_backend.get_selected_text", side_effect=_mock_get_selected_text),
        patch("uiax.backends.macos.axapi_backend.get_title", side_effect=_mock_get_title),
        patch("uiax.backends.macos.axapi_backend.get_value", side_effect=_mock_get_value),
        patch("uiax.backends.macos.axapi_backend.make_element_id", side_effect=_mock_make_element_id),
        patch("uiax.backends.macos.axapi_backend.role_name", side_effect=_mock_role_name),
        patch("uiax.backends.macos.axapi_backend.state_names", side_effect=_mock_state_names),
    ]
    started = [p.start() for p in patches]
    yield
    for p in patches:
        p.stop()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_calculator_tree() -> MockAXUIElement:
    """Build a Calculator.app-like mock AXUIElement tree."""
    btn_7 = MockAXUIElement(
        role="AXButton",
        title="7",
        actions=["AXPress"],
        position=(10.0, 100.0),
        size=(50.0, 40.0),
    )
    btn_8 = MockAXUIElement(
        role="AXButton",
        title="8",
        actions=["AXPress"],
        position=(70.0, 100.0),
        size=(50.0, 40.0),
    )
    btn_multiply = MockAXUIElement(
        role="AXButton",
        title="\u00d7",
        description="multiply",
        actions=["AXPress"],
        position=(130.0, 100.0),
        size=(50.0, 40.0),
    )
    btn_equals = MockAXUIElement(
        role="AXButton",
        title="=",
        description="equals",
        actions=["AXPress"],
        position=(190.0, 100.0),
        size=(50.0, 40.0),
    )
    display = MockAXUIElement(
        role="AXStaticText",
        title="Display",
        value="0",
        position=(10.0, 10.0),
        size=(300.0, 50.0),
    )
    button_group = MockAXUIElement(
        role="AXGroup",
        title="Buttons",
        children=[btn_7, btn_8, btn_multiply, btn_equals],
        position=(10.0, 80.0),
        size=(300.0, 200.0),
    )
    root = MockAXUIElement(
        role="AXWindow",
        title="Calculator",
        children=[display, button_group],
        enabled=True,
        focused=True,
        position=(0.0, 0.0),
        size=(320.0, 300.0),
    )
    return root


def _make_notepad_tree() -> MockAXUIElement:
    """Build a TextEdit.app-like mock AXUIElement tree."""
    save_btn = MockAXUIElement(
        role="AXButton",
        title="Save",
        actions=["AXPress"],
    )
    open_btn = MockAXUIElement(
        role="AXButton",
        title="Open",
        actions=["AXPress"],
    )
    toolbar = MockAXUIElement(
        role="AXToolbar",
        title="Toolbar",
        children=[save_btn, open_btn],
    )
    editor = MockAXUIElement(
        role="AXTextArea",
        title="Editor",
        value="Hello world",
        enabled=True,
        focused=True,
    )
    status = MockAXUIElement(
        role="AXStaticText",
        title="Status Bar",
        value="Ready",
    )
    root = MockAXUIElement(
        role="AXWindow",
        title="Untitled - TextEdit",
        children=[toolbar, editor, status],
        enabled=True,
    )
    return root


@pytest.fixture()
def calculator_tree() -> MockAXUIElement:
    return _make_calculator_tree()


@pytest.fixture()
def notepad_tree() -> MockAXUIElement:
    return _make_notepad_tree()


# ---------------------------------------------------------------------------
# Node model tests
# ---------------------------------------------------------------------------


class TestNode:
    def test_node_basic_fields(self):
        node = Node(
            id="abc123",
            name="Test Button",
            role="button",
            states=["enabled", "focused"],
            rect={"left": 10, "top": 20, "right": 110, "bottom": 50},
            children=["child1", "child2"],
        )
        assert node.id == "abc123"
        assert node.name == "Test Button"
        assert node.role == "button"
        assert len(node.states) == 2
        assert len(node.children) == 2

    def test_node_to_dict(self):
        node = Node(
            id="abc123",
            name="Test Button",
            role="button",
            states=["enabled"],
            rect={"left": 0, "top": 0, "right": 100, "bottom": 30},
            children=["child1"],
            text="Hello",
            value="42",
            selected_text="ell",
        )
        d = node.to_dict()
        assert d["id"] == "abc123"
        assert d["name"] == "Test Button"
        assert d["text"] == "Hello"
        assert d["value"] == "42"
        assert d["selected_text"] == "ell"
        assert d["children"] == ["child1"]

    def test_node_to_dict_omits_none_text(self):
        node = Node(id="x", name="", role="window")
        d = node.to_dict()
        assert "text" not in d
        assert "value" not in d
        assert "selected_text" not in d

    def test_node_to_dict_without_children(self):
        node = Node(id="x", name="", role="window", children=["c1"])
        d = node.to_dict(include_children=False)
        assert "children" not in d


# ---------------------------------------------------------------------------
# AXAPI backend (tree building) tests
# ---------------------------------------------------------------------------


class TestAXAPIBackend:
    def test_node_from_element(self, notepad_tree: MockAXUIElement):
        node = node_from_element(notepad_tree)
        assert node.name == "Untitled - TextEdit"
        assert node.role == "window"
        assert len(node.children) == 3  # toolbar, editor, status
        assert node.backend_data is notepad_tree

    def test_node_from_element_calculator(self, calculator_tree: MockAXUIElement):
        node = node_from_element(calculator_tree)
        assert node.name == "Calculator"
        assert node.role == "window"
        assert len(node.children) == 2  # display, button_group

    def test_build_element_dict_depth_zero(self, notepad_tree: MockAXUIElement):
        d = build_element_dict(notepad_tree, depth=0)
        assert d["name"] == "Untitled - TextEdit"
        assert d["children"] == []

    def test_build_element_dict_depth_one(self, notepad_tree: MockAXUIElement):
        d = build_element_dict(notepad_tree, depth=1)
        assert d["name"] == "Untitled - TextEdit"
        assert len(d["children"]) == 3
        for child in d["children"]:
            assert child["children"] == []

    def test_build_element_dict_full_depth(self, notepad_tree: MockAXUIElement):
        d = build_element_dict(notepad_tree, depth=3)
        assert d["name"] == "Untitled - TextEdit"
        toolbar = d["children"][0]
        assert toolbar["name"] == "Toolbar"
        assert len(toolbar["children"]) == 2
        assert toolbar["children"][0]["name"] == "Save"

    def test_build_element_dict_includes_value(self, calculator_tree: MockAXUIElement):
        d = build_element_dict(calculator_tree, depth=2)
        display = d["children"][0]
        assert display["name"] == "Display"
        assert display["value"] == "0"

    def test_build_element_dict_calculator_buttons(self, calculator_tree: MockAXUIElement):
        d = build_element_dict(calculator_tree, depth=3)
        button_group = d["children"][1]
        assert button_group["name"] == "Buttons"
        buttons = button_group["children"]
        assert len(buttons) == 4
        assert buttons[0]["name"] == "7"
        assert buttons[1]["name"] == "8"


# ---------------------------------------------------------------------------
# find_element tests
# ---------------------------------------------------------------------------


class TestFindElement:
    def test_find_by_name(self, notepad_tree: MockAXUIElement):
        result = find_element(notepad_tree, by="name", value="Save")
        assert _mock_get_title(result) == "Save"

    def test_find_by_role(self, notepad_tree: MockAXUIElement):
        result = find_element(notepad_tree, by="role", value="toolbar")
        assert _mock_get_title(result) == "Toolbar"

    def test_find_by_name_substring(self, notepad_tree: MockAXUIElement):
        result = find_element(notepad_tree, by="name_substring", value="textedit")
        assert _mock_get_title(result) == "Untitled - TextEdit"

    def test_find_by_description(self, calculator_tree: MockAXUIElement):
        result = find_element(calculator_tree, by="description", value="multiply")
        assert _mock_get_title(result) == "\u00d7"

    def test_find_by_path(self, notepad_tree: MockAXUIElement):
        result = find_element(notepad_tree, by="path", value="Toolbar/Save")
        assert _mock_get_title(result) == "Save"

    def test_find_not_found_raises(self, notepad_tree: MockAXUIElement):
        with pytest.raises(LookupError, match="No element matched"):
            find_element(notepad_tree, by="name", value="NonExistent")

    def test_find_by_index(self, notepad_tree: MockAXUIElement):
        """Multiple matches with index selection."""
        # "button" role matches both Save and Open
        result = find_element(notepad_tree, by="role", value="button", index=1)
        assert _mock_get_title(result) == "Open"

    def test_find_invalid_strategy(self, notepad_tree: MockAXUIElement):
        with pytest.raises(ValueError, match="Unknown selector"):
            find_element(notepad_tree, by="invalid_strategy", value="x")

    def test_find_calculator_button(self, calculator_tree: MockAXUIElement):
        result = find_element(calculator_tree, by="name", value="7")
        assert _mock_get_title(result) == "7"

    def test_find_by_value(self, calculator_tree: MockAXUIElement):
        result = find_element(calculator_tree, by="value", value="0")
        assert _mock_get_title(result) == "Display"


# ---------------------------------------------------------------------------
# Element ID stability tests
# ---------------------------------------------------------------------------


class TestElementId:
    def test_make_element_id_stable(self, notepad_tree: MockAXUIElement):
        id1 = _mock_make_element_id(notepad_tree)
        id2 = _mock_make_element_id(notepad_tree)
        assert id1 == id2
        assert len(id1) == 16

    def test_make_element_id_differs(self):
        a = MockAXUIElement(role="AXButton", title="A")
        b = MockAXUIElement(role="AXButton", title="B")
        assert _mock_make_element_id(a) != _mock_make_element_id(b)

    def test_make_element_id_child_vs_parent(self, notepad_tree: MockAXUIElement):
        parent_id = _mock_make_element_id(notepad_tree)
        child = notepad_tree._children[0]
        child_id = _mock_make_element_id(child)
        assert parent_id != child_id


# ---------------------------------------------------------------------------
# Bridge tests (with mock process manager)
# ---------------------------------------------------------------------------


class TestMacOSBridge:
    """Test the MacOSBridge using mock elements."""

    @pytest.fixture(autouse=True)
    def _setup_bridge(self, notepad_tree):
        """Patch the bridge's dependencies."""
        self.root = notepad_tree

        # Patch bridge-level imports
        self._patches = [
            patch("uiax.backends.macos.bridge.require_axapi"),
            patch("uiax.backends.macos.bridge.ax_action_names", side_effect=_mock_ax_action_names),
            patch("uiax.backends.macos.bridge.ax_attribute", side_effect=_mock_ax_attribute),
            patch("uiax.backends.macos.bridge.ax_perform_action", side_effect=_mock_ax_perform_action),
            patch("uiax.backends.macos.bridge.ax_set_attribute", side_effect=_mock_ax_set_attribute),
            patch("uiax.backends.macos.bridge.get_children", side_effect=_mock_get_children),
            patch("uiax.backends.macos.bridge.get_description", side_effect=_mock_get_description),
            patch("uiax.backends.macos.bridge.get_frame", side_effect=_mock_get_frame),
            patch("uiax.backends.macos.bridge.get_title", side_effect=_mock_get_title),
            patch("uiax.backends.macos.bridge.get_value", side_effect=_mock_get_value),
            patch("uiax.backends.macos.bridge.make_element_id", side_effect=_mock_make_element_id),
            patch("uiax.backends.macos.bridge.role_name", side_effect=_mock_role_name),
            patch("uiax.backends.macos.bridge.send_keys_quartz"),
            patch("uiax.backends.macos.bridge.mouse_click_quartz"),
        ]
        for p in self._patches:
            p.start()

        # Set up a mock process manager with the notepad tree attached
        from uiax.backends.macos.bridge import (
            MacOSBridge,
            MacOSProcessManager,
            reset_macos_process_manager,
        )

        reset_macos_process_manager()
        mock_pm = MacOSProcessManager()
        mock_pm._attached_window = self.root

        self._pm_patch = patch(
            "uiax.backends.macos.bridge.get_macos_process_manager",
            return_value=mock_pm,
        )
        self._pm_patch.start()

        self.bridge = MacOSBridge()

        yield

        self._pm_patch.stop()
        for p in self._patches:
            p.stop()
        reset_macos_process_manager()

    def test_inspect_root(self):
        result = self.bridge.inspect({})
        assert result["name"] == "Untitled - TextEdit"
        assert result["role"] == "window"

    def test_inspect_with_depth(self):
        result = self.bridge.inspect({"depth": 1})
        assert len(result["children"]) == 3
        for child in result["children"]:
            assert child["children"] == []

    def test_inspect_by_name(self):
        result = self.bridge.inspect({"by": "name", "value": "Save"})
        assert result["name"] == "Save"
        assert result["role"] == "button"

    def test_invoke_button(self):
        # Should not raise for a button with AXPress action
        self.bridge.invoke({"by": "name", "value": "Save"})

    def test_invoke_no_action_raises(self):
        from server.uia_bridge import PatternNotSupportedError

        # The root window has no actions → should raise
        # Patch ax_set_attribute to return False so it exhausts all options
        with patch("uiax.backends.macos.bridge.ax_set_attribute", return_value=False):
            with pytest.raises(PatternNotSupportedError):
                self.bridge.invoke({})

    def test_set_value(self):
        # The editor is a text area with AXValue
        self.bridge.set_value({"by": "name", "value": "Editor"}, "New text")

    def test_send_keys(self):
        self.bridge.send_keys("hello")

    def test_send_keys_with_target(self):
        self.bridge.send_keys("hello", {"by": "name", "value": "Editor"})

    def test_legacy_invoke(self):
        self.bridge.legacy_invoke({"by": "name", "value": "Save"})

    def test_legacy_invoke_no_action_raises(self):
        from server.uia_bridge import PatternNotSupportedError

        with pytest.raises(PatternNotSupportedError):
            self.bridge.legacy_invoke({})

    def test_mouse_click(self):
        self.bridge.mouse_click(100, 200)

    def test_mouse_click_double(self):
        self.bridge.mouse_click(100, 200, double=True)

    def test_mouse_click_right(self):
        self.bridge.mouse_click(100, 200, button="right")

    def test_element_not_found_raises(self):
        from server.uia_bridge import ElementNotFoundError

        with pytest.raises(ElementNotFoundError):
            self.bridge.inspect({"by": "name", "value": "NonExistentElement"})

    def test_target_not_attached_raises(self):
        from server.uia_bridge import TargetNotFoundError
        from uiax.backends.macos.bridge import MacOSProcessManager

        empty_pm = MacOSProcessManager()
        with patch(
            "uiax.backends.macos.bridge.get_macos_process_manager",
            return_value=empty_pm,
        ):
            with pytest.raises(TargetNotFoundError):
                self.bridge.inspect({})


# ---------------------------------------------------------------------------
# Process manager tests
# ---------------------------------------------------------------------------


class TestMacOSProcessManager:
    def test_list_windows_returns_list(self):
        from uiax.backends.macos.bridge import MacOSProcessManager

        with patch("uiax.backends.macos.bridge.list_all_windows", return_value=[]):
            pm = MacOSProcessManager()
            result = pm.list_windows()
            assert result == []

    def test_attach_by_title(self):
        from uiax.backends.macos.bridge import MacOSProcessManager

        mock_win = MockAXUIElement(role="AXWindow", title="Calculator")
        window_list = [{
            "hwnd": 12345,
            "hwnd_hex": hex(12345),
            "title": "Calculator",
            "class_name": "window",
            "pid": 100,
            "process_name": "Calculator",
            "bundle_id": "com.apple.calculator",
            "visible": True,
            "rect": {"left": 0, "top": 0, "right": 320, "bottom": 300},
            "_ax_element": mock_win,
            "_app_pid": 100,
        }]

        with patch("uiax.backends.macos.bridge.list_all_windows", return_value=window_list):
            pm = MacOSProcessManager()
            result = pm.attach(window_title="Calculator")
            assert result["title"] == "Calculator"
            assert pm.attached is mock_win

    def test_attach_no_criteria_raises(self):
        from server.uia_bridge import ProcessNotFoundError
        from uiax.backends.macos.bridge import MacOSProcessManager

        pm = MacOSProcessManager()
        with pytest.raises(ProcessNotFoundError, match="criterion"):
            pm.attach()

    def test_attach_no_match_raises(self):
        from server.uia_bridge import ProcessNotFoundError
        from uiax.backends.macos.bridge import MacOSProcessManager

        with patch("uiax.backends.macos.bridge.list_all_windows", return_value=[]):
            pm = MacOSProcessManager()
            with pytest.raises(ProcessNotFoundError, match="No window matched"):
                pm.attach(window_title="NoSuchApp")

    def test_detach(self):
        from uiax.backends.macos.bridge import MacOSProcessManager

        pm = MacOSProcessManager()
        pm._attached_window = MockAXUIElement()
        pm._attached_app_pid = 100
        pm.detach()
        assert pm.attached is None
        assert pm.attached_app_pid is None

    def test_attach_by_bundle_id(self):
        from uiax.backends.macos.bridge import MacOSProcessManager

        mock_win = MockAXUIElement(role="AXWindow", title="Calculator")
        window_list = [{
            "hwnd": 12345,
            "hwnd_hex": hex(12345),
            "title": "Calculator",
            "class_name": "window",
            "pid": 100,
            "process_name": "Calculator",
            "bundle_id": "com.apple.calculator",
            "visible": True,
            "rect": {"left": 0, "top": 0, "right": 320, "bottom": 300},
            "_ax_element": mock_win,
            "_app_pid": 100,
        }]

        with patch("uiax.backends.macos.bridge.list_all_windows", return_value=window_list):
            pm = MacOSProcessManager()
            result = pm.attach(bundle_id="com.apple.calculator")
            assert result["bundle_id"] == "com.apple.calculator"


# ---------------------------------------------------------------------------
# State computation tests
# ---------------------------------------------------------------------------


class TestStateNames:
    def test_enabled_state(self):
        el = MockAXUIElement(enabled=True)
        states = _mock_state_names(el)
        assert "enabled" in states

    def test_disabled_state(self):
        el = MockAXUIElement(enabled=False)
        states = _mock_state_names(el)
        assert "disabled" in states

    def test_focused_state(self):
        el = MockAXUIElement(focused=True)
        states = _mock_state_names(el)
        assert "focused" in states

    def test_selected_state(self):
        el = MockAXUIElement(selected=True)
        states = _mock_state_names(el)
        assert "selected" in states


# ---------------------------------------------------------------------------
# Role name mapping tests
# ---------------------------------------------------------------------------


class TestRoleName:
    def test_known_role(self):
        el = MockAXUIElement(role="AXButton")
        assert _mock_role_name(el) == "button"

    def test_known_role_window(self):
        el = MockAXUIElement(role="AXWindow")
        assert _mock_role_name(el) == "window"

    def test_known_role_text(self):
        el = MockAXUIElement(role="AXStaticText")
        assert _mock_role_name(el) == "text"

    def test_unknown_role_strips_ax(self):
        el = MockAXUIElement(role="AXCustomWidget")
        assert _mock_role_name(el) == "customwidget"


# ---------------------------------------------------------------------------
# get_bridge factory test
# ---------------------------------------------------------------------------


class TestBridgeFactory:
    def test_get_bridge_macos(self):
        """get_bridge('macos') returns a MacOSBridge."""
        with patch("uiax.backends.macos.bridge.require_axapi"):
            from server.uia_bridge import get_bridge

            bridge = get_bridge("macos")
            from uiax.backends.macos.bridge import MacOSBridge

            assert isinstance(bridge, MacOSBridge)

    def test_get_bridge_auto_detects_macos(self):
        """get_bridge('real') auto-detects macOS on darwin."""
        with (
            patch("server.uia_bridge._is_linux", return_value=False),
            patch("server.uia_bridge._is_macos", return_value=True),
            patch("uiax.backends.macos.bridge.require_axapi"),
        ):
            from server.uia_bridge import get_bridge

            bridge = get_bridge("real")
            from uiax.backends.macos.bridge import MacOSBridge

            assert isinstance(bridge, MacOSBridge)
