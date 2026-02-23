"""
Docker-friendly integration tests for the Linux AT-SPI2 backend.

These tests target **gnome-calculator** running inside Xvfb and exercise the
full LinuxBridge stack: window enumeration → attach → inspect → invoke →
read result.

Run inside the Docker container (or any headless environment):

    UIAX_RUN_INTEGRATION=1 UIAX_TEST_APP=gnome-calculator \\
        ./tests/run_headless.sh pytest tests/test_linux_integration_docker.py -v

Or via docker-compose:

    docker compose -f docker/docker-compose.ci.yml run integration-tests
"""

from __future__ import annotations

import os
import time

import pytest

# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------

_run_integration = os.environ.get("UIAX_RUN_INTEGRATION", "").lower() in (
    "1",
    "true",
    "yes",
)

requires_integration = pytest.mark.skipif(
    not _run_integration,
    reason="Set UIAX_RUN_INTEGRATION=1 to run integration tests.",
)

try:
    import pyatspi  # type: ignore[import-untyped]

    _ATSPI_OK = True
except ImportError:
    _ATSPI_OK = False

requires_atspi = pytest.mark.skipif(
    not _ATSPI_OK,
    reason="python3-pyatspi is not installed",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wait_for_calculator(timeout: float = 10.0) -> None:
    """Block until gnome-calculator appears in the AT-SPI tree."""
    from uiax.backends.linux.bridge import LinuxProcessManager

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pm = LinuxProcessManager()
        windows = pm.list_windows(visible_only=False)
        if any("calculator" in w.get("title", "").lower() for w in windows):
            return
        time.sleep(0.5)
    # List what we *did* find for debugging
    pm = LinuxProcessManager()
    found = [w.get("title", "<no title>") for w in pm.list_windows(visible_only=False)]
    pytest.fail(
        f"gnome-calculator did not appear within {timeout}s. "
        f"Windows found: {found}"
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@requires_atspi
@requires_integration
class TestCalculatorDiscovery:
    """Verify we can see and attach to gnome-calculator."""

    def test_calculator_visible(self) -> None:
        """gnome-calculator shows up in the window list."""
        _wait_for_calculator()

        from uiax.backends.linux.bridge import LinuxProcessManager

        pm = LinuxProcessManager()
        windows = pm.list_windows(visible_only=False)
        titles = [w["title"] for w in windows]
        assert any(
            "calculator" in t.lower() for t in titles
        ), f"Calculator not found. Windows: {titles}"

    def test_attach_calculator(self) -> None:
        """Attach to the Calculator window by title."""
        _wait_for_calculator()

        from uiax.backends.linux.bridge import (
            LinuxProcessManager,
            reset_linux_process_manager,
        )

        reset_linux_process_manager()
        pm = LinuxProcessManager()
        win = pm.attach(window_title="Calculator")
        assert "Calculator" in win["title"]
        assert win["pid"] > 0


@requires_atspi
@requires_integration
class TestCalculatorInspect:
    """Inspect the calculator's AT-SPI tree."""

    @pytest.fixture(autouse=True)
    def _attach(self) -> None:
        _wait_for_calculator()
        from uiax.backends.linux.bridge import (
            LinuxProcessManager,
            get_linux_process_manager,
            reset_linux_process_manager,
        )

        reset_linux_process_manager()
        pm = get_linux_process_manager()
        pm.attach(window_title="Calculator")

    def test_inspect_root(self) -> None:
        from uiax.backends.linux.bridge import LinuxBridge

        bridge = LinuxBridge()
        tree = bridge.inspect({})
        assert tree["name"]
        assert tree["role"]

    def test_inspect_depth(self) -> None:
        from uiax.backends.linux.bridge import LinuxBridge

        bridge = LinuxBridge()
        tree = bridge.inspect({"depth": 3})
        assert len(tree.get("children", [])) > 0

    def test_find_button(self) -> None:
        from uiax.backends.linux.bridge import LinuxBridge

        bridge = LinuxBridge()
        result = bridge.inspect({"by": "name", "value": "7"})
        assert result["role"] in ("push button", "button")
        assert "click" in result.get("actions", [])


@requires_atspi
@requires_integration
class TestCalculatorCompute:
    """The big one: compute 7 × 6 = 42 end-to-end."""

    @pytest.fixture(autouse=True)
    def _attach(self) -> None:
        _wait_for_calculator()
        from uiax.backends.linux.bridge import (
            get_linux_process_manager,
            reset_linux_process_manager,
        )

        reset_linux_process_manager()
        pm = get_linux_process_manager()
        pm.attach(window_title="Calculator")

    def test_7_times_6_equals_42(self) -> None:
        """Press C, 7, ×, 6, = and verify the result is 42."""
        from uiax.backends.linux.bridge import LinuxBridge

        bridge = LinuxBridge()

        # Clear first
        try:
            bridge.invoke({"by": "name", "value": "C"})
            time.sleep(0.3)
        except Exception:
            pass  # Some calculator versions use "Clear" or "AC"

        # Press sequence
        for btn in ("7", "×", "6", "="):
            bridge.invoke({"by": "name", "value": btn})
            time.sleep(0.3)

        # Read the result — look for a text element or the main display
        tree = bridge.inspect({"depth": 8})
        result_text = _extract_result(tree)
        dump = _dump_tree(tree)
        assert result_text is not None, (
            f"Could not find result in tree:\n{dump}"
        )
        assert "42" in result_text, f"Expected '42' in result, got: {result_text}"

    def test_addition(self) -> None:
        """Press C, 1, 9, +, 2, 3, = and verify 42."""
        from uiax.backends.linux.bridge import LinuxBridge

        bridge = LinuxBridge()

        try:
            bridge.invoke({"by": "name", "value": "C"})
            time.sleep(0.3)
        except Exception:
            pass

        for btn in ("1", "9", "+", "2", "3", "="):
            bridge.invoke({"by": "name", "value": btn})
            time.sleep(0.3)

        tree = bridge.inspect({"depth": 8})
        result_text = _extract_result(tree)
        dump = _dump_tree(tree)
        assert result_text is not None, (
            f"Could not find result in tree:\n{dump}"
        )
        assert "42" in result_text, f"Expected '42', got: {result_text}"


def _extract_result(node: dict, depth: int = 0) -> str | None:
    """
    Walk the inspect tree to find the calculator's result display.

    gnome-calculator versions expose the result in different ways:
    - As ``text`` content on an editbar / text element
    - As ``value`` on a numeric display
    - As the ``name`` of a label, section, or panel

    We do a DFS and return the first node whose text/value/name looks
    like it contains a numeric result, skipping interactive controls
    like push buttons.
    """
    role = node.get("role", "")

    # Skip buttons — they have numeric names ("7", "42") but aren't the display
    if role in ("push button", "button", "toggle button"):
        return None

    # 1. Check text content  (EditableText / Text interface)
    text = node.get("text", "")
    if text and any(c.isdigit() for c in str(text)):
        return str(text)

    # 2. Check value  (Value interface — some calculators use this)
    value = node.get("value")
    if value is not None:
        val_str = str(value)
        if any(c.isdigit() for c in val_str):
            return val_str

    # 3. Check name — accept any non-button role whose name has digits
    name = node.get("name", "")
    if name and any(c.isdigit() for c in name):
        return name

    # 4. Recurse into children
    for child in node.get("children", []):
        found = _extract_result(child, depth + 1)
        if found is not None:
            return found

    return None


def _dump_tree(node: dict, indent: int = 0) -> str:
    """Produce a compact text dump of the inspect tree for debug output."""
    lines: list[str] = []
    prefix = "  " * indent
    role = node.get("role", "?")
    name = node.get("name", "")
    text = node.get("text", "")
    value = node.get("value", "")
    extra = ""
    if text:
        extra += f" text={text!r}"
    if value:
        extra += f" value={value!r}"
    lines.append(f"{prefix}[{role}] name={name!r}{extra}")
    for child in node.get("children", []):
        lines.append(_dump_tree(child, indent + 1))
    return "\n".join(lines)
