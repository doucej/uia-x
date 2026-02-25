"""
Tests for the authentication layer.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from server.auth import (
    ApiKeyProvider,
    BearerAuthMiddleware,
    NoAuthProvider,
    _bearer_authenticated,
    delete_key_file,
    generate_api_key,
    get_auth_provider,
    load_key_hash,
    require_auth,
    reset_auth,
    set_auth_provider,
    _KEY_FILE,
    _CONFIG_DIR,
)
from server.uia_bridge import AuthenticationError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset():
    """Reset the cached auth provider between tests."""
    reset_auth()
    yield
    reset_auth()


# ---------------------------------------------------------------------------
# NoAuthProvider
# ---------------------------------------------------------------------------


class TestNoAuth:
    def test_always_validates(self):
        p = NoAuthProvider()
        assert p.validate({"api_key": ""}) is True
        assert p.validate({"api_key": "anything"}) is True
        assert p.validate({}) is True

    def test_provider_name(self):
        assert NoAuthProvider().provider_name() == "none"


# ---------------------------------------------------------------------------
# ApiKeyProvider
# ---------------------------------------------------------------------------


class TestApiKeyProvider:
    def test_valid_key_accepted(self):
        raw = "my-secret-key-12345"
        h = hashlib.sha256(raw.encode()).hexdigest()
        p = ApiKeyProvider(h)
        assert p.validate({"api_key": raw}) is True

    def test_wrong_key_rejected(self):
        raw = "correct-key"
        h = hashlib.sha256(raw.encode()).hexdigest()
        p = ApiKeyProvider(h)
        assert p.validate({"api_key": "wrong-key"}) is False

    def test_empty_key_rejected(self):
        h = hashlib.sha256(b"key").hexdigest()
        p = ApiKeyProvider(h)
        assert p.validate({"api_key": ""}) is False
        assert p.validate({}) is False

    def test_provider_name(self):
        h = hashlib.sha256(b"x").hexdigest()
        assert ApiKeyProvider(h).provider_name() == "api_key"


# ---------------------------------------------------------------------------
# require_auth
# ---------------------------------------------------------------------------


class TestRequireAuth:
    def test_no_auth_mode_passes(self):
        set_auth_provider(NoAuthProvider())
        require_auth("")  # should not raise

    def test_valid_key_passes(self):
        raw = "test-key-abc123"
        h = hashlib.sha256(raw.encode()).hexdigest()
        set_auth_provider(ApiKeyProvider(h))
        require_auth(raw)  # should not raise

    def test_invalid_key_raises(self):
        raw = "real-key"
        h = hashlib.sha256(raw.encode()).hexdigest()
        set_auth_provider(ApiKeyProvider(h))
        with pytest.raises(AuthenticationError) as exc_info:
            require_auth("bad-key")
        assert exc_info.value.code == "AUTH_ERROR"

    def test_missing_key_raises(self):
        h = hashlib.sha256(b"key").hexdigest()
        set_auth_provider(ApiKeyProvider(h))
        with pytest.raises(AuthenticationError):
            require_auth("")


# ---------------------------------------------------------------------------
# get_auth_provider with env var
# ---------------------------------------------------------------------------


class TestAuthProviderFactory:
    def test_none_mode_returns_noauth(self):
        with patch.dict(os.environ, {"UIA_X_AUTH": "none"}):
            p = get_auth_provider()
            assert isinstance(p, NoAuthProvider)

    def test_env_key_creates_provider(self):
        with patch.dict(os.environ, {"UIA_X_API_KEY": "env-key-123"}, clear=False):
            p = get_auth_provider()
            assert isinstance(p, ApiKeyProvider)
            assert p.validate({"api_key": "env-key-123"}) is True


# ---------------------------------------------------------------------------
# Key generation
# ---------------------------------------------------------------------------


class TestKeyGeneration:
    def test_generate_returns_string(self):
        with patch("server.auth._CONFIG_DIR", Path(tempfile.mkdtemp())):
            with patch("server.auth._KEY_FILE", Path(tempfile.mkdtemp()) / "api_key"):
                key = generate_api_key()
                assert isinstance(key, str)
                assert len(key) > 20


# ---------------------------------------------------------------------------
# Key file deletion (delete_key_file)
# ---------------------------------------------------------------------------


class TestDeleteKeyFile:
    def test_deletes_existing_file(self):
        tmp_dir = Path(tempfile.mkdtemp())
        tmp_key = tmp_dir / "api_key"
        tmp_key.write_text("somehash", encoding="utf-8")
        with patch("server.auth._KEY_FILE", tmp_key):
            result = delete_key_file()
        assert result is True
        assert not tmp_key.exists()

    def test_returns_false_when_no_file(self):
        tmp_dir = Path(tempfile.mkdtemp())
        tmp_key = tmp_dir / "api_key"  # does not exist
        with patch("server.auth._KEY_FILE", tmp_key):
            result = delete_key_file()
        assert result is False

    def test_reset_key_triggers_new_generation(self):
        """Deleting the key file causes get_auth_provider to generate a new key."""
        tmp_dir = Path(tempfile.mkdtemp())
        tmp_key = tmp_dir / "api_key"
        # Write an existing key hash
        old_hash = hashlib.sha256(b"old-key").hexdigest()
        tmp_key.write_text(old_hash, encoding="utf-8")
        with patch("server.auth._KEY_FILE", tmp_key):
            delete_key_file()
            assert not tmp_key.exists()
            # Now get_auth_provider should generate a new key
            p = get_auth_provider()
        assert isinstance(p, ApiKeyProvider)
        # The new hash on disk must differ from the old one
        new_hash = tmp_key.read_text(encoding="utf-8").strip()
        assert new_hash != old_hash


# ---------------------------------------------------------------------------
# Env-var precedence over disk key
# ---------------------------------------------------------------------------


class TestEnvVarPrecedence:
    def test_env_var_overrides_disk_key(self):
        """UIAX_API_KEY must win even when a disk key hash is present."""
        tmp_dir = Path(tempfile.mkdtemp())
        tmp_key = tmp_dir / "api_key"
        disk_hash = hashlib.sha256(b"disk-key").hexdigest()
        tmp_key.write_text(disk_hash, encoding="utf-8")
        env_key = "env-override-key"
        with patch("server.auth._KEY_FILE", tmp_key):
            with patch.dict(os.environ, {"UIAX_API_KEY": env_key}, clear=False):
                p = get_auth_provider()
        assert isinstance(p, ApiKeyProvider)
        # Env var key must authenticate successfully
        assert p.validate({"api_key": env_key}) is True
        # Disk key must NOT authenticate
        assert p.validate({"api_key": "disk-key"}) is False


# ---------------------------------------------------------------------------
# Bearer context-var bypass
# ---------------------------------------------------------------------------


class TestBearerContextVar:
    """require_auth skips key check when _bearer_authenticated is True."""

    def test_bearer_flag_bypasses_key_check(self):
        raw = "real-key"
        h = hashlib.sha256(raw.encode()).hexdigest()
        set_auth_provider(ApiKeyProvider(h))
        # Without Bearer flag, empty key should fail
        with pytest.raises(AuthenticationError):
            require_auth("")
        # With Bearer flag set, empty key is fine
        token = _bearer_authenticated.set(True)
        try:
            require_auth("")  # should not raise
        finally:
            _bearer_authenticated.reset(token)


# ---------------------------------------------------------------------------
# BearerAuthMiddleware (ASGI unit tests)
# ---------------------------------------------------------------------------


class TestBearerAuthMiddleware:
    """Test the ASGI middleware for Authorization: Bearer headers."""

    @pytest.fixture(autouse=True)
    def _set_api_key_provider(self):
        """Set up a known API key for Bearer tests."""
        self.raw_key = "test-bearer-key-xyz"
        h = hashlib.sha256(self.raw_key.encode()).hexdigest()
        set_auth_provider(ApiKeyProvider(h))

    @staticmethod
    def _make_scope(auth_header: str | None = None) -> dict:
        headers = []
        if auth_header is not None:
            headers.append([b"authorization", auth_header.encode()])
        return {
            "type": "http",
            "method": "POST",
            "path": "/mcp",
            "headers": headers,
        }

    @pytest.mark.asyncio
    async def test_valid_bearer_calls_app(self):
        """Valid Bearer token → app is invoked, _bearer_authenticated is set."""
        called = {}

        async def fake_app(scope, receive, send):
            called["invoked"] = True
            called["bearer_flag"] = _bearer_authenticated.get(False)

        mw = BearerAuthMiddleware(fake_app)
        scope = self._make_scope(f"Bearer {self.raw_key}")
        await mw(scope, None, None)
        assert called.get("invoked") is True
        assert called.get("bearer_flag") is True
        # After middleware returns, flag should be reset
        assert _bearer_authenticated.get(False) is False

    @pytest.mark.asyncio
    async def test_invalid_bearer_returns_401(self):
        """Invalid Bearer token → 401 response, app NOT invoked."""
        app_called = False

        async def fake_app(scope, receive, send):
            nonlocal app_called
            app_called = True

        responses = []

        async def fake_send(msg):
            responses.append(msg)

        mw = BearerAuthMiddleware(fake_app)
        scope = self._make_scope("Bearer wrong-key")
        await mw(scope, None, fake_send)
        assert app_called is False
        assert responses[0]["status"] == 401

    @pytest.mark.asyncio
    async def test_no_header_passes_through(self):
        """No Authorization header → app invoked normally (no bearer flag)."""
        called = {}

        async def fake_app(scope, receive, send):
            called["invoked"] = True
            called["bearer_flag"] = _bearer_authenticated.get(False)

        mw = BearerAuthMiddleware(fake_app)
        scope = self._make_scope()  # no auth header
        await mw(scope, None, None)
        assert called.get("invoked") is True
        assert called.get("bearer_flag") is False

    @pytest.mark.asyncio
    async def test_non_bearer_header_passes_through(self):
        """Authorization header with non-Bearer scheme → pass through."""
        called = {}

        async def fake_app(scope, receive, send):
            called["invoked"] = True

        mw = BearerAuthMiddleware(fake_app)
        scope = self._make_scope("Basic dXNlcjpwYXNz")
        await mw(scope, None, None)
        assert called.get("invoked") is True
