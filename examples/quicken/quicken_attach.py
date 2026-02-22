"""
Helper script to attach to a running Quicken instance.

Usage:
    python -m examples.quicken.quicken_attach

This is a convenience wrapper that calls select_window with
Quicken-specific criteria.
"""

from __future__ import annotations

import sys

# Ensure the project root is importable
sys.path.insert(0, ".")

from server.process_manager import get_process_manager


def attach_quicken() -> dict:
    """Attach to Quicken and return the window info."""
    pm = get_process_manager()

    # Try class name first (most reliable)
    for cls in ("QWinFrame", "QFRAME"):
        try:
            win = pm.attach(class_name=cls)
            print(f"Attached to: {win.title} (PID={win.pid}, HWND={hex(win.hwnd)})")
            return {
                "ok": True,
                "hwnd": win.hwnd,
                "title": win.title,
                "pid": win.pid,
            }
        except Exception:
            continue

    # Fallback: title search
    try:
        win = pm.attach(window_title="Quicken")
        print(f"Attached to: {win.title} (PID={win.pid}, HWND={hex(win.hwnd)})")
        return {
            "ok": True,
            "hwnd": win.hwnd,
            "title": win.title,
            "pid": win.pid,
        }
    except Exception as exc:
        print(f"Failed to attach to Quicken: {exc}", file=sys.stderr)
        return {"ok": False, "error": str(exc)}


if __name__ == "__main__":
    result = attach_quicken()
    if not result.get("ok"):
        sys.exit(1)
