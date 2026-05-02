# Part 4: Proposed Platform Interface Layer

## Design Goals

1. **Platform-agnostic API**: Same method calls on Windows/macOS/Linux
2. **Natural language semantics**: Methods reflect UI concepts, not technical details
3. **Graceful degradation**: Partial implementation if full features unavailable
4. **Performance**: Avoid unnecessary abstraction overhead

---

## Core Interface: `UIAPowerface` Protocol

```python
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass

@dataclass
class Rect:
    left: int
    top: int
    right: int
    bottom: int
    width: int = 0
    height: int = 0

    def __post_init__(self):
        if self.width == 0:
            self.width = self.right - self.left
        if self.height == 0:
            self.height = self.bottom - self.top

@dataclass
class Element:
    id: str
    name: str
    role: str  # Unified role: "button", "edit", "list", etc.
    rect: Rect
    states: List[str]  # ["enabled", "focused", "checked", ...]
    actions: List[str]  # ["click", "setValue", "expand", ...]
    text: Optional[str] = None
    value: Optional[str] = None
    children: List[str] = None  # Element ID references
    description: str = ""

class UIAPlatform(ABC):
    """Abstract interface for platform UI automation backends."""
    
    @abstractmethod
    def list_windows(self) -> List[Dict]:
        """List all visible top-level windows."""
        pass
    
    @abstractmethod
    def attach_to_window(self, window_id: str) -> bool:
        """Attach to a window by ID. Returns success."""
        pass
    
    @abstractmethod
    def detach(self) -> None:
        """Detach from current window."""
        pass
    
    @abstractmethod
    def find_element(
        self,
        by: str,
        value: str,
        role_filter: Optional[str] = None,
        prefer_interactive: bool = False
    ) -> Element:
        """
        Find an element by selector.
        
        Supported selectors:
        - "name": Exact name match
        - "name_substring": Substring match
        - "role": Element role match
        - "automation_id": Platform-specific ID
        - "path": "/"-separated hierarchy path
        """
        pass
    
    @abstractmethod
    def find_all_elements(
        self,
        role_filter: Optional[str] = None,
        interactive_only: bool = True,
        max_depth: int = 10
    ) -> List[Element]:
        """Enumerate all elements matching criteria."""
        pass
    
    @abstractmethod
    def element_get_text(self, element: Element) -> str:
        """Get text value from element."""
        pass
    
    @abstractmethod
    def element_set_value(self, element: Element, value: str) -> bool:
        """Set value on editable element. Returns success."""
        pass
    
    @abstractmethod
    def element_invoke(self, element: Element) -> bool:
        """Invoke/button click action. Returns success."""
        pass
    
    @abstractmethod
    def element_send_keys(self, keys: str, element: Optional[Element] = None) -> bool:
        """Send keyboard input to element or focused element."""
        pass
    
    @abstractmethod
    def element_get_all_text(self, element: Element) -> str:
        """Get all descendant text (for rows, complex controls)."""
        pass
    
    # ----------- Mouse operations -----------
    
    @abstractmethod
    def mouse_click_absolute(
        self,
        x: int,
        y: int,
        double: bool = False,
        button: str = "left"
    ) -> bool:
        """Click at absolute screen coordinates."""
        pass
    
    @abstractmethod
    def mouse_scroll(
        self,
        x: int,
        y: int,
        amount: int,
        horizontal: bool = False
    ) -> bool:
        """Scroll at screen coordinates."""
        pass
    
    # ----------- Screen capture -----------
    
    @abstractmethod
    def capture_screen_region(
        self,
        left: int,
        top: int,
        right: int,
        bottom: int
    ) -> bytes:
        """Capture screenshot region as PNG bytes."""
        pass
    
    @abstractmethod
    def ocr_extract_text(self, image_bytes: bytes) -> List[Tuple[str, Rect]]:
        """
        Extract text with positions from image.
        Returns list of (text, Rect) tuples.
        """
        pass
    
    # ----------- Dialog handling -----------
    
    @abstractmethod
    def dismiss_dialogs(self, root_window_id: str, max_rounds: int = 3) -> bool:
        """
        Find and dismiss modal dialogs owned by window.
        Returns True if any dialog was dismissed.
        """
        pass


class QuickenPlatform(ABC):
    """Quicken-specific semantics layer."""
    
    # These implement Quicken's application semantics
    # Each platform provides its own implementation
    
    @abstractmethod
    def enumerate_sidebar_accounts(self) -> List[Dict]:
        """
        Enumerate all accounts in Quicken's sidebar.
        Returns [{name, section, scannable}, ...]
        """
        pass
    
    @abstractmethod
    def navigate_to_account(self, account_name: str) -> bool:
        """Open account register view."""
        pass
    
    @abstractmethod
    def read_register_state(self) -> Dict:
        """
        Read current register context.
        Returns {account_name, balance_total, tx_count, reconcile_active, filter_text}
        """
        pass
    
    @abstractmethod
    def read_register_rows(self, max_rows: int = 50) -> List[Dict]:
        """
        Read transaction rows from register.
        Returns [{date, payee, check_num, category, memo, payment, deposit, balance}]
        """
        pass
    
    @abstractmethod
    def set_register_filter(self, text: str) -> int:
        """Set search filter. Returns resulting transaction count."""
        pass
    
    @abstractmethod
    def open_reconcile(
        self,
        account_name: str,
        statement_date: str,
        ending_balance: str,
        **kwargs
    ) -> bool:
        """Open reconcile dialog and fill details."""
        pass
```

---

## Platform-Specific Implementations

### Windows Implementation (`server/win_bridge.py`)

Current code already implements `UIABridge` which is close to the target interface.

**Needs:**
1. Rename `WinUIABridge` → `WinAPlatform`
2. Move Windows-specific methods (`send_win32_message`) to protocol extension
3. Create `QuickenWindows` subclass in `skill` layer

### macOS Implementation (`uiax/backends/macos/bridge.py`)

Current code implements `MacosBridge` which mirrors `WinUIABridge` interface.

**Needs:**
1. Rename `MacosBridge` → `MacosAPlatform`
2. Add `capture_screen_region()` implementation using Quartz
3. Add `ocr_extract_text()` using Tesseract or Vision Framework
4. Add `QuickenMacOS` subclass implementing Quicken-specific semantics

### Linux Implementation (`uiax/backends/linux/bridge.py`)

Similar structure with AT-SPI2 backend.

**Needs:**
1. Add screen capture/OCR if needed
2. Consider whether Quicken runs on Linux (unlikely - use Windows VM)

---

## Skill Layer: Platform Abstraction

The Quicken skill layer shouldn't care which platform backend is used:

```python
class QuickenSkill:
    def __init__(self, platform: UIAPlatform, quicken_ops: QuickenPlatform):
        self.platform = platform
        self.quicken = quicken_ops
    
    @tool
    def list_accounts(self) -> Dict:
        """List accounts from sidebar."""
        accounts = self.quicken.enumerate_sidebar_accounts()
        return {"ok": True, "accounts": [a["name"] for a in accounts]}
    
    @tool
    def navigate_to_account(self, account_name: str) -> Dict:
        result = self.quicken.navigate_to_account(account_name)
        return {"ok": result}
```

The `QuickenWindows` and `QuickenMacOS` classes would provide implementation, using platform-specific APIs under the hood.

---

## Migration Path

### Phase 1: Interface Stabilization
- [x] Document current Windows API usage (Part 1-3)
- [ ] Create `/uiax/interface.py` with `UIAPlatform` protocol
- [ ] Move `MacosBridge` to implement protocol
- [ ] Move `LinuxBridge` to implement protocol

### Phase 2: Screen/OCR Abstraction
- [ ] Implement `capture_screen_region_macos` using Quartz
- [ ] Implement `ocr_extract_text_macos` using Tesseract/Vision
- [ ] Keep `read_screen_text()` Windows-only until macOS backend ready

### Phase 3: Quicken Semantics for macOS
- [ ] Design `QuickenMacOS` operations (differs significantly from Windows)
- [ ] Implement sidebar account enumeration (macQuicken has different UI)
- [ ] Implement register row reading (requires OCR or AX extension)
- [ ] Implement reconcile workflow (if supported on macOS)

### Phase 4: Cross-Platform Skill
- [ ] Remove Windows-specific comments from tool definitions
- [ ] Add platform detection and graceful degradation
- [ ] Update documentation to reflect cross-platform capability
