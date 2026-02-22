# UIA-X — User Interface Automation, eXtended

An **MCP server** that gives AI agents full control of desktop applications
through UI Automation.  Point any MCP client (Claude Desktop, VS Code Copilot,
opencode, custom agents) at UIA-X and it can see, click, type, and navigate
any windowed app — just like a human operator.

> **Today:** Windows (UIA / MSAA via pywinauto).
> **Planned:** Linux (AT-SPI / Qt) and macOS (Accessibility API) backends.
> The bridge abstraction is already in place — new platforms plug in without
> changing the tool surface.

---

## Quick start (HTTP + API key)

The recommended deployment: serve over HTTP with a generated API key.

```powershell
# 1. Clone and install
git clone https://github.com/doucej/uia-x.git
cd uia-x
python -m venv .venv
.venv\Scripts\activate
pip install -e .

# 2. Start the server  (first run prints an API key — save it!)
$env:MCP_TRANSPORT="streamable-http"
python -m server.server
```

On first launch you'll see:

```
[uia-x] *** NEW API KEY GENERATED ***
[uia-x] Key: <your-key>
[uia-x] Stored hash in: C:\Users\<you>\.uia_x\api_key
[uia-x] Save this key – it will not be shown again.
[uiax] starting server (backend=real, auth=apikey, transport=streamable-http, http://0.0.0.0:8000)
```

**3. Point your MCP client at `http://localhost:8000/mcp`** and pass the API
key as a Bearer token header or as the `api_key` parameter on each tool call.

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_TRANSPORT` | `stdio` | Transport: `stdio`, `sse`, `streamable-http` |
| `MCP_HOST` | `0.0.0.0` | Bind address (HTTP modes) |
| `MCP_PORT` | `8000` | Listen port (HTTP modes) |
| `UIA_X_AUTH` | `apikey` | Auth mode: `apikey` or `none` |
| `UIA_X_API_KEY` | *(auto)* | Override API key (skip on-disk generation) |
| `UIA_BACKEND` | `real` | Backend: `real` (pywinauto) or `mock` (tests) |

---

## Authenticating clients

> **Key point:** Authentication is enforced **server-side**.  The `headers`
> block in your client config simply tells the client what credentials to
> present — the server decides whether to accept them.  A client that omits
> or forges the header is rejected with a 401.  If the server is started with
> `UIA_X_AUTH=none`, no credentials are checked regardless of what the client
> sends.

UIA-X supports **two ways** to present an API key — use whichever
your client supports:

| Method | When to use |
|--------|------------|
| **Bearer header** – `Authorization: Bearer <key>` | HTTP transports (SSE / streamable-http). Handled at the ASGI layer before any tool runs. VS Code, opencode, Open WebUI, curl, and most HTTP clients send this automatically. |
| **Tool parameter** – `api_key` on every call | stdio transports or clients that can't set headers. The LLM passes the key as part of the tool arguments. |

If the Bearer header is present and valid, the tool-level `api_key` parameter
is ignored (you can omit it).

---

## Client configuration examples

### Claude Desktop (`claude_desktop_config.json`)

Stdio — the server runs as a local subprocess. No key needed.

```json
{
  "mcpServers": {
    "uiax": {
      "command": "python",
      "args": ["-m", "server.server"],
      "cwd": "C:/path/to/uia-x",
      "env": { "UIA_X_AUTH": "none" }
    }
  }
}
```

### VS Code — stdio (local)

Stdio — no API key required.

```jsonc
// .vscode/mcp.json
{
  "servers": {
    "uiax": {
      "type": "stdio",
      "command": "${workspaceFolder}/.venv/Scripts/python.exe",
      "args": ["-m", "server.server"],
      "env": { "UIA_X_AUTH": "none", "UIA_BACKEND": "real" }
    }
  }
}
```

### VS Code — HTTP (remote / shared server)

Start the server on the target machine, then configure VS Code to connect
with a Bearer token.  The `${input:...}` variable causes VS Code to **prompt
you once** for the key and store it securely.

```jsonc
// .vscode/mcp.json
{
  "inputs": [
    {
      "type": "promptString",
      "id": "uiax-api-key",
      "description": "UIA-X API key (from server first-run output)",
      "password": true
    }
  ],
  "servers": {
    "uiax": {
      "type": "http",
      "url": "http://<host>:8000/mcp",
      "headers": {
        "Authorization": "Bearer ${input:uiax-api-key}"
      }
    }
  }
}
```

### opencode (`~/.config/opencode/opencode.json`)

opencode uses `"type": "remote"` for HTTP MCP servers.

**With auth enabled** — pass the API key the server printed at first run:

```jsonc
{
  "mcp": {
    "uiax": {
      "type": "remote",
      "url": "http://<host>:8000/mcp",
      "headers": {
        "Authorization": "Bearer <your-api-key>"
      }
    }
  }
}
```

**Without auth (local dev)** — start the server with `UIA_X_AUTH=none` and
omit the `headers` block:

```jsonc
{
  "mcp": {
    "uiax": {
      "type": "remote",
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

> **Note:** opencode stores the key in plain text (no `${input:...}` prompt
> like VS Code).  For local-only use, running with `UIA_X_AUTH=none` is the
> simplest path.  For remote/shared servers, treat the config file as
> sensitive.

### Open WebUI / generic HTTP clients

Any client that can add custom headers to an HTTP request works the same way:
set `Authorization: Bearer <key>` on every request to the `/mcp` endpoint.

```bash
# curl example
curl -H "Authorization: Bearer <your-api-key>" \
     http://<host>:8000/mcp
```

If auth is disabled server-side (`UIA_X_AUTH=none`), just hit the URL
directly — no header needed.

---

## Architecture

```
┌─────────────────────────────────────┐
│         MCP Client (LLM Agent)      │
│  Claude Desktop / VS Code / Custom  │
└──────────────┬──────────────────────┘
               │  MCP stdio / HTTP
               │  (Bearer auth or api_key param)
               ▼
┌─────────────────────────────────────┐
│          UIA-X  server.py           │
│  ┌─────────┐  ┌─────────────────┐   │
│  │  Auth    │  │ Process Manager │   │
│  │  Layer   │  │ (enumerate/     │   │
│  │ Bearer / │  │  attach windows)│   │
│  │ api_key  │  │                 │   │
│  └─────────┘  └─────────────────┘   │
│  ┌──────────────────────────────┐   │
│  │       Platform Bridge        │   │
│  │  ┌────────┬────────┬──────┐  │   │
│  │  │Windows │ Linux  │macOS │  │   │
│  │  │UIA/MSAA│AT-SPI/Q│A11y  │  │   │
│  │  │(today) │(future)│(fut.)│  │   │
│  │  └────────┴────────┴──────┘  │   │
│  └──────────────────────────────┘   │
│  ┌──────────────────────────────┐   │
│  │     Input Injection          │   │
│  │  SendKeys · Mouse Click      │   │
│  └──────────────────────────────┘   │
└─────────────────────────────────────┘
               │
               ▼  pywinauto / ctypes / comtypes
┌─────────────────────────────────────┐
│     Windows Desktop (RDP session)   │
│   Target application (any app)      │
└─────────────────────────────────────┘
```

### Key components

| Module | Responsibility |
|--------|---------------|
| `server/server.py` | FastMCP app, all tool registrations, auth gating |
| `server/uia_bridge.py` | Abstract bridge interface, error taxonomy |
| `server/real_bridge.py` | Live UIA + MSAA backend via pywinauto |
| `server/mock_bridge.py` | Mock backend for tests (no Windows required) |
| `server/process_manager.py` | Enumerate processes/windows, attach/detach |
| `server/auth.py` | API key generation, validation, pluggable auth |
| `mock_uia/tree.py` | Mock element trees (generic, Quicken, MSAA) |

---

## Project layout

```
uia-x/
├── server/
│   ├── server.py             ← FastMCP app, tool registrations
│   ├── uia_bridge.py         ← Abstract bridge + error types
│   ├── real_bridge.py         ← Live UIA + MSAA backend (pywinauto)
│   ├── mock_bridge.py         ← Mock backend for tests
│   ├── process_manager.py     ← Process/window enumeration & attachment
│   └── auth.py                ← API key authentication layer
├── mock_uia/
│   └── tree.py                ← MockElement, MockTree, fixture factories
├── tests/
│   ├── test_tools.py          ← Core UIA tool tests
│   ├── test_process.py        ← Process enumeration & attachment tests
│   ├── test_auth.py           ← Authentication layer tests
│   ├── test_input.py          ← Keystroke & mouse input tests
│   └── test_msaa.py           ← MSAA / LegacyIAccessible tests
├── schemas/                   ← JSON Schema for every tool
├── examples/
│   └── quicken/               ← Quicken-specific skill (from V1)
│       ├── AGENT_SKILL_GUIDE.md
│       ├── quicken_attach.py
│       └── example_calls.json
├── pyproject.toml
├── requirements.txt
├── LICENSE                    ← MIT
├── MIGRATION.md               ← V1 → V2 migration guide
└── README.md                  ← This file
```

---

## Requirements

- **Python 3.11+**
- **Windows** (for the real UIA backend today; mock backend runs anywhere)
- A **desktop session** (physical or RDP) — UI Automation requires an active desktop

> Linux (AT-SPI / Qt) and macOS (Accessibility API) backends are on the roadmap.
> The abstract bridge in `server/uia_bridge.py` makes adding new platforms
> a matter of implementing a single class — no tool API changes needed.

---

## Installation

```bash
pip install -e ".[dev]"
```

Or just the runtime dependencies:

```bash
pip install -r requirements.txt
```

---

## Security model

UIA-X authenticates every request unless explicitly disabled.

### ⚠️ Desktop access warning

UIA-X gives the connected agent **full control of every visible application
on the desktop session where it runs**.  It can click, type, read screen
content, and invoke UI actions — exactly what a human sitting at the keyboard
could do.  This is the point of the tool, but it means:

* **Sensitive data is exposed.** Any window the agent can see (email, banking,
  password managers, file explorers) is fair game for `uia_inspect`.
* **Destructive actions are possible.** The agent can click "Delete",
  "Send", "Format", or close unsaved documents.
* **Credentials may be visible.** Auto-filled passwords, session tokens in
  browser dev-tools, environment variables in terminal windows — all readable
  via the accessibility tree.

**Best practice:** never run UIA-X on the same desktop session you use for
day-to-day work.  See [Isolation strategies](#isolation-strategies) below for
recommended setups on each platform.

### First run

On first start the server will:
1. Generate a cryptographically random API key
2. Print it to **stderr** (copy it now — it's shown only once)
3. Store a SHA-256 hash in `~/.uia_x/api_key`

### Authenticating via HTTP header (recommended for HTTP transports)

Send the key as a **Bearer token** on every HTTP request:

```
Authorization: Bearer <your-key>
```

The server validates the header in ASGI middleware *before* any tool
executes.  When the header is valid, the tool-level `api_key` parameter
is not required.

### Authenticating via tool parameter (stdio or fallback)

Every tool also accepts `api_key` as a parameter:

```json
{
  "tool": "uia_inspect",
  "input": {
    "target": {},
    "api_key": "your-key-here"
  }
}
```

### Disabling auth (local dev)

```bash
UIA_X_AUTH=none python -m server.server
```

### Overriding the key via environment

```bash
UIA_X_API_KEY=my-fixed-key python -m server.server
```

### Future auth methods

The auth layer is pluggable — swap in mTLS, OAuth device-code, or any custom
provider by implementing the `AuthProvider` protocol in `server/auth.py`.

---

## Running the server

**Against a live desktop (stdio, default):**
```bash
python -m server.server
```

**HTTP mode (recommended for remote / multi-client):**
```powershell
$env:MCP_TRANSPORT="streamable-http"
python -m server.server
# → Listening on http://0.0.0.0:8000/mcp
```

**Mock backend (no Windows required):**
```bash
UIA_BACKEND=mock python -m server.server
```

---

## Isolation strategies

Because UIA-X has full desktop access (see
[Desktop access warning](#%EF%B8%8F-desktop-access-warning)), you should run
it in an **isolated session** that contains only the application(s) the agent
needs.  Below are platform-specific recommendations.

### Windows — dedicated RDP session (recommended)

The simplest and most battle-tested approach: open a separate Remote Desktop
session on the same machine (or a VM) and run the server there.

1. **Create a restricted local user** (optional but recommended):
   ```powershell
   net user uiax-agent P@ssw0rd123 /add
   # Do NOT add to Administrators — limit what the agent can reach
   ```
2. **Open an RDP session** as that user — to `localhost` or a dedicated VM.
3. **Keep the session connected.** UI Automation requires an active desktop.
   If you need to disconnect your *viewer* without killing the session:
   ```cmd
   tscon %sessionname% /dest:console
   ```
4. **Install only the target application** in that session.  The agent can
   only see what's on this desktop — your email, browser, and password
   manager stay on your real session.
5. **Start UIA-X** inside the RDP session:
   ```powershell
   $env:MCP_TRANSPORT = "streamable-http"
   $env:UIA_X_AUTH    = "apikey"        # or "none" for local-only
   python -m server.server
   ```
6. **Connect your MCP client** from anywhere to `http://<host>:8000/mcp`.

> **Cloud VMs:** Azure / AWS instances work well.  Use a GPU-accelerated SKU
> only if the target app requires hardware rendering — most Win32/WPF apps
> are fine on standard instances.

### Linux — Docker + virtual display (planned)

> **Note:** Linux support (AT-SPI / Qt accessibility) is on the roadmap but
> not yet implemented.  The isolation pattern below is ready for when it is.

Run UIA-X inside a Docker container with a virtual X11 or Wayland display.
The agent sees only what's inside the container.

```dockerfile
# Dockerfile.uiax-sandbox
FROM ubuntu:24.04

RUN apt-get update && apt-get install -y \
    xvfb x11vnc python3 python3-pip \
    # install target app dependencies here
    && rm -rf /var/lib/apt/lists/*

COPY . /opt/uia-x
WORKDIR /opt/uia-x
RUN pip install -r requirements.txt

# Start a virtual framebuffer + the MCP server
CMD Xvfb :99 -screen 0 1920x1080x24 & \
    export DISPLAY=:99 && \
    export MCP_TRANSPORT=streamable-http && \
    python -m server.server
```

```bash
docker build -t uiax-sandbox -f Dockerfile.uiax-sandbox .
docker run -d -p 8000:8000 --name uiax uiax-sandbox

# Optional: attach a VNC viewer to watch the agent work
# (add x11vnc to the CMD and expose port 5900)
```

**If you need to observe the agent in real time**, add `x11vnc` to the
container and expose port 5900 so you can connect a VNC viewer.

**If you don't need to watch**, the headless `Xvfb` approach is lighter and
more secure — there's no way to exfiltrate screen content outside the
container.

### macOS — secondary user session (planned)

> **Note:** macOS Accessibility API support is on the roadmap but not yet
> implemented.

macOS doesn't support Docker containers with native GUI access (Darwin
containers are experimental and limited).  Instead:

1. **Create a dedicated macOS user account** with minimal permissions.
2. **Fast User Switch** to that account (`System Settings → Users & Groups →
   Login Options → Show fast user switching menu`).
3. Open only the target application in that session.
4. Run UIA-X there.  Your primary session remains untouched.

Alternatively, use a **macOS VM** (supported on Apple Silicon via the
Virtualization framework, or via UTM/Parallels) and run UIA-X inside the VM.

### Summary

| Platform | Recommended isolation | Observability | Status |
|----------|----------------------|---------------|--------|
| Windows  | RDP session (restricted user) | RDP viewer / `tscon` keep-alive | **Available now** |
| Linux    | Docker + Xvfb | VNC into container (optional) | Planned |
| macOS    | Secondary user / macOS VM | Fast User Switch / VNC | Planned |

---

## Exposed tools

Ten tools are registered.  All return `{"ok": true, ...}` on success or
`{"ok": false, "error": "...", "code": "..."}` on failure.

### `process_list`

Enumerate running processes and their top-level windows.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `api_key` | string | Yes* | — | API key |
| `visible_only` | boolean | No | `true` | Only return visible windows |

### `select_window`

Attach to a specific window as the automation target.  At least one search
criterion is required.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `api_key` | string | Yes* | API key |
| `pid` | integer | No | Process ID |
| `process_name` | string | No | Executable name (e.g. `"notepad.exe"`) |
| `window_title` | string | No | Substring match on title |
| `class_name` | string | No | Win32 window class |
| `hwnd` | integer | No | Window handle |

### `uia_inspect`

Inspect the UIA element tree.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `api_key` | string | Yes* | — | API key |
| `target` | object | No | `{}` | Element selector |

### `uia_invoke`

Invoke (click / activate) an element.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `api_key` | string | Yes* | API key |
| `target` | object | Yes | Element selector |

### `uia_set_value`

Set the value of an element (text fields, date pickers, combo boxes).

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `api_key` | string | Yes* | API key |
| `target` | object | Yes | Element selector |
| `value` | string | Yes | New value |

### `uia_send_keys`

Send keystrokes to the target window (with optional element focus).

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `api_key` | string | Yes* | — | API key |
| `keys` | string | Yes | — | Key sequence |
| `target` | object | No | `{}` | Element to focus first |

### `uia_legacy_invoke`

Invoke via MSAA `DoDefaultAction`.  For owner-drawn controls invisible to
standard UIA.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `api_key` | string | Yes* | API key |
| `target` | object | Yes | Selector (supports MSAA extras) |

### `uia_mouse_click`

Click at absolute screen coordinates.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `api_key` | string | Yes* | — | API key |
| `x` | integer | Yes | — | Screen X |
| `y` | integer | Yes | — | Screen Y |
| `double` | boolean | No | `false` | Double-click |
| `button` | string | No | `"left"` | `"left"`, `"right"`, `"middle"` |

### `send_keys`

Lower-level keystroke injection (no UIA target focus).

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `api_key` | string | Yes* | API key |
| `keys` | string | Yes | Key sequence |

### `mouse_click`

Lower-level mouse click (no UIA target context).

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `api_key` | string | Yes* | — | API key |
| `x` | integer | Yes | — | Screen X |
| `y` | integer | Yes | — | Screen Y |
| `double` | boolean | No | `false` | Double-click |
| `button` | string | No | `"left"` | Mouse button |

> *Required unless `UIA_X_AUTH=none`.

---

## Using the process picker

```
Agent → process_list()
     ← [Notepad (pid=5678), Quicken (pid=1234), Calculator (pid=9012), ...]

Agent → select_window(process_name="notepad.exe")
     ← { window: { hwnd: 0xBB01, title: "Untitled - Notepad", ... } }

Agent → uia_inspect(target={})
     ← { element: { name: "Untitled - Notepad", children: [...] } }
```

To switch targets at runtime, just call `select_window` again with different
criteria.

---

## Target selector

All UIA tools accept a `target` object:

| Key | Type | Description |
|-----|------|-------------|
| `by` | string | Strategy (see table) |
| `value` | string | Value for the strategy |
| `index` | integer | Zero-based index for multiple matches (default `0`) |
| `depth` | integer | Children depth for inspect (default `3`) |

### Selector strategies

| `by` value | Matches on |
|------------|------------|
| `name` | UIA `Name` property |
| `automation_id` | UIA `AutomationId` |
| `control_type` | UIA `ControlType` (e.g. `"Button"`) |
| `class_name` | Win32 class name |
| `path` | `/`-separated name path from root |
| `hwnd` | Windows HWND (int or hex string) |
| `legacy_name` | MSAA `accName` |
| `legacy_role` | MSAA role constant (int or string) |
| `child_id` | MSAA `CHILDID` integer |

---

## Writing app-specific skills

1. Use `process_list` + `select_window` to attach to your app.
2. Use `uia_inspect` with `depth=1` to map the top-level window tree.
3. Drill into child elements to discover automation IDs, names, and class names.
4. Write a skill guide (see `examples/quicken/AGENT_SKILL_GUIDE.md`) documenting:
   - Window hierarchy
   - Tab order of forms
   - Known class names and automation IDs
   - Common workflows (CRUD, navigation, keyboard shortcuts)
5. Create a helper script like `examples/quicken/quicken_attach.py` for quick
   attachment.

---

## Error codes

| Code | Meaning |
|------|---------|
| `TARGET_NOT_ATTACHED` | No window selected — call `select_window` first |
| `PROCESS_NOT_FOUND` | No process/window matched the criteria |
| `ELEMENT_NOT_FOUND` | No element matched the target selector |
| `PATTERN_NOT_SUPPORTED` | Element doesn't support the required pattern |
| `INVALID_SELECTOR` | Unknown `by` strategy |
| `AUTH_ERROR` | Invalid or missing API key |
| `PYWINAUTO_UNAVAILABLE` | pywinauto not installed or not on Windows |
| `UNEXPECTED_ERROR` | Unhandled exception |

---

## Running tests

```bash
pytest tests/ -v
```

All tests use the **mock backend** — no Windows or target app required.

```
tests/test_tools.py      – Core UIA tools (inspect/invoke/set_value)
tests/test_process.py    – Process enumeration & window attachment
tests/test_auth.py       – API key authentication
tests/test_input.py      – Keystroke & mouse input
tests/test_msaa.py       – MSAA / LegacyIAccessible fallback
```

---

## License

MIT — see [LICENSE](LICENSE).
