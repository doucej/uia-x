"""
Live MCP server integration test against gnome-calculator.

Exercises the full stack:
  process_list → select_window → uia_inspect → uia_invoke (buttons) → uia_inspect result

Run with:
  .uiax/bin/python tests/mcp_live_test.py
(server must be running on http://localhost:8765 with UIAX_AUTH=none)
"""
import asyncio
import json
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


SERVER_URL = "http://localhost:8765/mcp"


async def call(session: ClientSession, tool: str, **kwargs) -> dict:
    result = await session.call_tool(tool, kwargs)
    # FastMCP returns a list of TextContent items
    raw = result.content[0].text if result.content else "{}"
    try:
        data = json.loads(raw)
        # Unwrap {"ok": true, "element": {...}} envelope when present
        if isinstance(data, dict) and "element" in data:
            return data["element"]
        return data
    except json.JSONDecodeError:
        return {"_raw": raw}


async def main() -> None:
    async with streamablehttp_client(SERVER_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # ----------------------------------------------------------------
            # 1. List available tools
            # ----------------------------------------------------------------
            tools = await session.list_tools()
            tool_names = [t.name for t in tools.tools]
            print(f"[tools] {tool_names}\n")

            # ----------------------------------------------------------------
            # 2. List processes / windows
            # ----------------------------------------------------------------
            procs = await call(session, "process_list")
            windows = procs.get("windows", [])
            calc_win = next(
                (w for w in windows if "calculator" in w.get("title", "").lower()),
                None,
            )
            if not calc_win:
                print("ERROR: Calculator not found in process list")
                print("Windows:", [w.get("title") for w in windows])
                sys.exit(1)
            print(f"[process_list] found: {calc_win['title']}  pid={calc_win['pid']}")

            # ----------------------------------------------------------------
            # 3. Select (attach to) the calculator window
            # ----------------------------------------------------------------
            sel = await call(session, "select_window", hwnd=calc_win["hwnd"])
            print(f"[select_window] {sel}\n")

            # ----------------------------------------------------------------
            # 4. Inspect root to confirm we have the right window
            # ----------------------------------------------------------------
            root = await call(session, "uia_inspect", target={"depth": 2})
            print(f"[uia_inspect root] name={root.get('name')!r}  role={root.get('role')!r}")
            print(f"  children: {[c.get('name') or c.get('role') for c in root.get('children', [])]}\n")

            # ----------------------------------------------------------------
            # 5. Clear the calculator
            # ----------------------------------------------------------------
            for clear_name in ("C", "Clear", "AC"):
                try:
                    r = await call(session, "uia_invoke", target={"by": "name", "value": clear_name})
                    print(f"[clear] invoked {clear_name!r}: {r}")
                    await asyncio.sleep(0.3)
                    break
                except Exception:
                    pass

            # ----------------------------------------------------------------
            # 6. Press 7 × 6 = via uia_invoke (AT-SPI click on each button)
            # ----------------------------------------------------------------
            sequence = [("7", "7"), ("×", "×"), ("6", "6"), ("=", "=")]
            for btn_name, label in sequence:
                result = await call(session, "uia_invoke", target={"by": "name", "value": btn_name})
                print(f"[uia_invoke] {label!r} → {result}")
                await asyncio.sleep(0.3)

            # ----------------------------------------------------------------
            # 7. Read the result from the AT-SPI tree
            # ----------------------------------------------------------------
            print()
            # Strategy 1: look for an element whose name contains "42"
            try:
                found = await call(session, "uia_inspect", target={"by": "name_substring", "value": "42"})
                for field in ("text", "name", "value"):
                    v = found.get(field, "")
                    if v and "42" in str(v):
                        print(f"[result] ✓  Found '42' via field={field!r}: {v!r}")
                        return
            except Exception as e:
                print(f"[result] name_substring search failed: {e}")

            # Strategy 2: deep tree dump
            deep = await call(session, "uia_inspect", target={"depth": 20})
            result_text = _find_result(deep)
            if result_text and "42" in result_text:
                print(f"[result] ✓  Found '42' in deep tree: {result_text!r}")
            elif result_text:
                print(f"[result] ✗  Deep tree found number but not 42: {result_text!r}")
            else:
                print("[result] ✗  Could not find numeric result in tree")
                print("[debug] deep tree:")
                _dump(deep)


def _find_result(node: dict, depth: int = 0) -> str | None:
    role = node.get("role", "")
    if role in ("push button", "button", "toggle button"):
        return None
    for field in ("text", "value", "name"):
        v = str(node.get(field) or "")
        if v and any(c.isdigit() for c in v):
            return v
    for child in node.get("children", []):
        found = _find_result(child, depth + 1)
        if found:
            return found
    return None


def _dump(node: dict, indent: int = 0) -> None:
    prefix = "  " * indent
    role = node.get("role", "?")
    name = node.get("name", "")
    text = node.get("text", "")
    value = node.get("value", "")
    extra = f" text={text!r}" if text else ""
    extra += f" value={value!r}" if value else ""
    print(f"{prefix}[{role}] {name!r}{extra}")
    for child in node.get("children", []):
        _dump(child, indent + 1)
    if indent == 0:
        pass  # done


if __name__ == "__main__":
    asyncio.run(main())
