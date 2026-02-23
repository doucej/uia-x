#!/usr/bin/env python
"""
Live macOS backend demo – exercises the full AXAPI stack against Calculator.app.

Run (from GUI session for TCC trust):
    open /path/to/python.app --args /path/to/tests/live_macos_demo.py
    # then: cat /tmp/uiax_live_demo.txt

Or directly if already trusted:
    python tests/live_macos_demo.py
"""
from __future__ import annotations

import sys
import time
import io
import os

# ── Tee stdout to file so we can read results from SSH ───────────────────
_OUTPUT_FILE = "/tmp/uiax_live_demo.txt"


class _Tee:
    def __init__(self, *streams):
        self._streams = streams

    def write(self, data):
        for s in self._streams:
            s.write(data)
            s.flush()

    def flush(self):
        for s in self._streams:
            s.flush()


_file_out = open(_OUTPUT_FILE, "w")
sys.stdout = _Tee(sys.__stdout__, _file_out)
sys.stderr = _Tee(sys.__stderr__, _file_out)

# ── Step 0: prerequisites ────────────────────────────────────────────────
from uiax.backends.macos.util import axapi_available, is_trusted

print("=" * 60)
print("UIA-X macOS Backend – Live Demo")
print("=" * 60)
print(f"AXAPI available : {axapi_available()}")
print(f"Trusted (a11y)  : {is_trusted()}")
if not axapi_available():
    print("FATAL: PyObjC / ApplicationServices not available.")
    sys.exit(1)

# ── Step 1: enumerate running apps ───────────────────────────────────────
from uiax.backends.macos.util import get_running_apps

print("\n── Running GUI Applications ──")
apps = get_running_apps()
for a in apps:
    print(f"  PID {a['pid']:>6}  {a['name']:<30}  {a['bundle_id']}")
print(f"  ({len(apps)} total)")

# ── Step 2: enumerate all windows ────────────────────────────────────────
from uiax.backends.macos.util import list_all_windows

print("\n── Top-Level Windows ──")
windows = list_all_windows()
for w in windows:
    vis = "visible" if w["visible"] else "hidden"
    print(f"  [{w['process_name']:<20}] {w['title']!r:<40} {vis}  rect={w['rect']}")
print(f"  ({len(windows)} total)")

# ── Step 3: attach to Calculator ─────────────────────────────────────────
from uiax.backends.macos.bridge import MacOSBridge, get_macos_process_manager

pm = get_macos_process_manager()
try:
    info = pm.attach(process_name="Calculator")
    print(f"\n── Attached to Calculator ──")
    print(f"  title : {info['title']}")
    print(f"  pid   : {info['pid']}")
    print(f"  rect  : {info['rect']}")
except Exception as exc:
    print(f"\nFailed to attach to Calculator: {exc}")
    print("Make sure Calculator.app is running: open -a Calculator")
    sys.exit(1)

# ── Step 4: inspect the accessibility tree ───────────────────────────────
bridge = MacOSBridge()
print("\n── Inspect root (depth=1) ──")
tree = bridge.inspect({"depth": 1})
print(f"  role    : {tree['role']}")
print(f"  name    : {tree['name']}")
print(f"  states  : {tree['states']}")
print(f"  children: {len(tree['children'])} top-level")
for child in tree["children"]:
    print(f"    - {child['role']:<20} {child['name']!r}")

print("\n── Inspect deep (depth=8) ──")
deep = bridge.inspect({"depth": 8})


def find_buttons(node, results=None):
    if results is None:
        results = []
    if node.get("role") == "button":
        results.append(node)
    for child in node.get("children", []):
        find_buttons(child, results)
    return results


buttons = find_buttons(deep)
print(f"  Found {len(buttons)} buttons:")
for b in buttons:
    desc = b.get("description", "")
    extra = f"  ({desc})" if desc else ""
    print(f"    [{b['name']!r:<12}] actions={b.get('actions', [])}{extra}")

# ── Step 5: perform calculation 7 × 8 = 56 ──────────────────────────────
print("\n── Calculation: 7 × 8 = ? ──")

# Clear first
for clear_name in ("Clear", "AC", "All Clear"):
    try:
        bridge.invoke({"by": "name", "value": clear_name})
        print(f"  Pressed: {clear_name}")
        time.sleep(0.3)
        break
    except Exception:
        continue
else:
    # Try name_substring
    try:
        bridge.invoke({"by": "name_substring", "value": "clear"})
        print("  Pressed: clear (substring match)")
        time.sleep(0.3)
    except Exception:
        print("  (could not find Clear button, continuing anyway)")

# Press 7
try:
    bridge.invoke({"by": "name", "value": "7"})
    print("  Pressed: 7")
    time.sleep(0.3)
except Exception as e:
    print(f"  FAILED to press 7: {e}")

# Press multiply
for mul_name in ("×", "multiply", "*", "Multiply"):
    try:
        bridge.invoke({"by": "name", "value": mul_name})
        print(f"  Pressed: {mul_name}")
        time.sleep(0.3)
        break
    except Exception:
        continue
else:
    try:
        bridge.invoke({"by": "name_substring", "value": "multipl"})
        print("  Pressed: multiply (substring)")
        time.sleep(0.3)
    except Exception as e:
        print(f"  FAILED to press multiply: {e}")

# Press 8
try:
    bridge.invoke({"by": "name", "value": "8"})
    print("  Pressed: 8")
    time.sleep(0.3)
except Exception as e:
    print(f"  FAILED to press 8: {e}")

# Press equals
for eq_name in ("=", "equals", "Equals"):
    try:
        bridge.invoke({"by": "name", "value": eq_name})
        print(f"  Pressed: {eq_name}")
        time.sleep(0.5)
        break
    except Exception:
        continue
else:
    try:
        bridge.invoke({"by": "name_substring", "value": "equal"})
        print("  Pressed: equals (substring)")
        time.sleep(0.5)
    except Exception as e:
        print(f"  FAILED to press equals: {e}")

# ── Step 6: read the result ──────────────────────────────────────────────
print("\n── Reading result ──")
result_tree = bridge.inspect({"depth": 8})


def find_display(node):
    """Walk tree looking for something with a numeric value."""
    import unicodedata

    val = node.get("value", "")
    role = node.get("role", "")
    if val and role in ("text", "static text", "text field", "group", "scroll area", "unknown"):
        try:
            # Strip Unicode control chars (e.g. LTR mark \u200e) before parsing
            cleaned = "".join(
                c for c in str(val) if not unicodedata.category(c).startswith("C")
            ).replace(",", "").replace(" ", "")
            float(cleaned)
            return val
        except (ValueError, TypeError):
            pass
    for child in node.get("children", []):
        result = find_display(child)
        if result is not None:
            return result
    return None


display = find_display(result_tree)
if display is not None:
    import unicodedata
    cleaned = "".join(
        c for c in str(display) if not unicodedata.category(c).startswith("C")
    )
    print(f"  Display value: {cleaned!r} (raw: {display!r})")
    if "56" in cleaned:
        print("  ✓ CORRECT – 7 × 8 = 56")
    else:
        print(f"  ✗ Expected 56 but got {cleaned!r}")
else:
    print("  Could not read display value from tree.")
    # Dump all values we can find
    def dump_values(node, depth=0):
        val = node.get("value", "")
        name = node.get("name", "")
        role = node.get("role", "")
        if val or depth < 3:
            print(f"  {'  ' * depth}[{role}] name={name!r} value={val!r}")
        for child in node.get("children", []):
            dump_values(child, depth + 1)
    dump_values(result_tree)

# ── Step 7: test send_keys ───────────────────────────────────────────────
print("\n── Testing send_keys (Escape to clear) ──")
try:
    bridge.send_keys("{ESCAPE}")
    print("  Sent: {ESCAPE}")
    time.sleep(0.3)
except Exception as e:
    print(f"  send_keys failed: {e}")

# ── Step 8: test mouse_click ─────────────────────────────────────────────
print("\n── Testing mouse_click ──")
rect = result_tree.get("rect", {})
if rect.get("right", 0) > 0:
    cx = (rect["left"] + rect["right"]) // 2
    cy = (rect["top"] + rect["bottom"]) // 2
    try:
        bridge.mouse_click(cx, cy)
        print(f"  Clicked at ({cx}, {cy})")
    except Exception as e:
        print(f"  mouse_click failed: {e}")
else:
    print("  No valid rect available, skipping")

print("\n" + "=" * 60)
print("Demo complete!")
print("=" * 60)
