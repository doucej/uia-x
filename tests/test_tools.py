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


# ---------------------------------------------------------------------------
# uia_get_text tests
# ---------------------------------------------------------------------------


class TestGetText:
    """Tests for MockUIABridge.get_text – covers the value/name fallback logic."""

    def test_get_text_returns_value_when_set(self, bridge_quicken: MockUIABridge):
        """Elements with a non-empty UIA value return it with source='value'."""
        text, source = bridge_quicken.get_text(
            {"by": "automation_id", "value": "txn_date"}
        )
        assert text == "02/21/2026"
        assert source == "value"

    def test_get_text_returns_value_over_name(self, bridge_quicken: MockUIABridge):
        """UIA value takes precedence over accessible name."""
        text, source = bridge_quicken.get_text(
            {"by": "automation_id", "value": "txn_amount"}
        )
        assert text == "0.00"
        assert source == "value"

    def test_get_text_falls_back_to_name_when_value_empty(
        self, bridge: MockUIABridge
    ):
        """When value is empty the accessible name is returned (simulates
        Windows Calculator CalculatorResults)."""
        text, source = bridge.get_text({"by": "name", "value": "Save"})
        assert text == "Save"
        assert source == "name"

    def test_get_text_status_bar(self, bridge: MockUIABridge):
        """Status bar has no value – name is returned."""
        text, source = bridge.get_text({"by": "control_type", "value": "StatusBar"})
        assert text == "Ready"
        assert source == "name"

    def test_get_text_root_returns_window_name(self, bridge: MockUIABridge):
        """Empty selector targets root window – its name is returned."""
        text, source = bridge.get_text({})
        assert text == "Untitled - Notepad"
        assert source == "name"

    def test_get_text_source_field_present(self, bridge: MockUIABridge):
        """Return value must always be a (str, str) 2-tuple."""
        result = bridge.get_text({})
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], str)
        assert isinstance(result[1], str)

    def test_get_text_element_not_found(self, bridge: MockUIABridge):
        """Missing element raises ElementNotFoundError."""
        with pytest.raises(UIAError) as exc_info:
            bridge.get_text({"by": "name", "value": "NoSuchElement"})
        assert exc_info.value.code == "ELEMENT_NOT_FOUND"

    def test_get_text_by_automation_id(self, bridge: MockUIABridge):
        """Selector by automation_id works for get_text."""
        text, source = bridge.get_text({"by": "automation_id", "value": "btn_save"})
        assert text == "Save"
        assert source == "name"

    def test_get_text_prefers_msaa_value_over_name(self):
        """When UIA value is absent but MSAA legacy_value is set, prefer it."""
        from mock_uia.tree import MockTree, MockElement, _el  # noqa: PLC0415

        result_el = MockElement(
            name="Display is 56",
            control_type="Static",
            automation_id="CalculatorResults",
            value="",           # UIA value absent – simulates Windows Calculator
            legacy_value="56",  # MSAA value present
        )
        tree = MockTree(root=result_el)
        b = MockUIABridge(tree=tree)
        text, source = b.get_text({})
        assert text == "56"
        assert source == "msaa_value"


# ---------------------------------------------------------------------------
# Selector shorthand / validation tests
# ---------------------------------------------------------------------------


class TestSelectorShorthand:
    """
    Verify that flat-dict shorthand selectors work and unknown keys are
    rejected with a clear error instead of silently falling back to
    name="" (which could invoke the wrong element, e.g. Minimize button).
    """

    def test_shorthand_automation_id(self, bridge: MockUIABridge):
        """{"automation_id": "btn_save"} works without "by"/"value" keys."""
        result = bridge.inspect({"automation_id": "btn_save"})
        assert result["name"] == "Save"

    def test_shorthand_name(self, bridge: MockUIABridge):
        """{"name": "Save"} works as shorthand."""
        result = bridge.inspect({"name": "Save"})
        assert result["automation_id"] == "btn_save"

    def test_shorthand_control_type(self, bridge: MockUIABridge):
        """{"control_type": "Button"} returns first matching button."""
        result = bridge.inspect({"control_type": "Button"})
        assert result["control_type"] == "Button"

    def test_shorthand_invoke(self, bridge: MockUIABridge):
        """invoke() also accepts shorthand selectors."""
        bridge.invoke({"automation_id": "btn_save"})

    def test_shorthand_get_text(self, bridge: MockUIABridge):
        """get_text() also accepts shorthand selectors."""
        text, source = bridge.get_text({"automation_id": "btn_save"})
        assert text == "Save"

    def test_unknown_key_raises(self, bridge: MockUIABridge):
        """A completely unknown key must raise INVALID_SELECTOR, not silently
        degrade to a name="" search that could hit the wrong element."""
        with pytest.raises(UIAError) as exc_info:
            bridge.invoke({"typo_automation_id": "btn_save"})
        assert exc_info.value.code == "INVALID_SELECTOR"
        assert "typo_automation_id" in str(exc_info.value)

    def test_ambiguous_shorthand_raises(self, bridge: MockUIABridge):
        """Providing two selector keys without 'by' is ambiguous and must error."""
        with pytest.raises(UIAError) as exc_info:
            bridge.inspect({"automation_id": "btn_save", "name": "Save"})
        assert exc_info.value.code == "INVALID_SELECTOR"
        assert "Ambiguous" in str(exc_info.value)

    def test_canonical_form_still_works(self, bridge: MockUIABridge):
        """The existing {"by": ..., "value": ...} form is unchanged."""
        result = bridge.inspect({"by": "automation_id", "value": "btn_save"})
        assert result["name"] == "Save"


# ---------------------------------------------------------------------------
# find_all index / states tests  (agent-ux improvement #1/#2)
# ---------------------------------------------------------------------------


class TestFindAllIndexAndStates:
    """Tests for the new 'index' field in find_all results."""

    def test_find_all_returns_index_field(self, bridge: MockUIABridge):
        """Every element returned by find_all must have a numeric 'index' field."""
        items = bridge.find_all({"has_actions": True, "named_only": True})
        assert len(items) > 0
        for item in items:
            assert "index" in item, f"Missing 'index' in {item}"
            assert isinstance(item["index"], int)

    def test_find_all_indices_are_sequential(self, bridge: MockUIABridge):
        """Indices must be 0-based and consecutive."""
        items = bridge.find_all({"has_actions": True, "named_only": True})
        for i, item in enumerate(items):
            assert item["index"] == i

    def test_find_all_includes_name_and_role(self, bridge: MockUIABridge):
        """Sanity check: basic fields are still present."""
        items = bridge.find_all({"has_actions": True, "named_only": True})
        assert any(it["name"] == "Save" for it in items)
        for item in items:
            assert "name" in item
            assert "role" in item
            assert "actions" in item

    def test_find_all_empty_filter_returns_elements(self, bridge: MockUIABridge):
        """Default filter (has_actions=True, named_only=True) returns something."""
        items = bridge.find_all({})
        assert len(items) > 0

    def test_find_all_has_actions_false_includes_all(self, bridge: MockUIABridge):
        """has_actions=False should return at least as many items as True."""
        with_actions = bridge.find_all({"has_actions": True})
        without_filter = bridge.find_all({"has_actions": False})
        assert len(without_filter) >= len(with_actions)

    def test_find_all_roles_filter(self, bridge: MockUIABridge):
        """Roles filter restricts results to the named roles."""
        items = bridge.find_all({"has_actions": True, "roles": ["button"]})
        for item in items:
            assert item["role"] == "button"

    def test_find_all_value_field_present_when_set(self):
        """Elements with a UIA value appear with 'value' key in find_all output."""
        from mock_uia.tree import MockTree  # noqa: PLC0415
        b = MockUIABridge(tree=MockTree.quicken())
        # Quicken has elements with values set
        items = b.find_all({"has_actions": False, "named_only": True})
        valued = [it for it in items if it.get("value")]
        # At least some quicken elements have values
        assert len(valued) > 0
        for item in valued:
            assert "index" in item  # index must be present even on valued elements


# ---------------------------------------------------------------------------
# get_text optional target (agent-ux improvement #4)
# ---------------------------------------------------------------------------


class TestGetTextOptionalTarget:
    """Tests for get_text with optional / None target."""

    def test_get_text_no_args_returns_root(self, bridge: MockUIABridge):
        """get_text() with no arguments returns root window text."""
        text, source = bridge.get_text()
        assert text == "Untitled - Notepad"
        assert source == "name"

    def test_get_text_none_target_returns_root(self, bridge: MockUIABridge):
        """get_text(None) returns root window text (mock has no focus concept)."""
        text, source = bridge.get_text(None)
        assert text == "Untitled - Notepad"
        assert source == "name"

    def test_get_text_empty_dict_unchanged(self, bridge: MockUIABridge):
        """get_text({}) still works as root selector (backward compat)."""
        text, source = bridge.get_text({})
        assert text == "Untitled - Notepad"
        assert source == "name"

    def test_get_text_explicit_target_still_works(self, bridge: MockUIABridge):
        """Providing a target still selects the named element."""
        text, source = bridge.get_text({"by": "name", "value": "Save"})
        assert text == "Save"
        assert source == "name"
