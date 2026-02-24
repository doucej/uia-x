"""
Authentication layer – modular API-key authentication.

Design
------
* On first run, if no key file exists, a new API key is generated and
  written to ``~/.uia_x/api_key``.  The key is also printed to
  stderr so the operator can copy it.
* Every MCP tool call passes through ``require_auth(headers)`` which
  validates the ``X-API-Key`` header (or an ``api_key`` tool parameter)
  against the stored key.
* The layer is deliberately kept behind a protocol so future providers
  (mTLS, OAuth device-code) can be swapped in.

Disabling auth
--------------
Set the environment variable ``UIA_X_AUTH=none`` to skip all
authentication checks (useful for local-only / dev usage).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional
import sys

from server.uia_bridge import AuthenticationError

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_CONFIG_DIR = Path.home() / ".uia_x"
_KEY_FILE = _CONFIG_DIR / "api_key"
_ENV_AUTH_MODE = "UIA_X_AUTH"         # "apikey" (default) | "none"
_ENV_API_KEY = "UIAX_API_KEY"         # primary override key from env
_ENV_API_KEY_LEGACY = "UIA_X_API_KEY" # backward-compat alias


# ---------------------------------------------------------------------------
# Abstract provider
# ---------------------------------------------------------------------------


class AuthProvider(ABC):
    """Protocol for pluggable authentication."""

    @abstractmethod
    def validate(self, credentials: dict[str, str]) -> bool:
        """
        Return True if the credentials are valid.

        Parameters
        ----------
        credentials : dict
            Keys depend on the provider.  For API key auth:
            ``{"api_key": "<key>"}``.
        """

    @abstractmethod
    def provider_name(self) -> str:
        """Human-readable provider name for logging."""


# ---------------------------------------------------------------------------
# No-op provider (auth disabled)
# ---------------------------------------------------------------------------


class NoAuthProvider(AuthProvider):
    """Always passes – used when ``UIA_X_AUTH=none``."""

    def validate(self, credentials: dict[str, str]) -> bool:
        return True

    def provider_name(self) -> str:
        return "none"


# ---------------------------------------------------------------------------
# API-key provider
# ---------------------------------------------------------------------------


class ApiKeyProvider(AuthProvider):
    """
    HMAC-based API key validation.

    The key is stored as a hex-encoded SHA-256 hash; the raw key is only
    ever shown once (on generation).
    """

    def __init__(self, key_hash: str) -> None:
        self._key_hash = key_hash

    def validate(self, credentials: dict[str, str]) -> bool:
        supplied = credentials.get("api_key", "")
        if not supplied:
            return False
        supplied_hash = hashlib.sha256(supplied.encode()).hexdigest()
        return hmac.compare_digest(supplied_hash, self._key_hash)

    def provider_name(self) -> str:
        return "api_key"


# ---------------------------------------------------------------------------
# Key management helpers
# ---------------------------------------------------------------------------


def _ensure_config_dir() -> Path:
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    return _CONFIG_DIR


def generate_api_key() -> str:
    """Generate a new 32-byte URL-safe API key and persist its hash."""
    raw_key = secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    _ensure_config_dir()
    _KEY_FILE.write_text(key_hash, encoding="utf-8")
    return raw_key


def load_key_hash() -> Optional[str]:
    """Load the stored key hash, or return None if no key file exists."""
    if _KEY_FILE.is_file():
        return _KEY_FILE.read_text(encoding="utf-8").strip()
    return None


# ---------------------------------------------------------------------------
# Provider factory
# ---------------------------------------------------------------------------


def get_auth_provider() -> AuthProvider:
    """
    Build the appropriate AuthProvider based on environment / config.

    Priority:
    1. ``UIA_X_AUTH=none``  → NoAuthProvider
    2. ``UIAX_API_KEY`` env var (or legacy ``UIA_X_API_KEY``) → ApiKeyProvider
    3. Key hash on disk    → ApiKeyProvider  (key shown on first-run only)
    4. No key yet          → generate one, print to **stdout**, persist hash
    """
    mode = os.environ.get(_ENV_AUTH_MODE, "apikey").lower()
    if mode == "none":
        return NoAuthProvider()

    # Check env-var override (UIAX_API_KEY takes precedence; UIA_X_API_KEY is
    # a backward-compatible alias kept for existing deployments).
    env_key = (
        os.environ.get(_ENV_API_KEY, "").strip()
        or os.environ.get(_ENV_API_KEY_LEGACY, "").strip()
    )
    if env_key:
        env_var_used = _ENV_API_KEY if os.environ.get(_ENV_API_KEY) else _ENV_API_KEY_LEGACY
        key_hash = hashlib.sha256(env_key.encode()).hexdigest()
        print(
            f"[uia-x] API key sourced from environment variable {env_var_used}.\n"
            f"[uia-x] Key: {env_key}",
            file=sys.stdout,
        )
        return ApiKeyProvider(key_hash)

    # Check on-disk key (only the hash is stored — the raw key cannot be
    # recovered after first generation).
    stored_hash = load_key_hash()
    if stored_hash:
        print(
            f"[uia-x] API key loaded from disk ({_KEY_FILE}).\n"
            f"[uia-x] The hash is stored; use your saved key to authenticate.\n"
            f"[uia-x] To display the key again set {_ENV_API_KEY}=<your-key> "
            f"or delete {_KEY_FILE} to generate a new one.",
            file=sys.stdout,
        )
        return ApiKeyProvider(stored_hash)

    # First-run: generate a new key, persist the hash, display the plaintext.
    raw_key = generate_api_key()
    print(
        f"[uia-x] *** NEW API KEY GENERATED ***\n"
        f"[uia-x] Key: {raw_key}\n"
        f"[uia-x] Stored hash in: {_KEY_FILE}\n"
        f"[uia-x] Save this key – it will not be shown again.",
        file=sys.stdout,
    )
    return ApiKeyProvider(hashlib.sha256(raw_key.encode()).hexdigest())


# ---------------------------------------------------------------------------
# Convenience: guard for tool calls
# ---------------------------------------------------------------------------

_provider: AuthProvider | None = None


def _get_provider() -> AuthProvider:
    global _provider
    if _provider is None:
        _provider = get_auth_provider()
    return _provider


def init_auth() -> None:
    """
    Eagerly initialise (and print) the auth provider at server startup.

    Call this once from ``main()`` before the server begins accepting
    connections so that the API key is printed to stdout before any
    log noise from the HTTP server fills the terminal.
    """
    _get_provider()


def reset_auth() -> None:
    """Reset cached provider (for tests)."""
    global _provider
    _provider = None


def set_auth_provider(p: AuthProvider) -> None:
    """Inject a specific provider (for tests)."""
    global _provider
    _provider = p


def require_auth(api_key: str = "") -> None:
    """
    Validate the API key.  Raises ``AuthenticationError`` on failure.

    Checks are applied in order:
    1. If a Bearer token was already validated at the HTTP transport
       layer (``_bearer_authenticated`` context-var), skip.
    2. Otherwise validate ``api_key`` via the configured provider.

    Parameters
    ----------
    api_key : str
        The raw API key supplied by the caller.
    """
    # If Bearer auth already succeeded for this request, allow through.
    if _bearer_authenticated.get(False):
        return
    provider = _get_provider()
    if not provider.validate({"api_key": api_key}):
        raise AuthenticationError()


# ---------------------------------------------------------------------------
# Bearer token ASGI middleware (for HTTP transports)
# ---------------------------------------------------------------------------

from contextvars import ContextVar

_bearer_authenticated: ContextVar[bool] = ContextVar(
    "bearer_authenticated", default=False
)


class BearerAuthMiddleware:
    """
    Lightweight ASGI middleware that validates ``Authorization: Bearer <key>``
    headers against the configured :class:`AuthProvider`.

    When the header is present and valid, it sets the ``_bearer_authenticated``
    context-var so that tool-level ``require_auth`` calls pass without
    requiring the ``api_key`` tool parameter.

    If no ``Authorization`` header is present, the request proceeds normally
    (the tool-level check will still enforce auth).
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            headers = dict(scope.get("headers", []))
            auth_header = headers.get(b"authorization", b"").decode("utf-8", errors="ignore")
            if auth_header.lower().startswith("bearer "):
                token = auth_header[7:].strip()
                provider = _get_provider()
                if provider.validate({"api_key": token}):
                    _bearer_authenticated.set(True)
                    try:
                        return await self.app(scope, receive, send)
                    finally:
                        _bearer_authenticated.set(False)
                else:
                    # Invalid bearer token → 401
                    await _send_401(send)
                    return
        await self.app(scope, receive, send)


async def _send_401(send):
    """Send a 401 Unauthorized ASGI response."""
    await send({
        "type": "http.response.start",
        "status": 401,
        "headers": [
            [b"content-type", b"application/json"],
            [b"www-authenticate", b'Bearer realm="uia-x"'],
        ],
    })
    await send({
        "type": "http.response.body",
        "body": b'{"ok":false,"error":"Invalid or expired API key","code":"AUTH_ERROR"}',
    })
