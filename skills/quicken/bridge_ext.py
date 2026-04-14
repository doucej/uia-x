"""
Quicken-specific bridge operations.

These functions implement Quicken-specific UI automation that was previously
embedded in the generic ``WinUIABridge``.  They accept a bridge instance and
use its public API plus direct Win32 ctypes calls.

None of this code should be imported by the core server or bridge layers.
"""

from __future__ import annotations

from typing import Any

from server.uia_bridge import UIAError, TargetNotFoundError


_SKIP_ACCT_ITEMS_MISC = frozenset({"custom...", "qcombo_separator", ""})


def _is_valid_hwnd(hwnd: int) -> bool:
    """Check whether a window handle is still valid."""
    import ctypes  # noqa: PLC0415
    return bool(ctypes.windll.user32.IsWindow(hwnd))

def _acct_match(candidate: str, query: str) -> bool:
    """Fuzzy account name matching: exact, substring, or all-words-present.

    Handles cases where AI agents use shortened names like "Costco Visa"
    to match "Costco Anywhere Visa® Card by Citi".
    """
    import re  # noqa: PLC0415

    c = candidate.lower()
    q = query.lower()
    if c == q:
        return True
    if q in c or c in q:
        return True
    # Strip non-ASCII (®, ™) and extra whitespace for comparison
    c_ascii = re.sub(r"[^\x00-\x7f]", "", c).strip()
    q_ascii = re.sub(r"[^\x00-\x7f]", "", q).strip()
    if c_ascii == q_ascii or q_ascii in c_ascii or c_ascii in q_ascii:
        return True
    # All words in query appear in candidate (order-independent)
    q_words = q_ascii.split()
    if q_words and all(w in c_ascii for w in q_words):
        return True
    return False


def _dismiss_modal_dialogs(root_hwnd: int) -> bool:
    """Find and dismiss any modal dialogs owned by the Quicken window.

    Looks for top-level ``QWinDlg`` windows that own ``root_hwnd`` and
    contain a "Done" or "Close" button.  Returns True if a dialog was
    dismissed.
    """
    import ctypes  # noqa: PLC0415
    import time as _time  # noqa: PLC0415

    user32 = ctypes.windll.user32
    WM_COMMAND = 0x0111
    dismissed = False

    # Collect visible top-level dialogs owned by root
    dialogs: list[int] = []
    def _enum_cb(h: int, _: Any) -> bool:
        if not user32.IsWindowVisible(h):
            return True
        owner = user32.GetWindow(h, 4)  # GW_OWNER
        if owner != root_hwnd:
            return True
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(h, cls, 64)
        if cls.value in ("QWinDlg", "#32770"):  # Quicken dialog or system dialog
            dialogs.append(h)
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p,
                                     ctypes.POINTER(ctypes.c_int))
    user32.EnumWindows(WNDENUMPROC(_enum_cb), None)

    for dlg in dialogs:
        # Find a dismiss button: Done, Close, OK, Cancel
        dismiss_btn = 0
        def _btn_cb(h: int, _: Any) -> bool:
            nonlocal dismiss_btn
            if not user32.IsWindowVisible(h):
                return True
            buf = ctypes.create_unicode_buffer(64)
            user32.GetWindowTextW(h, buf, 64)
            text = buf.value.strip().lower()
            if text in ("done", "close", "ok", "cancel"):
                dismiss_btn = h
                return False  # stop enumeration
            return True

        user32.EnumChildWindows(dlg, WNDENUMPROC(_btn_cb), None)
        if dismiss_btn:
            ctrl_id = user32.GetDlgCtrlID(dismiss_btn)
            user32.PostMessageW(dlg, WM_COMMAND, ctrl_id, dismiss_btn)
            _time.sleep(0.5)
            dismissed = True

    return dismissed


def _send_msg(hwnd: int, msg: int, wp: int, lp: int) -> int:
    """Thin wrapper around ``SendMessageW`` with proper 64-bit argtypes."""
    import ctypes  # noqa: PLC0415

    _fn = ctypes.windll.user32.SendMessageW
    if not hasattr(_fn, "_typed"):
        import ctypes.wintypes  # noqa: PLC0415

        _fn.argtypes = [
            ctypes.wintypes.HWND,
            ctypes.wintypes.UINT,
            ctypes.wintypes.WPARAM,
            ctypes.wintypes.LPARAM,
        ]
        _fn.restype = ctypes.wintypes.LPARAM
        _fn._typed = True  # type: ignore[attr-defined]
    return _fn(hwnd, msg, wp, lp)


def _combo_get_items(hwnd: int) -> list[str]:
    """Read all items from a Win32 combobox via CB_GETCOUNT/CB_GETLBTEXT."""
    import ctypes  # noqa: PLC0415

    CB_GETCOUNT = 0x0146
    CB_GETLBTEXT = 0x0148
    CB_GETLBTEXTLEN = 0x0149

    count = _send_msg(hwnd, CB_GETCOUNT, 0, 0)
    out: list[str] = []
    for i in range(min(count, 500)):
        tlen = _send_msg(hwnd, CB_GETLBTEXTLEN, i, 0)
        if tlen <= 0:
            out.append("")
            continue
        buf = ctypes.create_unicode_buffer(tlen + 1)
        _send_msg(hwnd, CB_GETLBTEXT, i, ctypes.addressof(buf))
        out.append(buf.value)
    return out


def _combo_cur_text(hwnd: int) -> str:
    """Read the currently selected item text from a Win32 combobox."""
    import ctypes  # noqa: PLC0415

    CB_GETCURSEL = 0x0147
    CB_GETLBTEXT = 0x0148
    CB_GETLBTEXTLEN = 0x0149

    idx = _send_msg(hwnd, CB_GETCURSEL, 0, 0)
    if idx < 0:
        return ""
    tl = _send_msg(hwnd, CB_GETLBTEXTLEN, idx, 0)
    if tl <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(tl + 1)
    _send_msg(hwnd, CB_GETLBTEXT, idx, ctypes.addressof(buf))
    return buf.value


# Known items that should NOT be treated as account names.
_FILTER_ITEMS = frozenset({
    "any type", "charge", "payment", "check", "atm", "deposit",
    "online", "transfer", "eft", "printed check",
})
_ACCOUNT_FILTERS = frozenset({
    "all accounts", "personal accounts only", "business accounts only",
})
_SKIP_ACCT_ITEMS = _FILTER_ITEMS | _ACCOUNT_FILTERS | _SKIP_ACCT_ITEMS_MISC
_TEMPORAL_WORDS = ("month", "year", "week", "day", "today", "quarter",
                   "type", "income", "expense", "all date", "earliest",
                   "custom date", "last 12", "last 3", "last 5")

# Quicken navigation/category views — these MDI titles are NOT account names.
_CATEGORY_VIEWS = frozenset({
    "home", "spending", "bills", "bills & income", "planning", "tax",
    "reports", "investing", "property & debt", "net worth", "budget",
    "debt reduction", "savings goals", "bills & reminders",
})


def _is_combo_filter_item(s: str) -> bool:
    """Return True if *s* is a combo filter/separator, not an account name."""
    low = s.lower().strip()
    if not low:
        return True
    if low in _SKIP_ACCT_ITEMS:
        return True
    if low.startswith("all ") and any(w in low for w in
                                       ("account", "debt", "checking",
                                        "saving", "credit", "liabilit",
                                        "transaction", "date")):
        return True
    if low.endswith(" only"):
        return True
    return False


def _find_account_combo(root_hwnd: int) -> tuple[int, list[str]] | None:
    """Find the account-selector QWComboBox anywhere in the window tree.

    Searches ALL QWComboBox children (including those in hidden/background
    MDI tabs) and returns ``(combo_hwnd, items)`` for the first combo that
    contains account-like names (not date/filter combos).
    """
    import ctypes  # noqa: PLC0415
    import ctypes.wintypes as wt  # noqa: PLC0415

    user32 = ctypes.windll.user32
    combos: list[int] = []

    EnumCB = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

    def _collect(h: int, _: int) -> bool:
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(h, cls, 64)
        if cls.value.lower() == "qwcombobox":
            combos.append(h)
        return True

    user32.EnumChildWindows(root_hwnd, EnumCB(_collect), 0)

    # Sort by x-position: the account combo is typically the leftmost.
    def _left(h: int) -> int:
        r = wt.RECT()
        user32.GetWindowRect(h, ctypes.byref(r))
        return r.left

    combos.sort(key=_left)

    for combo_h in combos:
        items = _combo_get_items(combo_h)
        if len(items) < 2:
            continue
        lower = [it.lower() for it in items]
        # Skip combos that are entirely filter/date combos.
        if all(_is_combo_filter_item(it) for it in items):
            continue
        temporal = sum(1 for it in lower if any(w in it for w in _TEMPORAL_WORDS))
        if temporal > len(items) / 2:
            continue
        return combo_h, items

    return None


def _discover_accounts_via_mdi(root_hwnd: int) -> list[dict[str, Any]]:
    """Discover accounts by enumerating QWMDI tabs and their combos.

    This is **much faster** than sidebar scanning (~0.2s vs 200s) because it
    uses pure Win32 message passing — no physical mouse clicks needed.

    Strategy:
    1. Enumerate all QWMDI children and read their titles.
    2. Non-category titles (not "Home", "Spending", etc.) are account names.
    3. For category-view tabs, read the leftmost combo that has account items.
    4. Merge and deduplicate.

    Returns a list of ``{"name": str, "source": "mdi_tab"|"combo",
    "combo_hwnd": hex, "combo_index": int}`` dicts.
    """
    import ctypes  # noqa: PLC0415
    import ctypes.wintypes as wt  # noqa: PLC0415

    user32 = ctypes.windll.user32

    # Step 1: find all QWMDI children.
    mdi_tabs: list[tuple[int, str]] = []
    EnumCB = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

    def _collect_mdi(h: int, _: int) -> bool:
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(h, cls, 64)
        if cls.value == "QWMDI":
            title = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(h, title, 256)
            mdi_tabs.append((h, title.value))
        return True

    user32.EnumChildWindows(root_hwnd, EnumCB(_collect_mdi), 0)

    seen: set[str] = set()
    result: list[dict[str, Any]] = []

    def _add(name: str, **extra: Any) -> None:
        key = name.lower().strip()
        if key and key not in seen:
            seen.add(key)
            result.append({"name": name, **extra})

    # Step 2: classify each MDI tab.
    for mdi_h, mdi_title in mdi_tabs:
        if not mdi_title or mdi_title.lower() in _CATEGORY_VIEWS:
            # Category view — read combos inside this tab for account lists.
            child_combos: list[tuple[int, int]] = []

            def _get_combos(h: int, _: int) -> bool:
                cc = ctypes.create_unicode_buffer(64)
                user32.GetClassNameW(h, cc, 64)
                if cc.value.lower() == "qwcombobox":
                    r = wt.RECT()
                    user32.GetWindowRect(h, ctypes.byref(r))
                    child_combos.append((h, r.left))
                return True

            user32.EnumChildWindows(mdi_h, EnumCB(_get_combos), 0)
            child_combos.sort(key=lambda t: t[1])

            for combo_h, _ in child_combos:
                items = _combo_get_items(combo_h)
                # Skip combos that are entirely temporal/filter items.
                if all(_is_combo_filter_item(it) or
                       any(w in it.lower() for w in _TEMPORAL_WORDS)
                       for it in items):
                    continue
                for idx, item in enumerate(items):
                    if _is_combo_filter_item(item):
                        continue
                    low = item.lower()
                    if any(w in low for w in _TEMPORAL_WORDS):
                        continue
                    _add(item, source="combo",
                         combo_hwnd=hex(combo_h), combo_index=idx)
        else:
            # Non-category MDI tab — title IS the account name.
            _add(mdi_title, source="mdi_tab")

    return result


def list_accounts(bridge: Any) -> list[dict[str, Any]]:
    """
    Return all accounts visible in Quicken.

    Strategy (fastest first)
    ---------
    1. MDI-tab discovery: enumerate all QWMDI children.  Account-specific
       tabs reveal their name via the window title; category-view tabs
       (Spending, Property & Debt, …) contain account-selector combos.
       This path takes ~0.2 s.
    2. Sidebar cache: if a sidebar scan was previously done, return it.
    3. Sidebar scan: full physical-click scan (~200 s).  Only used when
       no MDI tabs or combos exist.

    Parameters
    ----------
    bridge
        A ``WinUIABridge`` instance (or any bridge with ``find_all``).

    Returns
    -------
    list of dict
        Each entry has ``{"name": str, "source": ...}``.  Combo-sourced
        entries also include ``combo_hwnd`` and ``combo_index``.

    Raises
    ------
    UIAError
        If no accounts can be located.
    """
    from server.process_manager import get_process_manager  # noqa: PLC0415
    pm = get_process_manager()
    if not pm.attached:
        raise TargetNotFoundError("Use select_window to attach to a target first.")
    root = pm.attached.hwnd

    # --- Primary: MDI discovery (instant) ---
    mdi_accounts = _discover_accounts_via_mdi(root)
    if mdi_accounts:
        return mdi_accounts

    # --- Secondary: global combo search ---
    found = _find_account_combo(root)
    if found:
        combo_h, raw_items = found
        result = []
        for idx, name in enumerate(raw_items):
            if _is_combo_filter_item(name):
                continue
            result.append({"name": name, "combo_index": idx,
                           "combo_hwnd": hex(combo_h), "source": "combo"})
        if result:
            return result

    # --- Tertiary: sidebar ---
    if _sidebar_cache:
        return [{"name": e["name"], "source": "sidebar"} for e in _sidebar_cache]
    sidebar = list_sidebar_accounts(bridge)
    if sidebar:
        return [{"name": e["name"], "source": "sidebar"} for e in sidebar]

    raise UIAError(
        "No accounts found. Try navigating to a Spending or All Transactions view.",
        code="ACCOUNT_NOT_FOUND",
    )


# ---------------------------------------------------------------------------
# Sidebar navigation helpers
# ---------------------------------------------------------------------------

def _find_sidebar_accounts(root_hwnd: int) -> list[dict[str, Any]]:
    """Discover sidebar accounts by scanning QWAcctBarHolder ListBox items.

    The Quicken sidebar has section buttons (Banking, Investing, etc.) with
    child ``QWListViewer`` containers holding ``ListBox`` controls.  Each
    ListBox item represents an account.  Items with height ≤ 5 px are
    decorative separators and are skipped.

    Returns a list of ``{"section", "lb_hwnd", "item_index", "screen_x",
    "screen_y"}`` dicts (one per real item).  The ``name`` field is left
    empty because these are owner-drawn and unreadable via LB_GETTEXT.
    """
    import ctypes  # noqa: PLC0415
    import ctypes.wintypes as wt  # noqa: PLC0415

    user32 = ctypes.windll.user32
    SM = user32.SendMessageW
    SM.argtypes = [wt.HWND, ctypes.c_uint, wt.WPARAM, wt.LPARAM]
    SM.restype = wt.LPARAM

    EnumCB = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

    # Find the QWAcctBarHolder (may be hidden by investment views)
    holder = None
    def _find_holder(h: int, _: int) -> bool:
        nonlocal holder
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(h, cls, 64)
        if cls.value == "QWAcctBarHolder":
            holder = h
            return False
        return True
    user32.EnumChildWindows(root_hwnd, EnumCB(_find_holder), 0)
    if not holder:
        return []

    # Ensure the sidebar is visible (investment views may hide it)
    if not user32.IsWindowVisible(holder):
        SW_SHOW = 5
        # Walk up and show each hidden ancestor
        h = holder
        chain = []
        while h and h != root_hwnd:
            chain.append(h)
            h = user32.GetParent(h)
        for h in reversed(chain):
            if not user32.IsWindowVisible(h):
                user32.ShowWindow(h, SW_SHOW)
        import time as _time  # noqa: PLC0415
        _time.sleep(0.2)
        if not user32.IsWindowVisible(holder):
            return []  # couldn't recover visibility

    # Collect ListBoxes parented by QWListViewer within the holder
    items: list[dict[str, Any]] = []
    cls_buf = ctypes.create_unicode_buffer(64)
    txt_buf = ctypes.create_unicode_buffer(256)

    def _scan_lb(h: int, _: int) -> bool:
        user32.GetClassNameW(h, cls_buf, 64)
        if cls_buf.value != "ListBox" or not user32.IsWindowVisible(h):
            return True
        parent = user32.GetParent(h)
        user32.GetClassNameW(parent, cls_buf, 64)
        if cls_buf.value != "QWListViewer":
            return True
        # Section name is the QWListViewer window text
        user32.GetWindowTextW(parent, txt_buf, 256)
        section = txt_buf.value

        lb_rect = wt.RECT()
        user32.GetWindowRect(h, ctypes.byref(lb_rect))
        count = SM(h, 0x018B, 0, 0)  # LB_GETCOUNT
        for i in range(min(count, 100)):
            ir = wt.RECT()
            SM(h, 0x0198, i, ctypes.addressof(ir))  # LB_GETITEMRECT
            item_h = ir.bottom - ir.top
            if item_h <= 5:
                continue  # separator
            sx = lb_rect.left + (ir.left + ir.right) // 2
            sy = lb_rect.top + (ir.top + ir.bottom) // 2
            items.append({
                "section": section,
                "lb_hwnd": h,
                "item_index": i,
                "screen_x": sx,
                "screen_y": sy,
            })
        return True

    user32.EnumChildWindows(holder, EnumCB(_scan_lb), 0)
    return items


def _sidebar_dblclick(root_hwnd: int, screen_x: int, screen_y: int,
                      timeout: float = 6.0, *,
                      lb_hwnd: int = 0, item_index: int = -1,
                      pre_title: str = "",
                      bail_early: float = 0.8) -> str:
    """Double-click a sidebar item and wait for the title to stabilize.

    If ``lb_hwnd`` and ``item_index`` are provided, the item is scrolled
    into view first (via ``LB_SETTOPINDEX``) and its screen position is
    recalculated.

    If ``pre_title`` is given, the function uses a two-phase wait based
    on the **bracketed account name** in the title (e.g. ``[My Checking]``):

    Phase 1 (``bail_early`` seconds, default 0.8s):
        Poll at 100ms.  If the bracket content hasn't changed, bail out —
        the item is a non-account (header, total, separator).

    Phase 2 (up to ``timeout`` total):
        Bracket content is changing — wait for it to stabilize on a new
        account name.  If the title hasn't changed at all since the click,
        use a shortened Phase 2 (2.5s) to avoid wasting time on dead items.
        Dismiss modal dialogs once at start, not every poll.
    """
    import ctypes  # noqa: PLC0415
    import ctypes.wintypes as wt  # noqa: PLC0415
    import time as _time  # noqa: PLC0415

    user32 = ctypes.windll.user32

    def _bracket_name(t: str) -> str:
        if "[" in t and "]" in t:
            return t[t.rfind("[") + 1 : t.rfind("]")]
        return ""

    # Scroll the target item into view and recalculate coordinates
    if lb_hwnd and item_index >= 0 and _is_valid_hwnd(lb_hwnd):
        LB_SETTOPINDEX = 0x0197
        LB_GETITEMRECT = 0x0198
        _send_msg(lb_hwnd, LB_SETTOPINDEX, max(0, item_index - 1), 0)
        _time.sleep(0.05)
        ir = wt.RECT()
        _send_msg(lb_hwnd, LB_GETITEMRECT, item_index, ctypes.addressof(ir))
        pt = wt.POINT((ir.left + ir.right) // 2, (ir.top + ir.bottom) // 2)
        user32.ClientToScreen(lb_hwnd, ctypes.byref(pt))
        screen_x, screen_y = pt.x, pt.y

    user32.SetForegroundWindow(root_hwnd)
    _time.sleep(0.05)

    # Dismiss any pre-existing modal dialogs before clicking
    _dismiss_modal_dialogs(root_hwnd)

    user32.SetCursorPos(screen_x, screen_y)
    _time.sleep(0.03)
    for _ in range(2):
        user32.mouse_event(0x0002, 0, 0, 0, 0)
        user32.mouse_event(0x0004, 0, 0, 0, 0)
        _time.sleep(0.03)

    buf = ctypes.create_unicode_buffer(256)

    if not pre_title:
        _time.sleep(0.5)
        user32.GetWindowTextW(root_hwnd, buf, 256)
        return buf.value

    pre_bracket = _bracket_name(pre_title)

    # Phase 1 (bail_early seconds): poll for bracket change.
    bail_deadline = _time.monotonic() + bail_early
    while _time.monotonic() < bail_deadline:
        _time.sleep(0.1)
        user32.GetWindowTextW(root_hwnd, buf, 256)
        cur = buf.value
        cur_bracket = _bracket_name(cur)
        if cur_bracket and cur_bracket != pre_bracket:
            return cur  # new account opened

    if not pre_bracket:
        user32.GetWindowTextW(root_hwnd, buf, 256)
        return buf.value

    # Snapshot at end of Phase 1: is the title actively changing?
    user32.GetWindowTextW(root_hwnd, buf, 256)
    phase1_title = buf.value

    # Phase 2: wait for bracket to stabilize.
    # If the title hasn't changed at all since the click, the item is
    # likely a non-account (header/total/sub-view) — use a shortened
    # Phase 2 to catch slow accounts without wasting too much time.
    # If the title IS different, something is happening — use full timeout.
    if phase1_title == pre_title:
        phase2_budget = min(2.5, timeout - bail_early)
    else:
        phase2_budget = timeout - bail_early

    # Dismiss any modal that appeared during the click (once, not per-poll).
    if _dismiss_modal_dialogs(root_hwnd):
        _time.sleep(0.3)

    full_deadline = _time.monotonic() + phase2_budget
    while _time.monotonic() < full_deadline:
        _time.sleep(0.15)
        user32.GetWindowTextW(root_hwnd, buf, 256)
        cur = buf.value
        cur_bracket = _bracket_name(cur)
        if cur_bracket and cur_bracket != pre_bracket:
            return cur

    # One final modal dismiss — a dialog may have appeared during Phase 2
    # and blocked the account switch.
    if _dismiss_modal_dialogs(root_hwnd):
        _time.sleep(1.0)
        user32.GetWindowTextW(root_hwnd, buf, 256)
        cur_bracket = _bracket_name(buf.value)
        if cur_bracket and cur_bracket != pre_bracket:
            return buf.value

    # Final read
    user32.GetWindowTextW(root_hwnd, buf, 256)
    return buf.value


def list_sidebar_accounts(bridge: Any) -> list[dict[str, Any]]:
    """Return sidebar accounts with their names (discovered by clicking).

    Each entry has ``{"name", "section", "lb_hwnd", "item_index"}``.
    This is a one-time discovery that physically clicks each sidebar item,
    reads the resulting window title, then restores the original view.

    Only records a mapping when clicking an item actually changes the
    window title — headers, totals, and separators are skipped.
    """
    import time as _time  # noqa: PLC0415
    import ctypes  # noqa: PLC0415

    from server.process_manager import get_process_manager  # noqa: PLC0415
    pm = get_process_manager()
    if not pm.attached:
        raise TargetNotFoundError("Use select_window to attach first.")
    root = pm.attached.hwnd

    user32 = ctypes.windll.user32
    buf = ctypes.create_unicode_buffer(256)

    # Remember current view
    user32.GetWindowTextW(root, buf, 256)
    original_title = buf.value

    sidebar_items = _find_sidebar_accounts(root)
    results: list[dict[str, Any]] = []
    seen_names: set[str] = set()

    # Track the title BEFORE each click to detect actual changes
    prev_title = original_title

    for item in sidebar_items:
        # Read title immediately before this click to avoid attributing
        # a delayed title change from a previous click to this item.
        user32.GetWindowTextW(root, buf, 256)
        title_before = buf.value

        title = _sidebar_dblclick(
            root, item["screen_x"], item["screen_y"], timeout=6.0,
            lb_hwnd=item["lb_hwnd"], item_index=item["item_index"],
            pre_title=title_before,
        )

        # Compare bracket content (account name), not full title — the
        # prefix can change without the account actually switching.
        def _bracket(t: str) -> str:
            if "[" in t and "]" in t:
                return t[t.rfind("[") + 1 : t.rfind("]")]
            return ""

        name = _bracket(title)
        prev_name = _bracket(title_before)
        if not name or name == prev_name:
            continue  # header, total, or separator — no account opened
        # Skip section-header items (e.g. "Property & Debt" section opens
        # a summary view, not a real account register).  Use fuzzy match
        # so "Property & De" ≈ "Property & Debt" handles truncation too.
        if name and _acct_match(name, item["section"]):
            continue
        if name and name not in seen_names:
            seen_names.add(name)
            results.append({
                "name": name,
                "section": item["section"],
                "lb_hwnd": hex(item["lb_hwnd"]),
                "item_index": item["item_index"],
            })

    # Populate the module-level cache for navigate_to_account
    global _sidebar_cache  # noqa: PLW0603
    _sidebar_cache = list(results)

    return results


# Module-level sidebar cache: name → sidebar_item dict with screen coords
_sidebar_cache: list[dict[str, Any]] = []


def _sidebar_lookup(account_name: str) -> dict[str, Any] | None:
    """Find a cached sidebar entry matching account_name (fuzzy)."""
    for entry in _sidebar_cache:
        if _acct_match(entry["name"], account_name):
            return entry
    return None


def navigate_to_account(bridge: Any, account_name: str) -> dict[str, Any]:
    """
    Navigate to a specific account's register view.

    Strategy
    --------
    1. Check if a QWMDI tab already shows the account → bring it forward.
    2. Try the sidebar: double-click the matching ListBox item.
    3. Fall back to the All Transactions combo selector.

    Parameters
    ----------
    bridge
        A ``WinUIABridge`` instance.
    account_name : str
        Exact account name (case-insensitive).

    Returns
    -------
    dict
        ``{"ok": True, "account": str, "method": str}``
    """
    import ctypes  # noqa: PLC0415
    import ctypes.wintypes  # noqa: PLC0415
    import time  # noqa: PLC0415

    global _sidebar_cache  # noqa: PLW0603

    CB_SETCURSEL = 0x014E
    CB_GETCURSEL = 0x0147
    CBN_SELCHANGE = 1
    WM_COMMAND = 0x0111

    user32 = ctypes.windll.user32

    from server.process_manager import get_process_manager  # noqa: PLC0415
    pm = get_process_manager()
    if not pm.attached:
        raise TargetNotFoundError("Use select_window to attach first.")
    root_hwnd = pm.attached.hwnd
    if not _is_valid_hwnd(root_hwnd):
        raise TargetNotFoundError(
            "Quicken window is no longer valid. Use select_window to reattach."
        )

    EnumCB = ctypes.WINFUNCTYPE(
        ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
    )

    # ------------------------------------------------------------------
    # Phase 1: Check if there's already a QWMDI child showing this acct.
    # If so, bring it to front — no combo manipulation needed.
    # ------------------------------------------------------------------
    existing_mdi = None
    existing_idx = -1

    def _find_existing(h: int, _: int) -> bool:
        nonlocal existing_mdi, existing_idx
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(h, cls, 64)
        if cls.value != "QWMDI" or not user32.IsWindowVisible(h):
            return True
        # Match by QWMDI window title (works for account register tabs)
        mdi_title = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(h, mdi_title, 256)
        if _acct_match(mdi_title.value, account_name):
            existing_mdi = h
            return False
        # Also check first visible QWComboBox in this MDI
        def _check_combo(ch: int, _: int) -> bool:
            nonlocal existing_mdi, existing_idx
            cc = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(ch, cc, 64)
            if (cc.value.lower() == "qwcombobox"
                    and user32.IsWindowVisible(ch)):
                txt = _combo_cur_text(ch)
                if _acct_match(txt, account_name):
                    existing_mdi = h
                    existing_idx = _send_msg(ch, CB_GETCURSEL, 0, 0)
                    return False
            return True
        user32.EnumChildWindows(h, EnumCB(_check_combo), 0)
        if existing_mdi:
            return False
        return True

    user32.EnumChildWindows(root_hwnd, EnumCB(_find_existing), 0)

    if existing_mdi:
        user32.BringWindowToTop(existing_mdi)
        user32.SetFocus(existing_mdi)
        return {"ok": True, "account": account_name, "method": "existing_tab"}

    # ------------------------------------------------------------------
    # Phase 2: Try combo selector first (fast — no sidebar scan needed).
    # ------------------------------------------------------------------
    try:
        accounts = list_accounts(bridge)
    except UIAError:
        accounts = []
    match = next(
        (a for a in accounts if _acct_match(a["name"], account_name)),
        None,
    )
    if match and "combo_hwnd" in match:
        combo_h = int(match["combo_hwnd"], 16)
        idx = match["combo_index"]

        # Bring the MDI tab that owns this combo to the foreground so the
        # selection change actually activates the account view.
        _mdi = user32.GetParent(combo_h)
        while _mdi:
            _cls = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(_mdi, _cls, 64)
            if _cls.value == "QWMDI":
                user32.BringWindowToTop(_mdi)
                user32.SetFocus(_mdi)
                break
            _mdi = user32.GetParent(_mdi)

        _send_msg(combo_h, CB_SETCURSEL, idx, 0)
        parent = user32.GetParent(combo_h)
        ctrl_id = user32.GetDlgCtrlID(combo_h)
        wparam = (CBN_SELCHANGE << 16) | (ctrl_id & 0xFFFF)
        _send_msg(parent, WM_COMMAND, wparam, combo_h)
        time.sleep(0.8)

        # Verify the switch via window title bracket first.
        buf_v = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(root_hwnd, buf_v, 256)
        title = buf_v.value
        if "[" in title and "]" in title:
            opened = title[title.rfind("[") + 1 : title.rfind("]")]
            if _acct_match(opened, account_name):
                return {"ok": True, "account": opened, "method": "combo"}

        # For category-view combos (Property & Debt, etc.), the title
        # bracket won't change to the account name.  Verify by reading
        # the combo selection text instead.
        sel_text = _combo_cur_text(combo_h)
        if _acct_match(sel_text, account_name):
            return {"ok": True, "account": sel_text, "method": "combo_filter"}

    # ------------------------------------------------------------------
    # Phase 3: Sidebar — use cached entries if available.
    # Does NOT trigger a full sidebar scan (takes 200+ s).  Agents should
    # call list_sidebar_accounts explicitly if they need the full list.
    # ------------------------------------------------------------------
    cached = _sidebar_lookup(account_name) if _sidebar_cache else None

    def _try_sidebar_click(cached_entry: dict) -> dict | None:
        """Attempt to click the cached sidebar item, scrolling into view."""
        target_lb = int(cached_entry["lb_hwnd"], 16)
        target_idx = cached_entry["item_index"]
        cur_buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(root_hwnd, cur_buf, 256)
        title = _sidebar_dblclick(
            root_hwnd, 0, 0, timeout=4.0,
            lb_hwnd=target_lb, item_index=target_idx,
            pre_title=cur_buf.value,
        )
        if "[" in title and "]" in title:
            opened = title[title.rfind("[") + 1 : title.rfind("]")]
            if _acct_match(opened, account_name):
                return {"ok": True, "account": opened, "method": "sidebar"}
        return None

    if cached:
        result = _try_sidebar_click(cached)
        if result:
            return result
        # Sidebar click failed — invalidate cache
        _sidebar_cache = []

    raise UIAError(
        f"Account {account_name!r} not found via combo or sidebar cache.  "
        f"Make sure the account has an open tab, or use list_sidebar_accounts "
        f"to populate the sidebar cache first.",
        code="ACCOUNT_NOT_FOUND",
    )


def read_register_state(bridge: Any) -> dict[str, Any]:
    """
    Return the current state of the visible transaction register.

    Reads balance, transaction count, current account, and whether a
    reconcile is in progress — all from Static/ComboBox controls, without
    requiring access to owner-drawn rows.

    Parameters
    ----------
    bridge
        A ``WinUIABridge`` instance.

    Returns
    -------
    dict
        ``{"ok": True, "account": str, "total": str, "count": str,
           "reconcile_active": bool, "filter_text": str}``
    """
    import ctypes  # noqa: PLC0415
    import ctypes.wintypes  # noqa: PLC0415

    WM_GETTEXT = 0x000D
    WM_GETTEXTLENGTH = 0x000E

    user32 = ctypes.windll.user32

    from server.process_manager import get_process_manager  # noqa: PLC0415
    pm = get_process_manager()
    if not pm.attached:
        raise TargetNotFoundError("Use select_window to attach first.")

    root_hwnd = pm.attached.hwnd
    if not _is_valid_hwnd(root_hwnd):
        raise TargetNotFoundError(
            "Quicken window is no longer valid. Use select_window to reattach."
        )

    def _read_text(h: int) -> str:
        tlen = _send_msg(h, WM_GETTEXTLENGTH, 0, 0)
        if tlen <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(tlen + 1)
        _send_msg(h, WM_GETTEXT, len(buf), ctypes.addressof(buf))
        return buf.value

    # Find the active QWMDI and scope child enumeration to it.
    mdi_h = _find_active_mdi(root_hwnd)
    if mdi_h is None:
        raise UIAError("No active QWMDI found.", code="REGISTER_NOT_FOUND")

    mdi_children: list[tuple[int, str, str]] = []
    EnumCB = ctypes.WINFUNCTYPE(
        ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
    )

    def _cb(h: int, _: int) -> bool:
        cls_buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(h, cls_buf, 256)
        t = _read_text(h)
        mdi_children.append((h, cls_buf.value.lower(), t))
        return True

    user32.EnumChildWindows(mdi_h, EnumCB(_cb), 0)

    # Find the TxList within this MDI (absent in investment/portfolio views)
    txlist_h = next(
        (h for h, c, _ in mdi_children
         if c == "qwclass_transactionlist" and user32.IsWindowVisible(h)),
        None,
    )

    # Detect investment/portfolio views (ListBox + QWHtmlView, no TxList)
    has_html = any(c == "qwhtmlview" for _, c, _ in mdi_children)
    view_type = "register" if txlist_h else ("investment" if has_html else "unknown")

    if txlist_h is None:
        # Return partial state for investment/non-register views
        mdi_title = _read_text(mdi_h)
        return {
            "ok": True,
            "account": mdi_title if mdi_title else "",
            "total": "",
            "count": "",
            "reconcile_active": False,
            "filter_text": "",
            "view_type": view_type,
            "note": "This account uses an investment/portfolio view without "
                    "a transaction register. Use uia_read_display for content.",
        }

    # Total/balance — Static with numeric content (ignore "Total:" label)
    def _looks_numeric(s: str) -> bool:
        s2 = s.replace(",", "").replace(".", "").replace("-", "").strip()
        return bool(s2) and s2.isdigit()

    balance_static = next(
        (t for h, c, t in mdi_children
         if c == "static" and _looks_numeric(t) and user32.IsWindowVisible(h)),
        "",
    )

    # Transaction count — Static like "N Transaction(s)"
    count_static = next(
        (t for h, c, t in mdi_children
         if c == "static" and "transaction" in t.lower()
         and user32.IsWindowVisible(h)),
        "",
    )

    # Account name — prefer the QWMDI window title (present in account
    # register views like "My Checking"), fall back to the first visible
    # QWComboBox selection (works in Spending / All Transactions tab).
    mdi_title = _read_text(mdi_h)
    acct_combo_h = next(
        (h for h, c, t in mdi_children
         if c == "qwcombobox" and user32.IsWindowVisible(h)), None
    )
    if mdi_title and mdi_title.lower() not in ("home", "dashboard", ""):
        current_account = mdi_title
    elif acct_combo_h:
        current_account = _combo_cur_text(acct_combo_h)
    else:
        current_account = ""

    # Reconcile active? "C" button and "Reset" button both visible in header
    c_btn_visible = any(
        h for h, c, t in mdi_children
        if c == "qc_button" and t == "C" and user32.IsWindowVisible(h)
    )
    reset_visible = any(
        h for h, c, t in mdi_children
        if c == "qc_button" and t == "Reset" and user32.IsWindowVisible(h)
    )
    reconcile_active = c_btn_visible and reset_visible

    # Filter text (Edit control in reconcile header, y < TxList.top)
    txlist_rect = ctypes.wintypes.RECT()
    user32.GetWindowRect(txlist_h, ctypes.byref(txlist_rect))
    filter_text = ""
    for h, c, t in mdi_children:
        if c != "edit" or not user32.IsWindowVisible(h):
            continue
        r = ctypes.wintypes.RECT()
        user32.GetWindowRect(h, ctypes.byref(r))
        if r.bottom <= txlist_rect.top:
            filter_text = _read_text(h)
            break

    return {
        "ok": True,
        "account": current_account,
        "total": balance_static,
        "count": count_static,
        "reconcile_active": reconcile_active,
        "filter_text": filter_text,
        "view_type": "register",
    }


def _find_active_mdi(root_hwnd: int) -> int | None:
    """Find the active QWMDI child window.

    Quicken may have multiple QWMDI children open (one per account tab).
    The active one is identified by matching the bracketed account name in
    the root window title, e.g. ``[My Savings]``.  Falls back to the
    largest visible QWMDI if no bracket match is found.
    """
    import ctypes  # noqa: PLC0415
    import ctypes.wintypes  # noqa: PLC0415
    import re  # noqa: PLC0415

    user32 = ctypes.windll.user32

    # Extract account name from root title brackets
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(root_hwnd, buf, 512)
    title = buf.value
    m = re.search(r"\[(.+?)\]\s*$", title)
    target_name = m.group(1).lower().strip() if m else ""

    best_match: int | None = None
    best_area: int = 0
    name_match: int | None = None

    WNDENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
    )

    def _cb(h: int, _: int) -> bool:
        nonlocal best_match, best_area, name_match
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(h, cls, 64)
        if cls.value.upper() != "QWMDI":
            return True
        if not user32.IsWindowVisible(h):
            return True
        user32.GetWindowTextW(h, buf, 512)
        mdi_title = buf.value.strip()
        r = ctypes.wintypes.RECT()
        user32.GetWindowRect(h, ctypes.byref(r))
        area = (r.right - r.left) * (r.bottom - r.top)

        if target_name and mdi_title.lower().strip() == target_name:
            name_match = h
        if area > best_area:
            best_area = area
            best_match = h
        return True

    user32.EnumChildWindows(root_hwnd, WNDENUMPROC(_cb), 0)
    return name_match or best_match


def _find_txlist_hwnd(root_hwnd: int) -> int | None:
    """Find the main (largest, visible) QWClass_TransactionList child.

    Restricts the search to the active QWMDI child to avoid picking up
    TxList controls from background account tabs.
    """
    import ctypes  # noqa: PLC0415
    import ctypes.wintypes  # noqa: PLC0415

    user32 = ctypes.windll.user32

    # Scope to active MDI child if possible
    search_root = _find_active_mdi(root_hwnd) or root_hwnd

    best: tuple[int, int] | None = None

    def _cb(h: int, _: int) -> bool:
        nonlocal best
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(h, cls, 64)
        if "TransactionList" in cls.value and user32.IsWindowVisible(h):
            r = ctypes.wintypes.RECT()
            user32.GetWindowRect(h, ctypes.byref(r))
            area = (r.right - r.left) * (r.bottom - r.top)
            if best is None or area > best[1]:
                best = (h, area)
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
    )
    user32.EnumChildWindows(search_root, WNDENUMPROC(_cb), 0)
    return best[0] if best else None


def read_register_rows(
    bridge: Any,
    max_rows: int = 50,
) -> dict[str, Any]:
    """
    Read individual transaction rows from the visible register.

    Works across different register layouts (Checking, Savings, Money Market,
    etc.) by:
    1. Clicking into the TxList to ensure keyboard focus is in the register
    2. Using Ctrl+Home then Down to skip the new-transaction entry row
    3. Using HWND-based field classification (text vs. numeric QREdit)
    4. Adaptive payee/category identification by field count

    Parameters
    ----------
    bridge
        A ``WinUIABridge`` instance (unused directly but ensures attachment).
    max_rows : int
        Maximum number of rows to read (default 50).

    Returns
    -------
    dict
        ``{"ok": True, "rows": [...], "count": int}`` where each row is
        ``{"date", "payee", "category", "payment", "deposit"}``.
    """
    import ctypes  # noqa: PLC0415
    import ctypes.wintypes  # noqa: PLC0415
    import time as _time  # noqa: PLC0415

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    _SendMsg = user32.SendMessageW
    _SendMsg.argtypes = [
        ctypes.wintypes.HWND, ctypes.c_uint,
        ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM,
    ]
    _SendMsg.restype = ctypes.wintypes.LPARAM
    _PostMsg = user32.PostMessageW
    _PostMsg.argtypes = [
        ctypes.wintypes.HWND, ctypes.c_uint,
        ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM,
    ]

    from server.process_manager import get_process_manager  # noqa: PLC0415
    pm = get_process_manager()
    if not pm.attached:
        raise TargetNotFoundError("Use select_window to attach first.")

    root_hwnd = pm.attached.hwnd
    if not _is_valid_hwnd(root_hwnd):
        raise TargetNotFoundError(
            "Quicken window is no longer valid. Use select_window to reattach."
        )
    user32.SetForegroundWindow(root_hwnd)
    _time.sleep(0.3)

    # Overall timeout guard — prevent infinite loops if keyboard nav breaks
    deadline = _time.monotonic() + 60  # 60 second hard limit

    # --- Ensure keyboard focus is inside the register, not the sidebar ---
    txlist_h = _find_txlist_hwnd(root_hwnd)
    if txlist_h is None:
        return {
            "ok": False,
            "error": "No transaction register found in this view. "
                     "Investment/portfolio accounts use a different layout. "
                     "Use read_register_state to check the view_type first.",
            "code": "REGISTER_NOT_FOUND",
            "rows": [],
            "count": 0,
        }
    txlist_rect = ctypes.wintypes.RECT()
    user32.GetWindowRect(txlist_h, ctypes.byref(txlist_rect))
    # Click near the vertical center of the TxList body.  The top
    # ~40-60 px is column headers that don't accept keyboard focus,
    # so using the center avoids that dead zone reliably.
    click_x = (txlist_rect.left + txlist_rect.right) // 2
    click_y = (txlist_rect.top + txlist_rect.bottom) // 2
    user32.SetCursorPos(click_x, click_y)
    _time.sleep(0.1)
    user32.mouse_event(0x0002, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTDOWN
    user32.mouse_event(0x0004, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTUP
    _time.sleep(0.4)

    tid = kernel32.GetCurrentThreadId()
    qtid = user32.GetWindowThreadProcessId(root_hwnd, None)
    user32.AttachThreadInput(tid, qtid, True)

    def _kbe(vk: int, flags: int = 0) -> None:
        user32.keybd_event(vk, 0, flags, 0)

    def _press(vk: int, delay: float = 0.1) -> None:
        _kbe(vk)
        _time.sleep(0.02)
        _kbe(vk, 2)
        _time.sleep(delay)

    def _ctrl(vk: int) -> None:
        _kbe(0x11)
        _time.sleep(0.02)
        _press(vk, 0.05)
        _kbe(0x11, 2)
        _time.sleep(0.3)

    def _get_focused() -> tuple[int, str, str, int, int, int]:
        """Return (hwnd, class_name, text, x_left, y_top, width)."""
        h = user32.GetFocus()
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(h, cls, 64)
        r = ctypes.wintypes.RECT()
        user32.GetWindowRect(h, ctypes.byref(r))
        tlen = _SendMsg(h, 0x000E, 0, 0)  # WM_GETTEXTLENGTH
        buf = ctypes.create_unicode_buffer(tlen + 1)
        _SendMsg(h, 0x000D, tlen + 1, ctypes.addressof(buf))  # WM_GETTEXT
        return h, cls.value, buf.value, r.left, r.top, r.right - r.left

    def _tab() -> None:
        h = user32.GetFocus()
        _PostMsg(h, 0x0100, 0x09, 0)
        _time.sleep(0.02)
        _PostMsg(h, 0x0101, 0x09, 0)
        _time.sleep(0.35)

    # Column x-positions learned from the blank new-transaction row.
    # The blank row shows ALL money columns (none skipped), so we can
    # record their tab-order x-positions as a reference for data rows.
    # Tab order for money fields is always: debit → credit → balance.
    _money_col_xs: list[int] = []  # [debit_x, credit_x, balance_x]

    def _learn_columns_from_row() -> list[tuple[int, str]]:
        """Tab through the current row, collecting (x, value) for money
        fields.  Returns the money_fields list in tab order.
        Also populates ``_money_col_xs`` if three money fields are found
        (indicating a blank row with all columns visible)."""
        date_h, date_cls, date_txt, _dx, _dy, _dw = _get_focused()
        if date_cls.lower() != "qredit":
            return []
        date_hwnd = date_h

        text_fields: list[tuple[int, str]] = []
        money_fields: list[tuple[int, str]] = []

        for _fi in range(14):
            _tab()
            h, cls_name, txt, x, _y, _w = _get_focused()
            if cls_name.lower() != "qredit":
                break
            if h == date_hwnd:
                if money_fields:
                    break
                text_fields.append((x, txt))
            else:
                money_fields.append((x, txt))

        has_content = (
            any(v.strip() for _, v in text_fields)
            or any(v.strip() for _, v in money_fields)
        )

        # If all fields are empty → blank new-transaction row.
        # Record all money column x-positions as the reference layout.
        if not has_content and len(money_fields) >= 3:
            _money_col_xs.clear()
            _money_col_xs.extend(x for x, _ in money_fields)

        return money_fields if has_content else []

    def _read_one_row() -> dict[str, str] | None:
        """Read all fields of the current row.  Returns None if on a blank row.

        Uses x-position of each field to determine its column role.
        Text fields share one HWND; money fields share a different HWND.
        Quicken skips empty money columns in tab order, so we match
        x-positions against the reference layout learned from the blank
        new-transaction row.

        Money field semantics:
          payment = debit / outflow (checking withdrawal, credit charge)
          deposit = credit / inflow (checking deposit, card payment)
          balance = running total (always last in tab order)
        """
        date_h, date_cls, date_txt, _dx, _dy, _dw = _get_focused()
        if date_cls.lower() != "qredit":
            return None
        date_hwnd = date_h

        # Collect (x_position, value) for text and money fields
        text_fields: list[tuple[int, str]] = []  # (x, value)
        money_fields: list[tuple[int, str]] = []  # tab order preserved

        for _fi in range(14):
            _tab()
            h, cls_name, txt, x, _y, _w = _get_focused()
            if cls_name.lower() != "qredit":
                break
            if h == date_hwnd:
                if money_fields:
                    break
                text_fields.append((x, txt))
            else:
                money_fields.append((x, txt))

        # Blank new-transaction row detection
        has_content = (
            any(v.strip() for _, v in text_fields)
            or any(v.strip() for _, v in money_fields)
        )
        if not text_fields or not has_content:
            return None

        # Sort text fields by x for consistent left-to-right visual ordering
        text_fields.sort(key=lambda t: t[0])

        # --- Text field identification (x-sorted) ---
        # Visual column order left-to-right is always:
        #   Payee/Description | Check# | Category | Memo
        payee = text_fields[0][1] if text_fields else ""
        category = ""
        check_num = ""
        memo = ""
        if len(text_fields) >= 4:
            check_num = text_fields[1][1]
            category = text_fields[2][1]
            memo = text_fields[3][1]
        elif len(text_fields) == 3:
            check_num = text_fields[1][1]
            category = text_fields[2][1]
        elif len(text_fields) == 2:
            category = text_fields[1][1]

        # --- Money field identification ---
        # The last money field in tab order is ALWAYS Balance.
        # When the reference layout is available (from the blank row),
        # match each field's x-position (tolerance ±40px) to identify
        # the column.  Otherwise fall back to tab-order position.
        payment = ""
        deposit = ""
        balance = ""

        if len(money_fields) >= 3:
            # All three columns present
            payment = money_fields[0][1]
            deposit = money_fields[1][1]
            balance = money_fields[2][1]
        elif len(money_fields) == 2:
            # Last in tab order = Balance
            balance = money_fields[1][1]
            val = money_fields[0][1]
            val_x = money_fields[0][0]

            if len(_money_col_xs) >= 3:
                # Match val_x to learned debit_x or credit_x
                debit_x, credit_x = _money_col_xs[0], _money_col_xs[1]
                if abs(val_x - debit_x) < abs(val_x - credit_x):
                    payment = val
                else:
                    deposit = val
            else:
                # Fallback: use gap heuristic (works for banking regs)
                bal_x = money_fields[1][0]
                if bal_x - val_x > 150:
                    payment = val
                else:
                    deposit = val
        elif len(money_fields) == 1:
            balance = money_fields[0][1]

        return {
            "date": date_txt,
            "payee": payee,
            "check_num": check_num,
            "category": category,
            "memo": memo,
            "payment": payment,
            "deposit": deposit,
            "balance": balance,
        }

    try:
        # Clear any edit/selection state
        for _ in range(3):
            _press(0x1B, 0.15)  # Escape

        # Try to get expected row count BEFORE keyboard nav (no focus change)
        expected_count: int | None = None
        try:
            state = read_register_state(bridge)
            count_str = state.get("count", "")
            parts = count_str.split()
            if parts and parts[0].isdigit():
                expected_count = int(parts[0])
        except Exception:
            pass

        effective_max = min(max_rows, expected_count) if expected_count else max_rows

        # Ctrl+Home → first row (typically the new-transaction entry row)
        _ctrl(0x24)
        _time.sleep(0.5)

        # Attempt to read the first row.  If it's the blank new-transaction
        # row we use it to learn the full column layout (all money columns
        # visible), then skip to the first real data row.
        first_row = _read_one_row()
        if first_row is None:
            # Blank row — learn column layout if not already populated
            if not _money_col_xs:
                # Re-read this blank row using the learning helper.
                # Escape back to date field first, then learn.
                _press(0x1B, 0.2)
                _learn_columns_from_row()
            _press(0x1B, 0.2)
            _press(0x28, 0.3)  # Down → first data row
            _time.sleep(0.3)

        prev_row_sig: tuple[str, ...] | None = None
        dup_count = 0
        rows: list[dict[str, str]] = []

        # If first_row was already valid, include it
        start_offset = 0
        if first_row is not None:
            rows.append(first_row)
            prev_row_sig = (
                first_row["date"], first_row["payee"],
                first_row["category"], first_row["payment"],
                first_row["deposit"],
            )
            start_offset = 1

        for _ in range(effective_max - start_offset):
            if _time.monotonic() > deadline:
                break  # hard timeout
            row = _read_one_row()
            if row is None:
                break

            # Content-based stuck detection — always check to avoid
            # infinite looping when keyboard nav doesn't advance the row.
            row_sig = (
                row["date"], row["payee"], row["category"],
                row["payment"], row["deposit"],
            )
            if row_sig == prev_row_sig:
                dup_count += 1
                if dup_count >= 2:
                    break
            else:
                dup_count = 0
            prev_row_sig = row_sig

            rows.append(row)

            # Move to next row
            _press(0x1B, 0.2)
            _press(0x28, 0.3)
            _time.sleep(0.3)

        return {"ok": True, "rows": rows, "count": len(rows)}
    finally:
        user32.AttachThreadInput(tid, qtid, False)


def set_register_filter(bridge: Any, text: str) -> dict[str, Any]:
    """
    Type *text* into the register search/filter box and return the
    resulting transaction count.

    Parameters
    ----------
    bridge
        A ``WinUIABridge`` instance.
    text : str
        Search term.  Pass ``""`` to clear.

    Returns
    -------
    dict
        ``{"ok": True, "filter": str, "count": str}``
    """
    import ctypes  # noqa: PLC0415
    import ctypes.wintypes  # noqa: PLC0415
    import time  # noqa: PLC0415

    WM_SETTEXT = 0x000C
    WM_GETTEXT = 0x000D
    WM_GETTEXTLENGTH = 0x000E

    user32 = ctypes.windll.user32

    from server.process_manager import get_process_manager  # noqa: PLC0415
    pm = get_process_manager()
    if not pm.attached:
        raise TargetNotFoundError("Use select_window to attach first.")

    root_hwnd = pm.attached.hwnd

    def _read_text(h: int) -> str:
        tlen = _send_msg(h, WM_GETTEXTLENGTH, 0, 0)
        if tlen <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(tlen + 1)
        _send_msg(h, WM_GETTEXT, len(buf), ctypes.addressof(buf))
        return buf.value

    # Find TxList and its parent QWMDI
    all_ctrls: list[tuple[int, str, str]] = []
    EnumCB = ctypes.WINFUNCTYPE(
        ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
    )

    def _cb(h: int, _: int) -> bool:
        cls_buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(h, cls_buf, 256)
        all_ctrls.append((h, cls_buf.value.lower(), _read_text(h)))
        return True

    user32.EnumChildWindows(root_hwnd, EnumCB(_cb), 0)

    txlist_h = next(
        (h for h, c, _ in all_ctrls if c == "qwclass_transactionlist"
         and user32.IsWindowVisible(h)), None
    )
    if txlist_h is None:
        raise UIAError("No visible TxList found.", code="REGISTER_NOT_FOUND")

    txlist_rect = ctypes.wintypes.RECT()
    user32.GetWindowRect(txlist_h, ctypes.byref(txlist_rect))

    mdi_h = user32.GetParent(txlist_h)
    mdi_children: list[tuple[int, str, str]] = []

    def _cb2(h: int, _: int) -> bool:
        cls_buf2 = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(h, cls_buf2, 256)
        mdi_children.append((h, cls_buf2.value.lower(), _read_text(h)))
        return True

    user32.EnumChildWindows(mdi_h, EnumCB(_cb2), 0)

    # Find filter Edit above TxList
    filter_h: int | None = None
    for h, c, _ in mdi_children:
        if c != "edit" or not user32.IsWindowVisible(h):
            continue
        r = ctypes.wintypes.RECT()
        user32.GetWindowRect(h, ctypes.byref(r))
        if r.bottom <= txlist_rect.top:
            filter_h = h
            break

    if filter_h is None:
        raise UIAError(
            "No filter Edit found above TxList.",
            code="FILTER_EDIT_NOT_FOUND",
        )

    # Write text via WM_SETTEXT and trigger change notification
    buf = ctypes.create_unicode_buffer(text)
    _send_msg(filter_h, WM_SETTEXT, 0, ctypes.addressof(buf))
    # Post EN_CHANGE to parent so Quicken re-filters
    WM_COMMAND = 0x0111
    EN_CHANGE = 0x0300
    ctrl_id = user32.GetDlgCtrlID(filter_h)
    parent = user32.GetParent(filter_h)
    user32.PostMessageW(parent, WM_COMMAND, (EN_CHANGE << 16) | ctrl_id, filter_h)
    time.sleep(0.4)

    # Read updated count
    count_static = next(
        (t for h, c, t in mdi_children
         if c == "static" and "transaction" in t.lower()
         and user32.IsWindowVisible(h)),
        "",
    )
    # Refresh count (values change after filter)
    if count_static == "" and mdi_h:
        for h, c, _ in mdi_children:
            if c == "static" and user32.IsWindowVisible(h):
                t2 = _read_text(h)
                if "transaction" in t2.lower():
                    count_static = t2
                    break

    return {"ok": True, "filter": text, "count": count_static}


def open_reconcile(
    bridge: Any,
    account_name: str,
    statement_date: str,
    ending_balance: str,
    service_charge: str = "",
    service_date: str = "",
    interest_earned: str = "",
    interest_date: str = "",
    timeout_ms: int = 5000,
) -> dict[str, Any]:
    """Open the Quicken reconcile dialog and enter statement details.

    Sends WM_COMMAND 7203 to QFRAME to open the "Choose Reconcile Account"
    dialog, selects *account_name*, clicks OK, fills in the statement date
    and ending balance in the "Reconcile Details" dialog, then clicks OK to
    begin reconciliation.

    Parameters
    ----------
    bridge
        A ``WinUIABridge`` instance.
    account_name
        Account to reconcile.
    statement_date
        Statement end date (e.g. "03/31/2026").
    ending_balance
        Statement ending balance (e.g. "1,234.00").
    service_charge, service_date, interest_earned, interest_date
        Optional bank-charge and interest fields.
    timeout_ms
        Max wait (ms) for each dialog to appear.

    Returns
    -------
    dict
        ``{"ok": True, "account": str, "statement_date": str,
           "ending_balance": str}``
    """
    import ctypes  # noqa: PLC0415
    import ctypes.wintypes  # noqa: PLC0415
    import time  # noqa: PLC0415

    user32 = ctypes.windll.user32

    from server.process_manager import get_process_manager  # noqa: PLC0415
    pm = get_process_manager()
    if not pm.attached:
        raise TargetNotFoundError("Use select_window to attach first.")

    WM_COMMAND = 0x0111
    WM_GETTEXT = 0x000D
    WM_SETTEXT = 0x000C
    WM_GETTEXTLENGTH = 0x000E
    CB_GETCOUNT = 0x0146
    CB_GETLBTEXT = 0x0148
    CB_GETLBTEXTLEN = 0x0149
    CB_SETCURSEL = 0x014E

    root_hwnd = pm.attached.hwnd

    def _read_text(h: int) -> str:
        tlen = _send_msg(h, WM_GETTEXTLENGTH, 0, 0)
        if tlen <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(tlen + 1)
        _send_msg(h, WM_GETTEXT, len(buf), ctypes.addressof(buf))
        return buf.value

    def _click_qc_button(h: int) -> None:
        """Click a QC_button via WM_LBUTTONDOWN/WM_LBUTTONUP."""
        rc = ctypes.wintypes.RECT()
        user32.GetClientRect(h, ctypes.byref(rc))
        cx = (rc.right - rc.left) // 2
        cy = (rc.bottom - rc.top) // 2
        lp = ctypes.c_long((cy << 16) | (cx & 0xFFFF)).value
        user32.SetFocus(h)
        _send_msg(h, 0x0201, 1, lp)  # WM_LBUTTONDOWN
        _send_msg(h, 0x0202, 0, lp)  # WM_LBUTTONUP

    def _wait_for_dialog(title_substr: str, timeout: float) -> int | None:
        """Poll until a top-level dialog with matching title appears."""
        buf = ctypes.create_unicode_buffer(256)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            found: list[int] = []

            def _enum(h: int, _: int) -> bool:
                if user32.IsWindowVisible(h):
                    user32.GetWindowTextW(h, buf, 256)
                    if title_substr.lower() in buf.value.lower():
                        found.append(h)
                return True

            cb = ctypes.WINFUNCTYPE(
                ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
            )(_enum)
            user32.EnumWindows(cb, 0)
            if found:
                return found[0]
            time.sleep(0.15)
        return None

    def _get_dialog_children(dlg_hwnd: int) -> list[tuple[int, str, str]]:
        buf = ctypes.create_unicode_buffer(256)
        items: list[tuple[int, str, str]] = []

        def _ec(h: int, _: int) -> bool:
            cls = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(h, cls, 64)
            user32.GetWindowTextW(h, buf, 256)
            items.append((h, cls.value.lower(), buf.value))
            return True

        cb = ctypes.WINFUNCTYPE(
            ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
        )(_ec)
        user32.EnumChildWindows(dlg_hwnd, cb, 0)
        return items

    timeout_s = timeout_ms / 1000.0

    # Step 1: send WM_COMMAND 7203 to open Choose Reconcile Account
    user32.PostMessageW(root_hwnd, WM_COMMAND, 7203, 0)

    # Wait for either the Choose Account dialog OR the "no items" notification.
    acct_dlg: int | None = None
    already_done: bool = False
    buf_tmp = ctypes.create_unicode_buffer(512)
    deadline_step1 = time.monotonic() + timeout_s
    while time.monotonic() < deadline_step1:
        hits: list[int] = []

        def _poll(h: int, _: int) -> bool:
            if user32.IsWindowVisible(h):
                user32.GetWindowTextW(h, buf_tmp, 512)
                t_low = buf_tmp.value.lower()
                if "choose reconcile account" in t_low:
                    hits.append(h)
                elif "no uncleared" in t_low or "nothing to reconcile" in t_low:
                    hits.append(-(h))  # negative = already-done dialog
            return True

        cb_poll = ctypes.WINFUNCTYPE(
            ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
        )(_poll)
        user32.EnumWindows(cb_poll, 0)
        for hit in hits:
            if hit > 0:
                acct_dlg = hit
                break
            else:
                already_done = True
                ok_dismiss = next(
                    (h for h, c, t in _get_dialog_children(-hit)
                     if c == "qc_button" and t == "OK"),
                    None,
                )
                if ok_dismiss:
                    _click_qc_button(ok_dismiss)
                break
        if acct_dlg or already_done:
            break
        time.sleep(0.15)

    if already_done:
        return {
            "ok": True,
            "account": account_name,
            "statement_date": statement_date,
            "ending_balance": ending_balance,
            "note": "Reconcile already active or no uncleared items.",
        }

    if acct_dlg is None:
        raise UIAError(
            "Choose Reconcile Account dialog did not appear.",
            code="DIALOG_NOT_FOUND",
        )

    children = _get_dialog_children(acct_dlg)

    # Find the combo and select the account.
    combo_h = next((h for h, c, _ in children if c == "qwcombobox"), None)
    if combo_h is None:
        raise UIAError("Account combo not found in reconcile dialog.", code="ELEMENT_NOT_FOUND")

    count = _send_msg(combo_h, CB_GETCOUNT, 0, 0)
    target_idx: int | None = None
    for i in range(count):
        tlen = _send_msg(combo_h, CB_GETLBTEXTLEN, i, 0)
        tbuf = ctypes.create_unicode_buffer(tlen + 2)
        _send_msg(combo_h, CB_GETLBTEXT, i, ctypes.addressof(tbuf))
        if tbuf.value.strip().lower() == account_name.strip().lower():
            target_idx = i
            break

    if target_idx is None:
        cancel_h = next((h for h, c, t in children if c == "qc_button" and t == "Cancel"), None)
        if cancel_h:
            _click_qc_button(cancel_h)
        raise UIAError(
            f"Account '{account_name}' not found in reconcile combo.",
            code="ACCOUNT_NOT_FOUND",
        )

    _send_msg(combo_h, CB_SETCURSEL, target_idx, 0)

    # Click OK.
    ok_h = next((h for h, c, t in children if c == "qc_button" and t == "OK"), None)
    if ok_h is None:
        raise UIAError("OK button not found in account selection dialog.", code="ELEMENT_NOT_FOUND")
    _click_qc_button(ok_h)

    # Step 2: wait for Reconcile Details dialog
    det_dlg = _wait_for_dialog("Reconcile Details", timeout_s)
    if det_dlg is None:
        raise UIAError(
            "Reconcile Details dialog did not appear after account selection.",
            code="DIALOG_NOT_FOUND",
        )

    det_children = _get_dialog_children(det_dlg)
    edit_handles = [h for h, c, _ in det_children if c == "edit"]
    if len(edit_handles) < 3:
        raise UIAError(
            f"Expected ≥3 Edit fields in Reconcile Details, got {len(edit_handles)}.",
            code="UNEXPECTED_LAYOUT",
        )

    def _set_edit(h: int, text: str) -> None:
        if not text:
            return
        user32.SetFocus(h)
        tbuf = ctypes.create_unicode_buffer(text)
        _send_msg(h, WM_SETTEXT, 0, ctypes.addressof(tbuf))

    _set_edit(edit_handles[0], statement_date)   # Ending statement date
    _set_edit(edit_handles[2], ending_balance)   # Ending balance
    if service_charge:
        _set_edit(edit_handles[3], service_charge)
    if service_date:
        _set_edit(edit_handles[4], service_date)
    if interest_earned:
        _set_edit(edit_handles[6], interest_earned)
    if interest_date:
        _set_edit(edit_handles[7], interest_date)

    # Click OK to begin reconciliation.
    det_ok_h = next(
        (h for h, c, t in det_children if c == "qc_button" and t == "OK"), None
    )
    if det_ok_h is None:
        raise UIAError("OK button not found in Reconcile Details dialog.", code="ELEMENT_NOT_FOUND")
    _click_qc_button(det_ok_h)

    time.sleep(0.5)

    return {
        "ok": True,
        "account": account_name,
        "statement_date": statement_date,
        "ending_balance": ending_balance,
    }
