"""User CRUD + FastAPI router for Bibliotek.

Functions
    register_user(db, username, password, email) -> User   (role always "user")
    get_user(db, user_id) -> User or 404
    list_users(db, role_filter) -> list[User]
    update_user(db, user_id, **kwargs) -> User
    create_router() -> FastAPI router mounted at /api/users

Endpoints
    POST   /api/users/register          → {user}
    GET    /api/users                   → [users]          [admin]
    GET    /api/users/{id}              → {user}           [admin/librarian]
    PUT    /api/users/{id}              → {user}           [admin/librarian]
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from src.auth import hash_password, require_role
from src.database import get_session
from src.models import VALID_ROLES, User

# ---------------------------------------------------------------------------
# CRUD functions — accept explicit DB session
# ---------------------------------------------------------------------------


def register_user(
    db: Session,
    username: str,
    password: str,
    email: str | None = None,
) -> User:
    """Persist a new user with a bcrypt-hashed password.

    The role is ALWAYS ``"user"``. There is deliberately no ``role`` parameter:
    callers (API registration, web form) can never create a privileged account,
    which closes the register-path privilege-escalation hole (MC 743.1, F2/F3).

    Parameters
    ----------
    db:
        SQLAlchemy session.
    username:
        Unique username (max 50 chars).
    password:
        Plain-text password (hashed with bcrypt before storage).
    email:
        Optional email address.

    Returns
    -------
    User
        The newly created ORM User instance (role always ``"user"``).

    Raises
    ------
    HTTPException(409):
        If *username* already exists.
    """
    existing = (
        db.query(User)
        .filter(User.username == username)
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"User {username!r} already exists",
        )

    user = User(
        username=username,
        password_hash=hash_password(password),
        role="user",  # forced — never taken from caller input (F2/F3)
        email=email,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_user(db: Session, user_id: int) -> User:
    """Retrieve a single user by primary key.

    Returns
    -------
    User
        The user if found.

    Raises
    ------
    HTTPException(404):
        When no user with *user_id* exists.
    """
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    return user


def get_user_by_username(db: Session, username: str) -> User | None:
    """Look up a user by username (case-sensitive). Returns None if not found."""
    return db.query(User).filter(User.username == username).first()


def list_users(db: Session, role_filter: str | None = None) -> list[User]:
    """Return a (optionally role-filtered) list of users.

    Parameters
    ----------
    db:
        SQLAlchemy session.
    role_filter:
        Optional role to filter by (e.g. ``"librarian"``).

    Returns
    -------
    list[User]
    """
    q = db.query(User)
    if role_filter:
        q = q.filter(User.role == role_filter)
    return q.order_by(User.id).all()


def update_user(
    db: Session,
    user_id: int,
    **kwargs,
) -> User:
    """Update fields on an existing user.

    Accepts any keyword arguments that match User columns:
    ``username``, ``password``, ``role``, ``email``.

    ``password`` is automatically hashed with bcrypt before storage.

    Returns
    -------
    User
        The updated ORM User instance.

    Raises
    ------
    HTTPException(404):
        If no user with *user_id* exists.
    HTTPException(400):
        If a validation error occurs (e.g. invalid role).
    """
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Validate role if being updated
    if "role" in kwargs and kwargs["role"] not in VALID_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid role {kwargs['role']!r}. Must be one of {VALID_ROLES}",
        )

    # Hash password if being updated
    if "password" in kwargs:
        kwargs["password_hash"] = hash_password(kwargs.pop("password"))

    for key, value in kwargs.items():
        setattr(user, key, value)

    db.commit()
    db.refresh(user)
    return user


# ---------------------------------------------------------------------------
# FastAPI router
# ---------------------------------------------------------------------------


def create_router() -> APIRouter:
    """Build and return a FastAPI ``APIRouter`` mounted at ``/api/users``."""
    router = APIRouter(prefix="/api/users", tags=["users"])

    _dep_admin = require_role(["admin"])
    _dep_admin_librarian = require_role(["admin", "librarian"])

    # ------------------------------------------------------------------
    # POST /api/users/register — public registration
    # ------------------------------------------------------------------
    class RegisterRequest(BaseModel):
        """Request body for public user registration."""
        username: str
        password: str
        role: str = "user"
        email: str | None = None

        @field_validator("username", "password")
        @classmethod
        def not_empty(cls, v: str) -> str:
            if not v.strip():
                raise ValueError("must not be empty")
            return v

    @router.post("/register")
    async def register_endpoint(
        body: RegisterRequest,
        db: Session = Depends(get_session),
    ) -> dict:
        """Public user registration.

        The new account is ALWAYS created with role ``"user"`` — the
        ``RegisterRequest.role`` field is ignored on purpose so the register
        endpoint can never mint a privileged account (MC 743.1, F2).
        """
        # NOTE: body.role is deliberately NOT passed through — role is forced
        # to "user" inside register_user() (F2/F3).
        user = register_user(db, body.username, body.password, body.email)
        return {
            "id": user.id,
            "username": user.username,
            "role": user.role,
            "email": user.email,
            "created_at": user.created_at.isoformat() if user.created_at else None,
        }

    # ------------------------------------------------------------------
    # GET /api/users — list all users [admin]
    # ------------------------------------------------------------------
    @router.get("", response_model=dict)
    async def list_users_endpoint(
        role_filter: str | None = Query(None),
        db: Session = Depends(get_session),
        current_user: dict = Depends(_dep_admin),
    ) -> dict:
        """List all users (admin only)."""
        users = list_users(db, role_filter=role_filter)
        return {
            "users": [
                {
                    "id": u.id,
                    "username": u.username,
                    "role": u.role,
                    "email": u.email,
                    "created_at": u.created_at.isoformat() if u.created_at else None,
                    "active": True,
                }
                for u in users
            ],
            "total": len(users),
        }

    # ------------------------------------------------------------------
    # GET /api/users/{user_id} — get single user [admin/librarian]
    # ------------------------------------------------------------------
    @router.get("/{user_id}", response_model=dict)
    async def get_user_endpoint(
        user_id: int,
        db: Session = Depends(get_session),
        current_user: dict = Depends(_dep_admin_librarian),
    ) -> dict:
        """Get a single user by ID (admin/librarian only)."""
        user = get_user(db, user_id)
        return {
            "id": user.id,
            "username": user.username,
            "role": user.role,
            "email": user.email,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "active": True,
        }

    # ------------------------------------------------------------------
    # PUT /api/users/{user_id} — update user [admin/librarian]
    # ------------------------------------------------------------------
    @router.put("/{user_id}", response_model=dict)
    async def update_user_endpoint(
        user_id: int,
        username: str | None = Query(None),
        password: str | None = Query(None),
        role: str | None = Query(None),
        email: str | None = Query(None),
        db: Session = Depends(get_session),
        current_user: dict = Depends(_dep_admin_librarian),
    ) -> dict:
        """Update a user (admin/librarian only).

        Omitted fields are not changed.  Pass ``password`` to hash & store
        a new password.
        """
        kwargs: dict = {}
        if username is not None:
            kwargs["username"] = username
        if password is not None:
            kwargs["password"] = password
        if role is not None:
            kwargs["role"] = role
        if email is not None:
            kwargs["email"] = email

        if not kwargs:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No fields to update",
            )

        user = update_user(db, user_id, **kwargs)
        return {
            "id": user.id,
            "username": user.username,
            "role": user.role,
            "email": user.email,
            "created_at": user.created_at.isoformat() if user.created_at else None,
        }

    return router
