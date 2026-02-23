# Contributing to UIA‑X

Thanks for your interest in contributing!  **UIA‑X** (User Interface
Automation, eXtended) is a cross‑platform MCP server that gives LLM agents
full control of desktop applications through accessibility APIs.

All three platform backends are live:

| Platform | Backend | Accessibility API |
|----------|---------|-------------------|
| Windows  | `server/real_bridge.py` | UIA + MSAA via pywinauto |
| Linux    | `uiax/backends/linux/` | AT‑SPI2 via pyatspi |
| macOS    | `uiax/backends/macos/` | AXAPI via PyObjC |
| (any)    | `server/mock_bridge.py` | Mock backend for CI and local dev |

Contributions of all kinds are welcome — code, docs, tests, examples, and
platform‑specific improvements.

---

## Project structure

```
uia-x/
├── server/                    # MCP server core
│   ├── server.py              ← FastMCP app, tool registrations, auth gating
│   ├── uia_bridge.py          ← Abstract UIABridge interface, error taxonomy,
│   │                            platform detection & factory
│   ├── real_bridge.py         ← Windows UIA + MSAA backend (pywinauto)
│   ├── mock_bridge.py         ← Mock backend for tests
│   ├── process_manager.py     ← ProcessManager ABC + per-platform impls
│   └── auth.py                ← API key auth, Bearer middleware
├── uiax/
│   └── backends/
│       ├── linux/             ← LinuxBridge + LinuxProcessManager (AT-SPI2)
│       └── macos/             ← MacOSBridge + MacOSProcessManager (AXAPI)
├── schemas/                   ← JSON Schema for every tool
├── tests/                     ← pytest suite (mock + platform-specific)
├── examples/                  ← Example skills (e.g., Quicken)
├── mock_uia/                  ← Mock element trees for tests
├── conftest.py                ← pytest fixtures (mock bridge bootstrap)
├── pyproject.toml             ← Package metadata, entry points
└── requirements.txt           ← Runtime dependencies
```

---

## Getting started

### 1. Clone and install

```bash
git clone https://github.com/doucej/uia-x.git
cd uia-x
python -m venv .venv

# Windows
.venv\Scripts\activate

# Linux / macOS
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Run the server (mock backend)

```bash
UIA_BACKEND=mock python -m server.server
```

This lets you develop and test without needing a desktop session or any
platform‑specific dependencies.

### 3. Run tests

```bash
pytest -v
```

Tests run against the mock backend by default and do not require Windows,
Linux, or macOS.  Platform‑specific tests (e.g.,
`tests/test_linux_integration.py`, `tests/test_macos_integration.py`) run
only in the appropriate environment.

---

## The bridge interface

Every platform backend implements `UIABridge` (defined in
`server/uia_bridge.py`):

```python
class UIABridge(ABC):
    def inspect(self, target: dict) -> dict:       # Read the accessibility tree
    def invoke(self, target: dict) -> None:        # Click / activate an element
    def set_value(self, target: dict, value: str) -> None  # Set a value
    def send_keys(self, keys: str, target=None) -> None    # Send keystrokes
    def legacy_invoke(self, target: dict) -> None  # MSAA DoDefaultAction
    def mouse_click(self, x, y, double=False, button="left") -> None
```

And a corresponding `ProcessManager` subclass (in `server/process_manager.py`):

```python
class ProcessManager(ABC):
    def list_windows(self, *, visible_only=True) -> list[WindowInfo]
    def attach(self, *, pid=None, title=None, ...) -> WindowInfo
    def detach(self) -> None
```

The factory `get_bridge()` in `uia_bridge.py` auto‑detects the platform and
returns the right implementation.

### Adding or improving a backend

If you're working on a backend:

- Implement all `UIABridge` abstract methods
- Implement a `ProcessManager` subclass
- Keep behaviour consistent across platforms — match the Windows semantics
  where possible
- Add tests to `tests/` (unit tests against mocks, plus integration tests
  that only run on the target platform)
- Run `pytest -v` locally before submitting

---

## Submitting a pull request

1. **Fork** the repo
2. Create a **feature branch** from `main`
3. Make your changes — add tests for new functionality
4. Ensure `pytest` passes
5. Submit a **PR** with a clear description of what and why

PRs that include tests and follow the bridge abstraction are merged quickly.

### Code style

- Python 3.11+
- Type hints on all public APIs
- Docstrings on public classes and methods
- No hard dependencies on a single platform in shared code — keep
  platform‑specific imports behind `if` guards or in the backend packages

---

## Areas we'd love help with

- **More example skills** — Outlook, Excel, PowerPoint, GNOME apps, macOS
  apps — any application‑specific automation recipe
- **Cross‑platform input injection** — improving keystroke and mouse
  synthesis on Linux and macOS
- **Error recovery and diagnostics** — better error messages when elements
  aren't found, timing/retry helpers
- **Sandboxing templates** — Docker Compose files, macOS VM scripts, or
  Windows Sandbox configs for safe automation
- **Documentation** — tutorials, architecture deep‑dives, video walkthroughs
- **CI** — GitHub Actions workflows for running tests on all three platforms

---

## Security note

UIA‑X provides full desktop control.  Please read the
[Security model](README.md#security-model) and
[Isolation strategies](README.md#isolation-strategies) sections of the README
before deploying.  Never run the server on a desktop session that has access
to sensitive applications you don't intend to expose.

---

## License

UIA‑X is MIT‑licensed.  By contributing you agree that your contributions
will be licensed under the same terms.  See [LICENSE](LICENSE) for details.

---

Thanks for helping make desktop automation accessible to everyone. ❤️
