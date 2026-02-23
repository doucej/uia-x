"""
Linux AT-SPI2 backend for UIA-X.

This package provides the Linux-specific implementation of the UIABridge
interface, using AT-SPI2 (via python3-pyatspi) for accessibility tree
inspection and interaction.

Public API
----------
- ``LinuxBridge``             – UIABridge implementation for Linux
- ``LinuxProcessManager``     – Window enumeration and attachment
- ``Node``                    – Stable element representation
- ``node_from_accessible``    – Build a Node from a live AT-SPI object
- ``get_linux_process_manager`` – Singleton accessor

Example
-------
::

    from uiax.backends.linux import LinuxBridge, get_linux_process_manager

    pm = get_linux_process_manager()
    windows = pm.list_windows()
    pm.attach(window_title="gedit")

    bridge = LinuxBridge()
    tree = bridge.inspect({})
    print(tree)
"""

from uiax.backends.linux.atspi_backend import Node, node_from_accessible
from uiax.backends.linux.bridge import (
    LinuxBridge,
    LinuxProcessManager,
    get_linux_process_manager,
    reset_linux_process_manager,
)

__all__ = [
    "LinuxBridge",
    "LinuxProcessManager",
    "Node",
    "get_linux_process_manager",
    "node_from_accessible",
    "reset_linux_process_manager",
]
