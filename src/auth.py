"""Auth module — JWT helpers, password hashing, FastAPI role dependency.

Functions:
    create_access_token(user_id, role)  → JWT string (expires 24 h)
    verify_token(token)                 → dict(user_id, role) or None
    hash_password(password)             → bcrypt hash string
    check_password(password, hash)      → bool
    require_role(allowed_roles)         → FastAPI dependency callable

Endpoints:
    POST /api/auth/login  (username, password) → {access_token, user}
"""

from datetime import UTC, datetime, timedelta

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from src.config import ACCESS_TOKEN_EXPIRE_MINUTES, JWT_ALGORITHM, SECRET_KEY

# ---------------------------------------------------------------------------
# Password hashing (bcrypt directly — avoids passlib 1.7 / bcrypt 5 compat)
# ---------------------------------------------------------------------------


def hash_password(password: str) -> str:
    """Return a ``$2b$...`` bcrypt hash of *password*."""
    return bcrypt.hashpw(
        password.encode("utf-8"), bcrypt.gensalt()
    ).decode("utf-8")


def check_password(password: str, hashed: str) -> bool:
    """Return True if *password* matches *hashed*."""
    return bcrypt.checkpw(
        password.encode("utf-8"), hashed.encode("utf-8")
    )

# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------


def create_access_token(user_id: int, role: str) -> str:
    """Create a signed JWT that expires after 24 hours."""
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),       # subject = user id
        "role": role,
        "exp": now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
        "iat": now,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=JWT_ALGORITHM)


def verify_token(token: str) -> dict | None:
    """Decode and verify *token*; return payload dict or None on failure.

    A-01 (MC 1267): tokens recorded in the logout denylist are rejected
    here, so a logged-out session's JWT is invalid for its remaining TTL.
    """
    if token in _revoked_tokens:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[JWT_ALGORITHM])
        return {
            "user_id": int(payload["sub"]),
            "role": payload["role"],
        }
    except JWTError:
        return None


# A-01 (MC 1267): process-local logout denylist. /logout records the
# presented JWT here so the same token is rejected on every later request
# (verify_token returns None). In-memory is sufficient for this PoC: the
# denylist lives exactly as long as the process that issued the tokens,
# and a restart invalidates all sessions anyway (same SECRET_KEY caveat
# the PoC already accepts). Entries self-expire with the token TTL.
_revoked_tokens: set[str] = set()


def revoke_token(token: str) -> None:
    """Record *token* as logged out (A-01, MC 1267)."""
    _revoked_tokens.add(token)

# ---------------------------------------------------------------------------
# FastAPI dependency — role gate
# ---------------------------------------------------------------------------

_scheme = HTTPBearer(auto_error=False)


def require_role(allowed_roles: list[str]):
    """Return a FastAPI dependency that rejects requests lacking one of
    *allowed_roles* in the JWT payload.

    Usage::

        @router.get("/secret")
        def secret_view(current_user = require_role(["admin"])):
            ...
    """

    def _dep(credentials: HTTPAuthorizationCredentials = Depends(_scheme)) -> dict:
        """Inner dep — called by FastAPI for every protected route."""
        if credentials is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated",
                headers={"WWW-Authenticate": "Bearer"},
            )
        user = verify_token(credentials.credentials)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if user["role"] not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return user

    return _dep

# ---------------------------------------------------------------------------
# Login endpoint stub
# ---------------------------------------------------------------------------

from fastapi import APIRouter, Body
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.database import get_session
from src.models import User

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(
    body: LoginRequest = Body(...),
    db: Session = Depends(get_session),
):
    """Authenticate *username*/*password* against the User table."""
    user = db.query(User).filter(User.username == body.username).first()
    if user is None or not check_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ogiltiga användaruppgifter",
        )
    token = create_access_token(user.id, user.role)
    return {
        "access_token": token,
        "user": {
            "id": user.id,
            "username": user.username,
            "role": user.role,
        },
    }


@router.get("/verify")
async def verify(
    token: str,
    db: Session = Depends(get_session),
) -> dict:
    """Decode JWT and return user info (or 401 if invalid)."""
    payload = verify_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ogiltig eller utgången token",
        )
    user = db.query(User).filter(User.id == payload["user_id"]).first()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return {
        "user_id": payload["user_id"],
        "role": payload["role"],
        "username": user.username,
    }
