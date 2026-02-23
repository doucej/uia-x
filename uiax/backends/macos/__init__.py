"""
macOS AXAPI backend for UIA-X.

This package provides the macOS-specific implementation of the UIABridge
interface, using AXAPI (Accessibility API) via PyObjC for accessibility tree
inspection and interaction.

Public API
----------
- ``MacOSBridge``              – UIABridge implementation for macOS
- ``MacOSProcessManager``      – Window enumeration and attachment
- ``Node``                     – Stable element representation
- ``node_from_element``        – Build a Node from a live AXUIElement
- ``get_macos_process_manager`` – Singleton accessor

Example
-------
::

    from uiax.backends.macos import MacOSBridge, get_macos_process_manager

    pm = get_macos_process_manager()
    windows = pm.list_windows()
    pm.attach(window_title="Calculator")

    bridge = MacOSBridge()
    tree = bridge.inspect({})
    print(tree)
"""

from uiax.backends.macos.axapi_backend import Node, node_from_element
from uiax.backends.macos.bridge import (
    MacOSBridge,
    MacOSProcessManager,
    get_macos_process_manager,
    reset_macos_process_manager,
)

__all__ = [
    "MacOSBridge",
    "MacOSProcessManager",
    "Node",
    "get_macos_process_manager",
    "node_from_element",
    "reset_macos_process_manager",
]
