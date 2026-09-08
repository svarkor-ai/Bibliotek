"""Application configuration for Bibliotek.

Security note (MC 743.1, F1): this module NO LONGER falls back to any
default credentials. ``SECRET_KEY`` and ``JWT_EXPIRE_HOURS`` MUST be provided
via the environment (or a ``.env`` file); failing to set either one is a hard
error at import time rather than a silent, guessable secret.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root if present (dev convenience).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")


def _require_int(name: str) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        raise RuntimeError(
            f"{name} must be set in the environment (no default fallback). "
            "Set it in the process environment or a .env file."
        )
    try:
        return int(raw)
    except ValueError as e:
        raise RuntimeError(
            f"{name} must be an integer, got {raw!r}"
        ) from e


def _require_nonempty(name: str, min_length: int = 1) -> str:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        raise RuntimeError(
            f"{name} must be set in the environment (no default fallback). "
            "Set it in the process environment or a .env file."
        )
    if len(raw) < min_length:
        raise RuntimeError(
            f"{name} must be at least {min_length} characters long "
            f"(got {len(raw)})."
        )
    return raw


# --- JWT configuration ---
# No default secret. A missing / too-short SECRET_KEY aborts startup so a
# real, secret, guess-resistant key is always used (F1).
SECRET_KEY = _require_nonempty("SECRET_KEY", min_length=16)
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = _require_int("JWT_EXPIRE_HOURS")
# Backwards-compatible name used by src.auth.create_access_token.
ACCESS_TOKEN_EXPIRE_MINUTES = JWT_EXPIRE_HOURS * 60

# --- Session / role cookies (F4) ---
# The web session is short-lived: the JWT is NOT stored in the browser; the
# cookies only carry user_id + role and are re-validated on each request.
SESSION_TTL_HOURS = 24
ROLE_COOKIE_MAX_AGE = int(
    os.getenv("ROLE_COOKIE_MAX_AGE", str(SESSION_TTL_HOURS * 3600))
)

# --- CSRF (F4) ---
# Double-submit pattern. The cookie name is "csrf" — that is what the admin
# JS (static/js) and the test client read. The header and form-field names are
# the pair the client echoes back. These are imported by src.csrf, which is the
# single source of truth for the helpers (set_csrf_cookie / verify_csrf).
CSRF_COOKIE_NAME = "csrf"
CSRF_FIELD_NAME = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"
CSRF_MIN_LENGTH = 32

# --- Demo write guard (MC 2034.2) ---
# The per-visitor rate limiter is a demo hardening. It is ON by default (a
# public URL needs it), but the test suite toggles it OFF (set
# ENABLE_DEMO_WRITE_GUARD=false) because the 10/300s bucket makes the
# otherwise-deterministic suite flaky: a burst of writes from one test IP gets
# a 429 partway through a run. Tests opt out explicitly; production keeps it.
ENABLE_DEMO_WRITE_GUARD = (
    os.getenv("ENABLE_DEMO_WRITE_GUARD", "true").strip().lower() not in
    ("0", "false", "no", "off")
)

# --- Database ---
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./bibliotek.db")
