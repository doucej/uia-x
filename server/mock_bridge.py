"""
Mock UIA bridge – delegates to the in-process mock element tree.

No target application required.  Useful for unit tests and CI.
Supports both standard UIA selectors and MSAA/LegacyIAccessible selectors.
"""

from __future__ import annotations

from typing import Any

from mock_uia.tree import MockElement, MockTree
from server.uia_bridge import (
    UIABridge,
    ElementNotFoundError,
    PatternNotSupportedError,
    UIAError,
)


class MockUIABridge(UIABridge):
    """UIA bridge backed by the in-process :class:`MockTree`."""

    def __init__(self, tree: MockTree | None = None) -> None:
        self._tree = tree or MockTree.default()
        self.keys_log: list[str] = []
        self.mouse_log: list[dict] = []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    _META_KEYS = frozenset({"depth"})

    def _find(self, target: dict[str, Any]) -> MockElement:
        selector_keys = {k for k in target if k not in self._META_KEYS}
        if not selector_keys:
            return self._tree.root

        by = target.get("by", "name")
        value = target.get("value", "")
        index = int(target.get("index", 0))

        if by == "path":
            parts = [p.strip() for p in value.split("/") if p.strip()]
            node = self._tree.root
            for part in parts:
                children = [c for c in node.children if c.name == part]
                if not children:
                    raise ElementNotFoundError(target)
                node = children[0]
            return node

        strategy_map = {
            # Standard UIA selectors
            "name": lambda e: e.name == value,
            "automation_id": lambda e: e.automation_id == value,
            "control_type": lambda e: e.control_type == value,
            "class_name": lambda e: e.class_name == value,
            # MSAA / LegacyIAccessible selectors
            "legacy_name": lambda e: e.legacy_name == value,
            "legacy_role": lambda e: str(e.legacy_role) == str(value),
            "child_id": lambda e: str(e.child_id) == str(value),
            "hwnd": lambda e: e.hwnd is not None
            and e.hwnd
            == (
                int(value, 16)
                if isinstance(value, str) and value.startswith("0x")
                else int(value)
            ),
        }
        predicate = strategy_map.get(by)
        if predicate is None:
            raise UIAError(
                f"Unknown selector strategy: {by!r}", code="INVALID_SELECTOR"
            )

        matches = self._tree.root.find_all(predicate)
        if not matches:
            raise ElementNotFoundError(target)
        try:
            return matches[index]
        except IndexError:
            raise ElementNotFoundError(target) from None

    # ------------------------------------------------------------------
    # UIABridge implementation
    # ------------------------------------------------------------------

    def inspect(self, target: dict[str, Any]) -> dict[str, Any]:
        depth = int(target.get("depth", 3)) if target else 3
        element = self._find(target)
        return element.to_dict(depth=depth)

    def invoke(self, target: dict[str, Any]) -> None:
        element = self._find(target)
        if not element.invokable:
            raise PatternNotSupportedError("Invoke", element.name)
        element.invoke()

    def set_value(self, target: dict[str, Any], value: str) -> None:
        element = self._find(target)
        if not element.value_settable:
            raise PatternNotSupportedError("Value", element.name)
        element.set_value(value)

    def send_keys(self, keys: str, target: dict[str, Any] | None = None) -> None:
        self.keys_log.append(keys)

    def legacy_invoke(self, target: dict[str, Any]) -> None:
        element = self._find(target)
        if not element.legacy_invokable:
            raise PatternNotSupportedError(
                "LegacyIAccessible/DefaultAction",
                element.legacy_name or element.name,
            )
        element.legacy_invoke()

    def mouse_click(
        self,
        x: int,
        y: int,
        double: bool = False,
        button: str = "left",
    ) -> None:
        """Record the click in mouse_log (no real UI interaction in mock)."""
        self.mouse_log.append(
            {"x": x, "y": y, "double": double, "button": button}
        )

    def get_text(self, target: dict[str, Any]) -> tuple[str, str]:
        """
        Return the human-readable text of a mock element.

        Priority: UIA ``value`` → MSAA ``legacy_value`` → accessible ``name``.
        """
        element = self._find(target)
        if element.value:
            return element.value, "value"
        if element.legacy_value:
            return element.legacy_value, "msaa_value"
        return element.name, "name"
