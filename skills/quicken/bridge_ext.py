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

    Deliberately broad — callers like ``_sidebar_lookup`` use
    shortest-candidate-first ranking to pick the best match when
    multiple candidates qualify.
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

    Looks for top-level ``QWinDlg``, ``QWinPopup``, or ``#32770`` windows
    owned by *root_hwnd* and sends them a dismiss command.

    Strategy:
      1. Click a visible dismiss button (Done, Close, OK, Cancel, Yes, …)
      2. If no button found, send ``WM_CLOSE`` as fallback.

    Returns True if a dialog was dismissed.
    """
    import ctypes  # noqa: PLC0415
    import time as _time  # noqa: PLC0415

    user32 = ctypes.windll.user32
    WM_COMMAND = 0x0111
    WM_CLOSE = 0x0010
    dismissed = False

    _DIALOG_CLASSES = {"QWinDlg", "QWinPopup", "#32770"}
    _DISMISS_LABELS = frozenset({
        "done", "close", "ok", "cancel", "yes", "dismiss",
        "continue", "accept", "no thanks", "no", "ignore", "skip",
        "not now", "later", "remind me later",
    })

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
        if cls.value in _DIALOG_CLASSES:
            dialogs.append(h)
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p,
                                     ctypes.POINTER(ctypes.c_int))
    user32.EnumWindows(WNDENUMPROC(_enum_cb), None)

    for dlg in dialogs:
        # Find a dismiss button
        dismiss_btn = 0
        def _btn_cb(h: int, _: Any) -> bool:
            nonlocal dismiss_btn
            if not user32.IsWindowVisible(h):
                return True
            buf = ctypes.create_unicode_buffer(64)
            user32.GetWindowTextW(h, buf, 64)
            # Strip Windows accelerator prefix (&) for comparison
            text = buf.value.strip().replace("&", "").lower()
            if text in _DISMISS_LABELS:
                dismiss_btn = h
                return False  # stop enumeration
            return True

        user32.EnumChildWindows(dlg, WNDENUMPROC(_btn_cb), None)
        if dismiss_btn:
            ctrl_id = user32.GetDlgCtrlID(dismiss_btn)
            user32.PostMessageW(dlg, WM_COMMAND, ctrl_id, dismiss_btn)
            _time.sleep(0.5)
            dismissed = True
        else:
            # No recognized button — try WM_CLOSE as fallback
            user32.PostMessageW(dlg, WM_CLOSE, 0, 0)
            _time.sleep(0.5)
            if not user32.IsWindowVisible(dlg):
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
        if cls.value == "QWMDI" and user32.IsWindowVisible(h):
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

    # Dismiss any modal dialogs before scanning
    _dismiss_modal_dialogs(root)

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

def _find_sidebar_holder(root_hwnd: int) -> int:
    """Find the QWAcctBarHolder sidebar container window handle."""
    import ctypes  # noqa: PLC0415
    user32 = ctypes.windll.user32
    EnumCB = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND,
                                ctypes.wintypes.LPARAM)
    holder = 0

    def _cb(h: int, _: int) -> bool:
        nonlocal holder
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(h, cls, 64)
        if cls.value == "QWAcctBarHolder":
            holder = h
            return False
        return True

    user32.EnumChildWindows(root_hwnd, EnumCB(_cb), 0)
    return holder


def _expand_sidebar_sections(holder: int) -> bool:
    """Expand all collapsed sidebar sections that contain accounts.

    Returns True if any sections were expanded.
    """
    import ctypes  # noqa: PLC0415
    import ctypes.wintypes as wt  # noqa: PLC0415
    import time as _time  # noqa: PLC0415

    user32 = ctypes.windll.user32
    SM = user32.SendMessageW
    SM.argtypes = [wt.HWND, ctypes.c_uint, wt.WPARAM, wt.LPARAM]
    SM.restype = wt.LPARAM
    EnumCB = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    cls_buf = ctypes.create_unicode_buffer(64)
    txt_buf = ctypes.create_unicode_buffer(256)

    def _get_class(h: int) -> str:
        user32.GetClassNameW(h, cls_buf, 64)
        return cls_buf.value

    def _get_text(h: int) -> str:
        user32.GetWindowTextW(h, txt_buf, 256)
        return txt_buf.value

    def _get_rect(h: int) -> wt.RECT:
        r = wt.RECT()
        user32.GetWindowRect(h, ctypes.byref(r))
        return r

    # Map: section_name → {has_hidden_items, visible_btns}
    section_state: dict[str, dict] = {}

    def _scan_child(h: int, _: int) -> bool:
        if user32.GetParent(h) != holder:
            return True
        cls_name = _get_class(h)
        txt = _get_text(h)
        vis = bool(user32.IsWindowVisible(h))
        if cls_name == "QWListViewer" and txt:
            def _check_lb(ch: int, __: int) -> bool:
                if _get_class(ch) == "ListBox":
                    cnt = SM(ch, 0x018B, 0, 0)  # LB_GETCOUNT
                    lb_vis = bool(user32.IsWindowVisible(ch))
                    if cnt > 0 and not lb_vis:
                        section_state.setdefault(
                            txt, {"has_hidden_items": False, "visible_btns": []})
                        section_state[txt]["has_hidden_items"] = True
                return True
            user32.EnumChildWindows(h, EnumCB(_check_lb), 0)
        elif cls_name == "QC_button" and txt and vis:
            section_state.setdefault(
                txt, {"has_hidden_items": False, "visible_btns": []})
            section_state[txt]["visible_btns"].append(h)
        return True

    user32.EnumChildWindows(holder, EnumCB(_scan_child), 0)

    # Expand bottom-up so lower sections don't shift when higher ones expand
    candidates: list[tuple[int, int]] = []
    for section_name, state in section_state.items():
        if not state["has_hidden_items"] or not state["visible_btns"]:
            continue
        btns = state["visible_btns"]
        btns.sort(key=lambda b: _get_rect(b).top)
        candidates.append((_get_rect(btns[0]).top, btns[0]))
    candidates.sort(reverse=True)

    WM_LBUTTONDOWN = 0x0201
    WM_LBUTTONUP = 0x0202
    MK_LBUTTON = 0x0001
    expanded = False
    for _, btn in candidates:
        r = _get_rect(btn)
        mid_x = (r.right - r.left) // 2
        mid_y = (r.bottom - r.top) // 2
        lparam = (mid_y << 16) | (mid_x & 0xFFFF)
        user32.SendMessageW(btn, WM_LBUTTONDOWN, MK_LBUTTON, lparam)
        _time.sleep(0.05)
        user32.SendMessageW(btn, WM_LBUTTONUP, 0, lparam)
        _time.sleep(0.5)
        expanded = True

    if expanded:
        _time.sleep(0.2)
    return expanded


def _find_sidebar_accounts(root_hwnd: int) -> list[dict[str, Any]]:
    """Discover sidebar accounts by scanning QWAcctBarHolder ListBox items.

    The Quicken sidebar has section headers (QC_button) that toggle sections
    open/closed.  Within each section, child ``QWListViewer`` containers hold
    ``ListBox`` controls with one row per account.  Items with height ≤ 5 px
    are decorative separators and are skipped.

    **Three-stage approach**:
    1. *Ensure sidebar visible* and *expand collapsed sections*.
    2. *Multi-pass scroll enumeration* — enumerate ListBoxes regardless of
       visibility (to catch items in sections that toggle during scanning).
       Uses ``LB_GETCOUNT`` and ``LB_GETITEMRECT`` which work even on
       hidden ListBox windows.
    3. Compute ``content_y_lb`` for each ListBox — the Y offset within the
       holder's full content space — used for reliable scrolling later.

    Returns a list of ``{"section", "lb_hwnd", "item_index", "screen_x",
    "screen_y", "content_y_lb", "holder_hwnd"}`` dicts (one per real item).
    """
    import ctypes  # noqa: PLC0415
    import ctypes.wintypes as wt  # noqa: PLC0415
    import time as _time  # noqa: PLC0415

    user32 = ctypes.windll.user32
    SM = user32.SendMessageW
    SM.argtypes = [wt.HWND, ctypes.c_uint, wt.WPARAM, wt.LPARAM]
    SM.restype = wt.LPARAM

    EnumCB = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

    holder = _find_sidebar_holder(root_hwnd)
    if not holder:
        return []

    # Ensure the sidebar is visible (investment views may hide it)
    if not user32.IsWindowVisible(holder):
        SW_SHOW = 5
        h = holder
        chain = []
        while h and h != root_hwnd:
            chain.append(h)
            h = user32.GetParent(h)
        for h in reversed(chain):
            if not user32.IsWindowVisible(h):
                user32.ShowWindow(h, SW_SHOW)
        _time.sleep(0.2)
        if not user32.IsWindowVisible(holder):
            return []

    user32.SetForegroundWindow(root_hwnd)

    # Stage 1: Expand all collapsed sections
    _expand_sidebar_sections(holder)

    # --------------------------------------------------------------------------
    # Stage 2: Enumerate ALL ListBoxes (visible or not)
    # --------------------------------------------------------------------------
    cls_buf = ctypes.create_unicode_buffer(64)
    txt_buf = ctypes.create_unicode_buffer(256)

    def _get_class(h: int) -> str:
        user32.GetClassNameW(h, cls_buf, 64)
        return cls_buf.value

    def _get_text(h: int) -> str:
        user32.GetWindowTextW(h, txt_buf, 256)
        return txt_buf.value

    class _SI(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("fMask", ctypes.c_uint),
                    ("nMin", ctypes.c_int), ("nMax", ctypes.c_int),
                    ("nPage", ctypes.c_uint), ("nPos", ctypes.c_int),
                    ("nTrackPos", ctypes.c_int)]

    WM_VSCROLL = 0x0115
    SB_THUMBPOSITION = 4
    SB_ENDSCROLL = 8

    def _get_scroll_info() -> _SI:
        si = _SI()
        si.cbSize = ctypes.sizeof(si)
        si.fMask = 0x17
        user32.GetScrollInfo(holder, 1, ctypes.byref(si))
        return si

    def _scroll_to(pos: int) -> None:
        user32.SendMessageW(holder, WM_VSCROLL,
                            (pos << 16) | SB_THUMBPOSITION, 0)
        _time.sleep(0.25)
        user32.SendMessageW(holder, WM_VSCROLL, SB_ENDSCROLL, 0)
        _time.sleep(0.1)

    orig_pos = _get_scroll_info().nPos

    # Scroll to top so visible-ListBox positions are measured from scroll=0.
    _scroll_to(0)
    _time.sleep(0.15)

    holder_rect = wt.RECT()
    user32.GetWindowRect(holder, ctypes.byref(holder_rect))
    holder_top = holder_rect.top

    # Enumerate ALL ListBox children inside QWListViewer parents.
    # We intentionally skip the IsWindowVisible check because sections may
    # collapse/expand during scanning.  LB_GETCOUNT and LB_GETITEMRECT
    # work on hidden ListBox windows.
    all_lbs: list[dict[str, Any]] = []
    seen_lb_hwnds: set[int] = set()

    def _scan_lb(h: int, _: int) -> bool:
        if h in seen_lb_hwnds:
            return True
        if _get_class(h) != "ListBox":
            return True
        parent = user32.GetParent(h)
        if _get_class(parent) != "QWListViewer":
            return True
        section = _get_text(parent)
        count = SM(h, 0x018B, 0, 0)  # LB_GETCOUNT
        if count <= 0:
            seen_lb_hwnds.add(h)
            return True

        # Determine content_y: for visible LBs, use GetWindowRect; for hidden
        # ones, try to infer from the QWListViewer's position.
        lb_rect = wt.RECT()
        user32.GetWindowRect(h, ctypes.byref(lb_rect))
        vis = bool(user32.IsWindowVisible(h))
        if vis and lb_rect.bottom > lb_rect.top:
            content_y = lb_rect.top - holder_top  # scroll is 0 here
        else:
            # Hidden LB: use parent QWListViewer rect as best estimate
            pv_rect = wt.RECT()
            user32.GetWindowRect(parent, ctypes.byref(pv_rect))
            content_y = pv_rect.top - holder_top if pv_rect.bottom > pv_rect.top else 0

        seen_lb_hwnds.add(h)
        all_lbs.append({
            "hwnd": h, "section": section, "count": count,
            "content_y": content_y, "visible": vis,
        })
        return True

    user32.EnumChildWindows(holder, EnumCB(_scan_lb), 0)

    # Also scroll to bottom and re-scan to catch LBs only visible there
    si = _get_scroll_info()
    bottom_pos = max(0, si.nMax - int(si.nPage) + 1)
    if bottom_pos > 0:
        _scroll_to(bottom_pos)
        _time.sleep(0.1)
        user32.GetWindowRect(holder, ctypes.byref(holder_rect))
        holder_top_bot = holder_rect.top

        def _scan_lb_bot(h: int, _: int) -> bool:
            if h in seen_lb_hwnds:
                return True
            if _get_class(h) != "ListBox":
                return True
            parent = user32.GetParent(h)
            if _get_class(parent) != "QWListViewer":
                return True
            section = _get_text(parent)
            count = SM(h, 0x018B, 0, 0)
            if count <= 0:
                seen_lb_hwnds.add(h)
                return True
            lb_rect = wt.RECT()
            user32.GetWindowRect(h, ctypes.byref(lb_rect))
            vis = bool(user32.IsWindowVisible(h))
            if vis and lb_rect.bottom > lb_rect.top:
                content_y = lb_rect.top - holder_top_bot + bottom_pos
            else:
                pv_rect = wt.RECT()
                user32.GetWindowRect(parent, ctypes.byref(pv_rect))
                content_y = (pv_rect.top - holder_top_bot + bottom_pos
                             if pv_rect.bottom > pv_rect.top else 0)
            seen_lb_hwnds.add(h)
            all_lbs.append({
                "hwnd": h, "section": section, "count": count,
                "content_y": content_y, "visible": vis,
            })
            return True

        user32.EnumChildWindows(holder, EnumCB(_scan_lb_bot), 0)

    # Restore scroll position
    _scroll_to(orig_pos)

    # --------------------------------------------------------------------------
    # Stage 3: Build item list from all discovered ListBoxes
    # --------------------------------------------------------------------------
    items: list[dict[str, Any]] = []
    for lb_info in all_lbs:
        h = lb_info["hwnd"]
        section = lb_info["section"]
        count = lb_info["count"]
        content_y = lb_info["content_y"]
        for i in range(min(count, 100)):
            ir = wt.RECT()
            SM(h, 0x0198, i, ctypes.addressof(ir))  # LB_GETITEMRECT
            item_h = ir.bottom - ir.top
            if item_h <= 5:
                continue  # separator
            # Absolute content Y of this specific item within the holder
            item_mid_y = (ir.top + ir.bottom) // 2  # midpoint in LB client
            item_abs_y = max(0, content_y) + item_mid_y
            items.append({
                "section": section,
                "lb_hwnd": h,
                "item_index": i,
                "screen_x": 0,
                "screen_y": 0,
                "content_y_lb": max(0, content_y),
                "item_content_y": item_abs_y,
                "holder_hwnd": holder,
            })

    return items


_SIDEBAR_DEBUG = False  # set True temporarily to diagnose timing


def _scroll_holder_for_lb(lb_hwnd: int) -> None:
    """Scroll QWAcctBarHolder so that lb_hwnd is within its visible viewport.

    The sidebar container (QWAcctBarHolder) is taller than the screen area and
    has its own vertical scrollbar.  ListBoxes below the current scroll
    position are real windows (EnumChildWindows finds them) but clicking their
    screen coordinates misses — they're clipped by the container.  This
    function scrolls the container just enough to bring lb_hwnd into view.

    Hierarchy: ListBox → QWListViewer → QWAcctBarHolder
    """
    import ctypes  # noqa: PLC0415
    import ctypes.wintypes as wt  # noqa: PLC0415
    import time as _time  # noqa: PLC0415

    user32 = ctypes.windll.user32

    if not lb_hwnd or not _is_valid_hwnd(lb_hwnd):
        return

    # Walk up two levels: ListBox → QWListViewer → QWAcctBarHolder
    lv = user32.GetParent(lb_hwnd)
    holder = user32.GetParent(lv) if lv else 0
    if not holder:
        return
    cls_buf = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(holder, cls_buf, 64)
    if cls_buf.value != "QWAcctBarHolder":
        return  # unexpected hierarchy

    class SCROLLINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("fMask", ctypes.c_uint),
                    ("nMin", ctypes.c_int), ("nMax", ctypes.c_int),
                    ("nPage", ctypes.c_uint), ("nPos", ctypes.c_int),
                    ("nTrackPos", ctypes.c_int)]

    SIF_ALL = 0x17
    si = SCROLLINFO()
    si.cbSize = ctypes.sizeof(si)
    si.fMask = SIF_ALL
    if not user32.GetScrollInfo(holder, 1, ctypes.byref(si)):  # SB_VERT=1
        return
    if si.nMax == 0:
        return  # nothing to scroll

    holder_rect = wt.RECT()
    user32.GetWindowRect(holder, ctypes.byref(holder_rect))
    holder_top = holder_rect.top
    holder_height = holder_rect.bottom - holder_rect.top

    lb_rect = wt.RECT()
    user32.GetWindowRect(lb_hwnd, ctypes.byref(lb_rect))

    # Content coordinates: offset of lb within full (unscrolled) holder content
    content_top = lb_rect.top - holder_top + si.nPos
    content_bot = lb_rect.bottom - holder_top + si.nPos

    margin = 10  # px to leave visible above/below the ListBox
    new_pos = si.nPos

    if content_top < si.nPos + margin:
        new_pos = max(si.nMin, content_top - margin)
    elif content_bot > si.nPos + holder_height - margin:
        new_pos = min(si.nMax - int(si.nPage) + 1, content_bot - holder_height + margin)

    if new_pos == si.nPos:
        return  # already visible

    WM_VSCROLL = 0x0115
    SB_THUMBPOSITION = 4
    SB_ENDSCROLL = 8
    wp = (new_pos << 16) | (SB_THUMBPOSITION & 0xFFFF)
    user32.SendMessageW(holder, WM_VSCROLL, wp, 0)
    _time.sleep(0.05)
    user32.SendMessageW(holder, WM_VSCROLL, SB_ENDSCROLL, 0)
    _time.sleep(0.05)


def _scroll_holder_to_content_y(holder_hwnd: int, content_y_lb: int) -> None:
    """Scroll QWAcctBarHolder so the ListBox at *content_y_lb* is in view.

    Unlike ``_scroll_holder_for_lb`` (which relies on ``GetWindowRect`` of a
    possibly-hidden window), this function uses the pre-computed content-space
    Y coordinate stored in each sidebar item dict.  This works even when the
    ListBox is currently hidden (Quicken collapses off-screen ListBoxes to
    height=0 until they scroll into the viewport).

    After scrolling, Quicken shows the previously-hidden ListBox at its
    correct position.  A small sleep allows the window to be repositioned
    before the caller calls ``LB_SETTOPINDEX`` / ``LB_GETITEMRECT``.
    """
    import ctypes  # noqa: PLC0415
    import ctypes.wintypes as wt  # noqa: PLC0415
    import time as _time  # noqa: PLC0415

    user32 = ctypes.windll.user32

    class SCROLLINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("fMask", ctypes.c_uint),
                    ("nMin", ctypes.c_int), ("nMax", ctypes.c_int),
                    ("nPage", ctypes.c_uint), ("nPos", ctypes.c_int),
                    ("nTrackPos", ctypes.c_int)]

    si = SCROLLINFO()
    si.cbSize = ctypes.sizeof(si)
    si.fMask = 0x17  # SIF_ALL
    if not user32.GetScrollInfo(holder_hwnd, 1, ctypes.byref(si)):
        return
    if si.nMax == 0:
        return  # no scrollbar

    holder_rect = wt.RECT()
    user32.GetWindowRect(holder_hwnd, ctypes.byref(holder_rect))
    holder_height = holder_rect.bottom - holder_rect.top
    max_scroll = max(0, si.nMax - int(si.nPage) + 1)

    margin = 30  # px of extra space to show above the ListBox

    # Check if already in view (content_y_lb within current viewport)
    if si.nPos + margin <= content_y_lb <= si.nPos + holder_height - margin:
        return  # ListBox is already visible, no scroll needed

    # Scroll so content_y_lb lands near the top of the viewport
    target_pos = max(si.nMin, content_y_lb - margin)
    target_pos = min(target_pos, max_scroll)

    if target_pos == si.nPos:
        return

    WM_VSCROLL = 0x0115
    SB_THUMBPOSITION = 4
    SB_ENDSCROLL = 8
    user32.SendMessageW(holder_hwnd, WM_VSCROLL,
                        (target_pos << 16) | SB_THUMBPOSITION, 0)
    _time.sleep(0.25)  # wait for Quicken to show/reposition the ListBox
    user32.SendMessageW(holder_hwnd, WM_VSCROLL, SB_ENDSCROLL, 0)
    _time.sleep(0.1)


def _sidebar_dblclick(root_hwnd: int, screen_x: int, screen_y: int,
                      timeout: float = 6.0, *,
                      lb_hwnd: int = 0, item_index: int = -1,
                      pre_title: str = "",
                      bail_early: float = 0.3,
                      content_y_lb: int = 0,
                      holder_hwnd: int = 0) -> str:
    """Double-click a sidebar item and wait for the title to stabilize.

    If ``lb_hwnd`` and ``item_index`` are provided, the item is scrolled
    into view first (via ``LB_SETTOPINDEX``) and its screen position is
    recalculated.

    If ``pre_title`` is given, the function uses a two-phase wait based
    on the **bracketed account name** in the title (e.g. ``[My Checking]``):

    Phase 1 (``bail_early`` seconds, default 0.3s):
        Poll at 100ms.  If the bracket content changes, bail immediately.
        Modal dialogs are polled and dismissed every 0.2s within this loop
        (extending the deadline when dismissed so the account still loads).

    Phase 2 (up to ``timeout`` total):
        Bracket content is changing — wait for it to stabilize on a new
        account name.  If the title hasn't changed at all since the click,
        use a shortened Phase 2 (1.0s) to avoid wasting time on dead items.
        Modal dialogs are polled and dismissed every 0.3s within this loop.
    """
    import ctypes  # noqa: PLC0415
    import ctypes.wintypes as wt  # noqa: PLC0415
    import time as _time  # noqa: PLC0415

    user32 = ctypes.windll.user32

    def _bracket_name(t: str) -> str:
        if "[" in t and "]" in t:
            raw = t[t.rfind("[") + 1 : t.rfind("]")]
            try:
                raw = raw.encode("cp1252").decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                pass
            return raw
        return ""

    # Scroll the sidebar container so lb_hwnd is visible, then scroll the
    # ListBox to bring item_index to the top and recalculate screen coords.
    if lb_hwnd and item_index >= 0 and _is_valid_hwnd(lb_hwnd):
        # If the ListBox's section is collapsed, expand it first.
        if not user32.IsWindowVisible(lb_hwnd) and holder_hwnd:
            _expand_sidebar_sections(holder_hwnd)
            _time.sleep(0.15)
        # Prefer content_y_lb-based scrolling (works even if lb is hidden).
        # Fall back to GetWindowRect-based for old callers without content_y_lb.
        if content_y_lb > 0 and holder_hwnd:
            _scroll_holder_to_content_y(holder_hwnd, content_y_lb)
        else:
            _scroll_holder_for_lb(lb_hwnd)
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
    # Poll modals every 2 iterations (~0.2s) so a blocking dialog (e.g.
    # "securities mismatch") is dismissed immediately rather than stalling
    # the entire Phase 1 window.
    # Hard cap: modals can extend bail_deadline at most 4× (e.g. 1.2s total
    # at bail_early=0.3s) so a persistent/repeated modal can't loop forever.
    bail_start = _time.monotonic()
    bail_deadline = bail_start + bail_early
    bail_deadline_cap = bail_start + bail_early * 4
    if _SIDEBAR_DEBUG:
        import sys
        print(f"  [dbg] P1 start pre_bracket={pre_bracket!r}", flush=True, file=sys.stderr)
    _p1_poll = 0
    while _time.monotonic() < bail_deadline:
        _time.sleep(0.1)
        _p1_poll += 1
        if _p1_poll % 2 == 0:
            if _dismiss_modal_dialogs(root_hwnd):
                bail_deadline = min(bail_deadline + 0.3, bail_deadline_cap)
        user32.GetWindowTextW(root_hwnd, buf, 256)
        cur = buf.value
        cur_bracket = _bracket_name(cur)
        if cur_bracket and cur_bracket != pre_bracket:
            return cur  # new account opened

    if not pre_bracket:
        user32.GetWindowTextW(root_hwnd, buf, 256)
        return buf.value

    # Snapshot at end of Phase 1: is the bracket still the same?
    user32.GetWindowTextW(root_hwnd, buf, 256)
    phase1_title = buf.value
    phase1_bracket = _bracket_name(phase1_title)

    # Phase 2: wait for bracket to stabilize.
    # Use bracket comparison for no-change detection: Quicken updates the title
    # prefix/status text even when staying on the same account, so full-string
    # equality is too strict. If the bracket hasn't changed after Phase 1, this
    # is likely a duplicate sidebar item — use shortened Phase 2 (1.0s).
    # For investment accounts that briefly go blank (phase1_bracket==""), treat
    # as full-budget: the early-exit below will catch the same-bracket return.
    no_change = (phase1_bracket == pre_bracket)
    if no_change:
        phase2_budget = min(1.0, timeout - bail_early)
    else:
        phase2_budget = timeout - bail_early

    # Hard cap: total (Phase 1 elapsed + Phase 2) must not exceed timeout.
    # Apply the cap to the initial deadline as well as to extensions.
    full_deadline_cap = bail_start + timeout
    full_deadline = min(_time.monotonic() + phase2_budget, full_deadline_cap)
    if _SIDEBAR_DEBUG:
        import sys
        p1_elapsed = _time.monotonic() - bail_start
        print(f"  [dbg] P2 start phase1_bracket={phase1_bracket!r} "
              f"no_change={no_change} phase2_budget={phase2_budget:.2f}s "
              f"p1_elapsed={p1_elapsed:.2f}s deadline_cap={full_deadline_cap - bail_start:.2f}s",
              flush=True, file=sys.stderr)
    _p2_poll = 0
    while _time.monotonic() < full_deadline:
        _time.sleep(0.15)
        _p2_poll += 1
        user32.GetWindowTextW(root_hwnd, buf, 256)
        cur = buf.value
        cur_bracket = _bracket_name(cur)
        if cur_bracket:
            if cur_bracket != pre_bracket:
                return cur  # new account opened
            # Bracket returned to same value — confirmed duplicate, exit now
            break
        if _p2_poll % 2 == 0:
            dismissed = _dismiss_modal_dialogs(root_hwnd)
            if dismissed and not no_change:
                if _SIDEBAR_DEBUG:
                    import sys
                    print(f"  [dbg] P2 modal dismissed, extending deadline "
                          f"(time={_time.monotonic()-bail_start:.2f}s)", flush=True, file=sys.stderr)
                full_deadline = min(full_deadline + 0.5, full_deadline_cap)

    # One final modal dismiss — only worth the sleep for actively-loading
    # accounts; skip for no-change (duplicate) items to save time.
    if _SIDEBAR_DEBUG:
        import sys
        print(f"  [dbg] P2 done, elapsed={_time.monotonic()-bail_start:.2f}s no_change={no_change}",
              flush=True, file=sys.stderr)
    if not no_change and _dismiss_modal_dialogs(root_hwnd):
        _time.sleep(0.5)
        user32.GetWindowTextW(root_hwnd, buf, 256)
        cur_bracket = _bracket_name(buf.value)
        if cur_bracket and cur_bracket != pre_bracket:
            return buf.value

    # Final read
    user32.GetWindowTextW(root_hwnd, buf, 256)
    return buf.value


def list_sidebar_accounts(bridge: Any, resume: bool = False,
                           max_seconds: float = 20.0) -> dict[str, Any]:
    """Discover sidebar accounts by clicking each item and reading window titles.

    Because Quicken's sidebar is owner-drawn, the only reliable way to read
    account names is to click each item and watch the window title.  Investment
    accounts can take 2-7 seconds each, so a single full scan would exceed HTTP
    timeouts.

    **Resumable / time-bounded scan**

    Call repeatedly until ``done`` is ``True``::

        result = list_sidebar_accounts(bridge, resume=False)   # fresh start
        while not result["done"]:
            result = list_sidebar_accounts(bridge, resume=True)
        all_accounts = result["accounts"]

    Parameters
    ----------
    bridge
        Active UIABridge instance (unused directly; used for pm attachment).
    resume
        If *False* (default), restart scan from the beginning.
        If *True*, continue from where the last call left off.
    max_seconds
        Seconds budget per call (default 20).  When the budget expires the
        function returns immediately with ``done=False`` so the caller can
        resume in a fresh HTTP request.

    Returns
    -------
    dict with keys:
        ``ok`` -- always ``True`` on success.
        ``accounts`` -- list of ``{name, section}`` dicts discovered so far.
        ``scanned`` -- number of sidebar items processed so far (across calls).
        ``total`` -- total sidebar items enumerated.
        ``done`` -- ``True`` when all items have been processed.
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

    global _scan_state  # noqa: PLW0603

    if not resume or not _scan_state.get("items"):
        sidebar_items = _find_sidebar_accounts(root)
        _scan_state = {
            "items": sidebar_items,
            "idx": 0,
            "results": [],
            "seen_names": set(),
            "done": False,
            "last_bracket": "",
        }

    items = _scan_state["items"]
    idx = _scan_state["idx"]
    results: list[dict[str, Any]] = _scan_state["results"]
    seen_names: set[str] = _scan_state["seen_names"]
    last_bracket: str = _scan_state.get("last_bracket", "")

    deadline = _time.monotonic() + max_seconds

    # Re-expand collapsed sections on every resume call — navigating to
    # accounts in other sections may collapse the current one.
    holder = _find_sidebar_holder(root)
    if holder:
        _expand_sidebar_sections(holder)

    consecutive_same = 0  # count of sub-rows for current account

    while idx < len(items) and _time.monotonic() < deadline:
        item = items[idx]
        idx += 1

        # Optimisation: if at least TWO previous clicks produced the same
        # bracket name, this adjacent item is likely another sub-row.  Skip
        # without clicking (saves 1-5s per sub-row).  Require >= 2 to avoid
        # skipping single-row accounts adjacent to multi-row ones.  Cap at 3
        # consecutive skips to ensure we eventually click again.
        if consecutive_same >= 2 and consecutive_same < 5:
            # Peek: is this the same ListBox and adjacent item_index?
            prev = items[idx - 2] if idx >= 2 else None
            if (prev and prev["lb_hwnd"] == item["lb_hwnd"]
                    and abs(prev["item_index"] - item["item_index"]) <= 1):
                consecutive_same += 1
                continue

        user32.GetWindowTextW(root, buf, 256)
        title_before = buf.value

        # Dismiss any modal dialog that may have popped during previous click
        _dismiss_modal_dialogs(root)

        title = _sidebar_dblclick(
            root, item["screen_x"], item["screen_y"], timeout=6.0,
            lb_hwnd=item["lb_hwnd"], item_index=item["item_index"],
            pre_title=title_before,
            content_y_lb=item.get("content_y_lb", 0),
            holder_hwnd=item.get("holder_hwnd", 0),
        )

        def _bracket(t: str) -> str:
            if "[" in t and "]" in t:
                raw = t[t.rfind("[") + 1 : t.rfind("]")]
                # Fix UTF-8 double-encoding artifacts (e.g. "Â®" → "®")
                try:
                    raw = raw.encode("cp1252").decode("utf-8")
                except (UnicodeEncodeError, UnicodeDecodeError):
                    pass
                return raw
            return ""

        name = _bracket(title)
        if not name:
            consecutive_same = 0
            continue

        if name == last_bracket:
            consecutive_same += 1
        else:
            consecutive_same = 0
        last_bracket = name

        # Filter section summary rows — match against ALL known section
        # names, not just the current section (which may be misclassified).
        _SECTION_NAMES = {
            "banking", "investing", "property & debt", "separate",
            "rental property", "business", "savings goals",
        }
        if name.lower() in _SECTION_NAMES or _acct_match(name, item["section"]):
            continue  # section summary row
        if name not in seen_names:
            seen_names.add(name)
            results.append({
                "name": name,
                "section": item["section"],
                "lb_hwnd": item["lb_hwnd"],
                "item_index": item["item_index"],
                "content_y_lb": item.get("content_y_lb", 0),
                "item_content_y": item.get("item_content_y", 0),
                "holder_hwnd": item.get("holder_hwnd", 0),
                "screen_x": item["screen_x"],
                "screen_y": item["screen_y"],
            })

    _scan_state["idx"] = idx
    _scan_state["last_bracket"] = last_bracket
    _scan_state["done"] = idx >= len(items)

    global _sidebar_cache  # noqa: PLW0603
    _sidebar_cache = list(results)

    return {
        "ok": True,
        "accounts": list(results),
        "scanned": idx,
        "total": len(items),
        "done": _scan_state["done"],
    }


# Module-level sidebar cache: name → sidebar_item dict with screen coords
_sidebar_cache: list[dict[str, Any]] = []

# Resumable scan state for list_sidebar_accounts
_scan_state: dict[str, Any] = {}


def _sidebar_lookup(account_name: str) -> dict[str, Any] | None:
    """Find a cached sidebar entry matching account_name.

    Prefers exact (case-insensitive) matches, then the shortest fuzzy match
    (most specific name among candidates).
    """
    q = account_name.lower()
    # Pass 1: exact case-insensitive match
    for entry in _sidebar_cache:
        if entry["name"].lower() == q:
            return entry
    # Pass 2: fuzzy — pick the shortest matching name (most specific)
    best: dict[str, Any] | None = None
    best_len = float("inf")
    for entry in _sidebar_cache:
        if _acct_match(entry["name"], account_name):
            if len(entry["name"]) < best_len:
                best = entry
                best_len = len(entry["name"])
    return best


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

    global _sidebar_cache, _scan_state  # noqa: PLW0603

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

    # Dismiss any modal dialogs that may be blocking interaction
    _dismiss_modal_dialogs(root_hwnd)

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

        # Dismiss any modal that may have popped (e.g. Securities Mismatch)
        _dismiss_modal_dialogs(root_hwnd)

        # Verify the switch via window title bracket first.
        buf_v = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(root_hwnd, buf_v, 256)
        title = buf_v.value
        if "[" in title and "]" in title:
            opened = title[title.rfind("[") + 1 : title.rfind("]")]
            try:
                opened = opened.encode("cp1252").decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                pass
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
        """Click-and-search sidebar navigation with fuzzy index recovery.

        Quicken's owner-drawn virtual ListBoxes can shift item indices
        between the scan and subsequent navigations.  This function:

        1. Scrolls to the cached ``content_y_lb`` position.
        2. Re-resolves the nearest visible ListBox.
        3. Tries the cached ``item_index`` first (fast path).
        4. If the wrong account opens, searches ±4 nearby indices.
        5. Skips header/separator items (height < 10 px).
        6. Updates the sidebar cache entry on success.

        The search is deliberately limited (1 ListBox, ±4 indices) to
        keep latency under ~30 seconds.  If it fails, Phase 4 will do
        a full incremental rescan.
        """
        holder = cached_entry.get("holder_hwnd", 0)
        cy_lb = cached_entry.get("content_y_lb", 0)
        idx = cached_entry["item_index"]

        if not holder or not _is_valid_hwnd(holder):
            return None

        # Scroll the section into view
        if cy_lb > 0:
            _scroll_holder_to_content_y(holder, cy_lb)
            time.sleep(0.2)

        # Enumerate ALL visible ListBoxes in the holder with their
        # content-Y and item counts.
        import ctypes.wintypes as wt  # noqa: PLC0415
        _EnumCB = ctypes.WINFUNCTYPE(
            ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)

        class _SI(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("fMask", ctypes.c_uint),
                        ("nMin", ctypes.c_int), ("nMax", ctypes.c_int),
                        ("nPage", ctypes.c_uint), ("nPos", ctypes.c_int),
                        ("nTrackPos", ctypes.c_int)]

        si = _SI()
        si.cbSize = ctypes.sizeof(si)
        si.fMask = 0x17
        user32.GetScrollInfo(holder, 1, ctypes.byref(si))

        hr = wt.RECT()
        user32.GetWindowRect(holder, ctypes.byref(hr))

        all_lbs: list[tuple[int, int, int]] = []  # (hwnd, content_y, count)

        def _collect_lbs(h: int, _: int) -> bool:
            cls = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(h, cls, 64)
            if cls.value != "ListBox" or not user32.IsWindowVisible(h):
                return True
            cnt = _send_msg(h, 0x018B, 0, 0)  # LB_GETCOUNT
            if cnt < 1:
                return True
            r = wt.RECT()
            user32.GetWindowRect(h, ctypes.byref(r))
            lb_cy = r.top - hr.top + si.nPos
            all_lbs.append((h, lb_cy, cnt))
            return True

        user32.EnumChildWindows(holder, _EnumCB(_collect_lbs), 0)

        if not all_lbs:
            return None

        # Sort by distance from cached content_y_lb
        all_lbs.sort(key=lambda x: abs(x[1] - cy_lb))

        LB_GETITEMRECT = 0x0198

        def _item_height(lb_h: int, item_idx: int) -> int:
            """Return pixel height of a ListBox item (0 on error)."""
            ir2 = wt.RECT()
            ret = _send_msg(lb_h, LB_GETITEMRECT, item_idx,
                            ctypes.addressof(ir2))
            if ret == 0:
                return 0
            return max(0, ir2.bottom - ir2.top)

        def _click_and_check(lb_h: int, item_idx: int) -> dict | None:
            """Double-click item and verify the opened account name."""
            cur_buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(root_hwnd, cur_buf, 256)
            pre_title = cur_buf.value

            title = _sidebar_dblclick(
                root_hwnd, 0, 0, timeout=3.0,
                lb_hwnd=lb_h, item_index=item_idx,
                pre_title=pre_title,
                content_y_lb=cy_lb,
                holder_hwnd=holder,
                bail_early=0.2,
            )

            _dismiss_modal_dialogs(root_hwnd)

            # Re-read title in case dismiss changed it
            if title == pre_title:
                time.sleep(0.4)
                user32.GetWindowTextW(root_hwnd, cur_buf, 256)
                title = cur_buf.value

            opened = ""
            if "[" in title and "]" in title:
                opened = title[title.rfind("[") + 1 : title.rfind("]")]
                try:
                    opened = opened.encode("cp1252").decode("utf-8")
                except (UnicodeEncodeError, UnicodeDecodeError):
                    pass

            if opened and _acct_match(opened, account_name):
                # Update cache to reflect correct index for next time
                cached_entry["item_index"] = item_idx
                cached_entry["lb_hwnd"] = lb_h
                return {"ok": True, "account": opened, "method": "sidebar"}

            # Also check active MDI title
            mdi_h = _find_active_mdi(root_hwnd)
            if mdi_h:
                mdi_buf = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(mdi_h, mdi_buf, 256)
                mdi_name = mdi_buf.value.strip()
                if _acct_match(mdi_name, account_name):
                    cached_entry["item_index"] = item_idx
                    cached_entry["lb_hwnd"] = lb_h
                    return {"ok": True, "account": mdi_name,
                            "method": "sidebar"}

            return None

        def _search_order(center: int, count: int, radius: int = 4) -> list[int]:
            """Build search order: center first, then ±radius outward."""
            order = [center] if 0 <= center < count else []
            for delta in range(1, radius + 1):
                if center - delta >= 0:
                    order.append(center - delta)
                if center + delta < count:
                    order.append(center + delta)
            return order

        # Search the nearest ListBox only, ±4 indices from cached position.
        # This keeps worst-case latency under ~30s (9 items × 3s each).
        if not all_lbs:
            return None
        lb_h, lb_cy, lb_cnt = all_lbs[0]
        for item_i in _search_order(idx, lb_cnt):
            # Skip tiny header/separator items
            if _item_height(lb_h, item_i) < 10:
                continue
            result = _click_and_check(lb_h, item_i)
            if result:
                return result

        return None

    if cached:
        result = _try_sidebar_click(cached)
        if result:
            return result
        # Don't invalidate the whole cache — just this entry might be stale

    # ------------------------------------------------------------------
    # Phase 4: Auto-rebuild sidebar cache and retry (one attempt).
    # Runs when the cache is empty, when the account wasn't in the cache,
    # OR when Phase 3 found a cache entry but the click search failed
    # (the item may have moved to a different section entirely).
    # ------------------------------------------------------------------
    scan_done = _scan_state.get("done", False)
    if not scan_done:
        try:
            if not _scan_state.get("items"):
                _scan_state = {}
            for _attempt in range(15):  # max 15 batches (~7 min cap)
                resume = _attempt > 0 or bool(_scan_state.get("items"))
                res = list_sidebar_accounts(bridge, resume=resume,
                                            max_seconds=30)
                # Check if we found the target yet
                found = _sidebar_lookup(account_name)
                if found and (not cached or found is not cached):
                    # New/different cache entry — try clicking it
                    result = _try_sidebar_click(found)
                    if result:
                        return result
                    break  # found in cache but click failed
                if res.get("done"):
                    break
        except Exception:  # noqa: BLE001
            pass  # sidebar scan failed — fall through to error

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

    # Dismiss any modal dialogs before reading state
    _dismiss_modal_dialogs(root_hwnd)

    def _read_text(h: int) -> str:
        tlen = _send_msg(h, WM_GETTEXTLENGTH, 0, 0)
        if tlen <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(tlen + 1)
        _send_msg(h, WM_GETTEXT, len(buf), ctypes.addressof(buf))
        return buf.value

    # Find the active QWMDI and scope child enumeration to it.
    # Retry briefly — after navigation the MDI may still be loading.
    mdi_h = _find_active_mdi(root_hwnd)
    if mdi_h is None:
        import time as _time  # noqa: PLC0415
        for _retry in range(3):
            _time.sleep(0.5)
            _dismiss_modal_dialogs(root_hwnd)
            mdi_h = _find_active_mdi(root_hwnd)
            if mdi_h is not None:
                break
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
        # Investment/portfolio view: extract what we can
        mdi_title = _read_text(mdi_h)

        # Count holdings in the ListBox
        holdings_count = 0
        lb_h = next(
            (h for h, c, _ in mdi_children
             if c == "listbox" and user32.IsWindowVisible(h)
             and _send_msg(h, 0x018B, 0, 0) > 0),
            None,
        )
        if lb_h:
            holdings_count = _send_msg(lb_h, 0x018B, 0, 0)  # LB_GETCOUNT

        # Collect header buttons that are geometrically inside this MDI
        mdi_rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(mdi_h, ctypes.byref(mdi_rect))
        _invest_buttons: list[str] = []
        for h, c, t in mdi_children:
            if c != "qc_button" or not t.strip():
                continue
            if not user32.IsWindowVisible(h):
                continue
            br = ctypes.wintypes.RECT()
            user32.GetWindowRect(h, ctypes.byref(br))
            # Only include buttons in the MDI header area (top 60px)
            if (br.left >= mdi_rect.left and br.right <= mdi_rect.right
                    and br.top >= mdi_rect.top
                    and br.top <= mdi_rect.top + 60):
                _invest_buttons.append(t.strip())

        return {
            "ok": True,
            "account": mdi_title if mdi_title else "",
            "total": "",
            "count": "",
            "reconcile_active": False,
            "filter_text": "",
            "view_type": view_type,
            "holdings_count": holdings_count,
            "tabs": _invest_buttons,
            "note": (
                "Investment view \u2014 the portfolio balance is rendered in a "
                "custom HTML control (QWHtmlView) that does not expose text "
                "via Win32 or UIA accessibility APIs.  Use the "
                "read_screen_text tool to extract the balance via server-side "
                "OCR (no vision model required)."
            ),
        }

    # ── Balance fields ──
    # The register footer contains labeled Static pairs:
    #   "Online Balance:", "Credit Remaining:", "Ending Balance:" etc.
    # Collect all label→value pairs for richer output.

    def _looks_numeric(s: str) -> bool:
        s2 = s.replace(",", "").replace(".", "").replace("-", "").strip()
        return bool(s2) and s2.isdigit()

    balance_labels: dict[str, str] = {}
    _label_keywords = ("balance", "remaining", "available")

    # Build positional map of visible statics inside the MDI
    _statics_xy: list[tuple[int, int, str, int]] = []  # (x, y, text, hwnd)
    for h, c, t in mdi_children:
        if c != "static" or not user32.IsWindowVisible(h):
            continue
        if not t.strip():
            continue
        sr = ctypes.wintypes.RECT()
        user32.GetWindowRect(h, ctypes.byref(sr))
        _statics_xy.append((sr.left, sr.top, t.strip(), h))

    for sx, sy, st, sh in _statics_xy:
        sl = st.rstrip(":").lower()
        if not any(k in sl for k in _label_keywords):
            continue
        # Find the value static: same y (±3px), to the right, numeric
        best_val = ""
        for vx, vy, vt, vh in _statics_xy:
            if vh == sh:
                continue
            if abs(vy - sy) <= 3 and vx > sx and _looks_numeric(vt):
                best_val = vt
                break
        if best_val:
            balance_labels[st.rstrip(":")] = best_val

    # Primary balance: prefer "Ending Balance", then first numeric static
    balance_static = (
        balance_labels.get("Ending Balance")
        or balance_labels.get("Online Balance")
        or next(
            (t for h, c, t in mdi_children
             if c == "static" and _looks_numeric(t)
             and user32.IsWindowVisible(h)),
            "",
        )
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
        "balances": balance_labels,
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

        if target_name and _acct_match(mdi_title, target_name):
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

    # Dismiss any modal dialogs before attempting register interaction
    _dismiss_modal_dialogs(root_hwnd)

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

    def _press(vk: int, delay: float = 0.1) -> None:
        """Send a key press via PostMessage to the currently-focused HWND.

        Unlike ``keybd_event`` (which targets the system foreground window
        and easily gets stolen by editors/browsers running alongside
        Quicken), ``PostMessage`` delivers directly to the target HWND's
        message queue — immune to focus races.
        """
        h = user32.GetFocus()
        _PostMsg(h, 0x0100, vk, 0)   # WM_KEYDOWN
        _time.sleep(0.02)
        _PostMsg(h, 0x0101, vk, 0)   # WM_KEYUP
        _time.sleep(delay)

    def _ctrl(vk: int) -> None:
        """Send Ctrl+<vk> via PostMessage to the focused HWND."""
        h = user32.GetFocus()
        _PostMsg(h, 0x0100, 0x11, 0)  # Ctrl down
        _time.sleep(0.02)
        _PostMsg(h, 0x0100, vk, 0)    # Key down
        _time.sleep(0.02)
        _PostMsg(h, 0x0101, vk, 0)    # Key up
        _time.sleep(0.05)
        _PostMsg(h, 0x0101, 0x11, 0)  # Ctrl up
        _time.sleep(0.3)

    def _get_focused() -> tuple[int, str, str, int, int, int]:
        """Return (hwnd, class_name, text, x_left, y_top, width)."""
        h = user32.GetFocus()
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(h, cls, 64)
        r = ctypes.wintypes.RECT()
        user32.GetWindowRect(h, ctypes.byref(r))
        tlen = _SendMsg(h, 0x000E, 0, 0)  # WM_GETTEXTLENGTH
        if tlen <= 0 or tlen > 32768:
            return h, cls.value, "", r.left, r.top, r.right - r.left
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
            # After _read_one_row, focus is on a button (Save/More).
            # Escape back to the QREdit date field, then Down to advance.
            _press(0x1B, 0.2)
            _press(0x28, 0.3)  # Down → next row
            _time.sleep(0.3)

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


# ── Server-side screen text extraction (Windows OCR) ──────────────


def read_screen_text(
    bridge: Any,
    *,
    region: str = "",
) -> dict[str, Any]:
    """Capture text from the active Quicken window using Windows OCR.

    This enables **non-vision models** to read text that is rendered
    visually but not exposed through Win32 text APIs or UI Automation
    accessibility (e.g. investment portfolio values, custom-drawn
    ListBox items, QWHtmlView content).

    The OCR runs server-side and returns structured text — the calling
    model never needs to process images.

    Parameters
    ----------
    bridge
        A ``WinUIABridge`` instance.
    region : str, optional
        Comma-separated ``left,top,right,bottom`` in **screen pixels**.
        If empty, defaults to the active QWMDI child window.

    Returns
    -------
    dict
        ``{"ok": True, "lines": [...], "text": str}`` where each line
        is ``{"text": str, "y": int, "x": int}``.
    """
    import asyncio  # noqa: PLC0415
    import ctypes  # noqa: PLC0415
    import ctypes.wintypes  # noqa: PLC0415
    import io  # noqa: PLC0415

    from server.process_manager import get_process_manager  # noqa: PLC0415

    pm = get_process_manager()
    if not pm.attached:
        raise TargetNotFoundError("Use select_window to attach first.")

    root_hwnd = pm.attached.hwnd
    if not _is_valid_hwnd(root_hwnd):
        raise TargetNotFoundError(
            "Quicken window is no longer valid. Use select_window to reattach."
        )

    _dismiss_modal_dialogs(root_hwnd)

    user32 = ctypes.windll.user32

    # Determine capture region
    if region and region.strip():
        parts = [int(p.strip()) for p in region.split(",")]
        if len(parts) != 4:
            raise UIAError(
                "region must be 'left,top,right,bottom'",
                code="INVALID_ARGUMENT",
            )
        left, top, right, bottom = parts
    else:
        # Default: active QWMDI
        mdi_h = _find_active_mdi(root_hwnd)
        if mdi_h is None:
            # Fall back to the full root window
            r = ctypes.wintypes.RECT()
            user32.GetWindowRect(root_hwnd, ctypes.byref(r))
            left, top, right, bottom = r.left, r.top, r.right, r.bottom
        else:
            r = ctypes.wintypes.RECT()
            user32.GetWindowRect(mdi_h, ctypes.byref(r))
            left, top, right, bottom = r.left, r.top, r.right, r.bottom

    # Capture
    try:
        from PIL import ImageGrab  # noqa: PLC0415
    except ImportError:
        raise UIAError(
            "Pillow is required for screen capture (pip install Pillow).",
            code="DEPENDENCY_MISSING",
        )

    img = ImageGrab.grab(bbox=(left, top, right, bottom))

    # Run Windows OCR
    try:
        from winsdk.windows.graphics.imaging import BitmapDecoder  # noqa: PLC0415,E501
        from winsdk.windows.media.ocr import OcrEngine  # noqa: PLC0415
        from winsdk.windows.storage.streams import (  # noqa: PLC0415
            DataWriter,
            InMemoryRandomAccessStream,
        )
    except ImportError:
        raise UIAError(
            "winsdk is required for OCR (pip install winsdk).",
            code="DEPENDENCY_MISSING",
        )

    engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        raise UIAError(
            "No OCR engine available on this Windows install.",
            code="OCR_UNAVAILABLE",
        )

    async def _ocr(pil_img):  # noqa: ANN001
        buf = io.BytesIO()
        pil_img.save(buf, format="PNG")
        stream = InMemoryRandomAccessStream()
        writer = DataWriter(stream)
        writer.write_bytes(buf.getvalue())
        await writer.store_async()
        await writer.flush_async()
        stream.seek(0)
        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()
        return await engine.recognize_async(bitmap)

    # asyncio.run() may fail if there's already an event loop running
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures  # noqa: PLC0415

        with concurrent.futures.ThreadPoolExecutor() as pool:
            result = pool.submit(lambda: asyncio.run(_ocr(img))).result(
                timeout=10.0
            )
    else:
        result = asyncio.run(_ocr(img))

    lines_out: list[dict[str, Any]] = []
    for line in result.lines:
        words = " ".join(w.text for w in line.words)
        y = int(line.words[0].bounding_rect.y) if line.words else 0
        x = int(line.words[0].bounding_rect.x) if line.words else 0
        lines_out.append({"text": words, "y": y, "x": x})

    # Post-process: Quicken's proprietary font renders "$" as a glyph that
    # Windows OCR consistently reads as "5".  Fix dollar-amount patterns:
    #   "543,207.62" → "$43,207.62",  "55,000.00" → "$5,000.00"
    import re as _re  # noqa: PLC0415

    _dollar_re = _re.compile(
        r"(?<![.\d])5(\d{1,3}(?:,\d{3})*\.\d{2})(?!\d)"
    )

    def _fix_dollar(text: str) -> str:
        return _dollar_re.sub(r"$\1", text)

    for entry in lines_out:
        entry["text"] = _fix_dollar(entry["text"])

    full_text = "\n".join(ld["text"] for ld in lines_out)

    return {
        "ok": True,
        "region": f"{left},{top},{right},{bottom}",
        "lines": lines_out,
        "text": full_text,
    }
