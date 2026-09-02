"""CSRF protection helpers (MC 743.1, F4) — single source of truth.

The admin router (``src.admin``) and the web forms (``src.app``) both need to
issue and verify a double-submit CSRF token. Both used to import helpers from
``src.app``, which is a circular import (app mounts admin). This module breaks
that cycle and centralises the naming so the cookie name is the one the rest
of the code and the tests actually read.

Pattern: double-submit cookie.

* ``/login`` (and every page that renders a form) sets a per-session random
  ``csrf`` cookie.
* The form / JS reads the cookie and echoes it back in the ``X-CSRF-Token``
  header (or a ``csrf_token`` form field).
* ``verify_csrf(request)`` compares the submitted token to the cookie; a
  mismatch (or a missing cookie / token) is rejected with 403.

No JWT is stored in the browser; the token is a 32-byte ``secrets.token_hex``
value — unguessable and per-session.
"""

from __future__ import annotations

import secrets

from fastapi import HTTPException, Request, status

from src.config import CSRF_COOKIE_NAME, CSRF_FIELD_NAME, CSRF_HEADER_NAME


def generate_csrf_token() -> str:
    """Return a fresh random CSRF token (hex string, >= 32 chars)."""
    return secrets.token_hex(16)


async def _read_token(request: Request) -> str:
    """Return the token submitted by the client, or ``""`` if absent.

    The client may echo the token back via the ``X-CSRF-Token`` header
    (the JS-driven admin actions) or a ``csrf_token`` form field (the HTML
    register/login forms). The header wins when both are present.
    """
    header = request.headers.get(CSRF_HEADER_NAME) or ""
    if header:
        return header.strip()
    # ``request.form()`` is a coroutine — it MUST be awaited, otherwise it
    # returns an un-awaited wrapper and ``.get()`` below raises
    # ``AttributeError`` (the MC 743.4 await-crash). We only ever call this
    # from routes that carry a body, so awaiting is safe; a request with no
    # form body simply yields an empty form.
    try:
        form = await request.form()
    except Exception:
        form = None
    if form is not None:
        value = form.get(CSRF_FIELD_NAME)
        if isinstance(value, str) and value:
            return value.strip()
    query = request.query_params.get(CSRF_FIELD_NAME)
    if query:
        return query.strip()
    return ""


def set_csrf_cookie(response, token: str) -> None:
    """Attach the CSRF cookie to *response* (HttpOnly=False so JS can read it)."""
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=token,
        httponly=False,      # JS needs to read it to build the header
        samesite="Lax",
        max_age=24 * 3600,
    )


async def verify_csrf(request: Request, token: str | None = None):
    """Validate a double-submit CSRF token.

    Two calling shapes (both keep the pre-existing HTML admin routes working
    AND give the REST admin router a clean raise-based dependency):

    1. ``await verify_csrf(request)`` — used as a FastAPI ``Depends`` on the
       REST admin endpoints. Raises ``HTTPException(403)`` if the submitted
       token (header / form field) is missing or does not match the cookie.

    2. ``await verify_csrf(request, token)`` — the legacy boolean form used
       by the HTML admin routes in ``src/admin.py``. Returns ``True``/``False``
       and never raises. *token* is the client-submitted token (e.g. the
       ``csrf_token`` form field); it is compared to the cookie.

    The function is ``async`` because shape 1 reads the (already-parsed) form
    body via ``await request.form()``; callers must ``await`` it.
    """
    cookie = request.cookies.get(CSRF_COOKIE_NAME, "")

    if token is not None:
        # Legacy boolean form (src/admin.py HTML routes).
        if not cookie or not token:
            return False
        return bool(secrets.compare_digest(cookie, str(token).strip()))

    # Raise-based form (REST admin router — used via Depends()).
    submitted = await _read_token(request)
    if not cookie or not submitted:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing CSRF token",
        )
    if not secrets.compare_digest(cookie, submitted):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token mismatch",
        )
    return None
