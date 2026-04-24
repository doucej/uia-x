"""
Skill plugin infrastructure for uia-x.

Skills are application-specific extensions that register custom MCP tools
and Win32 class-name mappings with the core server.  They keep domain logic
(e.g. Quicken account navigation, reconcile dialogs) out of the generic
bridge/server layer.

A skill is a Python package under ``skills/`` that exposes a module-level
``SKILL`` instance implementing :class:`SkillPlugin`.

Usage from server.py::

    from skills import load_skills
    load_skills(mcp, get_bridge=_get_bridge, check_auth=_check_auth)
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import Any, Callable, Protocol, runtime_checkable

from mcp.server.fastmcp import FastMCP

log = logging.getLogger("uiax.skills")


# ---------------------------------------------------------------------------
# Protocol that every skill must implement
# ---------------------------------------------------------------------------


@runtime_checkable
class SkillPlugin(Protocol):
    """Interface that skill plugins expose via their ``SKILL`` attribute."""

    @property
    def name(self) -> str:
        """Short identifier, e.g. ``"quicken"``."""
        ...

    def class_role_mappings(self) -> dict[str, str]:
        """Return custom Win32 class-name → UIA role mappings.

        Example: ``{"qc_button": "button", "qwcombobox": "combobox"}``
        """
        ...

    def interactive_classes(self) -> set[str]:
        """Return Win32 class names that should be treated as interactive."""
        ...

    def register_tools(
        self,
        mcp: FastMCP,
        get_bridge: Callable[[], Any],
        check_auth: Callable[[str], dict[str, Any] | None],
    ) -> None:
        """Register application-specific MCP tools with *mcp*."""
        ...


# ---------------------------------------------------------------------------
# Auto-discovery and loading
# ---------------------------------------------------------------------------


def load_skills(
    mcp: FastMCP,
    get_bridge: Callable[[], Any],
    check_auth: Callable[[str], dict[str, Any] | None],
    *,
    skill_names: list[str] | None = None,
) -> list[SkillPlugin]:
    """Discover and load skill plugins.

    Parameters
    ----------
    mcp
        The FastMCP server instance to register tools with.
    get_bridge
        Callable that returns the current bridge instance.
    check_auth
        Auth-checking callable (returns error dict or None).
    skill_names
        If given, load only these skills.  Otherwise auto-discover all
        packages under ``skills/``.

    Returns
    -------
    list[SkillPlugin]
        Successfully loaded skill plugins.
    """
    loaded: list[SkillPlugin] = []
    package = importlib.import_module("skills")

    if skill_names is not None:
        names = skill_names
    else:
        # Auto-discover sub-packages
        names = [
            mod.name
            for mod in pkgutil.iter_modules(package.__path__)
            if mod.ispkg
        ]

    for name in names:
        try:
            mod = importlib.import_module(f"skills.{name}")
        except Exception:
            log.warning("Failed to import skill %r", name, exc_info=True)
            continue

        skill: SkillPlugin | None = getattr(mod, "SKILL", None)
        if skill is None:
            log.warning("Skill %r has no SKILL attribute, skipping", name)
            continue

        # Register Win32 class mappings (only on Windows)
        try:
            _apply_class_mappings(skill)
        except Exception:
            log.warning("Failed to apply class mappings for skill %r", name, exc_info=True)

        # Register MCP tools
        try:
            skill.register_tools(mcp, get_bridge, check_auth)
            log.info("Loaded skill %r (%d tools)", skill.name, _count_new_tools(mcp))
        except Exception:
            log.warning("Failed to register tools for skill %r", name, exc_info=True)
            continue

        loaded.append(skill)

    return loaded


def _apply_class_mappings(skill: SkillPlugin) -> None:
    """Register a skill's class-name mappings with the Win32 bridge."""
    try:
        from server.win_bridge import register_class_role, register_interactive_class
    except ImportError:
        return  # Not on Windows / win_bridge not available

    for cls_name, role in skill.class_role_mappings().items():
        register_class_role(cls_name, role)

    for cls_name in skill.interactive_classes():
        register_interactive_class(cls_name)


def _count_new_tools(mcp: FastMCP) -> int:
    """Best-effort count of registered tools (for logging)."""
    try:
        return len(mcp._tool_manager._tools)  # noqa: SLF001
    except Exception:
        return -1
