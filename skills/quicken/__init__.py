"""
Quicken skill plugin for uia-x.

Registers Quicken-specific Win32 class-name → role mappings and MCP tools
for account navigation, register state reading, filter control, and
reconcile dialog automation.

This module exposes a ``SKILL`` attribute that the skill loader discovers
automatically.
"""

from __future__ import annotations

from typing import Any, Callable

from mcp.server.fastmcp import FastMCP


# ---------------------------------------------------------------------------
# Quicken Win32 class → UIA role mappings
# ---------------------------------------------------------------------------

_QUICKEN_CLASS_ROLES: dict[str, str] = {
    "qc_button": "button",
    "qwcombobox": "combobox",
    "qwpanel": "pane",
    "qwicondisplay": "image",
    "qw_bag_toolbar": "toolbar",
    "qw_main_toolbar": "toolbar",
    "qwmenubar": "menubar",
    "qwlistbox": "list",
    "qwedit": "edit",
    # Transaction register classes
    "qredit": "edit",
    "qwclass_transactionlist": "list",
    "qwclass_txtoolbar": "toolbar",
    "qwscrollbar": "scrollbar",
    "qwinchild": "pane",
    "qwnavbtntray": "toolbar",
    "qwacctbarholder": "pane",
    "qwnavigator": "pane",
    "qsidebar": "pane",
    "qwmdi": "pane",
    "mdifr": "pane",
}

_QUICKEN_INTERACTIVE_CLASSES: set[str] = {
    "qc_button",
    "qwcombobox",
    "qwlistbox",
    "qwedit",
    "qwmenubar",
    # Transaction register
    "qredit",
    "qwclass_transactionlist",
}


# ---------------------------------------------------------------------------
# Skill plugin class
# ---------------------------------------------------------------------------


class QuickenSkill:
    """Quicken automation skill for uia-x."""

    name = "quicken"

    def class_role_mappings(self) -> dict[str, str]:
        return dict(_QUICKEN_CLASS_ROLES)

    def interactive_classes(self) -> set[str]:
        return set(_QUICKEN_INTERACTIVE_CLASSES)

    def register_tools(
        self,
        mcp: FastMCP,
        get_bridge: Callable[[], Any],
        check_auth: Callable[[str], dict[str, Any] | None],
    ) -> None:
        from skills.quicken.tools import register  # noqa: PLC0415
        register(mcp, get_bridge, check_auth)


# Module-level SKILL instance for auto-discovery
SKILL = QuickenSkill()
