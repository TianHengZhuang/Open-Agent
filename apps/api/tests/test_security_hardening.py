"""Regression tests for security hardening controls."""

import uuid

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from openagent.core.config import Settings, get_settings
from openagent.core.security.csrf import CSRFProtection
from openagent.core.security.rate_limit import (
    AUTH_RATE_LIMITS,
    check_auth_rate_limit,
    get_client_identifier,
    get_rate_limiter,
)


def _request(client: str, headers: dict[str, str] | None = None) -> Request:
    raw_headers = [
        (key.lower().encode(), value.encode())
        for key, value in (headers or {}).items()
    ]
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/v1/auth/login",
            "raw_path": b"/api/v1/auth/login",
            "query_string": b"",
            "headers": raw_headers,
            "client": (client, 12345),
            "server": ("testserver", 80),
        }
    )


def test_rate_limiter_is_process_wide_singleton():
    assert get_rate_limiter() is get_rate_limiter()


async def test_untrusted_forwarded_headers_are_ignored():
    request = _request(
        "203.0.113.9",
        {"X-Forwarded-For": "1.2.3.4", "X-Real-IP": "5.6.7.8"},
    )

    assert await get_client_identifier(request) == "203.0.113.9"


async def test_trusted_proxy_forwarded_header_is_used():
    settings = get_settings()
    original = settings.TRUSTED_PROXY_CIDRS
    settings.TRUSTED_PROXY_CIDRS = "10.0.0.0/8"
    try:
        request = _request("10.1.2.3", {"X-Forwarded-For": "198.51.100.7, 10.1.2.3"})
        assert await get_client_identifier(request) == "198.51.100.7"
    finally:
        settings.TRUSTED_PROXY_CIDRS = original


def test_csrf_requires_matching_double_submit_token():
    csrf = CSRFProtection()

    valid = _request(
        "127.0.0.1",
        {
            "Content-Type": "application/json",
            "Cookie": "csrf_token=token-value",
            "X-CSRF-Token": "token-value",
        },
    )
    missing = _request(
        "127.0.0.1",
        {"Content-Type": "application/json", "Cookie": "csrf_token=token-value"},
    )
    mismatched = _request(
        "127.0.0.1",
        {
            "Content-Type": "application/json",
            "Cookie": "csrf_token=token-value",
            "X-CSRF-Token": "different",
        },
    )

    assert csrf.validate_token(valid) is True
    assert csrf.validate_token(missing) is False
    assert csrf.validate_token(mismatched) is False


async def test_repeated_auth_attempts_return_429_and_xff_cannot_reset_it():
    email = f"nobody-{uuid.uuid4().hex}@example.com"
    limit = AUTH_RATE_LIMITS["login"]["limit"]

    for _ in range(limit):
        # A different spoofed X-Forwarded-For on every attempt must not
        # create a fresh bucket for an untrusted peer.
        await check_auth_rate_limit(
            _request("203.0.113.50", {"X-Forwarded-For": uuid.uuid4().hex}),
            "login",
            email,
        )

    with pytest.raises(HTTPException) as exc:
        await check_auth_rate_limit(
            _request("203.0.113.50", {"X-Forwarded-For": uuid.uuid4().hex}),
            "login",
            email,
        )
    assert exc.value.status_code == 429


def test_production_forces_secure_session_cookies():
    settings = Settings(OPENAGENT_ENV="production", SECRET_KEY="x" * 40, ENCRYPTION_KEY="y" * 40)

    assert settings.SESSION_COOKIE_SECURE is True
