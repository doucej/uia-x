"""
Canonical entry-point for the UIA-X MCP server.

This module re-exports :func:`server.server.main` so that the server can be
invoked via the ``uiax.server`` namespace:

    python -m uiax.server
    uiax-server                 # console-scripts entry point

The ``server.server`` module remains importable for backward compatibility.
"""

from server.server import main  # noqa: F401 – public re-export

__all__ = ["main"]

if __name__ == "__main__":
    main()
