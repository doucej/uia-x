"""
UIA bridge layer – abstracts real pywinauto/UIA and the mock backend.

V2 generalisation: no longer Quicken-specific.  The bridge operates against
whatever window is currently selected via the process manager.

Call ``get_bridge(backend)`` to obtain the correct implementation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


# ---------------------------------------------------------------------------
# Error taxonomy
# ---------------------------------------------------------------------------


class UIAError(Exception):
    """Raised for all known, expected UIA failures."""

    def __init__(self, message: str, code: str = "UIA_ERROR") -> None:
        super().__init__(message)
        self.code = code


class TargetNotFoundError(UIAError):
    """No automation target (process/window) is currently attached."""

    def __init__(self, detail: str = "") -> None:
        msg = "No automation target attached."
        if detail:
            msg += f" {detail}"
        super().__init__(msg, code="TARGET_NOT_ATTACHED")


class ElementNotFoundError(UIAError):
    def __init__(self, target: dict) -> None:
        super().__init__(
            f"No element matched target: {target}",
            code="ELEMENT_NOT_FOUND",
        )


class PatternNotSupportedError(UIAError):
    def __init__(self, pattern: str, element_name: str) -> None:
        super().__init__(
            f"Element '{element_name}' does not support the {pattern} pattern.",
            code="PATTERN_NOT_SUPPORTED",
        )


class ProcessNotFoundError(UIAError):
    def __init__(self, detail: str = "") -> None:
        msg = "Requested process or window not found."
        if detail:
            msg += f" {detail}"
        super().__init__(msg, code="PROCESS_NOT_FOUND")


class AuthenticationError(UIAError):
    def __init__(self, detail: str = "Invalid or missing API key.") -> None:
        super().__init__(detail, code="AUTH_ERROR")


# ---------------------------------------------------------------------------
# Backward compat alias (V1 code used QuickenNotFoundError)
# ---------------------------------------------------------------------------
QuickenNotFoundError = TargetNotFoundError


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class UIABridge(ABC):
    """Common interface for real and mock UIA backends."""

    @abstractmethod
    def inspect(self, target: dict[str, Any]) -> dict[str, Any]:
        """Return a JSON-serialisable tree of the matched element."""

    @abstractmethod
    def invoke(self, target: dict[str, Any]) -> None:
        """Invoke (click/activate) the matched element."""

    @abstractmethod
    def set_value(self, target: dict[str, Any], value: str) -> None:
        """Set the value of the matched element."""

    @abstractmethod
    def send_keys(self, keys: str, target: dict[str, Any] | None = None) -> None:
        """
        Send keystrokes to the attached window.

        Parameters
        ----------
        keys : str
            Key sequence in pywinauto / SendKeys notation.
        target : dict or None
            Optional element selector.  When provided the element is focused
            before keys are sent.  Pass ``None`` to send to the currently
            focused control.
        """

    @abstractmethod
    def legacy_invoke(self, target: dict[str, Any]) -> None:
        """
        Invoke an element via LegacyIAccessiblePattern.DoDefaultAction (MSAA).

        Parameters
        ----------
        target : dict
            Selector.  Supports all standard ``by`` strategies plus the MSAA
            extras: ``legacy_name``, ``legacy_role``, ``child_id``, ``hwnd``.
        """

    @abstractmethod
    def mouse_click(
        self,
        x: int,
        y: int,
        double: bool = False,
        button: str = "left",
    ) -> None:
        """
        Click at absolute screen coordinates.

        Parameters
        ----------
        x, y : int
            Screen coordinates (physical pixels, origin at top-left).
        double : bool
            If True, send a double-click instead of a single click.
        button : str
            ``"left"`` (default), ``"right"``, or ``"middle"``.
        """


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_bridge(backend: str = "real") -> UIABridge:
    """
    Return the appropriate bridge implementation.

    Parameters
    ----------
    backend : str
        ``"real"``  – live Windows UI Automation via pywinauto
        ``"mock"``  – in-process mock tree (no target app required)
    """
    if backend == "mock":
        from server.mock_bridge import MockUIABridge  # noqa: PLC0415

        return MockUIABridge()
    else:
        from server.real_bridge import RealUIABridge  # noqa: PLC0415

        return RealUIABridge()
