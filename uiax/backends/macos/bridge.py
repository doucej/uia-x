"""
macOS AXAPI bridge – implements the ``UIABridge`` interface.

This is the macOS equivalent of :mod:`server.real_bridge` and
:mod:`uiax.backends.linux.bridge`.  It translates the MCP tool surface
(inspect/invoke/set_value/send_keys/…) into AXAPI calls via
:mod:`uiax.backends.macos.axapi_backend` and
:mod:`uiax.backends.macos.util`.
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
from uiax.backends.macos.axapi_backend import (
    Node,
    build_element_dict,
    find_element,
    node_from_element,
)
from uiax.backends.macos.util import (
    ax_action_names,
    ax_attribute,
    ax_perform_action,
    ax_set_attribute,
    axapi_available,
    get_app_element,
    get_children,
    get_description,
    get_frame,
    get_title,
    get_value,
    list_all_windows,
    make_element_id,
    mouse_click_quartz,
    require_axapi,
    role_name,
    send_keys_quartz,
)

_DEPTH_DEFAULT = 3


# ---------------------------------------------------------------------------
# MacOSProcessManager – AXAPI equivalent of the Windows ProcessManager
# ---------------------------------------------------------------------------


class MacOSProcessManager:
    """
    Enumerate accessible applications/windows and attach to a target.

    This parallels :class:`server.process_manager.RealProcessManager` and
    :class:`uiax.backends.linux.bridge.LinuxProcessManager` but uses
    macOS AXAPI instead of Win32 / AT-SPI2.
    """

    def __init__(self) -> None:
        self._attached_window: Any | None = None
        self._attached_app_pid: int | None = None

    @property
    def attached(self) -> Any | None:
        """Currently attached AXUIElement window, or None."""
        return self._attached_window

    @property
    def attached_app_pid(self) -> int | None:
        """PID of the currently attached application, or None."""
        return self._attached_app_pid

    def list_windows(self, *, visible_only: bool = True) -> list[dict[str, Any]]:
        """
        Enumerate top-level windows across all applications.

        Returns a list of dicts matching the WindowInfo schema from the
        Windows backend (with platform-appropriate substitutions).
        """
        require_axapi()

        results: list[dict[str, Any]] = []
        for win in list_all_windows():
            try:
                if visible_only and not win.get("visible", True):
                    continue
                results.append(win)
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
        bundle_id: str | None = None,
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

        if not any([pid, process_name, window_title, class_name, hwnd, bundle_id]):
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
            if bundle_id is not None and win.get("bundle_id", "").lower() != bundle_id.lower():
                continue

            self._attached_window = win["_ax_element"]
            self._attached_app_pid = win.get("_app_pid")
            return win

        criteria = {
            k: v for k, v in {
                "pid": pid, "process_name": process_name,
                "window_title": window_title, "class_name": class_name,
                "hwnd": hwnd, "bundle_id": bundle_id,
            }.items() if v is not None
        }
        raise ProcessNotFoundError(f"No window matched: {criteria}")

    def detach(self) -> None:
        """Detach from the current target."""
        self._attached_window = None
        self._attached_app_pid = None


# Module-level singleton
_macos_pm: MacOSProcessManager | None = None


def get_macos_process_manager() -> MacOSProcessManager:
    """Return the singleton MacOSProcessManager."""
    global _macos_pm
    if _macos_pm is None:
        _macos_pm = MacOSProcessManager()
    return _macos_pm


def reset_macos_process_manager() -> None:
    """Reset the singleton (for tests)."""
    global _macos_pm
    _macos_pm = None


# ---------------------------------------------------------------------------
# MacOSBridge – UIABridge implementation
# ---------------------------------------------------------------------------


class MacOSBridge(UIABridge):
    """
    macOS AXAPI bridge implementing the cross-platform UIABridge interface.

    This is the macOS equivalent of :class:`server.real_bridge.RealUIABridge`
    and :class:`uiax.backends.linux.bridge.LinuxBridge`.  All methods operate
    on the currently-attached window (selected via ``MacOSProcessManager``).
    """

    def __init__(self) -> None:
        require_axapi()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_root(self) -> Any:
        """Return the AXUIElement for the attached window."""
        pm = get_macos_process_manager()
        root = pm.attached
        if root is None:
            raise TargetNotFoundError(
                "Use select_window to attach to a target first."
            )
        return root

    _META_KEYS = frozenset({"depth"})

    def _find(self, target: dict[str, Any]) -> Any:
        """Locate an AXUIElement from a target selector dict."""
        root = self._get_root()

        selector_keys = {k for k in target if k not in self._META_KEYS}
        if not selector_keys:
            return root

        by = target.get("by", "name")
        value = target.get("value", "")
        index = int(target.get("index", 0))

        # Map Windows-centric strategies to AXAPI equivalents
        strategy_remap: dict[str, str] = {
            "class_name": "role",
            "legacy_name": "name",
            "legacy_role": "role",
        }
        by = strategy_remap.get(by, by)

        try:
            return find_element(root, by=by, value=value, index=index)
        except (LookupError, ValueError) as exc:
            raise ElementNotFoundError(target) from exc

    # ------------------------------------------------------------------
    # UIABridge implementation
    # ------------------------------------------------------------------

    def inspect(self, target: dict[str, Any]) -> dict[str, Any]:
        depth = int(target.get("depth", _DEPTH_DEFAULT)) if target else _DEPTH_DEFAULT
        element = self._find(target or {})
        return build_element_dict(element, depth)

    def invoke(self, target: dict[str, Any]) -> None:
        element = self._find(target)
        actions = ax_action_names(element)

        # Try common action names in priority order
        for action_name in (
            "AXPress", "AXConfirm", "AXOpen", "AXPick",
            "AXCancel", "AXShowMenu",
        ):
            if action_name in actions:
                if ax_perform_action(element, action_name):
                    return

        # Fall back to the first available action
        if actions:
            if ax_perform_action(element, actions[0]):
                return

        # Last resort: try to set focus
        try:
            if ax_set_attribute(element, "AXFocused", True):
                return
        except Exception:
            pass

        name = get_title(element) or get_description(element) or role_name(element)
        raise PatternNotSupportedError("Action", name)

    def set_value(self, target: dict[str, Any], value: str) -> None:
        element = self._find(target)

        # Try AXValue (settable)
        if ax_set_attribute(element, "AXValue", value):
            return

        # Try setting via focused text entry
        try:
            ax_set_attribute(element, "AXFocused", True)
            # Select all existing text, then type new value
            ax_perform_action(element, "AXPress")
            send_keys_quartz("@a")  # Cmd+A to select all
            send_keys_quartz(value)
            return
        except Exception:
            pass

        name = get_title(element) or get_description(element) or role_name(element)
        raise PatternNotSupportedError("Value", name)

    def send_keys(self, keys: str, target: dict[str, Any] | None = None) -> None:
        if target:
            element = self._find(target)
            # Try to focus the element first
            try:
                ax_set_attribute(element, "AXFocused", True)
            except Exception:
                pass
        else:
            # Focus the attached window
            root = self._get_root()
            try:
                ax_perform_action(root, "AXRaise")
            except Exception:
                pass

        send_keys_quartz(keys)

    def legacy_invoke(self, target: dict[str, Any]) -> None:
        """
        Invoke via the default action (macOS equivalent of MSAA
        DoDefaultAction).

        On macOS, accessibility actions subsume what Windows splits
        across InvokePattern and LegacyIAccessiblePattern.
        """
        element = self._find(target)
        actions = ax_action_names(element)

        # AXPress is the most common "default action"
        if "AXPress" in actions:
            if ax_perform_action(element, "AXPress"):
                return

        # Fall back to first action
        if actions:
            if ax_perform_action(element, actions[0]):
                return

        name = get_title(element) or get_description(element) or role_name(element)
        raise PatternNotSupportedError("Action (legacy_invoke)", name)

    def mouse_click(
        self,
        x: int,
        y: int,
        double: bool = False,
        button: str = "left",
    ) -> None:
        mouse_click_quartz(x, y, double=double, button=button)

    def get_text(self, target: dict[str, Any]) -> tuple[str, str]:
        """
        Return the human-readable text of an AXAPI element.

        Priority
        --------
        1. ``AXValue`` — the most specific programmatic value (editable
           fields, display labels such as macOS Calculator's result).
        2. ``AXTitle`` / accessible name — the human-readable label.
        3. ``AXDescription`` — longer description text.
        """
        element = self._find(target)

        # 1. AXValue
        val = get_value(element)
        if val is not None and val.strip():
            return val, "value"

        # 2. AXTitle / name
        title = get_title(element)
        if title and title.strip():
            return title, "name"

        # 3. AXDescription
        desc = get_description(element)
        if desc and desc.strip():
            return desc, "description"

        return "", "none"
