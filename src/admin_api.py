"""REST admin router — /api/admin/users (MC 743.1, F4).

A small, JSON admin surface over the users table, mounted at ``/api/admin``.
Every route is admin-gated (401 unless the Bearer token carries role ``admin``)
AND double-submit-CSRF-gated on mutating operations (403 unless the
``X-CSRF-Token`` header matches the ``csrf`` cookie the client fetched from
``GET /admin``).

Routes
------
GET    /api/admin/users          -> {"users": [ ... ]}      (admin only)
PUT    /api/admin/users/{user_id} -> update a user          (admin + CSRF)
DELETE /api/admin/users/{user_id} -> delete a user          (admin + CSRF)

This is the shape the ``tests/test_admin_hardening.py`` gate asserts:
* unauthenticated  -> 401
* mutating without a CSRF token -> 403
* mutating with a valid CSRF token + admin Bearer -> 200

The CSRF primitives come from ``src.csrf`` (single source of truth, breaks the
old app<->admin circular import). The admin role gate reuses
``src.auth.require_role`` (one implementation, no duplication).
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel

from src.auth import require_role
from src.csrf import verify_csrf
from src.database import get_session
from src.models import User
from src.users import list_users, update_user

# FastAPI treats an ``Optional[...] = None`` body parameter as *required*
# and returns 422 ('body field required') for a bodyless PUT/DELETE
# (verified 2026-08-28 with a TestClient probe). An explicit
# ``Body(default=None)`` keeps the payload optional, so the REST admin
# surface works with or without a JSON body.
_EMPTY = Body(default=None)

_dep_admin = require_role(["admin"])


class UserUpdate(BaseModel):
    """Payload for PUT /api/admin/users/{user_id} — all fields optional."""
    username: Optional[str] = None
    role: Optional[str] = None
    email: Optional[str] = None
    password: Optional[str] = None


def create_router() -> APIRouter:
    """Build and return the REST admin router (mount at ``/api/admin``)."""
    router = APIRouter(prefix="/api/admin", tags=["admin"])

    # ------------------------------------------------------------------
    # GET /api/admin/users — list users (admin only)
    # ------------------------------------------------------------------
    @router.get("/users")
    async def admin_list_users(
        _admin: dict = Depends(_dep_admin),
        db=Depends(get_session),
    ) -> dict:
        """Return all users as a JSON list (admin only; 401 for anyone else)."""
        users = [
            {
                "id": u.id,
                "username": u.username,
                "role": u.role,
                "email": u.email,
            }
            for u in list_users(db)
        ]
        return {"users": users}

    # ------------------------------------------------------------------
    # PUT /api/admin/users/{user_id} — update a user (admin + CSRF)
    # ------------------------------------------------------------------
    @router.put("/users/{user_id}")
    async def admin_update_user(
        user_id: int,
        request: Request,
        body: UserUpdate = _EMPTY,
        _admin: dict = Depends(_dep_admin),
        _csrf: None = Depends(verify_csrf),
        db=Depends(get_session),
    ) -> dict:
        """Update a user's mutable fields. Requires a valid CSRF token (403
        without) and an admin Bearer token (401 without).

        The payload is a JSON body; a few test clients and curl invocations
        pass the fields as query parameters instead, so the query string is
        merged in as a fallback (the JSON body wins when both are present).
        """
        fields = {}
        for key in ("username", "role", "email", "password"):
            val = request.query_params.get(key)
            if val is not None:
                fields[key] = val
        if body is not None:
            for key in ("username", "role", "email", "password"):
                if getattr(body, key) is not None:
                    fields[key] = getattr(body, key)
        kwargs: dict[str, Any] = {k: v for k, v in fields.items() if v is not None}
        if not kwargs:
            raise HTTPException(status_code=400, detail="No fields to update")
        user = update_user(db, user_id, **kwargs)
        return {
            "id": user.id,
            "username": user.username,
            "role": user.role,
            "email": user.email,
        }

    # ------------------------------------------------------------------
    # DELETE /api/admin/users/{user_id} — delete a user (admin + CSRF)
    # ------------------------------------------------------------------
    @router.delete("/users/{user_id}")
    async def admin_delete_user(
        user_id: int,
        request: Request,
        _admin: dict = Depends(_dep_admin),
        _csrf: None = Depends(verify_csrf),
        db=Depends(get_session),
    ) -> dict:
        """Delete a user. Requires a valid CSRF token (403 without) and an
        admin Bearer token (401 without)."""
        user = db.query(User).filter(User.id == user_id).first()
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")
        db.delete(user)
        db.commit()
        return {"deleted": user_id}

    return router
