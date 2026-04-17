"""Quick MCP integration test for list_sidebar_accounts."""
import httpx, json

base = "http://localhost:8000/mcp"
hdrs_init = {"Accept": "application/json, text/event-stream"}

with httpx.Client(timeout=180) as c:
    # Initialize session
    r = c.post(base, json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"}
        }
    }, headers=hdrs_init)
    sid = r.headers.get("mcp-session-id", "")
    print("session:", sid)

    hdrs = {**hdrs_init, "mcp-session-id": sid}

    # Send notifications/initialized
    c.post(base, json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}, headers=hdrs)

    call_id = 2

    def call_tool(name, arguments, timeout=90):
        global call_id
        r = c.post(base, json={
            "jsonrpc": "2.0", "id": call_id, "method": "tools/call",
            "params": {"name": name, "arguments": arguments}
        }, headers=hdrs, timeout=timeout)
        call_id += 1
        for line in r.text.split("\n"):
            if line.startswith("data:"):
                d = json.loads(line[5:])
                if "result" in d:
                    txt = d["result"]["content"][0]["text"]
                    return json.loads(txt)
                if "error" in d:
                    return d["error"]
        return None

    # Attach to Quicken first
    print("Attaching to Quicken...")
    r_sw = call_tool("select_window", {"api_key": "demo", "process_name": "qw.exe"})
    print("select_window:", r_sw)

    # Call list_sidebar_accounts (may need resume calls)
    accounts = []
    resume = False
    passes = 0
    while True:
        passes += 1
        args = {"api_key": "demo", "resume": resume}
        result = call_tool("list_sidebar_accounts", args, timeout=90)
        if result is None:
            print(f"  pass {passes}: no result!")
            break
        if not result.get("ok"):
            print(f"  pass {passes}: error: {result}")
            break
        accounts = result.get("accounts", [])
        done = result.get("done", False)
        scanned = result.get("scanned", 0)
        total = result.get("total", 0)
        print(f"  pass {passes}: {len(accounts)} accounts, scanned={scanned}/{total}, done={done}")
        if done:
            break
        resume = True
        if passes > 15:
            print("too many passes, giving up")
            break

    print(f"\nTotal: {len(accounts)} accounts found:")
    for a in accounts:
        print(f"  [{a.get('section','')}] {a.get('name','')}")
