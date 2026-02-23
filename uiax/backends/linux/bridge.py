"""
Linux AT-SPI2 bridge – implements the ``UIABridge`` interface.

This is the Linux equivalent of :mod:`server.real_bridge`.  It translates
the MCP tool surface (inspect/invoke/set_value/send_keys/…) into AT-SPI2
calls via :mod:`uiax.backends.linux.atspi_backend` and
:mod:`uiax.backends.linux.util`.
"""

from __future__ import annotations

from typing import Any

from server.uia_bridge import (
    UIABridge,
    ElementNotFoundError,
    PatternNotSupportedError,
    TargetNotFoundError,
    UIAError,
)
from uiax.backends.linux.atspi_backend import (
    Node,
    build_element_dict,
    find_accessible,
    get_desktop,
    list_applications,
    list_top_level_windows,
    node_from_accessible,
)
from uiax.backends.linux.util import (
    atspi_available,
    do_action,
    do_action_by_name,
    get_actions,
    get_text_content,
    mouse_click_atspi,
    require_atspi,
    role_name,
    send_keys_atspi,
    send_keys_xdotool,
    set_text_content,
)

_DEPTH_DEFAULT = 3


# ---------------------------------------------------------------------------
# LinuxProcessManager – AT-SPI equivalent of the Windows ProcessManager
# ---------------------------------------------------------------------------


class LinuxProcessManager:
    """
    Enumerate accessible applications/windows and attach to a target.

    This parallels :class:`server.process_manager.RealProcessManager` but
    uses AT-SPI2 instead of Win32 APIs.
    """

    def __init__(self) -> None:
        self._attached_window: Any | None = None
        self._attached_app: Any | None = None

    @property
    def attached(self) -> Any | None:
        """Currently attached window accessible, or None."""
        return self._attached_window

    def list_windows(self, *, visible_only: bool = True) -> list[dict[str, Any]]:
        """
        Enumerate top-level windows across all applications.

        Returns a list of dicts matching the WindowInfo schema from the
        Windows backend (with platform-appropriate substitutions).
        """
        require_atspi()
        import pyatspi  # type: ignore[import-untyped]

        results: list[dict[str, Any]] = []
        for win in list_top_level_windows():
            try:
                states = win.getState()
                is_visible = states.contains(pyatspi.STATE_VISIBLE)
                is_showing = states.contains(pyatspi.STATE_SHOWING)
                if visible_only and not (is_visible or is_showing):
                    continue

                # Get the parent application
                app = win.parent
                app_name = app.name if app else ""
                pid = 0
                try:
                    pid = app.get_process_id() if app else 0
                except Exception:
                    pass

                from uiax.backends.linux.util import bounding_rect, make_element_id

                rect = bounding_rect(win)
                results.append({
                    "hwnd": hash(make_element_id(win)) & 0xFFFFFFFF,
                    "hwnd_hex": hex(hash(make_element_id(win)) & 0xFFFFFFFF),
                    "title": win.name or "",
                    "class_name": role_name(win),
                    "pid": pid,
                    "process_name": app_name,
                    "visible": is_visible or is_showing,
                    "rect": rect if rect else {
                        "left": 0, "top": 0, "right": 0, "bottom": 0,
                    },
                    # Linux-specific: store the accessible for later attachment
                    "_accessible": win,
                    "_app_accessible": app,
                })
            except Exception:
                continue
        return results

    def attach(
        self,
        *,
        pid: int | None = None,
        process_name: str | None = None,
        window_title: str | None = None,
        class_name: str | None = None,
        hwnd: int | None = None,
    ) -> dict[str, Any]:
        """
        Attach to a window by criteria.

        At least one criterion must be provided.  Returns a window-info
        dict on success.

        Raises
        ------
        ProcessNotFoundError
            If no window matches.
        """
        from server.uia_bridge import ProcessNotFoundError

        if not any([pid, process_name, window_title, class_name, hwnd]):
            raise ProcessNotFoundError(
                "At least one search criterion is required."
            )

        candidates = self.list_windows(visible_only=False)
        for win in candidates:
            if pid is not None and win["pid"] != pid:
                continue
            if process_name is not None and win["process_name"].lower() != process_name.lower():
                continue
            if window_title is not None and window_title.lower() not in win["title"].lower():
                continue
            if class_name is not None and win["class_name"].lower() != class_name.lower():
                continue
            if hwnd is not None and win["hwnd"] != hwnd:
                continue

            self._attached_window = win["_accessible"]
            self._attached_app = win.get("_app_accessible")
            return win

        criteria = {
            k: v for k, v in {
                "pid": pid, "process_name": process_name,
                "window_title": window_title, "class_name": class_name,
                "hwnd": hwnd,
            }.items() if v is not None
        }
        raise ProcessNotFoundError(f"No window matched: {criteria}")

    def detach(self) -> None:
        """Detach from the current target."""
        self._attached_window = None
        self._attached_app = None


# Module-level singleton
_linux_pm: LinuxProcessManager | None = None


def get_linux_process_manager() -> LinuxProcessManager:
    """Return the singleton LinuxProcessManager."""
    global _linux_pm
    if _linux_pm is None:
        _linux_pm = LinuxProcessManager()
    return _linux_pm


def reset_linux_process_manager() -> None:
    """Reset the singleton (for tests)."""
    global _linux_pm
    _linux_pm = None


# ---------------------------------------------------------------------------
# LinuxBridge – UIABridge implementation
# ---------------------------------------------------------------------------


class LinuxBridge(UIABridge):
    """
    Linux AT-SPI2 bridge implementing the cross-platform UIABridge interface.

    This is the Linux equivalent of :class:`server.real_bridge.RealUIABridge`.
    All methods operate on the currently-attached window (selected via the
    ``LinuxProcessManager``).
    """

    def __init__(self) -> None:
        require_atspi()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_root(self) -> Any:
        """Return the AT-SPI accessible for the attached window."""
        pm = get_linux_process_manager()
        root = pm.attached
        if root is None:
            raise TargetNotFoundError(
                "Use select_window to attach to a target first."
            )
        return root

    _META_KEYS = frozenset({"depth"})

    def _find(self, target: dict[str, Any]) -> Any:
        """Locate an accessible from a target selector dict."""
        root = self._get_root()

        selector_keys = {k for k in target if k not in self._META_KEYS}
        if not selector_keys:
            return root

        by = target.get("by", "name")
        value = target.get("value", "")
        index = int(target.get("index", 0))

        # Map Windows-centric strategies to AT-SPI equivalents
        strategy_remap: dict[str, str] = {
            "class_name": "role",          # closest equivalent on Linux
            "legacy_name": "name",         # MSAA name → AT-SPI name
            "legacy_role": "role",         # MSAA role → AT-SPI role
        }
        by = strategy_remap.get(by, by)

        try:
            return find_accessible(root, by=by, value=value, index=index)
        except (LookupError, ValueError) as exc:
            raise ElementNotFoundError(target) from exc

    # ------------------------------------------------------------------
    # UIABridge implementation
    # ------------------------------------------------------------------

    def inspect(self, target: dict[str, Any]) -> dict[str, Any]:
        depth = int(target.get("depth", _DEPTH_DEFAULT)) if target else _DEPTH_DEFAULT
        acc = self._find(target or {})
        return build_element_dict(acc, depth)

    def invoke(self, target: dict[str, Any]) -> None:
        acc = self._find(target)
        actions = get_actions(acc)

        # Try common action names in priority order
        for action_name in ("click", "activate", "press", "invoke", "open"):
            if action_name in [a.lower() for a in actions]:
                if do_action_by_name(acc, action_name):
                    return

        # Fall back to default (first) action
        if actions:
            if do_action(acc, 0):
                return

        # Last resort: try to grab focus
        try:
            comp = acc.queryComponent()
            comp.grabFocus()
            return
        except Exception:
            pass

        raise PatternNotSupportedError(
            "Action",
            acc.name or role_name(acc),
        )

    def set_value(self, target: dict[str, Any], value: str) -> None:
        acc = self._find(target)

        # Try EditableText first
        if set_text_content(acc, value):
            return

        # Try Value interface
        try:
            vi = acc.queryValue()
            vi.currentValue = float(value)
            return
        except (ValueError, TypeError):
            pass
        except Exception:
            pass

        raise PatternNotSupportedError(
            "Value/EditableText",
            acc.name or role_name(acc),
        )

    def send_keys(self, keys: str, target: dict[str, Any] | None = None) -> None:
        if target:
            acc = self._find(target)
            # Try to focus the element first
            try:
                comp = acc.queryComponent()
                comp.grabFocus()
            except Exception:
                pass
        else:
            # Focus the attached window
            root = self._get_root()
            try:
                comp = root.queryComponent()
                comp.grabFocus()
            except Exception:
                pass

        # Send keys – try AT-SPI first, fall back to xdotool
        try:
            send_keys_atspi(keys)
        except Exception:
            send_keys_xdotool(keys)

    def legacy_invoke(self, target: dict[str, Any]) -> None:
        """
        Invoke via the default action (AT-SPI equivalent of MSAA
        DoDefaultAction).

        On Linux, AT-SPI's Action interface subsumes what Windows
        splits across InvokePattern and LegacyIAccessiblePattern.
        """
        acc = self._find(target)
        actions = get_actions(acc)
        if actions:
            if do_action(acc, 0):
                return
        raise PatternNotSupportedError(
            "Action (legacy_invoke)",
            acc.name or role_name(acc),
        )

    def mouse_click(
        self,
        x: int,
        y: int,
        double: bool = False,
        button: str = "left",
    ) -> None:
        mouse_click_atspi(x, y, double=double, button=button)
