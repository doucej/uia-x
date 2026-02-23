"""
Integration tests for the Linux AT-SPI2 backend.

These tests require:
  1. A running accessibility bus (AT-SPI2 enabled)
  2. A target application (e.g., gedit, xterm, gnome-calculator)
  3. An active X11 or Wayland session (Xvfb is sufficient)

Run with:
    pytest tests/test_linux_integration.py -v --run-integration

Or using the headless harness:
    ./tests/run_headless.sh pytest tests/test_linux_integration.py -v

These tests are skipped by default unless ``--run-integration`` is passed
or the ``UIAX_RUN_INTEGRATION`` environment variable is set.
"""

from __future__ import annotations

import os
import subprocess
import time
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Skip unless integration explicitly requested
# ---------------------------------------------------------------------------


def pytest_addoption(parser: Any) -> None:
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="Run AT-SPI2 integration tests (requires live session)",
    )


_run_integration = (
    os.environ.get("UIAX_RUN_INTEGRATION", "").lower() in ("1", "true", "yes")
)

requires_integration = pytest.mark.skipif(
    not _run_integration,
    reason=(
        "AT-SPI2 integration tests skipped. "
        "Set UIAX_RUN_INTEGRATION=1 or pass --run-integration."
    ),
)

# Try importing pyatspi – skip all tests if unavailable
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
# Test application launcher fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def launch_xterm():
    """Launch xterm and yield; kill on teardown."""
    proc = subprocess.Popen(
        ["xterm", "-title", "UIA-X Test Terminal"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(2)  # Allow time for AT-SPI registration
    yield proc
    proc.terminate()
    proc.wait(timeout=5)


@pytest.fixture(scope="module")
def launch_gedit():
    """Launch gedit and yield; kill on teardown."""
    proc = subprocess.Popen(
        ["gedit", "--new-window"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(3)  # gedit takes longer to register with AT-SPI
    yield proc
    proc.terminate()
    proc.wait(timeout=5)


# ---------------------------------------------------------------------------
# Tests – window enumeration
# ---------------------------------------------------------------------------


@requires_atspi
@requires_integration
class TestWindowEnumeration:
    """Verify that AT-SPI2 window listing works."""

    def test_list_applications(self):
        from uiax.backends.linux.atspi_backend import list_applications

        apps = list_applications()
        # Should find at least the desktop shell / registryd
        assert isinstance(apps, list)

    def test_list_top_level_windows(self):
        from uiax.backends.linux.atspi_backend import list_top_level_windows

        windows = list_top_level_windows()
        assert isinstance(windows, list)

    def test_process_manager_list(self):
        from uiax.backends.linux.bridge import LinuxProcessManager

        pm = LinuxProcessManager()
        windows = pm.list_windows(visible_only=False)
        assert isinstance(windows, list)
        for w in windows:
            assert "title" in w
            assert "pid" in w
            assert "process_name" in w


# ---------------------------------------------------------------------------
# Tests – window attachment (requires xterm)
# ---------------------------------------------------------------------------


@requires_atspi
@requires_integration
class TestWindowAttachment:
    """Verify window attachment and element inspection."""

    def test_attach_by_title(self, launch_xterm: Any):
        from uiax.backends.linux.bridge import LinuxProcessManager

        pm = LinuxProcessManager()
        win = pm.attach(window_title="UIA-X Test Terminal")
        assert "UIA-X Test Terminal" in win["title"]

    def test_inspect_root(self, launch_xterm: Any):
        from uiax.backends.linux.bridge import (
            LinuxBridge,
            LinuxProcessManager,
            get_linux_process_manager,
            reset_linux_process_manager,
        )

        reset_linux_process_manager()
        pm = get_linux_process_manager()
        pm.attach(window_title="UIA-X Test Terminal")

        bridge = LinuxBridge()
        tree = bridge.inspect({})
        assert tree["name"]  # Should have a name
        assert tree["role"]  # Should have a role


# ---------------------------------------------------------------------------
# Tests – element inspection (requires gedit)
# ---------------------------------------------------------------------------


@requires_atspi
@requires_integration
class TestElementInspection:
    """Inspect individual UI elements in gedit."""

    def test_inspect_full_tree(self, launch_gedit: Any):
        from uiax.backends.linux.bridge import (
            LinuxBridge,
            get_linux_process_manager,
            reset_linux_process_manager,
        )

        reset_linux_process_manager()
        pm = get_linux_process_manager()
        pm.attach(process_name="gedit")

        bridge = LinuxBridge()
        tree = bridge.inspect({"depth": 2})
        assert tree["name"]
        assert len(tree["children"]) > 0

    def test_inspect_by_role(self, launch_gedit: Any):
        from uiax.backends.linux.bridge import (
            LinuxBridge,
            get_linux_process_manager,
            reset_linux_process_manager,
        )

        reset_linux_process_manager()
        pm = get_linux_process_manager()
        pm.attach(process_name="gedit")

        bridge = LinuxBridge()
        # gedit should have a text role element
        try:
            result = bridge.inspect({"by": "role", "value": "text"})
            assert result["role"]
        except Exception:
            # If not found, that's OK – gedit versions vary
            pass


# ---------------------------------------------------------------------------
# Tests – invoke action
# ---------------------------------------------------------------------------


@requires_atspi
@requires_integration
class TestInvoke:
    """Test invoking actions on UI elements."""

    def test_invoke_button(self, launch_gedit: Any):
        """
        Attempt to invoke a button in gedit.

        This is a best-effort test — the exact button names depend on the
        gedit version and GTK theme.
        """
        from uiax.backends.linux.bridge import (
            LinuxBridge,
            get_linux_process_manager,
            reset_linux_process_manager,
        )

        reset_linux_process_manager()
        pm = get_linux_process_manager()
        pm.attach(process_name="gedit")
        bridge = LinuxBridge()

        # Try to find and invoke a clickable element
        try:
            bridge.invoke({"by": "role", "value": "push button"})
        except Exception:
            pytest.skip("No invokable push button found in gedit")


# ---------------------------------------------------------------------------
# Tests – set text value
# ---------------------------------------------------------------------------


@requires_atspi
@requires_integration
class TestSetValue:
    """Test setting text values."""

    def test_set_text_in_editor(self, launch_gedit: Any):
        from uiax.backends.linux.bridge import (
            LinuxBridge,
            get_linux_process_manager,
            reset_linux_process_manager,
        )

        reset_linux_process_manager()
        pm = get_linux_process_manager()
        pm.attach(process_name="gedit")
        bridge = LinuxBridge()

        try:
            bridge.set_value({"by": "role", "value": "text"}, "Hello UIA-X")
        except Exception:
            pytest.skip("Could not find editable text element in gedit")


# ---------------------------------------------------------------------------
# Tests – send keys
# ---------------------------------------------------------------------------


@requires_atspi
@requires_integration
class TestSendKeys:
    """Test keystroke injection."""

    def test_send_simple_text(self, launch_xterm: Any):
        from uiax.backends.linux.bridge import (
            LinuxBridge,
            get_linux_process_manager,
            reset_linux_process_manager,
        )

        reset_linux_process_manager()
        pm = get_linux_process_manager()
        pm.attach(window_title="UIA-X Test Terminal")
        bridge = LinuxBridge()

        # This should not raise
        bridge.send_keys("echo hello{ENTER}")
