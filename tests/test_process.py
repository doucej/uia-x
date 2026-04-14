"""
Tests for process enumeration and window attachment.
"""

from __future__ import annotations

import pytest

from server.process_manager import (
    MockProcessManager,
    WindowInfo,
    _default_mock_windows,
)
from server.uia_bridge import ProcessNotFoundError


@pytest.fixture()
def pm() -> MockProcessManager:
    """Fresh MockProcessManager with default fixture windows."""
    return MockProcessManager()


class TestProcessList:
    def test_list_returns_visible_windows(self, pm: MockProcessManager):
        windows = pm.list_windows(visible_only=True)
        assert all(w.visible for w in windows)
        assert len(windows) >= 3  # Notepad, Quicken, Calculator

    def test_list_all_includes_hidden(self, pm: MockProcessManager):
        all_windows = pm.list_windows(visible_only=False)
        visible_only = pm.list_windows(visible_only=True)
        assert len(all_windows) > len(visible_only)

    def test_list_returns_windowinfo_objects(self, pm: MockProcessManager):
        windows = pm.list_windows()
        for w in windows:
            assert isinstance(w, WindowInfo)
            assert isinstance(w.hwnd, int)
            assert isinstance(w.title, str)
            assert isinstance(w.pid, int)

    def test_list_has_expected_fixtures(self, pm: MockProcessManager):
        names = {w.process_name for w in pm.list_windows(visible_only=False)}
        assert "notepad.exe" in names
        assert "qw.exe" in names


class TestAttach:
    def test_attach_by_process_name(self, pm: MockProcessManager):
        win = pm.attach(process_name="notepad.exe")
        assert win.process_name == "notepad.exe"
        assert pm.attached is win

    def test_attach_by_pid(self, pm: MockProcessManager):
        win = pm.attach(pid=1234)
        assert win.pid == 1234

    def test_attach_by_window_title(self, pm: MockProcessManager):
        win = pm.attach(window_title="Notepad")
        assert "Notepad" in win.title

    def test_attach_by_class_name(self, pm: MockProcessManager):
        win = pm.attach(class_name="Notepad")
        assert win.class_name == "Notepad"

    def test_attach_by_hwnd(self, pm: MockProcessManager):
        win = pm.attach(hwnd=0xBB01)
        assert win.hwnd == 0xBB01

    def test_attach_combined_criteria(self, pm: MockProcessManager):
        win = pm.attach(process_name="qw.exe", window_title="Quicken")
        assert win.process_name == "qw.exe"
        assert "Quicken" in win.title

    def test_attach_not_found_raises(self, pm: MockProcessManager):
        with pytest.raises(ProcessNotFoundError) as exc_info:
            pm.attach(process_name="nonexistent.exe")
        assert exc_info.value.code == "PROCESS_NOT_FOUND"

    def test_attach_no_criteria_raises(self, pm: MockProcessManager):
        with pytest.raises(ProcessNotFoundError):
            pm.attach()

    def test_attach_switches_target(self, pm: MockProcessManager):
        pm.attach(process_name="notepad.exe")
        assert pm.attached.process_name == "notepad.exe"
        pm.attach(process_name="qw.exe")
        assert pm.attached.process_name == "qw.exe"


class TestDetach:
    def test_detach_clears_attached(self, pm: MockProcessManager):
        pm.attach(process_name="notepad.exe")
        assert pm.attached is not None
        pm.detach()
        assert pm.attached is None

    def test_detach_when_not_attached(self, pm: MockProcessManager):
        pm.detach()  # should not raise
        assert pm.attached is None


class TestCustomWindows:
    def test_custom_window_list(self):
        custom = [
            WindowInfo(
                hwnd=0x9999, title="My App", class_name="MyClass",
                pid=42, process_name="myapp.exe",
            ),
        ]
        pm = MockProcessManager(windows=custom)
        assert len(pm.list_windows()) == 1
        win = pm.attach(hwnd=0x9999)
        assert win.title == "My App"


class TestWindowRanking:
    """Issue #9 — attach() must prefer the large visible window over invisible helper."""

    def test_attach_qw_returns_main_frame_not_fly_window(self, pm: MockProcessManager):
        """QWFly (invisible 1×1 helper) must NOT win over QFRAME (main window)."""
        win = pm.attach(process_name="qw.exe")
        # The main Quicken window should win; QWFly is invisible and tiny
        assert win.visible, "attach() returned an invisible window"
        rect = win.rect
        if rect and isinstance(rect, dict):
            w = rect.get("right", 0) - rect.get("left", 0)
            h = rect.get("bottom", 0) - rect.get("top", 0)
            assert w * h > 100, f"attach() returned a tiny window with area {w * h}"

    def test_attach_prefers_visible_over_invisible(self):
        """When two windows have same process_name, visible one wins."""
        windows = [
            WindowInfo(
                hwnd=0x0001, title="Invisible Helper", class_name="QWFly",
                pid=500, process_name="qw.exe", visible=False,
                rect={"left": 0, "top": 0, "right": 1, "bottom": 1},
            ),
            WindowInfo(
                hwnd=0x0002, title="Quicken 2024", class_name="QFRAME",
                pid=500, process_name="qw.exe", visible=True,
                rect={"left": 100, "top": 100, "right": 1700, "bottom": 940},
            ),
        ]
        pm = MockProcessManager(windows=windows)
        win = pm.attach(process_name="qw.exe")
        assert win.hwnd == 0x0002, f"Expected QFRAME (0x0002) but got {hex(win.hwnd)}"

    def test_attach_among_equally_visible_prefers_larger(self):
        """Among visible windows, the one with greater area wins."""
        windows = [
            WindowInfo(
                hwnd=0x0010, title="Small Panel", class_name="QPANEL",
                pid=500, process_name="qw.exe", visible=True,
                rect={"left": 0, "top": 0, "right": 100, "bottom": 100},  # area = 10 000
            ),
            WindowInfo(
                hwnd=0x0020, title="Quicken 2024", class_name="QFRAME",
                pid=500, process_name="qw.exe", visible=True,
                rect={"left": 0, "top": 0, "right": 1600, "bottom": 900},  # area = 1 440 000
            ),
        ]
        pm = MockProcessManager(windows=windows)
        win = pm.attach(process_name="qw.exe")
        assert win.hwnd == 0x0020, f"Expected QFRAME (0x0020) but got {hex(win.hwnd)}"

    def test_attach_prefers_app_over_shell_window(self):
        """When title matches both an app and a File Explorer, prefer the app."""
        windows = [
            WindowInfo(
                hwnd=0x0030, title="Quicken - File Explorer",
                class_name="CabinetWClass",
                pid=600, process_name="explorer.exe", visible=True,
                rect={"left": 0, "top": 0, "right": 1920, "bottom": 1080},
            ),
            WindowInfo(
                hwnd=0x0040, title="Quicken Classic Premier - [Checking]",
                class_name="QFRAME",
                pid=700, process_name="qw.exe", visible=True,
                rect={"left": 0, "top": 0, "right": 1300, "bottom": 750},
            ),
        ]
        pm = MockProcessManager(windows=windows)
        win = pm.attach(window_title="Quicken")
        assert win.hwnd == 0x0040, (
            f"Expected QFRAME (0x0040) but got {hex(win.hwnd)} "
            f"({win.class_name}) — shell window should rank lower"
        )


class TestDpiScale:
    """Issue #10/#11 — dpi_scale field is present on WindowInfo."""

    def test_windowinfo_has_dpi_scale_field(self):
        w = WindowInfo(
            hwnd=1, title="Test", class_name="TestClass",
            pid=1, process_name="test.exe",
        )
        # Field must exist; None is acceptable when DPI is not determined
        assert hasattr(w, "dpi_scale")

    def test_dpi_scale_none_by_default(self):
        w = WindowInfo(
            hwnd=1, title="Test", class_name="TestClass",
            pid=1, process_name="test.exe",
        )
        assert w.dpi_scale is None
