"""
Process and window manager – enumerate running processes, their top-level
windows, and attach to a specific process/window as the active automation
target.

Operates in several modes:
  * **real**   – auto-detects platform and delegates to the appropriate backend
  * **mock**   – returns canned data for unit tests
  * **linux**  – force AT-SPI2 backend (Linux)
  * **macos**  – force AXAPI backend (macOS)
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class WindowInfo:
    """Minimal description of a top-level window."""

    hwnd: int
    title: str
    class_name: str
    pid: int
    process_name: str
    visible: bool = True
    rect: dict[str, int] = field(default_factory=lambda: {
        "left": 0, "top": 0, "right": 800, "bottom": 600,
    })


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class ProcessManager(ABC):
    """Common interface for real and mock process managers."""

    def __init__(self) -> None:
        self._attached: Optional[WindowInfo] = None

    @property
    def attached(self) -> Optional[WindowInfo]:
        """Currently-attached window, or ``None``."""
        return self._attached

    @abstractmethod
    def list_windows(self, *, visible_only: bool = True) -> list[WindowInfo]:
        """Return all top-level windows on the desktop."""

    @abstractmethod
    def attach(
        self,
        *,
        pid: int | None = None,
        process_name: str | None = None,
        window_title: str | None = None,
        class_name: str | None = None,
        hwnd: int | None = None,
    ) -> WindowInfo:
        """
        Attach to a process/window.

        At least one parameter must be provided.  When multiple are supplied
        they act as an AND filter.

        Returns
        -------
        WindowInfo
            The window that was matched and attached.

        Raises
        ------
        ProcessNotFoundError
            If no window matches the given criteria.
        """

    def detach(self) -> None:
        """Detach from the current target (if any)."""
        self._attached = None


# ---------------------------------------------------------------------------
# Real implementation
# ---------------------------------------------------------------------------


class RealProcessManager(ProcessManager):
    """Live Windows implementation using ctypes + pywinauto."""

    def list_windows(self, *, visible_only: bool = True) -> list[WindowInfo]:
        import ctypes  # noqa: PLC0415
        import ctypes.wintypes  # noqa: PLC0415

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi

        windows: list[WindowInfo] = []

        def _enum_callback(hwnd: int, _lparam: Any) -> bool:
            if visible_only and not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True

            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value

            cls_buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls_buf, 256)
            cls_name = cls_buf.value

            pid = ctypes.wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            pid_val = pid.value

            # Get process name
            proc_name = ""
            handle = kernel32.OpenProcess(0x0410, False, pid_val)  # PROCESS_QUERY_INFORMATION | PROCESS_VM_READ
            if handle:
                name_buf = ctypes.create_unicode_buffer(260)
                psapi.GetModuleBaseNameW(handle, None, name_buf, 260)
                proc_name = name_buf.value
                kernel32.CloseHandle(handle)

            rect_struct = ctypes.wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect_struct))

            windows.append(WindowInfo(
                hwnd=hwnd,
                title=title,
                class_name=cls_name,
                pid=pid_val,
                process_name=proc_name,
                visible=bool(user32.IsWindowVisible(hwnd)),
                rect={
                    "left": rect_struct.left,
                    "top": rect_struct.top,
                    "right": rect_struct.right,
                    "bottom": rect_struct.bottom,
                },
            ))
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.POINTER(ctypes.c_int))
        user32.EnumWindows(WNDENUMPROC(_enum_callback), 0)
        return windows

    def attach(
        self,
        *,
        pid: int | None = None,
        process_name: str | None = None,
        window_title: str | None = None,
        class_name: str | None = None,
        hwnd: int | None = None,
    ) -> WindowInfo:
        from server.uia_bridge import ProcessNotFoundError  # noqa: PLC0415

        if not any([pid, process_name, window_title, class_name, hwnd]):
            raise ProcessNotFoundError("At least one search criterion is required.")

        candidates = self.list_windows(visible_only=False)

        for win in candidates:
            if pid is not None and win.pid != pid:
                continue
            if process_name is not None and win.process_name.lower() != process_name.lower():
                continue
            if window_title is not None and window_title.lower() not in win.title.lower():
                continue
            if class_name is not None and win.class_name.lower() != class_name.lower():
                continue
            if hwnd is not None and win.hwnd != hwnd:
                continue
            self._attached = win
            return win

        criteria = {
            k: v for k, v in {
                "pid": pid, "process_name": process_name,
                "window_title": window_title, "class_name": class_name,
                "hwnd": hwnd,
            }.items() if v is not None
        }
        raise ProcessNotFoundError(f"No window matched: {criteria}")


# ---------------------------------------------------------------------------
# Mock implementation
# ---------------------------------------------------------------------------


class MockProcessManager(ProcessManager):
    """In-process mock for testing – uses a static window list."""

    def __init__(self, windows: list[WindowInfo] | None = None) -> None:
        super().__init__()
        self._windows = windows if windows is not None else _default_mock_windows()

    def list_windows(self, *, visible_only: bool = True) -> list[WindowInfo]:
        if visible_only:
            return [w for w in self._windows if w.visible]
        return list(self._windows)

    def attach(
        self,
        *,
        pid: int | None = None,
        process_name: str | None = None,
        window_title: str | None = None,
        class_name: str | None = None,
        hwnd: int | None = None,
    ) -> WindowInfo:
        from server.uia_bridge import ProcessNotFoundError  # noqa: PLC0415

        if not any([pid, process_name, window_title, class_name, hwnd]):
            raise ProcessNotFoundError("At least one search criterion is required.")

        for win in self._windows:
            if pid is not None and win.pid != pid:
                continue
            if process_name is not None and win.process_name.lower() != process_name.lower():
                continue
            if window_title is not None and window_title.lower() not in win.title.lower():
                continue
            if class_name is not None and win.class_name.lower() != class_name.lower():
                continue
            if hwnd is not None and win.hwnd != hwnd:
                continue
            self._attached = win
            return win

        criteria = {
            k: v for k, v in {
                "pid": pid, "process_name": process_name,
                "window_title": window_title, "class_name": class_name,
                "hwnd": hwnd,
            }.items() if v is not None
        }
        raise ProcessNotFoundError(f"No window matched: {criteria}")


# ---------------------------------------------------------------------------
# Linux AT-SPI2 adapter
# ---------------------------------------------------------------------------


class LinuxProcessManagerAdapter(ProcessManager):
    """
    Adapter that wraps :class:`uiax.backends.linux.bridge.LinuxProcessManager`
    to conform to the :class:`ProcessManager` ABC.

    Translates AT-SPI2 window dicts into :class:`WindowInfo` instances.
    """

    def __init__(self) -> None:
        super().__init__()
        from uiax.backends.linux.bridge import get_linux_process_manager  # noqa: PLC0415

        self._lpm = get_linux_process_manager()

    def list_windows(self, *, visible_only: bool = True) -> list[WindowInfo]:
        raw = self._lpm.list_windows(visible_only=visible_only)
        return [self._to_window_info(w) for w in raw]

    def attach(
        self,
        *,
        pid: int | None = None,
        process_name: str | None = None,
        window_title: str | None = None,
        class_name: str | None = None,
        hwnd: int | None = None,
    ) -> WindowInfo:
        win = self._lpm.attach(
            pid=pid,
            process_name=process_name,
            window_title=window_title,
            class_name=class_name,
            hwnd=hwnd,
        )
        self._attached = self._to_window_info(win)
        return self._attached

    @staticmethod
    def _to_window_info(w: dict) -> WindowInfo:
        return WindowInfo(
            hwnd=w.get("hwnd", 0),
            title=w.get("title", ""),
            class_name=w.get("class_name", ""),
            pid=w.get("pid", 0),
            process_name=w.get("process_name", ""),
            visible=w.get("visible", True),
            rect=w.get("rect", {"left": 0, "top": 0, "right": 0, "bottom": 0}),
        )


# ---------------------------------------------------------------------------
# macOS AXAPI adapter
# ---------------------------------------------------------------------------


class MacOSProcessManagerAdapter(ProcessManager):
    """
    Adapter that wraps :class:`uiax.backends.macos.bridge.MacOSProcessManager`
    to conform to the :class:`ProcessManager` ABC.

    Translates AXAPI window dicts into :class:`WindowInfo` instances.
    """

    def __init__(self) -> None:
        super().__init__()
        from uiax.backends.macos.bridge import get_macos_process_manager  # noqa: PLC0415

        self._mpm = get_macos_process_manager()

    def list_windows(self, *, visible_only: bool = True) -> list[WindowInfo]:
        raw = self._mpm.list_windows(visible_only=visible_only)
        return [self._to_window_info(w) for w in raw]

    def attach(
        self,
        *,
        pid: int | None = None,
        process_name: str | None = None,
        window_title: str | None = None,
        class_name: str | None = None,
        hwnd: int | None = None,
    ) -> WindowInfo:
        win = self._mpm.attach(
            pid=pid,
            process_name=process_name,
            window_title=window_title,
            class_name=class_name,
            hwnd=hwnd,
        )
        self._attached = self._to_window_info(win)
        return self._attached

    @staticmethod
    def _to_window_info(w: dict) -> WindowInfo:
        return WindowInfo(
            hwnd=w.get("hwnd", 0),
            title=w.get("title", ""),
            class_name=w.get("class_name", ""),
            pid=w.get("pid", 0),
            process_name=w.get("process_name", ""),
            visible=w.get("visible", True),
            rect=w.get("rect", {"left": 0, "top": 0, "right": 0, "bottom": 0}),
        )


# ---------------------------------------------------------------------------
# Default mock fixtures
# ---------------------------------------------------------------------------


def _default_mock_windows() -> list[WindowInfo]:
    """A few representative windows for testing."""
    return [
        WindowInfo(
            hwnd=0xAA01, title="Quicken Classic Premier - finances",
            class_name="QWinFrame", pid=1234, process_name="qw.exe",
            visible=True,
            rect={"left": 0, "top": 0, "right": 1280, "bottom": 800},
        ),
        WindowInfo(
            hwnd=0xBB01, title="Untitled - Notepad",
            class_name="Notepad", pid=5678, process_name="notepad.exe",
            visible=True,
            rect={"left": 100, "top": 100, "right": 900, "bottom": 700},
        ),
        WindowInfo(
            hwnd=0xCC01, title="Calculator",
            class_name="ApplicationFrameWindow", pid=9012, process_name="Calculator.exe",
            visible=True,
            rect={"left": 200, "top": 200, "right": 600, "bottom": 600},
        ),
        WindowInfo(
            hwnd=0xDD01, title="",
            class_name="Shell_TrayWnd", pid=4, process_name="explorer.exe",
            visible=True,
            rect={"left": 0, "top": 1040, "right": 1920, "bottom": 1080},
        ),
        WindowInfo(
            hwnd=0xEE01, title="Background Worker",
            class_name="HiddenWindow", pid=7777, process_name="svchost.exe",
            visible=False,
        ),
    ]


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------

_instance: ProcessManager | None = None


def get_process_manager(backend: str | None = None) -> ProcessManager:
    """Return the singleton ProcessManager, creating if needed."""
    global _instance
    if _instance is None:
        if backend is None:
            backend = (
                os.environ.get("UIAX_BACKEND", "")
                or os.environ.get("UIA_BACKEND", "real")
            ).lower()
        if backend == "mock":
            _instance = MockProcessManager()
        elif backend == "linux" or (backend == "real" and _is_linux()):
            _instance = LinuxProcessManagerAdapter()
        elif backend == "macos" or (backend == "real" and _is_macos()):
            _instance = MacOSProcessManagerAdapter()
        else:
            _instance = RealProcessManager()  # Windows
    return _instance


def _is_linux() -> bool:
    """Return True if the current platform is Linux."""
    import sys  # noqa: PLC0415

    return sys.platform.startswith("linux")


def _is_macos() -> bool:
    """Return True if the current platform is macOS."""
    import sys  # noqa: PLC0415

    return sys.platform == "darwin"


def reset_process_manager() -> None:
    """Reset the singleton (for tests)."""
    global _instance
    _instance = None


def set_process_manager(pm: ProcessManager) -> None:
    """Inject a specific ProcessManager (for tests)."""
    global _instance
    _instance = pm
