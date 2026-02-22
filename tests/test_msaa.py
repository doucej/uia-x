"""
Tests for MSAA / LegacyIAccessiblePattern support.
"""

from __future__ import annotations

import pytest

from server.mock_bridge import MockUIABridge
from server.uia_bridge import (
    ElementNotFoundError,
    PatternNotSupportedError,
    UIAError,
)
from mock_uia.tree import (
    MockTree,
    ROLE_SYSTEM_LISTITEM,
    ROLE_SYSTEM_OUTLINEITEM,
)


@pytest.fixture()
def bridge() -> MockUIABridge:
    """MockUIABridge with the MSAA fixture tree."""
    return MockUIABridge(tree=MockTree.with_msaa_fixtures())


# ---------------------------------------------------------------------------
# inspect: MSAA sub-dict exposure
# ---------------------------------------------------------------------------


class TestMSAAInspect:
    def test_owner_drawn_listitem_exposes_msaa(self, bridge: MockUIABridge):
        result = bridge.inspect({"by": "legacy_name", "value": "Example Bank Checking"})
        assert "msaa" in result
        assert result["msaa"]["name"] == "Example Bank Checking"

    def test_selected_state_in_msaa(self, bridge: MockUIABridge):
        result = bridge.inspect({"by": "legacy_name", "value": "Example Bank Checking"})
        assert result["msaa"].get("selected") is True

    def test_unselected_listitem(self, bridge: MockUIABridge):
        result = bridge.inspect({"by": "legacy_name", "value": "Example Bank Savings"})
        assert result["msaa"].get("selected") is not True

    def test_default_action_in_patterns(self, bridge: MockUIABridge):
        result = bridge.inspect({"by": "legacy_name", "value": "Example Bank Checking"})
        assert "LegacyIAccessiblePattern" in result["patterns"]

    def test_msaa_role(self, bridge: MockUIABridge):
        result = bridge.inspect({"by": "legacy_name", "value": "Example Bank Checking"})
        assert result["msaa"]["role"] == ROLE_SYSTEM_LISTITEM


# ---------------------------------------------------------------------------
# selector: find by MSAA attributes
# ---------------------------------------------------------------------------


class TestMSAASelectors:
    def test_find_by_legacy_name(self, bridge: MockUIABridge):
        el = bridge._find({"by": "legacy_name", "value": "Example Bank Savings"})
        assert el.legacy_name == "Example Bank Savings"

    def test_find_by_legacy_role(self, bridge: MockUIABridge):
        el = bridge._find({
            "by": "legacy_role",
            "value": str(ROLE_SYSTEM_LISTITEM),
            "index": 0,
        })
        assert el.legacy_role == ROLE_SYSTEM_LISTITEM

    def test_find_by_child_id(self, bridge: MockUIABridge):
        el = bridge._find({"by": "child_id", "value": "1"})
        assert el.legacy_name == "Example Bank Checking"

    def test_find_by_child_id_2(self, bridge: MockUIABridge):
        el = bridge._find({"by": "child_id", "value": "2"})
        assert el.legacy_name == "Example Bank Savings"

    def test_find_by_hwnd(self, bridge: MockUIABridge):
        el = bridge._find({"by": "hwnd", "value": "0x1001"})
        assert el.hwnd == 0x1001

    def test_treeitems_reachable_by_legacy_name(self, bridge: MockUIABridge):
        el = bridge._find({"by": "legacy_name", "value": "Banking"})
        assert el.legacy_role == ROLE_SYSTEM_OUTLINEITEM

    def test_find_nonexistent_legacy_name_raises(self, bridge: MockUIABridge):
        with pytest.raises(ElementNotFoundError):
            bridge._find({"by": "legacy_name", "value": "NoSuchAccount"})


# ---------------------------------------------------------------------------
# legacy_invoke
# ---------------------------------------------------------------------------


class TestLegacyInvoke:
    def test_legacy_invoke_sets_flag(self, bridge: MockUIABridge):
        bridge.legacy_invoke({"by": "legacy_name", "value": "Example Bank Checking"})
        el = bridge._find({"by": "legacy_name", "value": "Example Bank Checking"})
        assert el._legacy_invoked is True

    def test_legacy_invoke_non_invokable_raises(self, bridge: MockUIABridge):
        with pytest.raises(PatternNotSupportedError) as exc_info:
            bridge.legacy_invoke({"by": "name", "value": "AccountList"})
        assert exc_info.value.code == "PATTERN_NOT_SUPPORTED"

    def test_legacy_invoke_by_child_id(self, bridge: MockUIABridge):
        bridge.legacy_invoke({"by": "child_id", "value": "3"})
        el = bridge._find({"by": "child_id", "value": "3"})
        assert el._legacy_invoked is True

    def test_legacy_invoke_element_not_found(self, bridge: MockUIABridge):
        with pytest.raises(ElementNotFoundError):
            bridge.legacy_invoke({"by": "legacy_name", "value": "NoSuchAccount"})

    def test_legacy_invoke_tree_item(self, bridge: MockUIABridge):
        bridge.legacy_invoke({"by": "legacy_name", "value": "Banking"})
        el = bridge._find({"by": "legacy_name", "value": "Banking"})
        assert el._legacy_invoked is True
