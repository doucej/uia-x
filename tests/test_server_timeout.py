"""
Tests for the MCP bridge tool-call watchdog timeout logic in server/server.py.

Background (2026-07-10): navigate_to_account's sidebar-scan fallback has a
hardcoded internal deadline of 180s, and list_sidebar_accounts defaults to a
720s scan budget — both were silently capped by the old blanket 60s watchdog,
which meant the outer asyncio.wait_for gave up and reset the bridge executor
WHILE the operation was still legitimately running (not actually hung),
orphaning a thread that could then race with a subsequent retry.  These tests
lock in the fix: _effective_tool_timeout must return a timeout that is at
least as large as any known per-tool override or caller-supplied duration
argument (max_seconds / timeout_ms), on top of the global default.

See Z:\\mcp_stability_handoff.md for the original investigation.
"""

from __future__ import annotations

import pytest

from server import server as srv


def _fn(name: str):
    """Build a real function object with the given __name__ (dynamically
    created classes do NOT let you override __name__ via a namespace dict —
    only real functions/def statements do)."""

    def _f():
        pass

    _f.__name__ = name
    return _f


class TestEffectiveToolTimeout:
    def test_default_timeout_for_unlisted_tool(self):
        """A tool with no override and no duration argument gets the global default."""
        fn = _fn("read_register_state_tool")
        assert srv._effective_tool_timeout(fn, {}) == srv._BRIDGE_TOOL_TIMEOUT

    def test_navigate_to_account_gets_override(self):
        """navigate_to_account_tool must get a timeout comfortably exceeding
        its internal 180s sidebar-scan deadline (see windows_impl.py:3136)."""
        fn = _fn("navigate_to_account_tool")
        timeout = srv._effective_tool_timeout(fn, {})
        assert timeout >= 180.0 + 10.0  # deadline + some margin
        assert timeout == srv._TOOL_TIMEOUT_OVERRIDES["navigate_to_account_tool"]

    def test_max_seconds_argument_extends_timeout(self):
        """list_sidebar_accounts(max_seconds=720) must not be killed before
        720s + margin elapses, even though it has no explicit override entry."""
        fn = _fn("list_sidebar_accounts_tool")
        timeout = srv._effective_tool_timeout(fn, {"max_seconds": 720.0})
        assert timeout >= 720.0
        assert timeout == 720.0 + srv._TIMEOUT_MARGIN_SECONDS

    def test_small_max_seconds_does_not_shrink_below_default(self):
        """A tool requesting a shorter duration than the global default must
        not have its effective timeout reduced below the default."""
        fn = _fn("list_sidebar_accounts_tool")
        timeout = srv._effective_tool_timeout(fn, {"max_seconds": 5.0})
        assert timeout == srv._BRIDGE_TOOL_TIMEOUT

    def test_timeout_ms_argument_converted_and_extends_timeout(self):
        """timeout_ms is milliseconds and must be converted to seconds before
        being compared/added to the watchdog budget."""
        fn = _fn("open_reconcile_tool")
        timeout = srv._effective_tool_timeout(fn, {"timeout_ms": 120_000})  # 120s
        assert timeout == 120.0 + srv._TIMEOUT_MARGIN_SECONDS

    def test_override_and_caller_argument_combine_via_max(self):
        """If a tool has both a per-tool override AND the caller passes an
        even larger explicit duration, the larger of the two wins."""
        fn = _fn("navigate_to_account_tool")
        timeout = srv._effective_tool_timeout(fn, {"max_seconds": 1000.0})
        assert timeout == 1000.0 + srv._TIMEOUT_MARGIN_SECONDS
        assert timeout > srv._TOOL_TIMEOUT_OVERRIDES["navigate_to_account_tool"]

    def test_non_numeric_or_zero_duration_args_are_ignored(self):
        """Malformed or zero/negative duration arguments must not raise and
        must not affect the computed timeout."""
        fn = _fn("read_register_state_tool")
        assert srv._effective_tool_timeout(fn, {"max_seconds": "oops"}) == srv._BRIDGE_TOOL_TIMEOUT
        assert srv._effective_tool_timeout(fn, {"max_seconds": 0}) == srv._BRIDGE_TOOL_TIMEOUT
        assert srv._effective_tool_timeout(fn, {"max_seconds": -5}) == srv._BRIDGE_TOOL_TIMEOUT

    def test_fn_without_dunder_name_falls_back_to_default(self):
        """A callable lacking __name__ (e.g. a functools.partial or odd
        object) must not raise — getattr(..., '', '') should just miss the
        override lookup and fall back to the default."""

        class _Weird:
            def __call__(self):
                pass

        timeout = srv._effective_tool_timeout(_Weird(), {})
        assert timeout == srv._BRIDGE_TOOL_TIMEOUT


class TestToolCallLock:
    def test_lock_is_lazily_created_and_reused(self):
        """_get_tool_call_lock() must return the same Lock instance across
        calls (not a fresh one each time, which would defeat serialization)."""
        srv._tool_call_lock = None  # reset module state for a clean test
        lock1 = srv._get_tool_call_lock()
        lock2 = srv._get_tool_call_lock()
        assert lock1 is lock2
