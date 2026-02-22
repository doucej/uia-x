"""
In-process mock UIA element tree.

``MockElement`` mirrors the properties and patterns of a real UIA element so
that tests can run without a live application.  MSAA / LegacyIAccessible
properties are modelled alongside UIA properties so that the full merged
element abstraction can be exercised.

Fixtures
--------
``MockTree.default()``            – generic Notepad-like window
``MockTree.with_msaa_fixtures()`` – owner-drawn list + MSAA-only TreeItems
``MockTree.quicken()``            – Quicken register view (V1 compat)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, List


# ---------------------------------------------------------------------------
# MSAA role constants (subset of oleacc.h ROLE_SYSTEM_*)
# ---------------------------------------------------------------------------

ROLE_SYSTEM_TITLEBAR = 0x01
ROLE_SYSTEM_PUSHBUTTON = 0x2B
ROLE_SYSTEM_CHECKBUTTON = 0x2C
ROLE_SYSTEM_RADIOBUTTON = 0x2D
ROLE_SYSTEM_COMBOBOX = 0x26
ROLE_SYSTEM_TEXT = 0x2A
ROLE_SYSTEM_STATICTEXT = 0x29
ROLE_SYSTEM_LIST = 0x21
ROLE_SYSTEM_LISTITEM = 0x22
ROLE_SYSTEM_OUTLINE = 0x23
ROLE_SYSTEM_OUTLINEITEM = 0x24
ROLE_SYSTEM_MENUITEM = 0x0C
ROLE_SYSTEM_MENUBAR = 0x02
ROLE_SYSTEM_TOOLBAR = 0x16
ROLE_SYSTEM_STATUSBAR = 0x17
ROLE_SYSTEM_WINDOW = 0x09
ROLE_SYSTEM_CLIENT = 0x0A
ROLE_SYSTEM_PAGETAB = 0x25

_ROLE_TEXT: dict[int, str] = {
    ROLE_SYSTEM_TITLEBAR: "title bar",
    ROLE_SYSTEM_PUSHBUTTON: "push button",
    ROLE_SYSTEM_CHECKBUTTON: "check button",
    ROLE_SYSTEM_RADIOBUTTON: "radio button",
    ROLE_SYSTEM_COMBOBOX: "combo box",
    ROLE_SYSTEM_TEXT: "editable text",
    ROLE_SYSTEM_STATICTEXT: "text",
    ROLE_SYSTEM_LIST: "list",
    ROLE_SYSTEM_LISTITEM: "list item",
    ROLE_SYSTEM_OUTLINE: "outline",
    ROLE_SYSTEM_OUTLINEITEM: "outline item",
    ROLE_SYSTEM_MENUITEM: "menu item",
    ROLE_SYSTEM_MENUBAR: "menu bar",
    ROLE_SYSTEM_TOOLBAR: "tool bar",
    ROLE_SYSTEM_STATUSBAR: "status bar",
    ROLE_SYSTEM_WINDOW: "window",
    ROLE_SYSTEM_CLIENT: "client",
    ROLE_SYSTEM_PAGETAB: "page tab",
}

# MSAA state bit flags
STATE_SYSTEM_SELECTED = 0x0002
STATE_SYSTEM_FOCUSED = 0x0004
STATE_SYSTEM_READONLY = 0x0040
STATE_SYSTEM_INVISIBLE = 0x8000
STATE_SYSTEM_CHECKED = 0x0010


def role_text(role: int) -> str:
    return _ROLE_TEXT.get(role, f"role_0x{role:02x}")


# ---------------------------------------------------------------------------
# MockElement
# ---------------------------------------------------------------------------


@dataclass
class MockElement:
    """A node in the mock UIA element tree."""

    # UIA properties
    name: str
    control_type: str = "Pane"
    automation_id: str = ""
    class_name: str = ""
    enabled: bool = True
    rect: dict[str, int] = field(
        default_factory=lambda: {"left": 0, "top": 0, "right": 100, "bottom": 30}
    )
    invokable: bool = False
    value_settable: bool = False
    value: str = ""
    children: List["MockElement"] = field(default_factory=list)

    # MSAA / LegacyIAccessible properties
    legacy_name: str = ""
    legacy_role: int = 0
    default_action: str = ""
    legacy_description: str = ""
    legacy_value: str = ""
    legacy_state: int = 0
    child_id: int = 0
    hwnd: int = 0
    legacy_invokable: bool = False

    # Runtime state
    _invoked: bool = field(default=False, init=False, repr=False)
    _legacy_invoked: bool = field(default=False, init=False, repr=False)

    # ------------------------------------------------------------------
    # UIA operations
    # ------------------------------------------------------------------

    def invoke(self) -> None:
        if not self.invokable:
            raise RuntimeError(f"Element '{self.name}' is not invokable")
        self._invoked = True

    def set_value(self, new_value: str) -> None:
        if not self.value_settable:
            raise RuntimeError(
                f"Element '{self.name}' does not support Value pattern"
            )
        self.value = new_value

    # ------------------------------------------------------------------
    # MSAA operations
    # ------------------------------------------------------------------

    def legacy_invoke(self) -> None:
        """Execute the MSAA DefaultAction."""
        if not self.legacy_invokable:
            raise RuntimeError(
                f"Element '{self.name}' does not support LegacyIAccessible DefaultAction"
            )
        self._legacy_invoked = True

    # ------------------------------------------------------------------
    # Derived MSAA properties
    # ------------------------------------------------------------------

    @property
    def legacy_role_text(self) -> str:
        return role_text(self.legacy_role) if self.legacy_role else ""

    @property
    def selected(self) -> bool:
        return bool(self.legacy_state & STATE_SYSTEM_SELECTED)

    @property
    def focused(self) -> bool:
        return bool(self.legacy_state & STATE_SYSTEM_FOCUSED)

    # ------------------------------------------------------------------
    # Tree traversal
    # ------------------------------------------------------------------

    def find_all(
        self, predicate: Callable[["MockElement"], bool]
    ) -> list["MockElement"]:
        """Depth-first search for all matching descendants (inclusive of self)."""
        results: list[MockElement] = []
        if predicate(self):
            results.append(self)
        for child in self.children:
            results.extend(child.find_all(predicate))
        return results

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self, depth: int = 3) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict – same shape as real bridge output."""
        patterns: list[str] = []
        if self.invokable:
            patterns.append("InvokePattern")
        if self.value_settable:
            patterns.append("ValuePattern")
        if self.legacy_invokable:
            patterns.append("LegacyIAccessiblePattern")
        if self.selected:
            patterns.append("SelectionItemPattern")

        node: dict[str, Any] = {
            "name": self.name,
            "control_type": self.control_type,
            "automation_id": self.automation_id,
            "class_name": self.class_name,
            "enabled": self.enabled,
            "rect": self.rect,
            "patterns": patterns,
            "children": [],
        }
        if self.value_settable:
            node["value"] = self.value

        msaa: dict[str, Any] = {}
        if self.legacy_name:
            msaa["name"] = self.legacy_name
        if self.legacy_role:
            msaa["role"] = self.legacy_role
            msaa["role_text"] = self.legacy_role_text
        if self.default_action:
            msaa["default_action"] = self.default_action
        if self.legacy_description:
            msaa["description"] = self.legacy_description
        if self.legacy_value:
            msaa["value"] = self.legacy_value
        if self.legacy_state:
            msaa["state"] = self.legacy_state
            msaa["selected"] = self.selected
            msaa["focused"] = self.focused
        if self.child_id:
            msaa["child_id"] = self.child_id
        if self.hwnd:
            msaa["hwnd"] = self.hwnd
        if msaa:
            node["msaa"] = msaa

        if depth > 0:
            node["children"] = [c.to_dict(depth - 1) for c in self.children]
        return node


# ---------------------------------------------------------------------------
# MockTree
# ---------------------------------------------------------------------------


class MockTree:
    """Wrapper holding a root MockElement and convenience search helpers."""

    def __init__(self, root: MockElement) -> None:
        self.root = root

    @classmethod
    def default(cls) -> "MockTree":
        """Generic Notepad-like window fixture."""
        return cls(root=_build_generic_tree())

    @classmethod
    def quicken(cls) -> "MockTree":
        """Quicken register view fixture (V1 backward-compatible)."""
        return cls(root=_build_quicken_tree())

    @classmethod
    def with_msaa_fixtures(cls) -> "MockTree":
        """
        Tree containing owner-drawn and MSAA-only elements for testing the
        MSAA fallback path.
        """
        return cls(root=_build_msaa_fixture_tree())


# ---------------------------------------------------------------------------
# Element builder helper
# ---------------------------------------------------------------------------


def _el(
    name: str,
    control_type: str = "Pane",
    *,
    automation_id: str = "",
    class_name: str = "",
    enabled: bool = True,
    invokable: bool = False,
    value_settable: bool = False,
    value: str = "",
    children: list[MockElement] | None = None,
    rect: dict[str, int] | None = None,
    legacy_name: str = "",
    legacy_role: int = 0,
    default_action: str = "",
    legacy_description: str = "",
    legacy_value: str = "",
    legacy_state: int = 0,
    child_id: int = 0,
    hwnd: int = 0,
    legacy_invokable: bool = False,
) -> MockElement:
    return MockElement(
        name=name,
        control_type=control_type,
        automation_id=automation_id,
        class_name=class_name,
        enabled=enabled,
        invokable=invokable,
        value_settable=value_settable,
        value=value,
        children=children or [],
        rect=rect or {"left": 0, "top": 0, "right": 400, "bottom": 30},
        legacy_name=legacy_name,
        legacy_role=legacy_role,
        default_action=default_action,
        legacy_description=legacy_description,
        legacy_value=legacy_value,
        legacy_state=legacy_state,
        child_id=child_id,
        hwnd=hwnd,
        legacy_invokable=legacy_invokable,
    )


# ---------------------------------------------------------------------------
# Generic fixture (default for V2)
# ---------------------------------------------------------------------------


def _build_generic_tree() -> MockElement:
    """A simplified Notepad-like window for general testing."""

    menu_bar = _el(
        "Application",
        "MenuBar",
        automation_id="menuBar",
        class_name="MenuStrip",
        legacy_role=ROLE_SYSTEM_MENUBAR,
        children=[
            _el(
                "File",
                "MenuItem",
                automation_id="menu_file",
                invokable=True,
                legacy_role=ROLE_SYSTEM_MENUITEM,
            ),
            _el(
                "Edit",
                "MenuItem",
                automation_id="menu_edit",
                invokable=True,
                legacy_role=ROLE_SYSTEM_MENUITEM,
            ),
            _el(
                "Help",
                "MenuItem",
                automation_id="menu_help",
                invokable=True,
                legacy_role=ROLE_SYSTEM_MENUITEM,
            ),
        ],
    )

    toolbar = _el(
        "Toolbar",
        "ToolBar",
        automation_id="mainToolbar",
        class_name="ToolStrip",
        legacy_role=ROLE_SYSTEM_TOOLBAR,
        children=[
            _el(
                "New",
                "Button",
                automation_id="btn_new",
                invokable=True,
                legacy_role=ROLE_SYSTEM_PUSHBUTTON,
                default_action="Press",
                legacy_invokable=True,
            ),
            _el(
                "Open",
                "Button",
                automation_id="btn_open",
                invokable=True,
                legacy_role=ROLE_SYSTEM_PUSHBUTTON,
                default_action="Press",
                legacy_invokable=True,
            ),
            _el(
                "Save",
                "Button",
                automation_id="btn_save",
                invokable=True,
                legacy_role=ROLE_SYSTEM_PUSHBUTTON,
                default_action="Press",
                legacy_invokable=True,
            ),
            _el(
                "Print",
                "Button",
                automation_id="btn_print",
                invokable=True,
                legacy_role=ROLE_SYSTEM_PUSHBUTTON,
                default_action="Press",
                legacy_invokable=True,
            ),
        ],
    )

    editor = _el(
        "Editor",
        "Edit",
        automation_id="textEditor",
        class_name="RichEdit20W",
        value_settable=True,
        value="",
        legacy_role=ROLE_SYSTEM_TEXT,
    )

    status_bar = _el(
        "Ready",
        "StatusBar",
        automation_id="statusBar",
        class_name="StatusBar",
        legacy_role=ROLE_SYSTEM_STATUSBAR,
    )

    return _el(
        "Untitled - Notepad",
        "Window",
        class_name="Notepad",
        automation_id="mainWindow",
        legacy_role=ROLE_SYSTEM_WINDOW,
        rect={"left": 0, "top": 0, "right": 1024, "bottom": 768},
        children=[menu_bar, toolbar, editor, status_bar],
    )


# ---------------------------------------------------------------------------
# Quicken fixture (V1 backward-compat)
# ---------------------------------------------------------------------------


def _build_quicken_tree() -> MockElement:
    """Return a representative Quicken window tree."""

    menu_bar = _el(
        "Application",
        "MenuBar",
        automation_id="menuBar",
        class_name="QMenuBar",
        legacy_role=ROLE_SYSTEM_MENUBAR,
        children=[
            _el("File", "MenuItem", automation_id="menu_file", invokable=True, legacy_role=ROLE_SYSTEM_MENUITEM),
            _el("Edit", "MenuItem", automation_id="menu_edit", invokable=True, legacy_role=ROLE_SYSTEM_MENUITEM),
            _el("Tools", "MenuItem", automation_id="menu_tools", invokable=True, legacy_role=ROLE_SYSTEM_MENUITEM),
            _el("Help", "MenuItem", automation_id="menu_help", invokable=True, legacy_role=ROLE_SYSTEM_MENUITEM),
        ],
    )

    toolbar = _el(
        "Toolbar",
        "ToolBar",
        automation_id="mainToolbar",
        class_name="QToolBar",
        legacy_role=ROLE_SYSTEM_TOOLBAR,
        children=[
            _el("New Transaction", "Button", automation_id="btn_new_txn", invokable=True, legacy_role=ROLE_SYSTEM_PUSHBUTTON, default_action="Press", legacy_invokable=True),
            _el("Delete", "Button", automation_id="btn_delete", invokable=True, enabled=False, legacy_role=ROLE_SYSTEM_PUSHBUTTON, default_action="Press"),
            _el("Print", "Button", automation_id="btn_print", invokable=True, legacy_role=ROLE_SYSTEM_PUSHBUTTON, default_action="Press", legacy_invokable=True),
            _el("Reconcile", "Button", automation_id="btn_reconcile", invokable=True, legacy_role=ROLE_SYSTEM_PUSHBUTTON, default_action="Press", legacy_invokable=True),
        ],
    )

    entry_form = _el(
        "Transaction Entry",
        "Pane",
        automation_id="txnEntryForm",
        children=[
            _el("Date", "Edit", automation_id="txn_date", class_name="QDateField", value_settable=True, value="02/21/2026", legacy_role=ROLE_SYSTEM_TEXT),
            _el("Payee", "Edit", automation_id="txn_payee", class_name="QLineEdit", value_settable=True, value="", legacy_role=ROLE_SYSTEM_TEXT),
            _el("Amount", "Edit", automation_id="txn_amount", class_name="QLineEdit", value_settable=True, value="0.00", legacy_role=ROLE_SYSTEM_TEXT),
            _el("Category", "ComboBox", automation_id="txn_category", class_name="QComboBox", value_settable=True, value="", legacy_role=ROLE_SYSTEM_COMBOBOX),
            _el("Memo", "Edit", automation_id="txn_memo", class_name="QLineEdit", value_settable=True, value="", legacy_role=ROLE_SYSTEM_TEXT),
            _el("Save", "Button", automation_id="btn_save", invokable=True, legacy_role=ROLE_SYSTEM_PUSHBUTTON, default_action="Press", legacy_invokable=True),
            _el("Cancel", "Button", automation_id="btn_cancel", invokable=True, legacy_role=ROLE_SYSTEM_PUSHBUTTON, default_action="Press", legacy_invokable=True),
        ],
    )

    def _row(date: str, payee: str, amount: str, balance: str, row_id: str) -> MockElement:
        return _el(
            payee,
            "DataItem",
            automation_id=row_id,
            legacy_role=ROLE_SYSTEM_LISTITEM,
            children=[
                _el(date, "Text", automation_id=f"{row_id}_date"),
                _el(payee, "Text", automation_id=f"{row_id}_payee"),
                _el(amount, "Text", automation_id=f"{row_id}_amount"),
                _el(balance, "Text", automation_id=f"{row_id}_balance"),
            ],
        )

    register_list = _el(
        "Register",
        "DataGrid",
        automation_id="registerGrid",
        class_name="QRegisterView",
        legacy_role=ROLE_SYSTEM_LIST,
        children=[
            _row("02/18/2026", "Whole Foods", "-$87.42", "$1 234.56", "row_0"),
            _row("02/19/2026", "Netflix", "-$15.99", "$1 218.57", "row_1"),
            _row("02/20/2026", "Direct Deposit", "+$2000.00", "$3 218.57", "row_2"),
        ],
    )

    sidebar = _el(
        "Accounts",
        "Tree",
        automation_id="accountSidebar",
        class_name="QAccountTree",
        legacy_role=ROLE_SYSTEM_OUTLINE,
        children=[
            _el("Checking", "TreeItem", automation_id="acct_checking", invokable=True, legacy_role=ROLE_SYSTEM_OUTLINEITEM, default_action="Expand", legacy_invokable=True),
            _el("Savings", "TreeItem", automation_id="acct_savings", invokable=True, legacy_role=ROLE_SYSTEM_OUTLINEITEM, default_action="Expand", legacy_invokable=True),
            _el("Credit Card", "TreeItem", automation_id="acct_cc", invokable=True, legacy_role=ROLE_SYSTEM_OUTLINEITEM, default_action="Expand", legacy_invokable=True),
            _el("Investments", "TreeItem", automation_id="acct_invest", invokable=True, legacy_role=ROLE_SYSTEM_OUTLINEITEM, default_action="Expand", legacy_invokable=True),
        ],
    )

    status_bar = _el(
        "Ready",
        "StatusBar",
        automation_id="statusBar",
        class_name="QStatusBar",
        legacy_role=ROLE_SYSTEM_STATUSBAR,
    )

    return _el(
        "Quicken",
        "Window",
        class_name="QWinFrame",
        automation_id="quickenMainWindow",
        legacy_role=ROLE_SYSTEM_WINDOW,
        rect={"left": 0, "top": 0, "right": 1280, "bottom": 800},
        children=[menu_bar, toolbar, sidebar, register_list, entry_form, status_bar],
    )


# ---------------------------------------------------------------------------
# MSAA fixture tree (owner-drawn + MSAA-only controls)
# ---------------------------------------------------------------------------


def _build_msaa_fixture_tree() -> MockElement:
    """
    A window whose interesting controls are only visible through MSAA,
    mimicking owner-drawn list boxes and nav trees.
    """

    list_items = [
        _el(
            "",
            "Pane",
            legacy_name="Example Bank Checking",
            legacy_role=ROLE_SYSTEM_LISTITEM,
            default_action="Select",
            legacy_state=STATE_SYSTEM_SELECTED,
            child_id=1,
            hwnd=0x1001,
            legacy_invokable=True,
        ),
        _el(
            "",
            "Pane",
            legacy_name="Example Bank Savings",
            legacy_role=ROLE_SYSTEM_LISTITEM,
            default_action="Select",
            legacy_state=0,
            child_id=2,
            hwnd=0x1001,
            legacy_invokable=True,
        ),
        _el(
            "",
            "Pane",
            legacy_name="Visa Card",
            legacy_role=ROLE_SYSTEM_LISTITEM,
            default_action="Select",
            legacy_state=0,
            child_id=3,
            hwnd=0x1001,
            legacy_invokable=True,
        ),
    ]

    account_list = _el(
        "AccountList",
        "Pane",
        class_name="QWAcctBarHolder",
        automation_id="1000",
        legacy_role=ROLE_SYSTEM_LIST,
        hwnd=0x1001,
        children=list_items,
    )

    def _tree_item(
        label: str, cid: int, children: list | None = None
    ) -> MockElement:
        return _el(
            "",
            "Pane",
            legacy_name=label,
            legacy_role=ROLE_SYSTEM_OUTLINEITEM,
            default_action="Expand",
            legacy_invokable=True,
            child_id=cid,
            hwnd=0x2001,
            children=children or [],
        )

    nav_tree = _el(
        "NavTree",
        "Pane",
        class_name="QWNavBtnTray",
        automation_id="300",
        legacy_role=ROLE_SYSTEM_OUTLINE,
        hwnd=0x2001,
        children=[
            _tree_item("Banking", 1, children=[_tree_item("Example Bank Checking", 2)]),
            _tree_item("Investing", 3),
        ],
    )

    return _el(
        "MSAA Test Window",
        "Window",
        class_name="TestFrame",
        legacy_role=ROLE_SYSTEM_WINDOW,
        rect={"left": 0, "top": 0, "right": 1280, "bottom": 800},
        children=[account_list, nav_tree],
    )
