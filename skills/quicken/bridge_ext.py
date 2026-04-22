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


def _dismiss_modal_dialogs(root_hwnd: int, *, max_rounds: int = 3) -> bool:
    """Find and dismiss any modal dialogs owned by the Quicken window.

    Looks for top-level ``QWinDlg``, ``QWinPopup``, or ``#32770`` windows
    owned by *root_hwnd* and sends them a dismiss command.

    Strategy per dialog:
      1. Find a visible child with dismiss-like text.
      2. Click it via physical ``mouse_event`` (``SetForegroundWindow`` +
         ``SetCursorPos`` + left-click).  This is the only reliable method
         for Quicken's custom ``QC_button`` controls, which ignore
         ``BM_CLICK`` and stall on ``SendMessageW`` inside modal loops.
      3. Fall back to ``WM_CLOSE`` if no button found.
      4. Loop up to *max_rounds* to catch chained dialogs.

    Returns True if at least one dialog was dismissed.
    """
    import ctypes  # noqa: PLC0415
    import ctypes.wintypes as wt  # noqa: PLC0415
    import time as _time  # noqa: PLC0415

    user32 = ctypes.windll.user32
    WM_CLOSE = 0x0010
    any_dismissed = False

    _DIALOG_CLASSES = {"QWinDlg", "QWinPopup", "#32770"}

    # Exact-match labels (after lowering, stripping &)
    _DISMISS_LABELS_EXACT = frozenset({
        "done", "close", "ok", "cancel", "yes", "dismiss",
        "continue", "accept", "no thanks", "no", "ignore", "skip",
        "not now", "later", "remind me later", "accept all",
        "keep", "update", "replace", "use quicken data",
        "use online data", "finish later", "got it",
    })

    # Substring patterns — if button text *contains* any of these, click it.
    # Ordered by preference: safer/more-specific first.
    _DISMISS_SUBSTRINGS = (
        "accept", "done", "close", "ok", "cancel", "dismiss",
        "continue", "finish", "keep", "ignore", "skip",
    )

    # Classes likely to be clickable dismiss buttons
    _CLICKABLE_CLASSES = {"button", "qc_button"}

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p,
                                     ctypes.POINTER(ctypes.c_int))

    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004

    def _click_control(h: int, dlg: int) -> None:
        """Dismiss a button via physical mouse click.

        Message-based approaches (SendMessageW, PostMessageW) fail on
        Quicken's custom ``QC_button`` controls whose message loop stalls
        inside modal dialogs.  A physical mouse_event is the only
        reliable method.
        """
        # Bring dialog to foreground so it can receive input
        user32.SetForegroundWindow(dlg)
        _time.sleep(0.1)

        r = wt.RECT()
        user32.GetWindowRect(h, ctypes.byref(r))
        cx = (r.left + r.right) // 2
        cy = (r.top + r.bottom) // 2

        if r.left == 0 and r.top == 0 and r.right == 0 and r.bottom == 0:
            # Button rect invalid (UI thread hung) — click dialog centre
            user32.GetWindowRect(dlg, ctypes.byref(r))
            cx = (r.left + r.right) // 2
            cy = (r.top + r.bottom) // 2

        user32.SetCursorPos(cx, cy)
        _time.sleep(0.05)
        user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        _time.sleep(0.05)
        user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)

    for _round in range(max_rounds):
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

        user32.EnumWindows(WNDENUMPROC(_enum_cb), None)

        if not dialogs:
            break  # no dialogs visible — done

        dismissed_this_round = False
        for dlg in dialogs:
            # Collect ALL visible children with text, noting class
            buttons: list[tuple[int, str, str]] = []  # (hwnd, text, class)

            def _btn_cb(h: int, _: Any) -> bool:
                if not user32.IsWindowVisible(h):
                    return True
                ccls = ctypes.create_unicode_buffer(64)
                user32.GetClassNameW(h, ccls, 64)
                buf = ctypes.create_unicode_buffer(128)
                user32.GetWindowTextW(h, buf, 128)
                text = buf.value.strip()
                if text and ccls.value.lower() in _CLICKABLE_CLASSES:
                    buttons.append((h, text, ccls.value))
                return True

            user32.EnumChildWindows(dlg, WNDENUMPROC(_btn_cb), None)

            dismiss_btn = 0
            # Phase 1: exact match on cleaned text
            for btn_h, raw_text, _cls in buttons:
                cleaned = raw_text.replace("&", "").strip().lower()
                if cleaned in _DISMISS_LABELS_EXACT:
                    dismiss_btn = btn_h
                    break

            # Phase 2: substring match
            if not dismiss_btn:
                for sub in _DISMISS_SUBSTRINGS:
                    for btn_h, raw_text, _cls in buttons:
                        if sub in raw_text.replace("&", "").strip().lower():
                            dismiss_btn = btn_h
                            break
                    if dismiss_btn:
                        break

            if dismiss_btn:
                _click_control(dismiss_btn, dlg)
                _time.sleep(0.4)
                dismissed_this_round = True
                any_dismissed = True
            else:
                # No recognized button — try WM_CLOSE
                user32.PostMessageW(dlg, WM_CLOSE, 0, 0)
                _time.sleep(0.4)
                if not user32.IsWindowVisible(dlg):
                    dismissed_this_round = True
                    any_dismissed = True

        if not dismissed_this_round:
            break  # couldn't dismiss anything — stop looping

    # Restore focus to the main application window after dismissal
    if any_dismissed:
        user32.SetForegroundWindow(root_hwnd)
        _time.sleep(0.15)

    return any_dismissed


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


def _send_msg_timeout(
    hwnd: int, msg: int, wp: int, lp: int, timeout_ms: int = 2000,
) -> int | None:
    """``SendMessageTimeoutW`` — returns result or *None* on timeout/hang.

    Uses ``SMTO_ABORTIFHUNG | SMTO_BLOCK`` so we never wait longer than
    *timeout_ms*.  Callers that touch Quicken's UI thread during modal-dialog
    activity must use this instead of ``_send_msg`` to avoid deadlocking.
    """
    import ctypes  # noqa: PLC0415
    import ctypes.wintypes  # noqa: PLC0415

    _fn = ctypes.windll.user32.SendMessageTimeoutW
    if not hasattr(_fn, "_typed"):
        _fn.argtypes = [
            ctypes.wintypes.HWND,   # hWnd
            ctypes.wintypes.UINT,   # Msg
            ctypes.wintypes.WPARAM, # wParam
            ctypes.wintypes.LPARAM, # lParam
            ctypes.wintypes.UINT,   # fuFlags
            ctypes.wintypes.UINT,   # uTimeout
            ctypes.POINTER(ctypes.wintypes.DWORD),  # lpdwResult
        ]
        _fn.restype = ctypes.wintypes.LPARAM
        _fn._typed = True  # type: ignore[attr-defined]

    SMTO_ABORTIFHUNG = 0x0002
    SMTO_BLOCK = 0x0001
    result = ctypes.wintypes.DWORD(0)
    ok = _fn(
        hwnd, msg, wp, lp,
        SMTO_ABORTIFHUNG | SMTO_BLOCK,
        timeout_ms,
        ctypes.byref(result),
    )
    if not ok:
        return None  # timeout or hung thread
    return result.value


def _post_msg(hwnd: int, msg: int, wp: int, lp: int) -> bool:
    """Non-blocking ``PostMessageW`` — queues the message and returns."""
    import ctypes  # noqa: PLC0415

    _fn = ctypes.windll.user32.PostMessageW
    if not hasattr(_fn, "_typed"):
        import ctypes.wintypes  # noqa: PLC0415

        _fn.argtypes = [
            ctypes.wintypes.HWND,
            ctypes.wintypes.UINT,
            ctypes.wintypes.WPARAM,
            ctypes.wintypes.LPARAM,
        ]
        _fn.restype = ctypes.wintypes.BOOL
        _fn._typed = True  # type: ignore[attr-defined]
    return bool(_fn(hwnd, msg, wp, lp))


def _combo_get_items(hwnd: int) -> list[str]:
    """Read all items from a Win32 combobox via CB_GETCOUNT/CB_GETLBTEXT."""
    import ctypes  # noqa: PLC0415

    CB_GETCOUNT = 0x0146
    CB_GETLBTEXT = 0x0148
    CB_GETLBTEXTLEN = 0x0149

    count = _send_msg_timeout(hwnd, CB_GETCOUNT, 0, 0, timeout_ms=1500) or 0
    out: list[str] = []
    for i in range(min(count, 500)):
        tlen = _send_msg_timeout(hwnd, CB_GETLBTEXTLEN, i, 0,
                                 timeout_ms=1500)
        if not tlen or tlen <= 0:
            out.append("")
            continue
        buf = ctypes.create_unicode_buffer(tlen + 1)
        _send_msg_timeout(hwnd, CB_GETLBTEXT, i, ctypes.addressof(buf),
                          timeout_ms=1500)
        out.append(buf.value)
    return out


def _combo_cur_text(hwnd: int) -> str:
    """Read the currently selected item text from a Win32 combobox."""
    import ctypes  # noqa: PLC0415

    CB_GETCURSEL = 0x0147
    CB_GETLBTEXT = 0x0148
    CB_GETLBTEXTLEN = 0x0149

    idx = _send_msg_timeout(hwnd, CB_GETCURSEL, 0, 0, timeout_ms=1500)
    if idx is None or idx < 0:
        return ""
    tl = _send_msg_timeout(hwnd, CB_GETLBTEXTLEN, idx, 0, timeout_ms=1500)
    if not tl or tl <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(tl + 1)
    _send_msg_timeout(hwnd, CB_GETLBTEXT, idx, ctypes.addressof(buf),
                      timeout_ms=1500)
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
                    cnt = _send_msg_timeout(ch, 0x018B, 0, 0,
                                            timeout_ms=1500)
                    lb_vis = bool(user32.IsWindowVisible(ch))
                    if cnt and cnt > 0 and not lb_vis:
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
        user32.PostMessageW(btn, WM_LBUTTONDOWN, MK_LBUTTON, lparam)
        _time.sleep(0.05)
        user32.PostMessageW(btn, WM_LBUTTONUP, 0, lparam)
        _time.sleep(0.2)
        expanded = True

    if expanded:
        _time.sleep(0.15)
    return expanded


def _expand_single_section(lb_hwnd: int, holder: int) -> bool:
    """Expand only the sidebar section that contains *lb_hwnd*.

    This is much faster than ``_expand_sidebar_sections`` (which iterates
    ALL sections) — it finds the ``QWListViewer`` parent of lb_hwnd, locates
    the nearest ``QC_button`` toggle button above it, and clicks that button
    if the section appears collapsed.

    Returns True if a section was expanded.
    """
    import ctypes  # noqa: PLC0415
    import ctypes.wintypes as wt  # noqa: PLC0415
    import time as _time  # noqa: PLC0415

    user32 = ctypes.windll.user32
    cls_buf = ctypes.create_unicode_buffer(64)

    def _cls(h: int) -> str:
        user32.GetClassNameW(h, cls_buf, 64)
        return cls_buf.value

    # Walk up: ListBox → QWListViewer
    lv = user32.GetParent(lb_hwnd) if lb_hwnd else 0
    if not lv or _cls(lv) != "QWListViewer":
        return False

    # Get the QWListViewer's vertical position
    lv_rect = wt.RECT()
    user32.GetWindowRect(lv, ctypes.byref(lv_rect))
    lv_top = lv_rect.top

    # Find the nearest QC_button ABOVE this QWListViewer in the holder
    EnumCB = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    best_btn = 0
    best_y = -999999

    def _find_btn(h: int, _: int) -> bool:
        nonlocal best_btn, best_y
        if user32.GetParent(h) != holder:
            return True
        if _cls(h) != "QC_button":
            return True
        if not user32.IsWindowVisible(h):
            return True
        r = wt.RECT()
        user32.GetWindowRect(h, ctypes.byref(r))
        btn_top = r.top
        if btn_top < lv_top and btn_top > best_y:
            best_btn = h
            best_y = btn_top
        return True

    user32.EnumChildWindows(holder, EnumCB(_find_btn), 0)

    if not best_btn:
        return False

    # Click the button to toggle the section
    r = wt.RECT()
    user32.GetWindowRect(best_btn, ctypes.byref(r))
    mid_x = (r.right - r.left) // 2
    mid_y = (r.bottom - r.top) // 2
    lparam = (mid_y << 16) | (mid_x & 0xFFFF)
    WM_LBUTTONDOWN = 0x0201
    WM_LBUTTONUP = 0x0202
    MK_LBUTTON = 0x0001
    user32.PostMessageW(best_btn, WM_LBUTTONDOWN, MK_LBUTTON, lparam)
    _time.sleep(0.05)
    user32.PostMessageW(best_btn, WM_LBUTTONUP, 0, lparam)
    _time.sleep(0.3)
    return True


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
    if not holder or not user32.IsWindowVisible(holder):
        # Sidebar hidden — click the ACCOUNTS toolbar button to restore it
        acct_cls = ctypes.create_unicode_buffer(64)
        acct_txt = ctypes.create_unicode_buffer(256)
        acct_btn: int = 0
        def _find_acct_btn_old(ch: int, _: int) -> bool:
            nonlocal acct_btn
            user32.GetClassNameW(ch, acct_cls, 64)
            if acct_cls.value != "QC_button":
                return True
            if not user32.IsWindowVisible(ch):
                return True
            user32.GetWindowTextW(ch, acct_txt, 256)
            if acct_txt.value.upper() == "ACCOUNTS":
                acct_btn = ch
                return False
            return True
        user32.EnumChildWindows(root_hwnd, EnumCB(_find_acct_btn_old), 0)
        if acct_btn:
            wr = wt.RECT()
            user32.GetWindowRect(acct_btn, ctypes.byref(wr))
            mx = (wr.left + wr.right) // 2
            my = (wr.top + wr.bottom) // 2
            user32.SetForegroundWindow(root_hwnd)
            user32.SetCursorPos(mx, my)
            _time.sleep(0.05)
            user32.mouse_event(0x0002, 0, 0, 0, 0)
            user32.mouse_event(0x0004, 0, 0, 0, 0)
            _time.sleep(0.8)
        holder = _find_sidebar_holder(root_hwnd)
        if not holder or not user32.IsWindowVisible(holder):
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
        _send_msg_timeout(holder, WM_VSCROLL,
                          (pos << 16) | SB_THUMBPOSITION, 0,
                          timeout_ms=2000)
        _time.sleep(0.25)
        _send_msg_timeout(holder, WM_VSCROLL, SB_ENDSCROLL, 0,
                          timeout_ms=2000)
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
        count = _send_msg_timeout(h, 0x018B, 0, 0, timeout_ms=1500)
        if not count or count <= 0:
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
            count = _send_msg_timeout(h, 0x018B, 0, 0, timeout_ms=1500)
            if not count or count <= 0:
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
            rc = _send_msg_timeout(
                h, 0x0198, i, ctypes.addressof(ir), timeout_ms=1500)
            if rc is None:
                continue
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


def _bracket_name(title: str) -> str:
    """Extract the bracketed account name from a Quicken window title."""
    if "[" in title and "]" in title:
        raw = title[title.rfind("[") + 1 : title.rfind("]")]
        try:
            raw = raw.encode("cp1252").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
        return raw
    return ""


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
    _send_msg_timeout(holder, WM_VSCROLL, wp, 0, timeout_ms=2000)
    _time.sleep(0.05)
    _send_msg_timeout(holder, WM_VSCROLL, SB_ENDSCROLL, 0, timeout_ms=2000)
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
    _send_msg_timeout(holder_hwnd, WM_VSCROLL,
                      (target_pos << 16) | SB_THUMBPOSITION, 0,
                      timeout_ms=2000)
    _time.sleep(0.25)
    _send_msg_timeout(holder_hwnd, WM_VSCROLL, SB_ENDSCROLL, 0,
                      timeout_ms=2000)
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
    import sys  # noqa: PLC0415

    user32 = ctypes.windll.user32

    # Scroll the sidebar container so lb_hwnd is visible, then scroll the
    # ListBox to bring item_index to the top and recalculate screen coords.
    _lb_visible = True  # default: assume visible for non-lb paths
    client_x = client_y = 0
    _t0 = _time.monotonic()
    if lb_hwnd and item_index >= 0 and _is_valid_hwnd(lb_hwnd):
        # Scroll first — Quicken shows hidden ListBoxes once in viewport.
        if content_y_lb > 0 and holder_hwnd:
            _scroll_holder_to_content_y(holder_hwnd, content_y_lb)
        else:
            _scroll_holder_for_lb(lb_hwnd)
        # Give Quicken time to make the ListBox visible after scrolling.
        _time.sleep(0.3)
        if _SIDEBAR_DEBUG:
            print(f"  [dblclk] scroll={_time.monotonic()-_t0:.2f}s vis={user32.IsWindowVisible(lb_hwnd)}",
                  flush=True, file=sys.stderr)
        # Only expand if the LB is STILL hidden after scrolling (rare).
        # Use a targeted approach: just expand the parent section, not all.
        if not user32.IsWindowVisible(lb_hwnd) and holder_hwnd:
            _expand_single_section(lb_hwnd, holder_hwnd)
            _time.sleep(0.3)
            if _SIDEBAR_DEBUG:
                print(f"  [dblclk] expand={_time.monotonic()-_t0:.2f}s",
                      flush=True, file=sys.stderr)
            # Re-scroll after expansion shifts positions
            if content_y_lb > 0:
                _scroll_holder_to_content_y(holder_hwnd, content_y_lb)

        _lb_visible = bool(user32.IsWindowVisible(lb_hwnd))
        _was_visible = _lb_visible  # remember original visibility for fallback

        if _lb_visible:
            # ListBox is visible — scroll it and get screen coords for a
            # physical mouse click.  Use SendMessageTimeoutW to avoid blocking
            # for 10+ seconds when Quicken is loading account data.
            LB_SETTOPINDEX = 0x0197
            LB_GETITEMRECT = 0x0198
            _SMT = user32.SendMessageTimeoutW
            _SMT.argtypes = [wt.HWND, ctypes.c_uint, wt.WPARAM, wt.LPARAM,
                             ctypes.c_uint, ctypes.c_uint,
                             ctypes.POINTER(ctypes.c_size_t)]
            _SMT.restype = wt.LPARAM
            SMTO_ABORTIFHUNG = 0x0002
            _smto_result = ctypes.c_size_t(0)
            ret = _SMT(lb_hwnd, LB_SETTOPINDEX, max(0, item_index - 1), 0,
                       SMTO_ABORTIFHUNG, 3000, ctypes.byref(_smto_result))
            if ret == 0:
                # Timed out — keep _lb_visible True and use the original
                # screen_x/screen_y (from _find_sidebar_accounts) for a
                # physical click.  The item is on screen, just non-responsive.
                if _SIDEBAR_DEBUG:
                    print(f"  [dblclk] LB_SETTOPINDEX timeout, using original coords",
                          flush=True, file=sys.stderr)
            else:
                _time.sleep(0.05)
                ir = wt.RECT()
                ret2 = _SMT(lb_hwnd, LB_GETITEMRECT, item_index,
                             ctypes.addressof(ir),
                             SMTO_ABORTIFHUNG, 2000, ctypes.byref(_smto_result))
                if ret2 == 0:
                    # Use original screen coords — item is visible
                    if _SIDEBAR_DEBUG:
                        print(f"  [dblclk] LB_GETITEMRECT timeout, using original coords",
                              flush=True, file=sys.stderr)
                else:
                    pt = wt.POINT((ir.left + ir.right) // 2,
                                  (ir.top + ir.bottom) // 2)
                    client_x, client_y = pt.x, pt.y
                    user32.ClientToScreen(lb_hwnd, ctypes.byref(pt))
                    screen_x, screen_y = pt.x, pt.y
        # else: ListBox hidden — skip LB_SETTOPINDEX/LB_GETITEMRECT entirely;
        # the msg-click path uses LB_SETCURSEL by index, no coords needed.

        if _SIDEBAR_DEBUG:
            print(f"  [dblclk] setup_done={_time.monotonic()-_t0:.2f}s vis2={int(_lb_visible)}",
                  flush=True, file=sys.stderr)

    user32.SetForegroundWindow(root_hwnd)
    _time.sleep(0.05)

    # Dismiss any pre-existing modal dialogs before clicking
    _dismiss_modal_dialogs(root_hwnd)
    if _SIDEBAR_DEBUG:
        print(f"  [dblclk] pre_click={_time.monotonic()-_t0:.2f}s",
              flush=True, file=sys.stderr)

    # Choose click strategy based on ListBox visibility.
    # When the ListBox is hidden (Quicken's virtual viewport hides off-screen
    # sections), physical mouse clicks miss it entirely.  Fall back to sending
    # WM_LBUTTONDBLCLK directly to the ListBox window proc — this works even
    # when the window is hidden because the message is delivered directly.
    _use_msg_click = (lb_hwnd and item_index >= 0 and not _lb_visible)
    if _use_msg_click:
        # Select the item first, then send double-click message.
        # Use SendMessageTimeoutW for LB_SETCURSEL because SendMessage can
        # block 10+ seconds when Quicken is loading investment data.
        LB_SETCURSEL = 0x0186
        _SMT = user32.SendMessageTimeoutW
        _SMT.argtypes = [wt.HWND, ctypes.c_uint, wt.WPARAM, wt.LPARAM,
                         ctypes.c_uint, ctypes.c_uint,
                         ctypes.POINTER(ctypes.c_size_t)]
        _SMT.restype = wt.LPARAM
        SMTO_ABORTIFHUNG = 0x0002
        _smto_result = ctypes.c_size_t(0)
        ret = _SMT(lb_hwnd, LB_SETCURSEL, item_index, 0,
                   SMTO_ABORTIFHUNG, 3000, ctypes.byref(_smto_result))
        if ret == 0:
            if _SIDEBAR_DEBUG:
                print(f"  [dblclk] LB_SETCURSEL timeout, skipping",
                      flush=True, file=sys.stderr)
            return pre_title or ""
        _time.sleep(0.02)
        # Instead of sending WM_LBUTTONDBLCLK (which uses coordinates to
        # determine the clicked item — wrong when the ListBox isn't scrolled
        # to show our item), send WM_COMMAND(LBN_DBLCLK) to the parent.
        # This is what the ListBox normally sends after a double-click.
        # The parent uses LB_GETCURSEL (set by our LB_SETCURSEL above) to
        # determine which item was activated.
        LBN_DBLCLK = 2
        WM_COMMAND = 0x0111
        ctrl_id = user32.GetDlgCtrlID(lb_hwnd) & 0xFFFF
        parent_hwnd = user32.GetParent(lb_hwnd)
        wp_cmd = (LBN_DBLCLK << 16) | ctrl_id
        _post_msg(parent_hwnd, WM_COMMAND, wp_cmd, lb_hwnd)
        if _SIDEBAR_DEBUG:
            print(f"  [dblclk] msg_click lb={lb_hwnd} i={item_index} cx={client_x} cy={client_y}",
                  flush=True, file=sys.stderr)
    else:
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
        phase2_budget = min(0.15, timeout - bail_early)
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


def _sweep_scan_sidebar(root_hwnd: int, max_seconds: float = 300.0) -> list[dict[str, Any]]:
    """Discover sidebar accounts by scrolling and clicking ListBox items.

    Uses per-item targeting via LB_GETITEMRECT to click only actual ListBox
    items — never random pixel positions.  This avoids accidentally collapsing
    sidebar sections (which happens with grid-click approaches).

    Between passes, re-expands all sidebar sections to recover any that may
    have collapsed due to Quicken state changes.

    Returns a list of ``{name, section, nav_scroll, nav_y, ...}`` dicts.
    """
    import ctypes  # noqa: PLC0415
    import ctypes.wintypes as wt  # noqa: PLC0415
    import time as _time  # noqa: PLC0415
    import sys  # noqa: PLC0415

    user32 = ctypes.windll.user32
    SM = user32.SendMessageW
    SM.argtypes = [wt.HWND, ctypes.c_uint, wt.WPARAM, wt.LPARAM]
    SM.restype = wt.LPARAM
    EnumCB = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

    _holder = [_find_sidebar_holder(root_hwnd)]
    _acct_btn = [0]  # cached ACCOUNTS button handle
    if not _holder[0]:
        return []

    def _ensure_sidebar_visible() -> bool:
        h = _holder[0]
        if not h or not user32.IsWindow(h) or not user32.IsWindowVisible(h):
            h = _find_sidebar_holder(root_hwnd)
            if h:
                _holder[0] = h
        if h and user32.IsWindowVisible(h):
            return True
        if _SIDEBAR_DEBUG:
            print(f"  [ensure_vis] holder={_holder[0]:#x} "
                  f"IsWindow={user32.IsWindow(_holder[0])} "
                  f"IsVisible={user32.IsWindowVisible(_holder[0])}",
                  flush=True, file=sys.stderr)
        # Sidebar hidden — find the "ACCOUNTS" toolbar button and toggle it.
        # PostMessageW is used because the button hides alongside the sidebar
        # and SendMessage to invisible windows can stall.
        BM_CLICK = 0x00F5

        # Use cached ACCOUNTS button handle — only search if cache is invalid
        acct_btn = _acct_btn[0]
        if not acct_btn or not user32.IsWindow(acct_btn):
            acct_cls = ctypes.create_unicode_buffer(64)
            acct_txt = ctypes.create_unicode_buffer(256)
            acct_btn = 0
            def _find_acct_btn(ch: int, _: int) -> bool:
                nonlocal acct_btn
                user32.GetClassNameW(ch, acct_cls, 64)
                if acct_cls.value != "QC_button":
                    return True
                user32.GetWindowTextW(ch, acct_txt, 256)
                if acct_txt.value.upper() == "ACCOUNTS":
                    acct_btn = ch
                    return False
                return True
            user32.EnumChildWindows(root_hwnd, EnumCB(_find_acct_btn), 0)
            _acct_btn[0] = acct_btn

        if acct_btn:
            user32.SetForegroundWindow(root_hwnd)
            # Phase 1: Dismiss modals, send BM_CLICK, poll up to 20s.
            # Investment accounts can take 15-20s to process; banking
            # accounts complete in 3-5s.  The poll exits early on success.
            _dismiss_modal_dialogs(root_hwnd)
            if _SIDEBAR_DEBUG:
                print(f"  [ensure_vis] BM_CLICK phase1 btn={acct_btn:#x}",
                      flush=True, file=sys.stderr)
            user32.PostMessageW(acct_btn, BM_CLICK, 0, 0)
            cached_h = _holder[0]
            for _poll in range(80):  # 80 × 0.25s = 20s max
                _time.sleep(0.25)
                # Fast check: just test visibility of cached handle
                if cached_h and user32.IsWindow(cached_h) and user32.IsWindowVisible(cached_h):
                    if _SIDEBAR_DEBUG:
                        print(f"  [ensure_vis] restored phase1, "
                              f"{(_poll+1)*0.25:.2f}s",
                              flush=True, file=sys.stderr)
                    return True
                # Handle invalidated — re-find (rare)
                if not cached_h or not user32.IsWindow(cached_h):
                    h = _find_sidebar_holder(root_hwnd)
                    if h:
                        _holder[0] = h
                        cached_h = h
                        if user32.IsWindowVisible(h):
                            if _SIDEBAR_DEBUG:
                                print(f"  [ensure_vis] restored phase1 (re-find), "
                                      f"{(_poll+1)*0.25:.2f}s",
                                      flush=True, file=sys.stderr)
                            return True
            # Phase 2: Modal might have appeared during wait; dismiss + retry.
            if _dismiss_modal_dialogs(root_hwnd):
                if _SIDEBAR_DEBUG:
                    print("  [ensure_vis] dismissed modal, BM_CLICK phase2",
                          flush=True, file=sys.stderr)
                user32.PostMessageW(acct_btn, BM_CLICK, 0, 0)
                for _poll in range(12):
                    _time.sleep(0.25)
                    if cached_h and user32.IsWindow(cached_h) and user32.IsWindowVisible(cached_h):
                        if _SIDEBAR_DEBUG:
                            print(f"  [ensure_vis] restored phase2, "
                                  f"{(_poll+1)*0.25:.2f}s",
                                  flush=True, file=sys.stderr)
                        return True
                    if not cached_h or not user32.IsWindow(cached_h):
                        h = _find_sidebar_holder(root_hwnd)
                        if h:
                            _holder[0] = h
                            cached_h = h
                            if user32.IsWindowVisible(h):
                                if _SIDEBAR_DEBUG:
                                    print(f"  [ensure_vis] restored phase2 (re-find), "
                                          f"{(_poll+1)*0.25:.2f}s",
                                          flush=True, file=sys.stderr)
                                return True
            if _SIDEBAR_DEBUG:
                h2 = _find_sidebar_holder(root_hwnd)
                v2 = user32.IsWindowVisible(h2) if h2 else -1
                print(f"  [ensure_vis] FAILED: holder={h2:#x} vis={v2}",
                      flush=True, file=sys.stderr)
            h = _find_sidebar_holder(root_hwnd)
            if h:
                _holder[0] = h
        else:
            if _SIDEBAR_DEBUG:
                print("  [ensure_vis] NO ACCOUNTS btn found!",
                      flush=True, file=sys.stderr)
        return bool(h) and user32.IsWindowVisible(h) != 0

    if not _ensure_sidebar_visible():
        return []

    holder = _holder[0]
    user32.SetForegroundWindow(root_hwnd)

    # Expand sections twice — first pass may miss some due to layout shifts
    _expand_sidebar_sections(holder)
    _time.sleep(0.3)
    _expand_sidebar_sections(holder)
    _time.sleep(0.2)

    cls_buf = ctypes.create_unicode_buffer(64)
    txt_buf = ctypes.create_unicode_buffer(256)

    def _cls(h: int) -> str:
        user32.GetClassNameW(h, cls_buf, 64)
        return cls_buf.value

    def _txt(h: int) -> str:
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
    LB_GETCOUNT = 0x018B
    LB_GETITEMRECT = 0x0198

    def _get_si() -> _SI:
        si = _SI(); si.cbSize = ctypes.sizeof(si); si.fMask = 0x17
        user32.GetScrollInfo(_holder[0], 1, ctypes.byref(si))
        return si

    def _scroll_to(pos: int) -> None:
        _send_msg_timeout(
            _holder[0], WM_VSCROLL, (pos << 16) | SB_THUMBPOSITION, 0,
            timeout_ms=2000)
        _time.sleep(0.20)
        _send_msg_timeout(
            _holder[0], WM_VSCROLL, SB_ENDSCROLL, 0, timeout_ms=2000)
        _time.sleep(0.10)

    MOUSEEVENTF_WHEEL = 0x0800

    def _wheel_scroll(clicks: int = 5) -> None:
        """Scroll the sidebar down using mouse wheel events.

        *clicks* wheel notches to scroll (positive = down)."""
        user32.GetWindowRect(_holder[0], ctypes.byref(hr))
        cx = (hr.left + hr.right) // 2
        cy = (hr.top + hr.bottom) // 2
        user32.SetCursorPos(cx, cy)
        _time.sleep(0.05)
        for _ in range(clicks):
            user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, -120, 0)
            _time.sleep(0.05)
        _time.sleep(0.30)

    hr = wt.RECT()
    user32.GetWindowRect(holder, ctypes.byref(hr))
    viewport_h = hr.bottom - hr.top
    # Use 1/3 viewport steps for overlapping coverage
    step = max(50, viewport_h // 3)

    SECTION_NAMES = {
        "banking", "investing", "property & debt", "separate",
        "rental property", "business", "savings goals", "home",
        "all transactions",
    }

    accounts: dict[str, dict[str, Any]] = {}
    _invest_lbs: set[int] = set()  # ListBoxes in investment sections
    _defer_invest: set[int] = set()  # LBs deferred to investment phase
    _clicked: set[tuple[int, int]] = set()  # (lb_h, item_idx) already clicked
    _consec_dups: dict[int, int] = {}  # lb_h -> consecutive non-navigating clicks
    deadline = _time.monotonic() + max_seconds
    buf = ctypes.create_unicode_buffer(256)
    total_clicks = 0

    def _dblclick(sx: int, sy: int) -> None:
        """Double-click to navigate; single click only selects."""
        user32.SetCursorPos(sx, sy)
        _time.sleep(0.02)
        for _dc in range(2):
            user32.mouse_event(0x0002, 0, 0, 0, 0)
            user32.mouse_event(0x0004, 0, 0, 0, 0)
            _time.sleep(0.02)
        _time.sleep(0.20)

    def _read_bracket() -> str:
        user32.GetWindowTextW(root_hwnd, buf, 256)
        return _bracket_name(buf.value)

    def _read_active_mdi_name() -> str:
        """Read the active MDI child name directly (faster than title bar).

        Enumerates visible QWMDI and QWHtmlView children and returns the
        name of the largest one (the active/frontmost account view).
        """
        best_name = ""
        best_area = 0
        _mdi_cls = ctypes.create_unicode_buffer(64)
        _mdi_txt = ctypes.create_unicode_buffer(256)

        def _enum_mdi_active(h: int, _: int) -> bool:
            nonlocal best_name, best_area
            user32.GetClassNameW(h, _mdi_cls, 64)
            if _mdi_cls.value not in ("QWMDI", "QWHtmlView"):
                return True
            if not user32.IsWindowVisible(h):
                return True
            user32.GetWindowTextW(h, _mdi_txt, 256)
            n = _mdi_txt.value.strip()
            if not n:
                return True
            r = wt.RECT()
            user32.GetWindowRect(h, ctypes.byref(r))
            area = (r.right - r.left) * (r.bottom - r.top)
            if area > best_area:
                best_area = area
                best_name = n
            return True

        user32.EnumChildWindows(root_hwnd, EnumCB(_enum_mdi_active), 0)
        return best_name

    def _read_account_name() -> str:
        """Read current account name via title bar or MDI child."""
        name = _read_bracket()
        if not name:
            name = _read_active_mdi_name()
        return name

    MAX_PASSES = 4
    for pass_num in range(MAX_PASSES):
        if _time.monotonic() >= deadline:
            break
        accts_before = len(accounts)

        if not _ensure_sidebar_visible():
            _time.sleep(0.5)
            if not _ensure_sidebar_visible():
                break
        holder = _holder[0]

        # Re-expand sections each pass (navigations may collapse some)
        if pass_num > 0:
            _expand_sidebar_sections(holder)
            _time.sleep(0.3)
            # Reset consec_dups between passes so investment LBs
            # get retried — their items always look like dups.
            _consec_dups.clear()
            # Allow deferred investment LBs on subsequent passes
            # (pass 0 is banking-focused).
            _defer_invest.clear()

        si = _get_si()
        max_scroll = max(0, si.nMax - int(si.nPage) + 1)
        user32.GetWindowRect(holder, ctypes.byref(hr))
        viewport_h = hr.bottom - hr.top
        step = max(50, viewport_h // 3)

        if _SIDEBAR_DEBUG:
            print(f"  [pass {pass_num}] viewport={viewport_h}, "
                  f"max_scroll={max_scroll}, step={step}",
                  flush=True, file=sys.stderr)

        scroll_pos = 0
        sidebar_restored = False
        restore_budget = 30  # max sidebar restores per pass (invest items need many)
        _last_scroll = -1   # track last scroll position to skip redundant work
        _cached_lbs: list[tuple[int, int]] | None = None
        _t_loop_top = _time.monotonic()
        while scroll_pos <= max_scroll + step and _time.monotonic() < deadline:
            _t_iter_start = _time.monotonic()
            if _SIDEBAR_DEBUG:
                print(f"  [while-top] scroll_pos={scroll_pos} max_scroll={max_scroll} "
                      f"step={step} holder_vis={user32.IsWindowVisible(_holder[0])} "
                      f"dt_since_last={_t_iter_start - _t_loop_top:.2f}s",
                      flush=True, file=sys.stderr)
            _t_loop_top = _t_iter_start
            actual_pos = min(scroll_pos, max_scroll)
            user32.SetForegroundWindow(root_hwnd)
            # Only scroll + enumerate LBs if scroll position changed
            if actual_pos != _last_scroll:
                _scroll_to(actual_pos)
                _dismiss_modal_dialogs(root_hwnd)
                _last_scroll = actual_pos
                _cached_lbs = None  # invalidate cache on scroll change
            user32.GetWindowRect(holder, ctypes.byref(hr))
            htop, hbot = hr.top, hr.bottom

            if _cached_lbs is not None:
                vis_lbs = _cached_lbs
            else:
                # Enumerate visible ListBoxes at this scroll position
                vis_lbs = []

                def _enum_lb(h: int, _: int) -> bool:
                    if _cls(h) != "ListBox":
                        return True
                    wr = wt.RECT()
                    user32.GetWindowRect(h, ctypes.byref(wr))
                    if wr.bottom - wr.top > 5:
                        cnt = _send_msg_timeout(
                            h, LB_GETCOUNT, 0, 0, timeout_ms=1500)
                        if cnt and cnt > 0:
                            vis_lbs.append((h, cnt))
                    return True

                user32.EnumChildWindows(holder, EnumCB(_enum_lb), 0)
                _cached_lbs = vis_lbs

            if _SIDEBAR_DEBUG:
                print(f"  [pass {pass_num}] scroll={actual_pos}: "
                      f"{len(vis_lbs)} LBs, htop={htop} hbot={hbot}",
                      flush=True, file=sys.stderr)

            # Track ListBoxes in investment sections (cause sidebar hide)
            for lb_h, count in vis_lbs:
                if _time.monotonic() >= deadline:
                    break
                # Deferred investment LBs are processed in later passes
                if lb_h in _defer_invest:
                    if _SIDEBAR_DEBUG:
                        print(f"  [defer] lb={lb_h:#x}: deferred to investment phase",
                              flush=True, file=sys.stderr)
                    continue
                # Investment LBs always look like dups (title updates
                # slowly after sidebar recovery), so use a much higher
                # threshold — we rely on _clicked to avoid repeats.
                _dup_limit = 8 if lb_h in _invest_lbs else 3
                if _consec_dups.get(lb_h, 0) >= _dup_limit:
                    if _SIDEBAR_DEBUG:
                        print(f"  [skip-lb] lb={lb_h:#x}: "
                              f"{_consec_dups[lb_h]} consecutive dups "
                              f"(limit={_dup_limit}), skipping rest",
                              flush=True, file=sys.stderr)
                    continue
                for i in range(min(count, 30)):
                    if _time.monotonic() >= deadline:
                        break
                    if _consec_dups.get(lb_h, 0) >= _dup_limit:
                        if _SIDEBAR_DEBUG:
                            print(f"  [skip-lb] lb={lb_h:#x}: "
                                  f"{_consec_dups[lb_h]} consecutive dups "
                                  f"(limit={_dup_limit}), skipping rest",
                                  flush=True, file=sys.stderr)
                        break
                    if (lb_h, i) in _clicked:
                        continue  # already clicked this item
                    _t_item_start = _time.monotonic()
                    ir = wt.RECT()
                    rc = _send_msg_timeout(
                        lb_h, LB_GETITEMRECT, i, ctypes.addressof(ir),
                        timeout_ms=1500)
                    if rc is None:
                        if _SIDEBAR_DEBUG:
                            print(f"  [skip] lb={lb_h:#x} i={i}: "
                                  f"GETITEMRECT=None dt={_time.monotonic()-_t_item_start:.2f}s",
                                  flush=True, file=sys.stderr)
                        continue
                    item_h = ir.bottom - ir.top
                    if item_h < 10:
                        continue

                    pt = wt.POINT((ir.left + ir.right) // 2,
                                  (ir.top + ir.bottom) // 2)
                    user32.ClientToScreen(lb_h, ctypes.byref(pt))
                    if pt.y < htop or pt.y > hbot:
                        if _SIDEBAR_DEBUG:
                            print(f"  [skip] lb={lb_h:#x} i={i}: "
                                  f"y={pt.y} outside [{htop},{hbot}]",
                                  flush=True, file=sys.stderr)
                        continue

                    pre = _read_bracket()
                    if not pre:
                        pre = _read_active_mdi_name()
                    if not pre:
                        # Title blank — try brief wait + modal dismiss
                        _time.sleep(0.5)
                        _dismiss_modal_dialogs(root_hwnd)
                        pre = _read_bracket()
                        if not pre:
                            pre = _read_active_mdi_name()
                    if not pre:
                        if _SIDEBAR_DEBUG:
                            user32.GetWindowTextW(root_hwnd, buf, 256)
                            print(f"  [skip] lb={lb_h:#x} i={i}: "
                                  f"empty pre, title={buf.value!r}",
                                  flush=True, file=sys.stderr)
                        continue

                    # Record pre-click account (already navigated there)
                    if (pre.lower() not in SECTION_NAMES
                            and pre not in accounts):
                        accounts[pre] = {"name": pre, "section": "",
                                         "nav_lb": lb_h, "nav_item": i,
                                         "nav_scroll": actual_pos,
                                         "nav_y": pt.y, "nav_sx": pt.x}

                    _clicked.add((lb_h, i))
                    _t_click = _time.monotonic()
                    _dblclick(pt.x, pt.y)
                    total_clicks += 1

                    # Dismiss modals after click — investment accounts
                    # can spawn Securities Comparison Mismatch dialogs.
                    _dismiss_modal_dialogs(root_hwnd)
                    _t_post_modal = _time.monotonic()

                    # --- Determine post-click account name ---------------
                    sidebar_still_up = user32.IsWindowVisible(_holder[0])

                    post = _read_bracket()
                    if not post:
                        post = _read_active_mdi_name()
                    if not post:
                        # Title blank (loading) — poll up to 2s
                        for _w in range(4):
                            _time.sleep(0.5)
                            _dismiss_modal_dialogs(root_hwnd)
                            post = _read_bracket()
                            if not post:
                                post = _read_active_mdi_name()
                            if post:
                                break
                    elif post == pre and sidebar_still_up:
                        # Banking item: re-clicking current account (0.5s poll)
                        _time.sleep(0.5)
                        _dismiss_modal_dialogs(root_hwnd)
                        recheck = _read_bracket()
                        if not recheck:
                            recheck = _read_active_mdi_name()
                        if recheck and recheck != pre:
                            post = recheck
                    _t_post_read = _time.monotonic()

                    # When sidebar hides (investment click), the title bar
                    # keeps the PREVIOUS account name until after sidebar
                    # recovery.  The real account name will appear as the
                    # "pre" value in the next iteration.  No need to poll
                    # here — just proceed to sidebar recovery.

                    navigated = post and post != pre
                    name = post or pre
                    if navigated:
                        _consec_dups[lb_h] = 0
                    elif sidebar_still_up:
                        # Banking account dup (sidebar didn't hide)
                        _consec_dups[lb_h] = _consec_dups.get(lb_h, 0) + 1

                    if _SIDEBAR_DEBUG:
                        tag = "NAV" if navigated else "dup"
                        vis = "vis" if sidebar_still_up else "HID"
                        print(f"  [pass {pass_num}] #{total_clicks} "
                              f"{tag}({vis}): pre={pre!r} post={post!r} "
                              f"(scroll={actual_pos} lb={lb_h} "
                              f"i={i} y={pt.y} h={ir.bottom-ir.top}) "
                              f"dt_click={_t_post_modal - _t_click:.2f}s "
                              f"dt_post={_t_post_read - _t_post_modal:.2f}s",
                              flush=True, file=sys.stderr)

                    if name and name.lower() not in SECTION_NAMES:
                        if name not in accounts:
                            accounts[name] = {"name": name, "section": ""}
                        if navigated:
                            accounts[name]["nav_scroll"] = actual_pos
                            accounts[name]["nav_y"] = pt.y
                            accounts[name]["nav_sx"] = pt.x
                            accounts[name]["nav_lb"] = lb_h
                            accounts[name]["nav_item"] = i

                    # Mid-scan sidebar recovery: Quicken hides the
                    # sidebar on every double-click.  Restore immediately
                    # with BM_CLICK, then check title/MDI for new name.
                    # Banking accounts update the title within 3s of
                    # recovery; investment accounts often don't (10-30s
                    # load time).  For known-invest LBs, skip the title
                    # wait to iterate faster.
                    if not user32.IsWindowVisible(_holder[0]):
                        _t_recov = _time.monotonic()
                        if restore_budget <= 0 or not _ensure_sidebar_visible():
                            if _SIDEBAR_DEBUG:
                                print(f"  [pass {pass_num}] sidebar recovery "
                                      f"FAILED for click #{total_clicks} "
                                      f"({name!r}), lb={lb_h:#x} i={i}",
                                      flush=True, file=sys.stderr)
                            break
                        restore_budget -= 1
                        _recov_dt = _time.monotonic() - _t_recov
                        holder = _holder[0]
                        user32.GetWindowRect(holder, ctypes.byref(hr))
                        htop, hbot = hr.top, hr.bottom

                        # Classify: fast recovery = banking, slow = invest
                        if _recov_dt >= 8.0:
                            _invest_lbs.add(lb_h)
                            if pass_num == 0:
                                _defer_invest.add(lb_h)

                        is_invest = lb_h in _invest_lbs

                        if _SIDEBAR_DEBUG:
                            tag = "invest" if is_invest else "banking"
                            print(f"  [pass {pass_num}] sidebar hidden after "
                                  f"click #{total_clicks} ({name!r}), "
                                  f"lb={lb_h:#x} i={i} [{tag}] "
                                  f"dt_recov={_recov_dt:.2f}s",
                                  flush=True, file=sys.stderr)

                        # Read new account name (MDI first, then title).
                        new_name = _read_active_mdi_name()
                        if not new_name or new_name == pre:
                            new_name = _read_bracket()

                        # For banking LBs, wait for title to stabilize
                        # (accounts load in 3-5s). For invest LBs, skip
                        # the wait — title rarely changes and waiting
                        # wastes time budget.
                        if (new_name == pre or not new_name) and not is_invest:
                            for _tw in range(6):
                                _time.sleep(0.5)
                                _dismiss_modal_dialogs(root_hwnd)
                                new_name = _read_active_mdi_name()
                                if not new_name or new_name == pre:
                                    new_name = _read_bracket()
                                if new_name and new_name != pre:
                                    break

                        if (new_name and new_name != pre
                                and new_name.lower() not in SECTION_NAMES
                                and new_name not in accounts):
                            accounts[new_name] = {
                                "name": new_name, "section": "",
                                "nav_lb": lb_h, "nav_item": i,
                                "nav_scroll": actual_pos,
                                "nav_y": pt.y, "nav_sx": pt.x,
                            }
                            _consec_dups[lb_h] = 0
                            if _SIDEBAR_DEBUG:
                                print(f"  [recovery] captured new account: "
                                      f"{new_name!r}",
                                      flush=True, file=sys.stderr)
                        elif new_name and new_name != pre:
                            _consec_dups[lb_h] = 0
                        else:
                            pass  # don't penalize — click DID navigate
                        if _SIDEBAR_DEBUG:
                            print(f"  [recovery] total "
                                  f"{_time.monotonic() - _t_recov:.2f}s "
                                  f"title={new_name!r}",
                                  flush=True, file=sys.stderr)
                        sidebar_restored = True
                        break  # break item loop; re-enumerate LBs
                else:
                    continue  # item loop finished normally
                break  # item loop broken — break LB loop too

            if _SIDEBAR_DEBUG:
                print(f"  [pass {pass_num}] scroll={actual_pos} done, "
                      f"accounts={len(accounts)} restored={sidebar_restored}",
                      flush=True, file=sys.stderr)

            if sidebar_restored:
                sidebar_restored = False
                if _SIDEBAR_DEBUG:
                    remaining = deadline - _time.monotonic()
                    print(f"  [continue] scroll_pos={scroll_pos} "
                          f"remaining={remaining:.1f}s",
                          flush=True, file=sys.stderr)
                continue  # re-do same scroll position

            # If sidebar is hidden after a failed recovery, the BM_CLICK
            # from the inner recovery is likely still being processed.
            # DO NOT send another BM_CLICK (it would toggle sidebar OFF).
            # Just poll for the pending BM_CLICK to take effect.
            if not user32.IsWindowVisible(_holder[0]):
                _poll_ok = False
                _cached_h = _holder[0]
                for _lp in range(32):  # poll up to 8s
                    _time.sleep(0.25)
                    if _cached_h and user32.IsWindow(_cached_h) and user32.IsWindowVisible(_cached_h):
                        _poll_ok = True
                        break
                    if not _cached_h or not user32.IsWindow(_cached_h):
                        _h = _find_sidebar_holder(root_hwnd)
                        if _h:
                            _holder[0] = _h
                            _cached_h = _h
                            if user32.IsWindowVisible(_h):
                                _poll_ok = True
                                break
                if _poll_ok:
                    holder = _holder[0]
                    if _SIDEBAR_DEBUG:
                        print(f"  [late-recover] poll succeeded after "
                              f"{(_lp+1)*0.25:.2f}s, re-doing scroll={actual_pos}",
                              flush=True, file=sys.stderr)
                    continue  # re-do same scroll position with recovered sidebar
                else:
                    if _SIDEBAR_DEBUG:
                        print(f"  [late-recover] poll FAILED after 8s, advancing scroll",
                              flush=True, file=sys.stderr)

            scroll_pos += step

        # --- Wheel-scroll extension ---
        # After exhausting the scroll-to loop, use mouse-wheel events to
        # reach accounts that sit below the scroll range reported by
        # GetScrollInfo (the value is often smaller than the true content).
        if _time.monotonic() < deadline:
            _wheel_passes = 0
            _max_wheel_passes = 8  # 5 notches × 8 = 40 notches total
            while _wheel_passes < _max_wheel_passes and _time.monotonic() < deadline:
                _pre_wheel = len(accounts)
                if not _ensure_sidebar_visible():
                    break
                holder = _holder[0]
                _wheel_scroll(clicks=5)
                _dismiss_modal_dialogs(root_hwnd)
                _wheel_passes += 1
                # Invalidate cached LBs since scroll changed
                _cached_lbs = None
                user32.GetWindowRect(holder, ctypes.byref(hr))
                htop, hbot = hr.top, hr.bottom

                # Re-enumerate visible ListBoxes
                vis_lbs = []

                def _enum_lb_wheel(h: int, _: int) -> bool:
                    if _cls(h) != "ListBox":
                        return True
                    wr = wt.RECT()
                    user32.GetWindowRect(h, ctypes.byref(wr))
                    if wr.bottom - wr.top > 5:
                        cnt = _send_msg_timeout(
                            h, LB_GETCOUNT, 0, 0, timeout_ms=1500)
                        if cnt and cnt > 0:
                            vis_lbs.append((h, cnt))
                    return True

                user32.EnumChildWindows(holder, EnumCB(_enum_lb_wheel), 0)

                if _SIDEBAR_DEBUG:
                    print(f"  [wheel {_wheel_passes}] "
                          f"{len(vis_lbs)} LBs, htop={htop} hbot={hbot}",
                          flush=True, file=sys.stderr)

                _found_new_in_wheel = False
                for lb_h, count in vis_lbs:
                    if _time.monotonic() >= deadline:
                        break
                    if lb_h in _defer_invest:
                        continue
                    if _consec_dups.get(lb_h, 0) >= 3:
                        continue
                    for i in range(min(count, 30)):
                        if _time.monotonic() >= deadline:
                            break
                        if _consec_dups.get(lb_h, 0) >= 3:
                            break
                        if (lb_h, i) in _clicked:
                            continue
                        ir = wt.RECT()
                        rc = _send_msg_timeout(
                            lb_h, LB_GETITEMRECT, i, ctypes.addressof(ir),
                            timeout_ms=1500)
                        if rc is None:
                            continue
                        item_h = ir.bottom - ir.top
                        if item_h < 10:
                            continue
                        pt = wt.POINT((ir.left + ir.right) // 2,
                                      (ir.top + ir.bottom) // 2)
                        user32.ClientToScreen(lb_h, ctypes.byref(pt))
                        if pt.y < htop or pt.y > hbot:
                            continue
                        pre = _read_bracket()
                        if not pre:
                            pre = _read_active_mdi_name()
                        if not pre:
                            _time.sleep(0.5)
                            _dismiss_modal_dialogs(root_hwnd)
                            pre = _read_bracket()
                            if not pre:
                                pre = _read_active_mdi_name()
                        if not pre:
                            continue
                        if (pre.lower() not in SECTION_NAMES
                                and pre not in accounts):
                            accounts[pre] = {"name": pre, "section": "",
                                             "nav_lb": lb_h, "nav_item": i}
                        _clicked.add((lb_h, i))
                        _t_click = _time.monotonic()
                        _dblclick(pt.x, pt.y)
                        total_clicks += 1
                        _dismiss_modal_dialogs(root_hwnd)
                        _time.sleep(0.30)
                        post = _read_bracket()
                        if not post:
                            post = _read_active_mdi_name()
                        navigated = (post and post != pre)
                        sidebar_still_up = user32.IsWindowVisible(_holder[0])
                        if navigated:
                            if (post.lower() not in SECTION_NAMES
                                    and post not in accounts):
                                accounts[post] = {"name": post, "section": "",
                                                  "nav_lb": lb_h, "nav_item": i}
                                _found_new_in_wheel = True
                            _consec_dups[lb_h] = 0
                        elif sidebar_still_up:
                            _consec_dups[lb_h] = _consec_dups.get(lb_h, 0) + 1
                        if not sidebar_still_up:
                            # Sidebar hidden — need recovery
                            if _SIDEBAR_DEBUG:
                                print(f"  [wheel {_wheel_passes}] "
                                      f"sidebar hidden after click, recovering",
                                      flush=True, file=sys.stderr)
                            if not _ensure_sidebar_visible():
                                break
                            holder = _holder[0]
                            new_name = _read_active_mdi_name()
                            if not new_name or new_name == pre:
                                new_name = _read_bracket()
                            if (new_name and new_name != pre
                                    and new_name.lower() not in SECTION_NAMES
                                    and new_name not in accounts):
                                accounts[new_name] = {"name": new_name,
                                                      "section": "",
                                                      "nav_lb": lb_h,
                                                      "nav_item": i}
                                _found_new_in_wheel = True
                                _consec_dups[lb_h] = 0
                            elif new_name and new_name != pre:
                                _consec_dups[lb_h] = 0
                            else:
                                _consec_dups[lb_h] = (
                                    _consec_dups.get(lb_h, 0) + 1)
                            # Re-scroll to same position
                            _wheel_scroll(clicks=5 * _wheel_passes)
                            _dismiss_modal_dialogs(root_hwnd)
                            break  # break item loop, retry

                if _SIDEBAR_DEBUG:
                    _post_wheel = len(accounts)
                    print(f"  [wheel {_wheel_passes}] "
                          f"+{_post_wheel - _pre_wheel} accounts "
                          f"(total {_post_wheel})",
                          flush=True, file=sys.stderr)

                # Stop wheel scrolling if no new items found in 2 passes
                if _wheel_passes >= 2 and len(accounts) == _pre_wheel:
                    if _SIDEBAR_DEBUG:
                        print(f"  [wheel] no new accounts in pass "
                              f"{_wheel_passes}, stopping wheel scan",
                              flush=True, file=sys.stderr)
                    break

        if _SIDEBAR_DEBUG:
            print(f"  [while-exit] scroll_pos={scroll_pos} "
                  f"deadline_reached={_time.monotonic() >= deadline}",
                  flush=True, file=sys.stderr)
        new_accounts = len(accounts) - accts_before
        if _SIDEBAR_DEBUG:
            print(f"  [pass {pass_num}] done: +{new_accounts} "
                  f"(total {len(accounts)})",
                  flush=True, file=sys.stderr)

        if new_accounts == 0:
            break

    if _SIDEBAR_DEBUG:
        elapsed = max_seconds - (deadline - _time.monotonic())
        print(f"  [sweep] DONE: {len(accounts)} accounts, {total_clicks} clicks, "
              f"{elapsed:.1f}s",
              flush=True, file=sys.stderr)

    # Supplement with MDI tabs — investment accounts that are open as tabs
    # but couldn't be discovered via sidebar clicking.
    mdi_cls = ctypes.create_unicode_buffer(64)
    mdi_txt = ctypes.create_unicode_buffer(256)

    def _enum_mdi(h: int, _: int) -> bool:
        user32.GetClassNameW(h, mdi_cls, 64)
        # QWMDI = banking registers, QWHtmlView = investment views
        if mdi_cls.value not in ("QWMDI", "QWHtmlView"):
            return True
        user32.GetWindowTextW(h, mdi_txt, 256)
        name = mdi_txt.value.strip()
        if name and name.lower() not in SECTION_NAMES and name != "Home":
            if name not in accounts:
                accounts[name] = {"name": name, "section": "",
                                  "source": "mdi_tab"}
        return True

    user32.EnumChildWindows(root_hwnd, EnumCB(_enum_mdi), 0)

    if _SIDEBAR_DEBUG:
        print(f"  [sweep] after MDI supplement: {len(accounts)} accounts",
              flush=True, file=sys.stderr)

    return list(accounts.values())


def list_sidebar_accounts(bridge: Any, resume: bool = False,
                           max_seconds: float = 300.0,
                           force_rescan: bool = False) -> dict[str, Any]:
    """Discover sidebar accounts by scrolling through and clicking visible items.

    Uses a scroll-sweep approach: walks the sidebar holder's scroll range in
    overlapping viewport-sized steps, clicking only physically-visible items
    at each position.  This avoids stale content-Y estimates and unreliable
    message-based clicks on hidden ListBoxes.

    When a previous scan has populated the cache, returns cached results
    immediately unless ``force_rescan`` is set.  This avoids the 300-second
    full scan on every call.

    Parameters
    ----------
    bridge
        Active UIABridge instance (unused directly; used for pm attachment).
    resume
        Accepted for API compatibility but ignored — the sweep scan always
        starts fresh and completes in a single call.
    max_seconds
        Seconds budget for the entire scan (default 300).
    force_rescan
        If True, discard the cache and run a fresh scan.

    Returns
    -------
    dict with keys:
        ``ok`` -- always ``True`` on success.
        ``accounts`` -- list of ``{name, section}`` dicts discovered.
        ``scanned`` -- number of sidebar items clicked.
        ``total`` -- total sidebar items (estimated).
        ``done`` -- always ``True`` (sweep completes in one call).
        ``cached`` -- ``True`` if results were served from cache.
    """
    import time as _time  # noqa: PLC0415

    from server.process_manager import get_process_manager  # noqa: PLC0415
    pm = get_process_manager()
    if not pm.attached:
        raise TargetNotFoundError("Use select_window to attach first.")
    root = pm.attached.hwnd

    global _sidebar_cache  # noqa: PLW0603

    # Return cached results when available (avoids 300s rescan)
    if _sidebar_cache and not force_rescan:
        accts = [{"name": e["name"], "section": e.get("section", "")}
                 for e in _sidebar_cache]
        return {
            "ok": True,
            "accounts": accts,
            "scanned": len(accts),
            "total": len(accts),
            "done": True,
            "cached": True,
        }

    results = _sweep_scan_sidebar(root, max_seconds=max_seconds)
    _sidebar_cache = list(results)

    return {
        "ok": True,
        "accounts": results,
        "scanned": len(results),
        "total": len(results),
        "done": True,
        "cached": False,
    }


# Module-level sidebar cache: name → sidebar_item dict with screen coords
_sidebar_cache: list[dict[str, Any]] = []

# Resumable scan state for list_sidebar_accounts
_scan_state: dict[str, Any] = {}

# Last successful navigate_to_account result: hwnd + name, used by
# read_register_state to re-activate the target MDI before reading.
# Cleared after first use so it doesn't affect unrelated reads.
_last_nav: dict[str, Any] = {}  # {"hwnd": int, "name": str}


def _activate_mdi(mdi_h: int) -> None:
    """Activate a QWMDI child window.

    BringWindowToTop + SetFocus is the reliable mechanism in Quicken's
    MDI architecture.  WM_MDIACTIVATE (sent to MDIClient) does not work.
    """
    import ctypes as _ct  # noqa: PLC0415
    _ct.windll.user32.BringWindowToTop(mdi_h)
    _ct.windll.user32.SetFocus(mdi_h)


def _sidebar_cache_add(name: str, scroll: int, y: int, sx: int,
                       lb: int, item: int) -> None:
    """Add or update an entry in the sidebar cache."""
    for entry in _sidebar_cache:
        if entry["name"].lower() == name.lower():
            entry.update(nav_scroll=scroll, nav_y=y, nav_sx=sx,
                         nav_lb=lb, nav_item=item)
            return
    _sidebar_cache.append({
        "name": name, "section": "",
        "nav_scroll": scroll, "nav_y": y, "nav_sx": sx,
        "nav_lb": lb, "nav_item": item,
    })


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

    target_name = account_name  # convenience alias

    EnumCB = ctypes.WINFUNCTYPE(
        ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
    )

    def _verify_and_stabilize(result: dict) -> dict:
        """Post-navigate stabilization with retry loop.

        After any successful navigation, verify the active account
        matches what we expect.  If Quicken switched away (common
        after sidebar clicks that trigger MDI activations in the
        background), re-activate the target QWMDI — up to 4 attempts
        with increasing wait times to let Quicken settle.
        """
        if not result.get("ok"):
            return result
        expected = result.get("account", "")
        if not expected:
            return result

        _vbuf = ctypes.create_unicode_buffer(256)

        def _bracket_ok() -> bool:
            _dismiss_modal_dialogs(root_hwnd)
            user32.GetWindowTextW(root_hwnd, _vbuf, 256)
            cur = _bracket_name(_vbuf.value)
            return bool(cur and _acct_match(cur, target_name))

        def _find_target_mdi() -> int:
            """Find a visible QWMDI whose title matches expected."""
            _found: list[int] = []

            def _ft(h: int, _: int) -> bool:
                cls = ctypes.create_unicode_buffer(64)
                user32.GetClassNameW(h, cls, 64)
                if cls.value != "QWMDI" or not user32.IsWindowVisible(h):
                    return True
                _tb = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(h, _tb, 256)
                if _tb.value.strip().lower() == expected.lower():
                    _found.append(h)
                    return False
                if _acct_match(_tb.value.strip(), expected):
                    _found.append(h)
                return True

            user32.EnumChildWindows(root_hwnd, EnumCB(_ft), 0)
            return _found[0] if _found else 0

        def _lock_in(mdi_h: int) -> None:
            """Re-activate target MDI and record it for read_register_state.

            Called just before returning success so that the QWMDI activation
            is the very last operation, giving read_register_state the best
            chance of seeing the correct account even if Quicken tries to
            switch away afterward.
            """
            if mdi_h:
                _activate_mdi(mdi_h)
            else:
                # No QWMDI title matches expected — Quicken may have reused
                # an existing QWMDI without updating its title.  Try
                # WM_MDIGETACTIVE to get the currently active QWMDI, which
                # is likely showing the target account's content.
                WM_MDIGETACTIVE_LI = 0x0229
                _mc_li: list[int] = []

                def _fmc_li(h: int, _: int) -> bool:
                    _cls_li = ctypes.create_unicode_buffer(64)
                    user32.GetClassNameW(h, _cls_li, 64)
                    if _cls_li.value == "MDIClient":
                        _mc_li.append(h)
                        return False
                    return True

                user32.EnumChildWindows(root_hwnd, EnumCB(_fmc_li), 0)
                if _mc_li:
                    _active_li = _send_msg_timeout(
                        _mc_li[0], WM_MDIGETACTIVE_LI, 0, 0, timeout_ms=1500)
                    if _active_li and user32.IsWindow(_active_li):
                        mdi_h = _active_li
                if mdi_h:
                    _activate_mdi(mdi_h)
            _last_nav["hwnd"] = mdi_h
            _last_nav["name"] = expected

        # First check — give Quicken a moment to settle.
        time.sleep(0.5)
        if _bracket_ok():
            # Confirm stable — Quicken sometimes shows the right account
            # briefly then switches away (race with MDI reactivation).
            time.sleep(0.5)
            if _bracket_ok():
                mdi_h = _find_target_mdi()
                _lock_in(mdi_h)
                return result

        # Retry loop: find target QWMDI, re-activate, verify.
        # For investment accounts _find_target_mdi() returns 0 (empty QWMDI
        # title), so we fall through to _lock_in(0) which uses WM_MDIGETACTIVE.
        # In all cases, once the bracket is confirmed we return immediately —
        # we do NOT need a matching QWMDI title for success.
        delays = [0.5, 0.8, 1.0, 1.5]
        for attempt, delay in enumerate(delays):
            mdi_h = _find_target_mdi()
            if mdi_h:
                _activate_mdi(mdi_h)
            time.sleep(delay)
            if _bracket_ok():
                time.sleep(0.5)
                if _bracket_ok():
                    _lock_in(mdi_h)
                    return result
            if mdi_h:
                # Extra force — SetForegroundWindow + re-activate
                user32.SetForegroundWindow(root_hwnd)
                _activate_mdi(mdi_h)
                time.sleep(delay)
                if _bracket_ok():
                    time.sleep(0.5)
                    if _bracket_ok():
                        _lock_in(mdi_h)
                        return result

        # All retries exhausted — lock in via WM_MDIGETACTIVE so read_register_state
        # still has a valid _last_nav to work with, then report with warning.
        _lock_in(0)
        user32.GetWindowTextW(root_hwnd, _vbuf, 256)
        current = _bracket_name(_vbuf.value)
        result["warning"] = (
            f"Navigate succeeded but Quicken switched to "
            f"'{current or 'unknown'}' afterward.  Use existing_tab "
            f"or retry."
        )
        return result

    # ------------------------------------------------------------------
    # Phase 1: Check if there's already a QWMDI child showing this acct.
    # If so, bring it to front — no combo manipulation needed.
    # Prefer exact title match over fuzzy to avoid "3 Duggan" vs
    # "3 Duggan Loan" ambiguity.
    # ------------------------------------------------------------------
    exact_mdi = None
    fuzzy_mdi = None
    fuzzy_mdi_len = 999999

    def _find_existing(h: int, _: int) -> bool:
        nonlocal exact_mdi, fuzzy_mdi, fuzzy_mdi_len
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(h, cls, 64)
        if cls.value != "QWMDI" or not user32.IsWindowVisible(h):
            return True
        mdi_title = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(h, mdi_title, 256)
        t = mdi_title.value.strip()
        if t.lower() == account_name.lower():
            exact_mdi = h
            return False  # stop — exact match wins
        if _acct_match(t, account_name):
            if len(t) < fuzzy_mdi_len:
                fuzzy_mdi = h
                fuzzy_mdi_len = len(t)
        return True

    user32.EnumChildWindows(root_hwnd, EnumCB(_find_existing), 0)

    existing_mdi = exact_mdi or fuzzy_mdi
    if existing_mdi:
        _activate_mdi(existing_mdi)
        time.sleep(0.3)
        _mtitle = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(existing_mdi, _mtitle, 256)
        matched_name = _mtitle.value.strip() or account_name
        # If this was a fuzzy match, verify it's reasonable: the matched
        # account should be shorter than the query (abbreviation) or within
        # 3 chars.  Longer fuzzy matches risk "DCU Savings" → "DCU LTD
        # Savings" when a real DCU Savings account exists in the sidebar.
        if not exact_mdi and len(matched_name) > len(account_name) + 3:
            pass  # skip — fall through to combo/sidebar for better match
        else:
            # Verify the root title bracket actually changed
            _vbuf_et = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(root_hwnd, _vbuf_et, 256)
            bracket_et = _bracket_name(_vbuf_et.value)
            if not bracket_et or not _acct_match(bracket_et, target_name):
                # BringWindowToTop didn't activate — try
                # SetForegroundWindow first, then re-activate
                user32.SetForegroundWindow(root_hwnd)
                _activate_mdi(existing_mdi)
                time.sleep(0.3)
            return _verify_and_stabilize(
                {"ok": True, "account": matched_name,
                 "method": "existing_tab"})

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
                _activate_mdi(_mdi)
                break
            _mdi = user32.GetParent(_mdi)

        _send_msg_timeout(combo_h, CB_SETCURSEL, idx, 0, timeout_ms=2000)
        parent = user32.GetParent(combo_h)
        ctrl_id = user32.GetDlgCtrlID(combo_h)
        wparam = (CBN_SELCHANGE << 16) | (ctrl_id & 0xFFFF)
        _send_msg_timeout(parent, WM_COMMAND, wparam, combo_h,
                          timeout_ms=2000)
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
                return _verify_and_stabilize(
                    {"ok": True, "account": opened, "method": "combo"})

        # For category-view combos (Property & Debt, etc.), the title
        # bracket won't change to the account name.  Verify by reading
        # the combo selection text instead.
        sel_text = _combo_cur_text(combo_h)
        if _acct_match(sel_text, account_name):
            return _verify_and_stabilize(
                {"ok": True, "account": sel_text, "method": "combo_filter"})

    # ------------------------------------------------------------------
    # Phase 3: Targeted sidebar scan — scroll through and click items
    # until we navigate to the target account.  Much faster than a full
    # scan because we stop as soon as we find the target.
    # ------------------------------------------------------------------
    def _navigate_via_sidebar(target_name: str) -> dict | None:
        """Scroll through sidebar, clicking items until target opens.

        If the sidebar cache has a recorded click position for this account,
        try that specific position first (fast path).  Otherwise, do a full
        scroll-and-click scan.
        """
        holder = _find_sidebar_holder(root_hwnd)

        # Ensure sidebar is visible — the sidebar is hidden when an investment
        # account is active.  Try the ACCOUNTS toolbar button to restore it.
        # This also covers the case where _find_sidebar_holder returns None
        # (completely hidden — holder not in window tree yet).
        def _restore_sidebar_btn() -> bool:
            nonlocal holder
            BM_CLICK_SB = 0x00F5
            _acls_sb = ctypes.create_unicode_buffer(64)
            _atxt_sb = ctypes.create_unicode_buffer(256)
            _abtn_sb = 0

            def _find_abtn(ch: int, _: int) -> bool:
                nonlocal _abtn_sb
                user32.GetClassNameW(ch, _acls_sb, 64)
                if _acls_sb.value != "QC_button":
                    return True
                user32.GetWindowTextW(ch, _atxt_sb, 256)
                if _atxt_sb.value.upper() == "ACCOUNTS":
                    _abtn_sb = ch
                    return False
                return True

            _EnumCB_nav = ctypes.WINFUNCTYPE(
                ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
            user32.EnumChildWindows(root_hwnd, _EnumCB_nav(_find_abtn), 0)
            if not _abtn_sb:
                return False
            user32.SetForegroundWindow(root_hwnd)
            _dismiss_modal_dialogs(root_hwnd)
            user32.PostMessageW(_abtn_sb, BM_CLICK_SB, 0, 0)
            for _poll in range(40):  # up to ~10s (investment sidebar restores in 3-8s)
                time.sleep(0.25)
                cur = holder if holder and user32.IsWindow(holder) else 0
                if cur and user32.IsWindowVisible(cur):
                    return True
                h2 = _find_sidebar_holder(root_hwnd)
                if h2 and user32.IsWindowVisible(h2):
                    holder = h2
                    return True
            return False

        sidebar_ok = (
            holder and _is_valid_hwnd(holder) and user32.IsWindowVisible(holder)
        )
        if not sidebar_ok:
            if not _restore_sidebar_btn():
                return None  # sidebar unrecoverable

        _expand_sidebar_sections(holder)
        _dismiss_modal_dialogs(root_hwnd)

        import ctypes.wintypes as wt  # noqa: PLC0415

        class _SI(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_uint), ("fMask", ctypes.c_uint),
                ("nMin", ctypes.c_int), ("nMax", ctypes.c_int),
                ("nPage", ctypes.c_uint), ("nPos", ctypes.c_int),
                ("nTrackPos", ctypes.c_int),
            ]

        SB_VERT = 1
        SIF_ALL = 0x17
        SBM_SETPOS = 0x00E0
        WM_VSCROLL = 0x0115
        SB_THUMBPOSITION = 4

        scrollbar_h = 0
        _EnumCB = ctypes.WINFUNCTYPE(
            ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)

        def _find_sb(h: int, _: int) -> bool:
            nonlocal scrollbar_h
            cls_b = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(h, cls_b, 64)
            if cls_b.value == "ScrollBar" and user32.IsWindowVisible(h):
                scrollbar_h = h
                return False
            return True

        user32.EnumChildWindows(holder, _EnumCB(_find_sb), 0)

        def _scroll_to(pos: int) -> None:
            if scrollbar_h:
                _send_msg_timeout(scrollbar_h, SBM_SETPOS, pos, 1,
                                  timeout_ms=2000)
            _send_msg_timeout(
                holder, WM_VSCROLL,
                (pos << 16) | SB_THUMBPOSITION, scrollbar_h,
                timeout_ms=2000)
            time.sleep(0.15)

        def _dblclick(sx_: int, y_: int) -> None:
            user32.SetCursorPos(sx_, y_)
            time.sleep(0.02)
            for _dc in range(2):
                user32.mouse_event(0x0002, 0, 0, 0, 0)
                user32.mouse_event(0x0004, 0, 0, 0, 0)
                time.sleep(0.02)
            time.sleep(0.20)

        buf = ctypes.create_unicode_buffer(256)

        def _check_match() -> str | None:
            """Read title bracket after a sidebar click.

            Dismisses modal dialogs (Securities Comparison Mismatch etc.)
            and waits for blank titles to resolve (investment accounts).
            Also gives property/loan accounts a brief grace period: they
            can transiently show "[Home]" while the register is loading.
            Returns the bracket name if it matches the target, else None.
            """
            _dismiss_modal_dialogs(root_hwnd)
            user32.GetWindowTextW(root_hwnd, buf, 256)
            bracket = _bracket_name(buf.value)
            if bracket and _acct_match(bracket, target_name):
                return bracket
            if bracket:
                # Non-target bracket.  Property/loan accounts can briefly
                # show "[Home]" while their register loads.  Give them one
                # extra second to settle before giving up.
                time.sleep(1.0)
                _dismiss_modal_dialogs(root_hwnd)
                user32.GetWindowTextW(root_hwnd, buf, 256)
                bracket = _bracket_name(buf.value)
                if bracket and _acct_match(bracket, target_name):
                    return bracket
                if bracket:
                    return None  # stable non-target
            # Blank title — investment/property account loading.  Poll up
            # to 5s for the title to appear, then check if it's our target.
            for _poll in range(10):  # up to ~5s
                time.sleep(0.5)
                _dismiss_modal_dialogs(root_hwnd)
                user32.GetWindowTextW(root_hwnd, buf, 256)
                bracket = _bracket_name(buf.value)
                if bracket:
                    if _acct_match(bracket, target_name):
                        return bracket
                    return None  # resolved to a non-target
            return None

        # --- Fast path: try the cached click position ---
        cached = _sidebar_lookup(target_name) if _sidebar_cache else None
        if cached and "nav_lb" in cached:
            user32.SetForegroundWindow(root_hwnd)
            nav_lb = cached["nav_lb"]
            nav_item = cached["nav_item"]
            LB_GETITEMRECT_FP = 0x0198

            # Scroll to the stored position first so the ListBox shows
            # the correct account at the cached item index.  The sidebar
            # ListBoxes are fixed-position containers whose *contents*
            # change based on scroll position — clicking nav_item without
            # scrolling to nav_scroll clicks the wrong account.
            if "nav_scroll" in cached:
                _scroll_to(cached["nav_scroll"])
                time.sleep(0.15)  # wait for LB content to update
            _dismiss_modal_dialogs(root_hwnd)

            # Use LB_GETITEMRECT for precise current screen position.
            if user32.IsWindow(nav_lb):
                ir_fp = wt.RECT()
                ret_fp = _send_msg_timeout(
                    nav_lb, LB_GETITEMRECT_FP, nav_item,
                    ctypes.addressof(ir_fp), timeout_ms=1000)
                if ret_fp and (ir_fp.bottom - ir_fp.top) > 5:
                    pt_fp = wt.POINT((ir_fp.left + ir_fp.right) // 2,
                                     (ir_fp.top + ir_fp.bottom) // 2)
                    user32.ClientToScreen(nav_lb, ctypes.byref(pt_fp))
                    hr_fp = wt.RECT()
                    user32.GetWindowRect(holder, ctypes.byref(hr_fp))
                    if hr_fp.top <= pt_fp.y <= hr_fp.bottom:
                        _dblclick(pt_fp.x, pt_fp.y)
                        match = _check_match()
                        if match:
                            return _verify_and_stabilize(
                                {"ok": True, "account": match,
                                 "method": "sidebar_cached_lb"})

            # Fallback: click at stored nav_y (already scrolled to nav_scroll)
            if "nav_y" in cached:
                sx_ = cached.get("nav_sx", 0)
                nav_y = cached["nav_y"]
                hr = wt.RECT()
                user32.GetWindowRect(holder, ctypes.byref(hr))
                if not sx_:
                    sx_ = (hr.left + hr.right) // 2
                for y_off in [0, -20, 20, -40, 40]:
                    y_ = nav_y + y_off
                    if y_ < hr.top or y_ > hr.bottom:
                        continue
                    _dblclick(sx_, y_)
                    match = _check_match()
                    if match:
                        return _verify_and_stabilize(
                            {"ok": True, "account": match,
                             "method": "sidebar_cached"})

        # --- Slow path: scroll + wheel-scroll sidebar scan ---
        _expand_sidebar_sections(holder)
        time.sleep(0.3)

        LB_GETCOUNT = 0x018B
        LB_GETITEMRECT = 0x0198

        si = _SI()
        si.cbSize = ctypes.sizeof(si)
        si.fMask = SIF_ALL
        user32.GetScrollInfo(holder, SB_VERT, ctypes.byref(si))
        max_scroll = max(0, si.nMax - int(si.nPage) + 1)
        hr = wt.RECT()
        user32.GetWindowRect(holder, ctypes.byref(hr))
        viewport_h = hr.bottom - hr.top
        step_size = max(50, viewport_h // 3)
        deadline = time.monotonic() + 180
        _clicked_nav: set[tuple[int, int]] = set()

        _nav_EnumCB = ctypes.WINFUNCTYPE(
            ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)

        def _enum_lbs_nav() -> list[tuple[int, int]]:
            """Enumerate visible ListBoxes in the sidebar holder."""
            result_lbs: list[tuple[int, int]] = []
            def _elb(h: int, _: int) -> bool:
                cls_b = ctypes.create_unicode_buffer(64)
                user32.GetClassNameW(h, cls_b, 64)
                if cls_b.value == "ListBox":
                    wr = wt.RECT()
                    user32.GetWindowRect(h, ctypes.byref(wr))
                    if wr.bottom - wr.top > 5:
                        cnt = _send_msg_timeout(
                            h, LB_GETCOUNT, 0, 0, timeout_ms=1500)
                        if cnt and cnt > 0:
                            result_lbs.append((h, cnt))
                return True
            user32.EnumChildWindows(
                holder, _nav_EnumCB(_elb), 0)
            return result_lbs

        def _restore_nav_sidebar() -> bool:
            """Restore sidebar after investment account hides it."""
            nonlocal holder
            BM_CLICK_R = 0x00F5
            _rcls = ctypes.create_unicode_buffer(64)
            _rtxt = ctypes.create_unicode_buffer(256)
            _rbtn = 0
            def _find_rbtn(ch: int, _: int) -> bool:
                nonlocal _rbtn
                user32.GetClassNameW(ch, _rcls, 64)
                if _rcls.value != "QC_button":
                    return True
                user32.GetWindowTextW(ch, _rtxt, 256)
                if _rtxt.value.upper() == "ACCOUNTS":
                    _rbtn = ch
                    return False
                return True
            user32.EnumChildWindows(
                root_hwnd, _nav_EnumCB(_find_rbtn), 0)
            if not _rbtn:
                return False
            _dismiss_modal_dialogs(root_hwnd)
            user32.PostMessageW(_rbtn, BM_CLICK_R, 0, 0)
            _rsbuf = ctypes.create_unicode_buffer(512)
            for _rp in range(80):  # up to ~20s — investment accounts take 15-20s
                time.sleep(0.25)
                # Early-exit: bracket already shows the target account —
                # navigation succeeded even if sidebar isn't visible yet.
                user32.GetWindowTextW(root_hwnd, _rsbuf, 512)
                _rbc = _bracket_name(_rsbuf.value)
                if _rbc and _acct_match(_rbc, target_name):
                    return True
                if (user32.IsWindow(holder)
                        and user32.IsWindowVisible(holder)):
                    return True
                h2 = _find_sidebar_holder(root_hwnd)
                if h2 and user32.IsWindowVisible(h2):
                    holder = h2
                    return True
            return False

        def _click_nav_items(
            vis_lbs: list[tuple[int, int]],
            htop: int, hbot: int,
        ) -> dict | None:
            """Click LB items and check for target match.

            Returns result dict if target found, else None.
            Skips already-clicked items.  Handles sidebar-hide
            from investment accounts by re-enumerating ListBoxes.
            """
            _prev_nav_count = len(_clicked_nav)
            while time.monotonic() < deadline:
                _need_restart = False
                for lb_h, count in vis_lbs:
                    if time.monotonic() >= deadline:
                        return None
                    for i in range(min(count, 30)):
                        if time.monotonic() >= deadline:
                            return None
                        if (lb_h, i) in _clicked_nav:
                            continue
                        ir = wt.RECT()
                        rc = _send_msg_timeout(
                            lb_h, LB_GETITEMRECT, i,
                            ctypes.addressof(ir), timeout_ms=1500)
                        if rc is None:
                            continue
                        if ir.bottom - ir.top < 10:
                            _clicked_nav.add((lb_h, i))
                            continue
                        pt = wt.POINT(
                            (ir.left + ir.right) // 2,
                            (ir.top + ir.bottom) // 2)
                        user32.ClientToScreen(lb_h, ctypes.byref(pt))
                        if pt.y < htop or pt.y > hbot:
                            continue

                        _clicked_nav.add((lb_h, i))
                        user32.GetWindowTextW(root_hwnd, buf, 256)
                        pre_bracket = _bracket_name(buf.value)

                        _dblclick(pt.x, pt.y)
                        match = _check_match()
                        if match:
                            return _verify_and_stabilize(
                                {"ok": True,
                                 "account": match,
                                 "method": "sidebar_scan"})

                        # Cache any non-target discovered account
                        user32.GetWindowTextW(root_hwnd, buf, 256)
                        post_bracket = _bracket_name(buf.value)
                        if post_bracket and post_bracket != pre_bracket:
                            _sidebar_cache_add(
                                post_bracket, 0, pt.y, pt.x, lb_h, i)

                        # Sidebar may hide after investment clicks
                        if not user32.IsWindowVisible(holder):
                            if not _restore_nav_sidebar():
                                return None
                            # Re-enumerate with fresh handles
                            vis_lbs = _enum_lbs_nav()
                            user32.GetWindowRect(
                                holder, ctypes.byref(hr))
                            htop, hbot = hr.top, hr.bottom
                            _need_restart = True
                            break
                    if _need_restart:
                        break
                if not _need_restart:
                    break
                # No new items clicked → stop restarting
                if len(_clicked_nav) == _prev_nav_count:
                    break
                _prev_nav_count = len(_clicked_nav)
            return None

        # --- Phase A: standard scroll positions ---
        scroll_pos = 0
        while (scroll_pos <= max_scroll + step_size
               and time.monotonic() < deadline):
            actual_pos = min(scroll_pos, max_scroll)
            user32.SetForegroundWindow(root_hwnd)
            _scroll_to(actual_pos)
            _dismiss_modal_dialogs(root_hwnd)

            user32.GetWindowRect(holder, ctypes.byref(hr))
            htop, hbot = hr.top, hr.bottom

            vis_lbs = _enum_lbs_nav()
            result = _click_nav_items(vis_lbs, htop, hbot)
            if result:
                return result

            scroll_pos += step_size

        # --- Phase B: wheel-scroll to reach items below the fold ---
        # Always try wheel-scroll when Phase A didn't find the target.
        if time.monotonic() < deadline:
            # Reset scroll to top so wheel-scroll covers full range
            _scroll_to(0)
            time.sleep(0.15)
            MOUSEEVENTF_WHEEL = 0x0800
            _whr = wt.RECT()

            def _nav_wheel(notches: int = 5) -> None:
                """Scroll sidebar down via mouse-wheel events."""
                user32.GetWindowRect(holder, ctypes.byref(_whr))
                cx = (_whr.left + _whr.right) // 2
                cy = (_whr.top + _whr.bottom) // 2
                user32.SetCursorPos(cx, cy)
                time.sleep(0.05)
                for _n in range(notches):
                    user32.mouse_event(
                        MOUSEEVENTF_WHEEL, 0, 0, -120, 0)
                    time.sleep(0.05)
                time.sleep(0.30)

            _wheel_pass = 0
            _prev_first_y: int | None = None
            # Restore sidebar before Phase B if Phase A left it hidden
            if not user32.IsWindowVisible(holder):
                if not _restore_nav_sidebar():
                    pass  # Phase B will skip if sidebar can't be restored
            while _wheel_pass < 20 and time.monotonic() < deadline:
                # Ensure sidebar is visible before scrolling
                if not user32.IsWindowVisible(holder):
                    if not _restore_nav_sidebar():
                        break

                _pre_wheel_clicks = len(_clicked_nav)
                _nav_wheel(notches=5)
                _dismiss_modal_dialogs(root_hwnd)
                _wheel_pass += 1
                user32.GetWindowRect(holder, ctypes.byref(hr))
                htop, hbot = hr.top, hr.bottom

                vis_lbs = _enum_lbs_nav()

                # Detect scroll stall using holder's GetScrollInfo
                _si2 = _SI()
                _si2.cbSize = ctypes.sizeof(_si2)
                _si2.fMask = SIF_ALL
                user32.GetScrollInfo(
                    holder, SB_VERT, ctypes.byref(_si2))
                _cur_scroll_pos = _si2.nPos
                if (_cur_scroll_pos == _prev_first_y
                        and _cur_scroll_pos is not None):
                    if len(_clicked_nav) == _pre_wheel_clicks:
                        break  # scroll didn't move and no new clicks
                _prev_first_y = _cur_scroll_pos

                result = _click_nav_items(vis_lbs, htop, hbot)
                if result:
                    return result

        return None

    # ------------------------------------------------------------------
    # Phase 4: Direct targeted sidebar navigation.
    # Scrolls through the sidebar clicking items until the target opens.
    # If a sidebar cache exists and has a hit, the function's fast path
    # tries the cached screen position first before falling back to a
    # full ListBox sweep.
    # ------------------------------------------------------------------
    result = _navigate_via_sidebar(account_name)
    if result:
        return result

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

    # Consume any pending navigate hint BEFORE the slow _find_active_mdi
    # retry loop so we capture the bracket name while it's still fresh.
    _nav_info = _last_nav.copy()
    _last_nav.clear()

    # Read bracket NOW (right after navigate_to_account returned) so we
    # get the "just navigated" state before any async Quicken switch.
    import re as _re_rrs  # noqa: PLC0415
    _rb_buf = ctypes.create_unicode_buffer(512)
    ctypes.windll.user32.GetWindowTextW(root_hwnd, _rb_buf, 512)
    _bm_early = _re_rrs.search(r"\[(.+?)\]\s*$", _rb_buf.value)
    _early_bracket = _bm_early.group(1).strip() if _bm_early else ""

    # If a navigate just ran and the bracket matches, record it so we can
    # override a stale QWMDI title later.  Also re-activate the stored
    # QWMDI hwnd (if valid) to hold the window in place.
    _nav_bracket_override = ""
    if _nav_info.get("name") and _early_bracket:
        if _acct_match(_early_bracket, _nav_info["name"]):
            _nav_bracket_override = _early_bracket
    if _nav_info.get("hwnd") and ctypes.windll.user32.IsWindow(_nav_info["hwnd"]):
        _activate_mdi(_nav_info["hwnd"])

    def _read_text(h: int) -> str:
        tlen = _send_msg_timeout(h, WM_GETTEXTLENGTH, 0, 0, timeout_ms=1500)
        if not tlen or tlen <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(tlen + 1)
        _send_msg_timeout(h, WM_GETTEXT, len(buf), ctypes.addressof(buf),
                          timeout_ms=1500)
        return buf.value

    # Find the active QWMDI and scope child enumeration to it.
    # Retry briefly — after navigation the MDI may still be loading.
    # Investment accounts can take 2-8s, so we retry more aggressively.
    mdi_h = _find_active_mdi(root_hwnd)
    if mdi_h is None:
        import time as _time  # noqa: PLC0415
        for _retry in range(8):
            _time.sleep(1.0)
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
    # Investment accounts may not have QWHtmlView as a direct descendant
    # but they have a holdings ListBox and/or an "Actions" button.
    has_holdings_lb = any(
        c == "listbox" and user32.IsWindowVisible(h)
        and (_send_msg_timeout(h, 0x018B, 0, 0, timeout_ms=500) or 0) > 0
        for h, c, _ in mdi_children
    )
    has_actions = any(
        c == "qc_button" and _read_text(h).strip() == "Actions"
        for h, c, _ in mdi_children if user32.IsWindowVisible(h)
    )
    is_investment = has_html or has_holdings_lb or has_actions
    view_type = "register" if txlist_h else ("investment" if is_investment else "unknown")

    if txlist_h is None:
        # Investment/portfolio view: extract what we can
        mdi_title = _read_text(mdi_h)

        # Fall back to bracket name from root title bar when MDI title
        # is empty (common for investment accounts whose QWMDI wrapper
        # doesn't carry the account name).
        if not mdi_title:
            import re as _re  # noqa: PLC0415
            root_buf = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(root_hwnd, root_buf, 512)
            _m = _re.search(r"\[(.+?)\]\s*$", root_buf.value)
            if _m:
                mdi_title = _m.group(1).strip()

        # Count holdings in the ListBox
        holdings_count = 0
        lb_h = next(
            (h for h, c, _ in mdi_children
             if c == "listbox" and user32.IsWindowVisible(h)
             and (_send_msg_timeout(h, 0x018B, 0, 0, timeout_ms=1500) or 0) > 0),
            None,
        )
        if lb_h:
            holdings_count = _send_msg_timeout(
                lb_h, 0x018B, 0, 0, timeout_ms=1500) or 0

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

        # Apply nav bracket override if the MDI title is stale.
        if (_nav_bracket_override
                and not _acct_match(mdi_title or "", _nav_bracket_override)):
            mdi_title = _nav_bracket_override

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
    # When navigate_to_account just ran (_nav_info consumed above), also
    # check the root bracket: if it matches the navigated account name and
    # the QWMDI title is stale (Quicken reused a QWMDI without updating its
    # title), use the bracket / nav name as the authoritative account name.
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

    # Bracket-override: when navigate_to_account just ran and we captured
    # the bracket as "DCU Checking" early on (before any async switch),
    # but the QWMDI title is stale (Quicken reused a QWMDI without updating
    # its title), prefer the early bracket name as the account name.
    if (
        _nav_bracket_override
        and not _acct_match(current_account, _nav_bracket_override)
    ):
        current_account = _nav_bracket_override

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

    Approach (in priority order):
    1. Title-matching: exact case-insensitive match on QWMDI title vs
       the bracketed account name in the root window title.
    2. WM_MDIGETACTIVE via the MDIClient — reliable for investment
       accounts whose QWMDI wrapper has an empty title.  Only used
       when the bracket name is absent or no QWMDI title matches.
    3. Fuzzy title match (shortest title wins — avoids "3 Duggan" vs
       "3 Duggan Loan" ambiguity).
    4. Largest visible QWMDI fallback.
    """
    import ctypes  # noqa: PLC0415
    import ctypes.wintypes  # noqa: PLC0415
    import re  # noqa: PLC0415

    user32 = ctypes.windll.user32
    WM_MDIGETACTIVE = 0x0229

    # Extract account name from root title brackets
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(root_hwnd, buf, 512)
    title = buf.value
    m = re.search(r"\[(.+?)\]\s*$", title)
    target_name = m.group(1).strip() if m else ""

    best_match: int | None = None
    best_area: int = 0
    exact_match: int | None = None
    fuzzy_match: int | None = None
    fuzzy_len: int = 999999

    WNDENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
    )

    def _cb(h: int, _: int) -> bool:
        nonlocal best_match, best_area, exact_match, fuzzy_match, fuzzy_len
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

        if target_name:
            if mdi_title.lower() == target_name.lower():
                exact_match = h
            elif not exact_match and _acct_match(mdi_title, target_name):
                if len(mdi_title) < fuzzy_len:
                    fuzzy_match = h
                    fuzzy_len = len(mdi_title)
        if area > best_area:
            best_area = area
            best_match = h
        return True

    user32.EnumChildWindows(root_hwnd, WNDENUMPROC(_cb), 0)

    # If title-matching found a result, use it.
    if exact_match or fuzzy_match:
        return exact_match or fuzzy_match

    # No title match — try WM_MDIGETACTIVE (works for investment accounts
    # whose QWMDI has an empty title that can't be matched).
    mdi_client: list[int] = []

    def _find_mdiclient(h: int, _: int) -> bool:
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(h, cls, 64)
        if cls.value == "MDIClient":
            mdi_client.append(h)
            return False
        return True

    user32.EnumChildWindows(root_hwnd, WNDENUMPROC(_find_mdiclient), 0)
    if mdi_client:
        active_h = _send_msg_timeout(
            mdi_client[0], WM_MDIGETACTIVE, 0, 0, timeout_ms=1500)
        if active_h and user32.IsWindow(active_h):
            cls = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(active_h, cls, 64)
            if cls.value.upper() == "QWMDI" and user32.IsWindowVisible(active_h):
                return active_h

    # Final fallback: largest visible QWMDI
    return best_match


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

    # Dismiss any modal dialogs before interacting
    _dismiss_modal_dialogs(root_hwnd)

    # Capture bracket NOW so we can verify account hasn't shifted after filter.
    import re as _re_srf  # noqa: PLC0415
    _sbuf_srf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(root_hwnd, _sbuf_srf, 512)
    _bm_srf = _re_srf.search(r"\[(.+?)\]\s*$", _sbuf_srf.value)
    _expected_acct = _bm_srf.group(1).strip() if _bm_srf else ""

    def _read_text(h: int) -> str:
        tlen = _send_msg_timeout(h, WM_GETTEXTLENGTH, 0, 0, timeout_ms=1500)
        if not tlen or tlen <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(tlen + 1)
        _send_msg_timeout(h, WM_GETTEXT, len(buf), ctypes.addressof(buf),
                          timeout_ms=1500)
        return buf.value

    # Scope to the ACTIVE QWMDI to avoid silently switching accounts.
    # Previously enumerated all root children — picking a TxList from
    # an inactive tab would shift focus to that account.
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
        mdi_children.append((h, cls_buf.value.lower(), _read_text(h)))
        return True

    user32.EnumChildWindows(mdi_h, EnumCB(_cb), 0)

    txlist_h = next(
        (h for h, c, _ in mdi_children if c == "qwclass_transactionlist"
         and user32.IsWindowVisible(h)), None
    )
    if txlist_h is None:
        raise UIAError("No visible TxList found in active MDI.",
                        code="REGISTER_NOT_FOUND")

    txlist_rect = ctypes.wintypes.RECT()
    user32.GetWindowRect(txlist_h, ctypes.byref(txlist_rect))

    # Find filter Edit above TxList (within the same MDI)
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
    _send_msg_timeout(filter_h, WM_SETTEXT, 0, ctypes.addressof(buf),
                      timeout_ms=2000)
    # Post EN_CHANGE to parent so Quicken re-filters.
    # EN_CHANGE can cause Quicken to internally switch the active QWMDI —
    # immediately re-activate our target MDI to counteract the focus steal.
    WM_COMMAND = 0x0111
    EN_CHANGE = 0x0300
    ctrl_id = user32.GetDlgCtrlID(filter_h)
    parent = user32.GetParent(filter_h)
    user32.PostMessageW(parent, WM_COMMAND, (EN_CHANGE << 16) | ctrl_id, filter_h)
    _activate_mdi(mdi_h)   # queue re-activation before Quicken can steal focus
    time.sleep(0.4)
    _activate_mdi(mdi_h)   # re-activate again after Quicken processes EN_CHANGE
    time.sleep(0.2)

    # Verify using the bracket (not _find_active_mdi) — bracket is the
    # most reliable indicator of which account Quicken considers active.
    def _bracket_now() -> str:
        user32.GetWindowTextW(root_hwnd, _sbuf_srf, 512)
        _bm = _re_srf.search(r"\[(.+?)\]\s*$", _sbuf_srf.value)
        return _bm.group(1).strip() if _bm else ""

    _post_acct = _bracket_now()
    if (_expected_acct and _post_acct
            and not _acct_match(_post_acct, _expected_acct)):
        # One more attempt — SetForegroundWindow helps Quicken commit the
        # MDI activation we issued above.
        user32.SetForegroundWindow(root_hwnd)
        _activate_mdi(mdi_h)
        time.sleep(0.5)
        _post_acct2 = _bracket_now()
        if not _acct_match(_post_acct2, _expected_acct):
            return {
                "ok": False,
                "error": "focus_shifted",
                "detail": (
                    f"Applying the filter caused Quicken to switch "
                    f"from '{_expected_acct}' to '{_post_acct}'. "
                    "The filter text was NOT applied to the expected "
                    "account.  Clear the filter and re-navigate."
                ),
                "filter": text,
                "count": "",
                "account": _post_acct,
            }

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

    # Read the account name from the root title bar so the caller can
    # verify which account the filter was applied to.
    _acct_buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(root_hwnd, _acct_buf, 512)
    _acct_m = _re_srf.search(r"\[(.+?)\]\s*$", _acct_buf.value)
    _acct_name = _acct_m.group(1).strip() if _acct_m else ""

    return {"ok": True, "filter": text, "count": count_static,
            "account": _acct_name}


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
        tlen = _send_msg_timeout(h, WM_GETTEXTLENGTH, 0, 0, timeout_ms=1500)
        if not tlen or tlen <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(tlen + 1)
        _send_msg_timeout(h, WM_GETTEXT, len(buf), ctypes.addressof(buf),
                          timeout_ms=1500)
        return buf.value

    def _click_qc_button(h: int) -> None:
        """Click a QC_button via WM_LBUTTONDOWN/WM_LBUTTONUP."""
        rc = ctypes.wintypes.RECT()
        user32.GetClientRect(h, ctypes.byref(rc))
        cx = (rc.right - rc.left) // 2
        cy = (rc.bottom - rc.top) // 2
        lp = ctypes.c_long((cy << 16) | (cx & 0xFFFF)).value
        user32.SetFocus(h)
        _send_msg_timeout(h, 0x0201, 1, lp, timeout_ms=2000)
        _send_msg_timeout(h, 0x0202, 0, lp, timeout_ms=2000)

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

    count = _send_msg_timeout(combo_h, CB_GETCOUNT, 0, 0, timeout_ms=1500) or 0
    target_idx: int | None = None
    for i in range(count):
        tlen = _send_msg_timeout(combo_h, CB_GETLBTEXTLEN, i, 0,
                                 timeout_ms=1500) or 0
        tbuf = ctypes.create_unicode_buffer(tlen + 2)
        _send_msg_timeout(combo_h, CB_GETLBTEXT, i, ctypes.addressof(tbuf),
                          timeout_ms=1500)
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

    _send_msg_timeout(combo_h, CB_SETCURSEL, target_idx, 0, timeout_ms=1500)

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
        _send_msg_timeout(h, WM_SETTEXT, 0, ctypes.addressof(tbuf),
                          timeout_ms=2000)

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
