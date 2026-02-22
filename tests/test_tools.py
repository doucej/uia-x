"""
Test harness for core UIA tools: uia_inspect / uia_invoke / uia_set_value.

All tests run against the mock UIA backend – no live application needed.
"""

from __future__ import annotations

import json

import pytest

from server.mock_bridge import MockUIABridge
from server.uia_bridge import (
    ElementNotFoundError,
    PatternNotSupportedError,
    UIAError,
)
from mock_uia.tree import MockTree


# ---------------------------------------------------------------------------
# Shared fixture – generic (V2 default) tree
# ---------------------------------------------------------------------------


@pytest.fixture()
def bridge() -> MockUIABridge:
    """Fresh MockUIABridge with the generic Notepad-like fixture tree."""
    return MockUIABridge(tree=MockTree.default())


@pytest.fixture()
def bridge_quicken() -> MockUIABridge:
    """Fresh MockUIABridge with the Quicken fixture tree (V1 compat)."""
    return MockUIABridge(tree=MockTree.quicken())


# ---------------------------------------------------------------------------
# uia_inspect tests
# ---------------------------------------------------------------------------


class TestInspect:
    def test_inspect_root_returns_window(self, bridge: MockUIABridge):
        result = bridge.inspect({})
        assert result["name"] == "Untitled - Notepad"
        assert result["control_type"] == "Window"

    def test_inspect_root_has_children(self, bridge: MockUIABridge):
        result = bridge.inspect({})
        assert len(result["children"]) > 0

    def test_inspect_by_name(self, bridge: MockUIABridge):
        result = bridge.inspect({"by": "name", "value": "Editor"})
        assert result["name"] == "Editor"
        assert result["control_type"] == "Edit"

    def test_inspect_by_automation_id(self, bridge: MockUIABridge):
        result = bridge.inspect({"by": "automation_id", "value": "btn_save"})
        assert result["name"] == "Save"
        assert "InvokePattern" in result["patterns"]

    def test_inspect_by_control_type(self, bridge: MockUIABridge):
        result = bridge.inspect({"by": "control_type", "value": "StatusBar"})
        assert result["control_type"] == "StatusBar"

    def test_inspect_by_path(self, bridge: MockUIABridge):
        result = bridge.inspect({"by": "path", "value": "Toolbar/Save"})
        assert result["name"] == "Save"

    def test_inspect_with_depth_zero(self, bridge: MockUIABridge):
        result = bridge.inspect({"depth": 0})
        assert result["children"] == []

    def test_inspect_depth_respected(self, bridge: MockUIABridge):
        result = bridge.inspect({"depth": 1})
        assert len(result["children"]) > 0
        for child in result["children"]:
            assert child["children"] == []

    def test_inspect_element_not_found(self, bridge: MockUIABridge):
        with pytest.raises(ElementNotFoundError) as exc_info:
            bridge.inspect({"by": "name", "value": "NonExistentElement"})
        assert exc_info.value.code == "ELEMENT_NOT_FOUND"

    def test_inspect_invalid_selector(self, bridge: MockUIABridge):
        with pytest.raises(UIAError) as exc_info:
            bridge.inspect({"by": "telepathy", "value": "Save"})
        assert exc_info.value.code == "INVALID_SELECTOR"

    def test_inspect_output_is_json_serialisable(self, bridge: MockUIABridge):
        result = bridge.inspect({})
        json_str = json.dumps(result)
        parsed = json.loads(json_str)
        assert parsed["name"] == "Untitled - Notepad"

    def test_inspect_dict_has_required_keys(self, bridge: MockUIABridge):
        result = bridge.inspect({"by": "name", "value": "Save"})
        required = {
            "name", "control_type", "automation_id", "enabled",
            "rect", "patterns", "children",
        }
        assert required.issubset(result.keys())

    def test_inspect_rect_has_ltrb(self, bridge: MockUIABridge):
        result = bridge.inspect({})
        rect = result["rect"]
        for key in ("left", "top", "right", "bottom"):
            assert key in rect


# V1 backward compatibility
class TestInspectQuicken:
    def test_inspect_quicken_root(self, bridge_quicken: MockUIABridge):
        result = bridge_quicken.inspect({})
        assert result["name"] == "Quicken"
        assert result["control_type"] == "Window"

    def test_inspect_quicken_register(self, bridge_quicken: MockUIABridge):
        result = bridge_quicken.inspect({"by": "name", "value": "Register"})
        assert result["control_type"] == "DataGrid"

    def test_inspect_quicken_transaction_fields(self, bridge_quicken: MockUIABridge):
        result = bridge_quicken.inspect({"by": "automation_id", "value": "txn_payee"})
        assert result["name"] == "Payee"


# ---------------------------------------------------------------------------
# uia_invoke tests
# ---------------------------------------------------------------------------


class TestInvoke:
    def test_invoke_button_succeeds(self, bridge: MockUIABridge):
        bridge.invoke({"by": "automation_id", "value": "btn_save"})
        el = bridge._find({"by": "automation_id", "value": "btn_save"})
        assert el._invoked is True

    def test_invoke_by_name(self, bridge: MockUIABridge):
        bridge.invoke({"by": "name", "value": "New"})
        el = bridge._find({"by": "name", "value": "New"})
        assert el._invoked is True

    def test_invoke_by_path(self, bridge: MockUIABridge):
        bridge.invoke({"by": "path", "value": "Toolbar/Print"})

    def test_invoke_non_invokable_raises(self, bridge: MockUIABridge):
        with pytest.raises(PatternNotSupportedError) as exc_info:
            bridge.invoke({"by": "name", "value": "Editor"})
        assert exc_info.value.code == "PATTERN_NOT_SUPPORTED"

    def test_invoke_element_not_found(self, bridge: MockUIABridge):
        with pytest.raises(ElementNotFoundError):
            bridge.invoke({"by": "name", "value": "GhostButton"})

    def test_invoke_all_toolbar_buttons(self, bridge: MockUIABridge):
        for btn in ("New", "Open", "Save", "Print"):
            bridge.invoke({"by": "name", "value": btn})


# ---------------------------------------------------------------------------
# uia_set_value tests
# ---------------------------------------------------------------------------


class TestSetValue:
    def test_set_value_text_field(self, bridge: MockUIABridge):
        bridge.set_value({"by": "automation_id", "value": "textEditor"}, "Hello World")
        el = bridge._find({"by": "automation_id", "value": "textEditor"})
        assert el.value == "Hello World"

    def test_set_value_on_non_settable_raises(self, bridge: MockUIABridge):
        with pytest.raises(PatternNotSupportedError) as exc_info:
            bridge.set_value({"by": "name", "value": "Save"}, "ignored")
        assert exc_info.value.code == "PATTERN_NOT_SUPPORTED"

    def test_set_value_element_not_found(self, bridge: MockUIABridge):
        with pytest.raises(ElementNotFoundError):
            bridge.set_value({"by": "automation_id", "value": "no_such_field"}, "x")

    def test_set_value_empty_string(self, bridge: MockUIABridge):
        bridge.set_value({"by": "automation_id", "value": "textEditor"}, "")
        el = bridge._find({"by": "automation_id", "value": "textEditor"})
        assert el.value == ""

    def test_set_then_inspect_reflects_new_value(self, bridge: MockUIABridge):
        bridge.set_value({"by": "automation_id", "value": "textEditor"}, "TEST")
        result = bridge.inspect({"by": "automation_id", "value": "textEditor"})
        assert result["value"] == "TEST"


# V1 backward compatibility
class TestSetValueQuicken:
    def test_set_value_quicken_payee(self, bridge_quicken: MockUIABridge):
        bridge_quicken.set_value({"by": "automation_id", "value": "txn_payee"}, "Amazon")
        el = bridge_quicken._find({"by": "automation_id", "value": "txn_payee"})
        assert el.value == "Amazon"

    def test_set_value_quicken_amount(self, bridge_quicken: MockUIABridge):
        bridge_quicken.set_value({"by": "automation_id", "value": "txn_amount"}, "99.99")
        el = bridge_quicken._find({"by": "automation_id", "value": "txn_amount"})
        assert el.value == "99.99"


# ---------------------------------------------------------------------------
# Error structure tests
# ---------------------------------------------------------------------------


class TestErrorCodes:
    def test_element_not_found_code(self, bridge: MockUIABridge):
        try:
            bridge.inspect({"by": "name", "value": "Missing"})
        except UIAError as exc:
            assert exc.code == "ELEMENT_NOT_FOUND"

    def test_pattern_not_supported_code(self, bridge: MockUIABridge):
        try:
            bridge.invoke({"by": "name", "value": "Editor"})
        except UIAError as exc:
            assert exc.code == "PATTERN_NOT_SUPPORTED"

    def test_invalid_selector_code(self, bridge: MockUIABridge):
        try:
            bridge.inspect({"by": "magic", "value": "x"})
        except UIAError as exc:
            assert exc.code == "INVALID_SELECTOR"
