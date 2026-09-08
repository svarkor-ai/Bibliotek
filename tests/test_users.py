"""Tests for User CRUD + role-based access control."""

from unittest.mock import patch

import pytest

from src.users import (
    register_user,
    get_user,
    list_users,
    update_user,
)
from src.models import VALID_ROLES


# ---------------------------------------------------------------------------
# register_user
# ---------------------------------------------------------------------------

class TestRegisterUser:
    def test_register_basic(self, session):
        user = register_user(session, "newuser", "pass123")
        assert user.username == "newuser"
        assert user.role == "user"
        assert user.email is None
        assert user.password_hash.startswith("$2b$")

    def test_register_forces_user_role(self, session):
        # MC 743.1 F2/F3: register_user has no role param and always yields
        # a plain "user". Any role kwarg is rejected outright.
        user = register_user(session, "lib1", "pass123")
        assert user.role == "user"
        with pytest.raises((TypeError, ValueError)):
            register_user(session, "lib1b", "pass123", role="librarian")

    def test_register_with_email(self, session):
        user = register_user(session, "mailuser", "pass123", email="a@b.com")
        assert user.email == "a@b.com"

    def test_duplicate_username_raises(self, session):
        register_user(session, "dup", "pass123")
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            register_user(session, "dup", "pass456")
        assert exc.value.status_code == 409
        assert "already exists" in exc.value.detail

    def test_invalid_role_kwarg_rejected(self, session):
        # A role kwarg no longer exists; passing one is rejected (TypeError).
        with pytest.raises((TypeError, ValueError)):
            register_user(session, "baduser", "pass123", role="superadmin")

    def test_register_ignores_role_kwarg_never_privileged(self, session):
        # Whatever the old "valid roles" were, the register path can never
        # create a privileged account (MC 743.1 F2/F3).
        for role in VALID_ROLES:
            try:
                u = register_user(session, f"role_{role}", "pass123", role=role)
            except (TypeError, ValueError):
                u = None
            if u is not None:
                assert u.role == "user"


# ---------------------------------------------------------------------------
# get_user
# ---------------------------------------------------------------------------

class TestGetUser:
    def test_get_existing(self, session):
        u = register_user(session, "getme", "pass123")
        user = get_user(session, u.id)
        assert user.username == "getme"

    def test_get_missing_404(self, session):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            get_user(session, 9999)
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# list_users
# ---------------------------------------------------------------------------

class TestListUsers:
    def test_list_all(self, session):
        register_user(session, "listall_a", "p")
        register_user(session, "listall_b", "p")
        users = list_users(session)
        usernames = {u.username for u in users}
        assert "listall_a" in usernames
        assert "listall_b" in usernames

    def test_list_filtered(self, session):
        from src.users import register_user as _ru
        from src.users import update_user as _uu
        _ru(session, "listu1", "p")
        l = _ru(session, "listu2", "p")
        _uu(session, l.id, role="librarian")
        librarians = list_users(session, role_filter="librarian")
        librarian_names = {u.username for u in librarians}
        assert "listu2" in librarian_names
        assert len(librarians) >= 1


# ---------------------------------------------------------------------------
# update_user
# ---------------------------------------------------------------------------

class TestUpdateUser:
    def test_update_email(self, session):
        register_user(session, "upd", "p")
        user = update_user(session, 1, email="new@email.com")
        assert user.email == "new@email.com"

    def test_update_password_hashes(self, session):
        register_user(session, "pwdupd", "p")
        user = update_user(session, 1, password="newpass")
        assert check_password("newpass", user.password_hash)

    def test_update_role_valid(self, session):
        register_user(session, "roleupd", "p")
        user = update_user(session, 1, role="librarian")
        assert user.role == "librarian"

    def test_update_role_invalid(self, session):
        register_user(session, "badrole", "p")
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            update_user(session, 1, role="superuser")
        assert exc.value.status_code == 400

    def test_update_missing_user_404(self, session):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            update_user(session, 9999, email="nope@nope.com")
        assert exc.value.status_code == 404


def check_password(password, hashed):
    """Inline bcrypt check for tests that import it."""
    import bcrypt
    return bcrypt.checkpw(
        password.encode("utf-8"), hashed.encode("utf-8")
    )


# ---------------------------------------------------------------------------
# Role-based access control via endpoints
# ---------------------------------------------------------------------------

class TestRBACEndpoints:
    """Verify that the FastAPI routers enforce role checks correctly."""

    def test_admin_list_users(self, client, admin_token):
        """Admin can list all users."""
        resp = client.get(
            "/api/users",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "users" in body
        assert body["total"] >= 3  # admin + librarian + testuser

    def test_librarian_cannot_list_users(self, client, librarian_token):
        """Only admin can list all users."""
        resp = client.get(
            "/api/users",
            headers={"Authorization": f"Bearer {librarian_token}"},
        )
        assert resp.status_code == 403

    def test_user_cannot_list_users(self, client, user_token):
        """Regular user cannot list all users."""
        resp = client.get(
            "/api/users",
            headers={"Authorization": f"Bearer {user_token}"},
        )
        assert resp.status_code == 403

    def test_no_auth_forbidden(self, client):
        """Unauthenticated requests are rejected."""
        resp = client.get("/api/users")
        assert resp.status_code in (401, 403)

    def test_admin_update_user(self, client, admin_token):
        """Admin can update a user."""
        resp = client.put(
            "/api/users/1",
            params={"email": "updated@email.com"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["email"] == "updated@email.com"

    def test_librarian_update_user(self, client, librarian_token):
        """Librarian can update a user."""
        resp = client.put(
            "/api/users/1",
            params={"email": "lib@email.com"},
            headers={"Authorization": f"Bearer {librarian_token}"},
        )
        assert resp.status_code == 200

    def test_user_cannot_update_other(self, client, user_token):
        """Regular user cannot update other users' data."""
        resp = client.put(
            "/api/users/2",
            params={"email": "hacker@email.com"},
            headers={"Authorization": f"Bearer {user_token}"},
        )
        assert resp.status_code == 403

    def test_user_cannot_update_self(self, client, user_token):
        """Regular user cannot use the PUT endpoint at all (admin/librarian only)."""
        resp = client.put(
            "/api/users/3",
            params={"email": "self@email.com"},
            headers={"Authorization": f"Bearer {user_token}"},
        )
        assert resp.status_code == 403

    def test_admin_get_user(self, client, admin_token):
        """Admin can view a specific user."""
        resp = client.get(
            "/api/users/3",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["username"] == "testuser"

    def test_librarian_get_user(self, client, librarian_token):
        """Librarian can view a specific user."""
        resp = client.get(
            "/api/users/1",
            headers={"Authorization": f"Bearer {librarian_token}"},
        )
        assert resp.status_code == 200

    def test_register_endpoint_public(self, client):
        """User registration is public (no auth required)."""
        resp = client.post(
            "/api/users/register",
            json={"username": "newreg", "password": "regpass", "role": "user"},
        )
        assert resp.status_code == 200
        assert resp.json()["username"] == "newreg"
