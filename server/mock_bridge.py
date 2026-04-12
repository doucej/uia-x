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
    _SELECTOR_STRATEGIES = frozenset({
        "automation_id", "name", "control_type", "class_name", "path",
        "legacy_name", "legacy_role", "child_id", "hwnd",
    })
    # All keys that have non-selector meaning; not treated as shorthand.
    _NON_SELECTOR_KEYS = frozenset({"by", "value", "index", "depth"})

    def _find(self, target: dict[str, Any]) -> MockElement:
        if not target:
            return self._tree.root

        # ------------------------------------------------------------------
        # Normalise shorthand form  {"automation_id": "okBtn"}  →
        # canonical form            {"by": "automation_id", "value": "okBtn"}
        # and reject unknown keys so a typo never silently matches the wrong
        # element (e.g. a name="" fallback that could invoke Minimize).
        # ------------------------------------------------------------------
        if "by" not in target:
            extra = {k for k in target if k not in self._NON_SELECTOR_KEYS}
            if not extra:
                # Only structural keys like "depth" — treat as root selector.
                return self._tree.root
            unknown = extra - self._SELECTOR_STRATEGIES
            if unknown:
                raise UIAError(
                    f"Unrecognised target key(s): {sorted(unknown)!r}. "
                    "Use {\"by\": \"<strategy>\", \"value\": \"<val>\"} "
                    "or a shorthand like {\"automation_id\": \"myButton\"}.",
                    code="INVALID_SELECTOR",
                )
            known = extra & self._SELECTOR_STRATEGIES
            if len(known) > 1:
                raise UIAError(
                    f"Ambiguous shorthand: multiple selector keys {sorted(known)!r}. "
                    "Use the explicit {\"by\": \"<strategy>\", \"value\": \"<val>\"} form.",
                    code="INVALID_SELECTOR",
                )
            if known:
                shorthand_by = next(iter(known))
                target = {
                    "by": shorthand_by,
                    "value": str(target[shorthand_by]),
                    "index": target.get("index", 0),
                }

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

    def find_all(self, filter: dict[str, Any]) -> list[dict[str, Any]]:
        named_only = bool(filter.get("named_only", True))
        must_have_actions = bool(filter.get("has_actions", True))
        roles_filter = [r.lower() for r in (filter.get("roles") or [])]

        # Walk _from_ root_target if specified, else full tree
        root_target = filter.get("root") or {}
        root_elem = self._find(root_target) if root_target else self._tree.root

        results: list[dict[str, Any]] = []
        name_counts: dict[str, int] = {}
        stack = [root_elem]
        while stack:
            elem = stack.pop()
            name = elem.name or ""
            node_role = elem.control_type.lower()
            actions: list[str] = []
            if elem.invokable:
                actions.append("click")
            if elem.legacy_invokable:
                actions.append("do default action")

            include = True
            if named_only and not name:
                include = False
            if roles_filter and node_role not in roles_filter:
                include = False
            if must_have_actions and not actions:
                include = False

            if include:
                per_name_idx = name_counts.get(name, 0)
                name_counts[name] = per_name_idx + 1
                d: dict[str, Any] = {
                    "index": per_name_idx,
                    "name": name,
                    "role": node_role,
                    "actions": actions,
                }
                if elem.value:
                    d["value"] = elem.value
                results.append(d)

            for child in reversed(elem.children):
                stack.append(child)

        return results

    def inspect(self, target: dict[str, Any]) -> dict[str, Any]:
        depth = int(target.get("depth", 3)) if target else 3
        element = self._find(target)
        return element.to_dict(depth=depth)

    def invoke(self, target: dict[str, Any]) -> None:
        element = self._find(target)
        if not element.invokable:
            raise PatternNotSupportedError("Invoke", element.name)
        element.invoke()

    def set_value(self, target: dict[str, Any], value: str) -> dict[str, Any]:
        element = self._find(target)
        if not element.value_settable:
            raise PatternNotSupportedError("Value", element.name)
        element.set_value(value)
        rb = element.value or ""
        return {"ok": True, "method": "mock", "written": value,
                "readback": rb, "validated": rb == value}

    def send_keys(self, keys: str, target: dict[str, Any] | None = None) -> None:
        self.keys_log.append(keys)

    def type_text(self, text: str, target: dict[str, Any] | None = None) -> None:
        self.keys_log.append(text)

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
        force_sendinput: bool = False,
    ) -> None:
        """Record the click in mouse_log (no real UI interaction in mock)."""
        self.mouse_log.append(
            {"x": x, "y": y, "double": double, "button": button}
        )

    def send_win32_message(
        self,
        hwnd: int,
        message: int,
        wparam: int = 0,
        lparam: int = 0,
        sync: bool = True,
    ) -> int:
        """Record the message in mock log and return 1."""
        self.keys_log.append(f"WIN32_MSG hwnd={hwnd} msg={message} wp={wparam} lp={lparam} sync={sync}")
        return 1

    def get_window_enabled_state(self, hwnd: int) -> dict[str, Any]:
        """Mock: always returns enabled=True."""
        return {"hwnd": hwnd, "enabled": True}

    def dismiss_modal_overlay(self, target_hwnd: int) -> dict[str, Any]:
        """Mock: no-op, target is always enabled in tests."""
        return {
            "ok": True,
            "target_hwnd": target_hwnd,
            "enabled": True,
            "dismissed": [],
            "re_enabled": False,
        }

    def list_accounts(self) -> list[dict[str, Any]]:
        """Mock: return a minimal fixed account list for test purposes."""
        return [
            {"name": "Checking", "combo_index": 1, "combo_hwnd": "0x0"},
            {"name": "Savings", "combo_index": 2, "combo_hwnd": "0x0"},
        ]

    def navigate_to_account(self, account_name: str) -> dict[str, Any]:
        """Mock: succeed for known mock accounts, raise for unknown."""
        known = {a["name"].lower() for a in self.list_accounts()}
        if account_name.lower() not in known:
            from server.uia_bridge import UIAError  # noqa: PLC0415
            raise UIAError(
                f"Account {account_name!r} not found in mock.",
                code="ACCOUNT_NOT_FOUND",
            )
        return {"ok": True, "account": account_name, "combo_index": 0}

    def read_register_state(self) -> dict[str, Any]:
        """Mock: return a fixed register state for testing."""
        return {
            "ok": True,
            "account": "Checking",
            "total": "1,234.00",
            "count": "1 Transaction",
            "reconcile_active": False,
            "filter_text": "",
        }

    def set_register_filter(self, text: str) -> dict[str, Any]:
        """Mock: echo the filter text; always returns 1 Transaction."""
        return {"ok": True, "filter": text, "count": "1 Transaction"}

    def get_text(self, target: dict[str, Any] | None = None) -> tuple[str, str]:
        """
        Return the human-readable text of a mock element.

        Priority: UIA ``value`` → MSAA ``legacy_value`` → accessible ``name``.
        When *target* is ``None`` or an empty dict, returns the root element's text.
        """
        element = self._find(target or {})
        if element.value:
            return element.value, "value"
        if element.legacy_value:
            return element.legacy_value, "msaa_value"
        return element.name, "name"
