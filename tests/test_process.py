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
