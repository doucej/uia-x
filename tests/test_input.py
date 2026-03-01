"""
Tests for keystroke and mouse input tools.
"""

from __future__ import annotations

import pytest

from server.mock_bridge import MockUIABridge
from server.uia_bridge import ElementNotFoundError
from mock_uia.tree import MockTree


@pytest.fixture()
def bridge() -> MockUIABridge:
    return MockUIABridge(tree=MockTree.default())


# ---------------------------------------------------------------------------
# send_keys tests
# ---------------------------------------------------------------------------


class TestSendKeys:
    def test_send_keys_records_in_log(self, bridge: MockUIABridge):
        bridge.send_keys("{ENTER}")
        assert bridge.keys_log == ["{ENTER}"]

    def test_send_keys_multiple(self, bridge: MockUIABridge):
        bridge.send_keys("^e")
        bridge.send_keys("{TAB}")
        bridge.send_keys("Hello World")
        assert bridge.keys_log == ["^e", "{TAB}", "Hello World"]

    def test_send_keys_with_target_selector(self, bridge: MockUIABridge):
        bridge.send_keys("test", target={"by": "automation_id", "value": "textEditor"})
        assert "test" in bridge.keys_log

    def test_send_keys_none_target(self, bridge: MockUIABridge):
        bridge.send_keys("{ESC}", target=None)
        assert bridge.keys_log[-1] == "{ESC}"

    def test_send_keys_log_starts_empty(self, bridge: MockUIABridge):
        assert bridge.keys_log == []

    def test_send_keys_empty_string(self, bridge: MockUIABridge):
        bridge.send_keys("")
        assert bridge.keys_log == [""]

    def test_send_keys_shift_tab(self, bridge: MockUIABridge):
        bridge.send_keys("+{TAB}")
        assert bridge.keys_log == ["+{TAB}"]

    def test_send_keys_ctrl_combination(self, bridge: MockUIABridge):
        bridge.send_keys("^s")
        assert bridge.keys_log == ["^s"]

    def test_send_keys_alt_combination(self, bridge: MockUIABridge):
        bridge.send_keys("%{F4}")
        assert bridge.keys_log == ["%{F4}"]


# ---------------------------------------------------------------------------
# mouse_click tests
# ---------------------------------------------------------------------------


class TestMouseClick:
    def test_single_click_logged(self, bridge: MockUIABridge):
        bridge.mouse_click(100, 200)
        assert bridge.mouse_log == [
            {"x": 100, "y": 200, "double": False, "button": "left"}
        ]

    def test_double_click_logged(self, bridge: MockUIABridge):
        bridge.mouse_click(300, 400, double=True)
        assert bridge.mouse_log[0]["double"] is True

    def test_right_click_button(self, bridge: MockUIABridge):
        bridge.mouse_click(50, 60, button="right")
        assert bridge.mouse_log[0]["button"] == "right"

    def test_middle_click(self, bridge: MockUIABridge):
        bridge.mouse_click(10, 20, button="middle")
        assert bridge.mouse_log[0]["button"] == "middle"

    def test_multiple_clicks_accumulated(self, bridge: MockUIABridge):
        bridge.mouse_click(10, 20)
        bridge.mouse_click(30, 40, double=True)
        assert len(bridge.mouse_log) == 2
        assert bridge.mouse_log[1]["x"] == 30

    def test_mouse_log_independent_of_keys_log(self, bridge: MockUIABridge):
        bridge.send_keys("{ENTER}")
        bridge.mouse_click(1, 2)
        assert bridge.keys_log == ["{ENTER}"]
        assert len(bridge.mouse_log) == 1

    def test_mouse_log_starts_empty(self, bridge: MockUIABridge):
        assert bridge.mouse_log == []


# ---------------------------------------------------------------------------
# type_text tests
# ---------------------------------------------------------------------------


class TestTypeText:
    def test_type_text_records_in_log(self, bridge: MockUIABridge):
        bridge.type_text("Hello, World!")
        assert bridge.keys_log == ["Hello, World!"]

    def test_type_text_spaces_preserved(self, bridge: MockUIABridge):
        bridge.type_text("hello world")
        assert bridge.keys_log == ["hello world"]

    def test_type_text_special_chars_preserved(self, bridge: MockUIABridge):
        """Characters that are special in SendKeys notation are stored as-is."""
        bridge.type_text("price: ^100 + 50% = ~$150 (approx)")
        assert bridge.keys_log == ["price: ^100 + 50% = ~$150 (approx)"]

    def test_type_text_with_newline(self, bridge: MockUIABridge):
        bridge.type_text("line1\nline2")
        assert bridge.keys_log == ["line1\nline2"]

    def test_type_text_with_target(self, bridge: MockUIABridge):
        bridge.type_text("typed", target={"by": "automation_id", "value": "textEditor"})
        assert "typed" in bridge.keys_log

    def test_type_text_empty(self, bridge: MockUIABridge):
        bridge.type_text("")
        assert bridge.keys_log == [""]

    def test_type_text_does_not_interfere_with_mouse_log(self, bridge: MockUIABridge):
        bridge.type_text("abc")
        assert bridge.mouse_log == []
