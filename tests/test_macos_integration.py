"""
Integration tests for the macOS AXAPI backend.

These tests require:
  1. A running macOS GUI session (user logged in)
  2. Accessibility permissions granted to Python
     (System Settings → Privacy & Security → Accessibility)
  3. Calculator.app available at /System/Applications/Calculator.app

Run with:
    pytest tests/test_macos_integration.py -v --run-macos-integration

Or using the environment variable:
    UIAX_RUN_MACOS_INTEGRATION=1 pytest tests/test_macos_integration.py -v

These tests are skipped by default unless explicitly enabled.

SSH test setup
--------------
If testing over SSH, you need:
  - A logged-in GUI session on the target Mac
  - SSH access to that machine
  - Accessibility permissions granted to the Python process
  - The SSH session must be able to reach the GUI session's WindowServer

On macOS, SSH sessions can interact with the GUI if the user is logged in:
    ssh user@mac-host "cd /path/to/uia-x && \
        UIAX_RUN_MACOS_INTEGRATION=1 python -m pytest tests/test_macos_integration.py -v"

VNC alternative
---------------
If you cannot test via SSH due to WindowServer restrictions:
  1. Enable Screen Sharing (System Settings → General → Sharing → Screen Sharing)
  2. Connect via VNC and run the tests in a Terminal window within the VNC session
  3. Or use ``caffeinate -disu`` to keep the display awake for headless testing
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Skip unless integration explicitly requested
# ---------------------------------------------------------------------------

_run_integration = (
    os.environ.get("UIAX_RUN_MACOS_INTEGRATION", "").lower() in ("1", "true", "yes")
)

requires_macos_integration = pytest.mark.skipif(
    not _run_integration,
    reason=(
        "macOS integration tests skipped. "
        "Set UIAX_RUN_MACOS_INTEGRATION=1 or pass --run-macos-integration."
    ),
)

requires_macos = pytest.mark.skipif(
    sys.platform != "darwin",
    reason="macOS-only tests (requires darwin platform)",
)

# Try importing the AXAPI backend
try:
    from uiax.backends.macos.util import axapi_available, is_trusted

    _AXAPI_OK = axapi_available()
except ImportError:
    _AXAPI_OK = False

requires_axapi = pytest.mark.skipif(
    not _AXAPI_OK,
    reason="PyObjC / AXAPI is not available",
)


def pytest_addoption(parser: Any) -> None:
    parser.addoption(
        "--run-macos-integration",
        action="store_true",
        default=False,
        help="Run macOS AXAPI integration tests (requires live session + Calculator.app)",
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def launch_calculator():
    """
    Launch Calculator.app and yield; kill on teardown.

    Calculator.app is a reliable test target because:
    - It's pre-installed on all macOS versions
    - It has a well-known accessibility tree
    - Buttons have stable AXTitle attributes
    - The display value is readable via AXValue
    """
    proc = subprocess.Popen(
        ["open", "-a", "Calculator"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(2)  # Allow time for the app to launch and register accessibility

    yield proc

    # Kill Calculator on teardown
    subprocess.run(
        ["osascript", "-e", 'tell application "Calculator" to quit'],
        capture_output=True,
    )
    time.sleep(0.5)


@pytest.fixture(scope="module")
def calculator_bridge(launch_calculator):
    """
    Attach to Calculator.app and return a configured MacOSBridge.

    This fixture ensures the bridge is connected to Calculator's main window.
    """
    from uiax.backends.macos.bridge import MacOSBridge, get_macos_process_manager

    pm = get_macos_process_manager()
    pm.attach(process_name="Calculator")

    bridge = MacOSBridge()
    return bridge


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@requires_macos
@requires_axapi
@requires_macos_integration
class TestAccessibilityTrust:
    """Verify accessibility permissions."""

    def test_is_trusted(self):
        """The current process must be trusted for accessibility."""
        assert is_trusted(), (
            "Python is not trusted for accessibility. "
            "Go to System Settings → Privacy & Security → Accessibility "
            "and add your Python interpreter or Terminal app."
        )


@requires_macos
@requires_axapi
@requires_macos_integration
class TestWindowEnumeration:
    """Test listing and attaching to windows."""

    def test_list_windows(self, launch_calculator):
        from uiax.backends.macos.bridge import get_macos_process_manager

        pm = get_macos_process_manager()
        windows = pm.list_windows()
        assert isinstance(windows, list)
        assert len(windows) > 0

        # At least one window should be from Calculator
        calc_windows = [w for w in windows if "Calculator" in w.get("process_name", "")]
        assert len(calc_windows) > 0, "Calculator window not found in window list"

    def test_attach_to_calculator(self, launch_calculator):
        from uiax.backends.macos.bridge import get_macos_process_manager

        pm = get_macos_process_manager()
        result = pm.attach(process_name="Calculator")
        assert "Calculator" in result["title"] or "Calculator" in result["process_name"]
        assert pm.attached is not None

    def test_attach_by_bundle_id(self, launch_calculator):
        from uiax.backends.macos.bridge import get_macos_process_manager

        pm = get_macos_process_manager()
        result = pm.attach(bundle_id="com.apple.calculator")
        assert result["bundle_id"] == "com.apple.calculator"


@requires_macos
@requires_axapi
@requires_macos_integration
class TestInspection:
    """Test inspecting the Calculator accessibility tree."""

    def test_inspect_root(self, calculator_bridge):
        tree = calculator_bridge.inspect({})
        assert tree["role"] in ("window", "group", "application")
        assert "children" in tree

    def test_inspect_with_depth(self, calculator_bridge):
        tree = calculator_bridge.inspect({"depth": 1})
        assert "children" in tree
        # Should have at least some children at depth 1
        assert len(tree["children"]) > 0

    def test_inspect_deep_tree(self, calculator_bridge):
        tree = calculator_bridge.inspect({"depth": 8})
        # The tree should contain buttons
        buttons = _find_all_by_role(tree, "button")
        assert len(buttons) > 0, "Expected to find buttons in Calculator tree"

    def test_find_number_buttons(self, calculator_bridge):
        """Calculator should have buttons for digits 0-9."""
        tree = calculator_bridge.inspect({"depth": 8})
        buttons = _find_all_by_role(tree, "button")
        button_names = {b.get("name", "") for b in buttons}

        # At least digits 0–9 should be present
        for digit in "0123456789":
            assert digit in button_names, (
                f"Button '{digit}' not found. Available: {sorted(button_names)}"
            )


@requires_macos
@requires_axapi
@requires_macos_integration
class TestInteraction:
    """Test interacting with Calculator via the bridge."""

    def test_press_clear(self, calculator_bridge):
        """Press the Clear button to reset the calculator."""
        try:
            calculator_bridge.invoke({"by": "name", "value": "Clear"})
        except Exception:
            # Clear might be named "AC" or "All Clear" on some versions
            try:
                calculator_bridge.invoke(
                    {"by": "name_substring", "value": "clear"}
                )
            except Exception:
                pass  # Continue even if clear fails

    def test_press_button_7(self, calculator_bridge):
        """Press button '7'."""
        calculator_bridge.invoke({"by": "name", "value": "7"})
        time.sleep(0.3)

    def test_calculate_7_times_8(self, calculator_bridge):
        """
        Full calculation test: 7 × 8 = 56.

        This is the core integration test: press buttons, read the display,
        and verify the result.
        """
        # Clear first
        try:
            calculator_bridge.invoke(
                {"by": "name_substring", "value": "clear"}
            )
            time.sleep(0.3)
        except Exception:
            pass

        # Press 7
        calculator_bridge.invoke({"by": "name", "value": "7"})
        time.sleep(0.3)

        # Press multiply (×)
        try:
            calculator_bridge.invoke(
                {"by": "name_substring", "value": "multipl"}
            )
        except Exception:
            # Try the × symbol directly
            calculator_bridge.invoke({"by": "name", "value": "\u00d7"})
        time.sleep(0.3)

        # Press 8
        calculator_bridge.invoke({"by": "name", "value": "8"})
        time.sleep(0.3)

        # Press equals
        try:
            calculator_bridge.invoke(
                {"by": "name_substring", "value": "equal"}
            )
        except Exception:
            calculator_bridge.invoke({"by": "name", "value": "="})
        time.sleep(0.5)

        # Read the display (depth=8 needed: Calculator's display text
        # is at window→group→split group→group→group→scroll area→text)
        tree = calculator_bridge.inspect({"depth": 8})
        display_value = _find_display_value(tree)
        assert display_value is not None, "Could not find display value in tree"
        # Strip Unicode control characters (macOS prefixes with LTR mark \u200e)
        import unicodedata
        cleaned = "".join(
            c for c in display_value if not unicodedata.category(c).startswith("C")
        )
        assert "56" in cleaned, (
            f"Expected display to show '56' but got '{cleaned}' (raw: {display_value!r})"
        )

    def test_send_keys(self, calculator_bridge):
        """Send keystrokes to Calculator."""
        # Clear with Escape or Cmd+C
        calculator_bridge.send_keys("{ESCAPE}")
        time.sleep(0.3)

        # Type a number
        calculator_bridge.send_keys("5")
        time.sleep(0.3)


@requires_macos
@requires_axapi
@requires_macos_integration
class TestMouseClick:
    """Test mouse click via Quartz CGEvent."""

    def test_mouse_click(self, calculator_bridge):
        """Click at a known coordinate (centre of the Calculator window)."""
        tree = calculator_bridge.inspect({})
        rect = tree.get("rect", {})
        if rect.get("right", 0) > 0:
            cx = (rect["left"] + rect["right"]) // 2
            cy = (rect["top"] + rect["bottom"]) // 2
            calculator_bridge.mouse_click(cx, cy)
            time.sleep(0.3)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_all_by_role(tree: dict[str, Any], role: str) -> list[dict[str, Any]]:
    """Recursively find all elements with a given role in a serialised tree."""
    results: list[dict[str, Any]] = []
    if tree.get("role") == role:
        results.append(tree)
    for child in tree.get("children", []):
        results.extend(_find_all_by_role(child, role))
    return results


def _find_display_value(tree: dict[str, Any]) -> str | None:
    """
    Find the Calculator display value in the accessibility tree.

    Calculator.app's display may be a static text or group element with
    an AXValue attribute showing the current result.  The value is often
    prefixed with Unicode directional marks (e.g. ``\u200e``).
    """
    import unicodedata

    # Walk the tree looking for elements with a numeric value
    stack = [tree]
    while stack:
        node = stack.pop()
        value = node.get("value", "")
        role = node.get("role", "")
        # The display is typically a static text / text field with a numeric value
        if value and role in ("text", "static text", "text field", "group",
                              "scroll area"):
            try:
                # Strip Unicode control characters before float check
                cleaned = "".join(
                    c for c in str(value)
                    if not unicodedata.category(c).startswith("C")
                ).replace(",", "").replace(" ", "")
                float(cleaned)
                return value
            except (ValueError, TypeError):
                pass
        for child in node.get("children", []):
            stack.append(child)
    return None
