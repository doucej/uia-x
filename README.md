# UIA-X — User Interface Automation, eXtended

An **MCP server** that gives AI agents full control of desktop applications
through UI Automation.  Point any MCP client (Claude Desktop, VS Code Copilot,
opencode, custom agents) at UIA-X and it can see, click, type, and navigate
any windowed app — just like a human operator.

> **Today:** Windows (UIA / MSAA via pywinauto), Linux (AT-SPI2 via pyatspi),
> and macOS (AXAPI via PyObjC).
> The bridge abstraction is in place — all three platforms share an identical
> MCP tool surface.

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
| `UIA_BACKEND` | `real` | Backend: `real` (auto-detect), `linux` (AT-SPI2), `macos` (AXAPI), or `mock` (tests) |

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
│  │  │UIA/MSAA│AT-SPI2 │AXAPI │  │   │
│  │  │        │pyatspi │PyObjC│  │   │
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
| `server/uia_bridge.py` | Abstract bridge interface, error taxonomy, platform detection |
| `server/real_bridge.py` | Live UIA + MSAA backend via pywinauto (Windows) |
| `server/mock_bridge.py` | Mock backend for tests (any platform) |
| `server/process_manager.py` | Enumerate processes/windows, attach/detach |
| `server/auth.py` | API key generation, validation, pluggable auth |
| `mock_uia/tree.py` | Mock element trees (generic, Quicken, MSAA) |
| `uiax/backends/linux/bridge.py` | LinuxBridge – AT-SPI2 UIABridge implementation |
| `uiax/backends/linux/atspi_backend.py` | Node model, tree traversal, element search |
| `uiax/backends/linux/util.py` | AT-SPI2 utility functions, keystroke synthesis |
| `uiax/backends/macos/bridge.py` | MacOSBridge – AXAPI UIABridge implementation |
| `uiax/backends/macos/axapi_backend.py` | Node model, tree traversal, element search |
| `uiax/backends/macos/util.py` | AXAPI utility functions, Quartz keystroke synthesis |

---

## Project layout

```
uia-x/
├── server/
│   ├── server.py             ← FastMCP app, tool registrations
│   ├── uia_bridge.py         ← Abstract bridge + error types + platform detection
│   ├── real_bridge.py         ← Live UIA + MSAA backend (pywinauto, Windows)
│   ├── mock_bridge.py         ← Mock backend for tests
│   ├── process_manager.py     ← Process/window enumeration & attachment
│   └── auth.py                ← API key authentication layer
├── uiax/
│   └── backends/
│       ├── linux/
│       │   ├── __init__.py        ← Public API exports
│       │   ├── atspi_backend.py   ← Node model, tree traversal, search
│       │   ├── bridge.py          ← LinuxBridge (UIABridge impl) + LinuxProcessManager
│       │   └── util.py            ← AT-SPI2 helpers, keystroke synthesis
│       └── macos/
│           ├── __init__.py        ← Public API exports
│           ├── axapi_backend.py   ← Node model, tree traversal, search
│           ├── bridge.py          ← MacOSBridge (UIABridge impl) + MacOSProcessManager
│           └── util.py            ← AXAPI helpers, Quartz keystroke synthesis
├── mock_uia/
│   └── tree.py                ← MockElement, MockTree, fixture factories
├── tests/
│   ├── test_tools.py          ← Core UIA tool tests
│   ├── test_process.py        ← Process enumeration & attachment tests
│   ├── test_auth.py           ← Authentication layer tests
│   ├── test_input.py          ← Keystroke & mouse input tests
│   ├── test_msaa.py           ← MSAA / LegacyIAccessible tests
│   ├── test_linux_backend.py   ← Linux backend unit tests (mock AT-SPI)
│   ├── test_linux_integration.py ← Linux integration tests (live AT-SPI)
│   ├── test_macos_backend.py   ← macOS backend unit tests (mock AXAPI)
│   ├── test_macos_integration.py ← macOS integration tests (live AXAPI + Calculator.app)
│   └── run_headless.sh        ← Headless test harness (Xvfb + D-Bus)
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
- **Windows** – pywinauto, comtypes (for the Windows UIA backend)
- **Linux** – python3-pyatspi, at-spi2-core, gir1.2-atspi-2.0 (for the Linux AT-SPI2 backend)
- **macOS** – PyObjC (pyobjc-framework-ApplicationServices, pyobjc-framework-Quartz, pyobjc-framework-Cocoa) for the macOS AXAPI backend
- A **desktop session** (physical, RDP, VNC, or virtual X11/Wayland) – accessibility APIs require an active desktop

> The abstract bridge in `server/uia_bridge.py` makes platform backends
> interchangeable — the MCP tool surface stays identical on Windows, Linux, and macOS.

### macOS accessibility permissions (TCC)

macOS requires an explicit **one-time** accessibility permission grant before
any process can read or interact with UI elements.  This is enforced by the
Transparency, Consent, and Control (TCC) framework and applies equally to
every macOS accessibility tool (Hammerspoon, BetterTouchTool, Keyboard
Maestro, etc.).

**Quick setup (interactive desktop):**

1. Open **System Settings → Privacy & Security → Accessibility**.
2. Click **+** and add your Python interpreter (e.g. `/usr/bin/python3`,
   your conda `python.app`, or **Terminal.app** / **iTerm2**).
3. Toggle the entry **on**.  That's it — the grant persists across reboots.

**What gets the permission:**

TCC grants trust to the *binary that calls the accessibility API*, not to
individual scripts.  So you grant permission to `python3` (or `Terminal.app`
which wraps your shell), and every Python script you run from that binary is
covered.  You never need to sign or whitelist individual `.py` files.

**Unsigned Python interpreters:**

Conda and Homebrew install unsigned Python binaries.  TCC identifies
processes by code signature, so unsigned binaries can behave inconsistently
— the permission may appear to be granted but not actually take effect,
especially in edge cases like SSH sessions.  If you hit this:

- **Prefer the system Python** (`/usr/bin/python3`) or a properly signed
  Python distribution when possible.
- **Conda** ships a `python.app` bundle (`$CONDA_PREFIX/python.app`) that
  has a bundle identifier (`com.continuum.python`).  Add *that* to
  Accessibility rather than the bare `bin/python3`.
- As a last resort, ad-hoc sign the binary:
  `codesign -s - -f /path/to/python3` (this creates a stable identity for
  TCC but is not a substitute for real code signing in production).

**SSH / remote sessions:**

SSH connections run in a different macOS security audit session from the
logged-in GUI.  Even if Python is trusted in TCC, an SSH-spawned process
won't inherit that trust.  Workarounds:

| Approach | How |
|----------|-----|
| **`open` command** | `open /path/to/python.app --args script.py` — launches in the GUI session |
| **LaunchAgent** | Create a `~/Library/LaunchAgents/*.plist` that runs the server — automatically runs in the user's GUI context |
| **Screen Sharing / VNC** | Connect via VNC and run from a Terminal window in the GUI session |
| **`launchctl asuser`** | `sudo launchctl asuser $(id -u) /path/to/python3 script.py` — runs under the GUI user's audit session |

**Enterprise (MDM) deployment:**

For fleet deployment without manual user interaction, push a
[Privacy Preferences Policy Control (PPPC) profile](https://developer.apple.com/documentation/devicemanagement/privacypreferencespolicycontrol)
that pre-grants `kTCCServiceAccessibility` to your signed Python binary.
This requires the binary to be properly code-signed (not ad-hoc).

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

### Linux — Docker + virtual display

Run UIA-X inside a Docker container with a virtual X11 or Wayland display.
The agent sees only what's inside the container.

```dockerfile
# Dockerfile.uiax-sandbox
FROM ubuntu:24.04

RUN apt-get update && apt-get install -y \
    xvfb x11vnc python3 python3-pip \
    python3-pyatspi gir1.2-atspi-2.0 at-spi2-core \
    dbus-x11 xdotool \
    # install target app dependencies here
    && rm -rf /var/lib/apt/lists/*

COPY . /opt/uia-x
WORKDIR /opt/uia-x
RUN pip install -r requirements.txt

# Start a virtual framebuffer + AT-SPI2 + the MCP server
CMD Xvfb :99 -screen 0 1920x1080x24 & \
    export DISPLAY=:99 && \
    export MCP_TRANSPORT=streamable-http && \
    dbus-run-session -- python -m server.server
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

### macOS — secondary user session

macOS doesn't support Docker containers with native GUI access (Darwin
containers are experimental and limited).  Instead:

1. **Create a dedicated macOS user account** with minimal permissions.
2. **Fast User Switch** to that account (`System Settings → Users & Groups →
   Login Options → Show fast user switching menu`).
3. Grant accessibility permission to Python in the new user's session
   (see [macOS accessibility permissions](#macos-accessibility-permissions-tcc) above).
4. Open only the target application in that session.
5. Run UIA-X there.  Your primary session remains untouched.

Alternatively, use a **macOS VM** (supported on Apple Silicon via the
Virtualization framework, or via UTM/Parallels) and run UIA-X inside the VM.

> **Note:** Each macOS user account has its own TCC database.  You must grant
> accessibility permission separately in each user session where UIA-X will run.

### Summary

| Platform | Recommended isolation | Observability | Status |
|----------|----------------------|---------------|--------|
| Windows  | RDP session (restricted user) | RDP viewer / `tscon` keep-alive | **Available now** |
| Linux    | Docker + Xvfb | VNC into container (optional) | **Available now** |
| macOS    | Secondary user / macOS VM | Fast User Switch / VNC | **Available now** |

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

All core tests use the **mock backend** — no Windows or target app required.

```
tests/test_tools.py              – Core UIA tools (inspect/invoke/set_value)
tests/test_process.py            – Process enumeration & window attachment
tests/test_auth.py               – API key authentication
tests/test_input.py              – Keystroke & mouse input
tests/test_msaa.py               – MSAA / LegacyIAccessible fallback
tests/test_linux_backend.py      – Linux AT-SPI2 backend unit tests
tests/test_linux_integration.py  – Linux integration tests (requires AT-SPI2)
tests/test_macos_backend.py      – macOS AXAPI backend unit tests
tests/test_macos_integration.py  – macOS integration tests (requires AXAPI + Calculator.app)
```

### Running macOS integration tests

macOS integration tests require a live GUI session, accessibility permissions,
and Calculator.app:

```bash
# Grant accessibility permission to Python first (manual, one-time):
#   System Settings → Privacy & Security → Accessibility → add Python / Terminal
#   (see "macOS accessibility permissions" section above for details)

# Run from the GUI session (preferred — TCC trust is automatic):
UIAX_RUN_MACOS_INTEGRATION=1 pytest tests/test_macos_integration.py -v

# Over SSH — launch via `open` so the process runs in the GUI Aqua session:
# (direct SSH execution won't have TCC trust even if Python is whitelisted)
ssh user@mac-host "open /path/to/python.app --args -m pytest \
    /path/to/uia-x/tests/test_macos_integration.py -v"

# Or use the live demo script for a quick smoke test:
open /path/to/python.app --args /path/to/uia-x/tests/live_macos_demo.py
cat /tmp/uiax_live_demo.txt   # output is tee'd to this file
```

Prerequisites (macOS):

```bash
pip install pyobjc-framework-ApplicationServices pyobjc-framework-Quartz pyobjc-framework-Cocoa
```

### Running Linux integration tests

Linux integration tests require a live AT-SPI2 session and a test application.
Use the headless harness:

```bash
# Run with Xvfb + D-Bus session (no real display needed)
./tests/run_headless.sh pytest tests/test_linux_integration.py -v

# Or manually enable integration tests
UIA_RUN_INTEGRATION=1 pytest tests/test_linux_integration.py -v
```

Prerequisites (Debian/Ubuntu):

```bash
sudo apt install -y \
    python3-pyatspi gir1.2-atspi-2.0 at-spi2-core \
    xvfb dbus xterm xdotool
```

---

## License

MIT — see [LICENSE](LICENSE).
