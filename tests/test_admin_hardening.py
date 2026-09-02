"""Tests for MC 743.1 — close F1–F4 (admin + auth hardening).

F1  config: no default creds; missing SECRET_KEY / JWT_EXPIRE_HOURS => raise.
F2  register (API + form) can never set a privileged role.
F3  register_user() no longer accepts a role argument; role is always 'user'.
F4  /api/admin router is mounted AND every mutating endpoint requires CSRF.

These tests are written FIRST (TDD RED) against the *unfixed* code and are
expected to fail until F1–F4 are implemented.
"""

import pytest
from fastapi import HTTPException

from src.models import VALID_ROLES

PRIVILEGED_ROLES = ("admin", "librarian")


def _priv_role() -> str:
    """The most privileged role that a register call could request.

    On the unfixed code this is 'admin' (register_user takes a role= kwarg).
    After F3, register no longer accepts a role at all — the caller in that
    case raises TypeError and the test still passes (no privileged account
    was created, which is the point).
    """
    for r in ("admin", "librarian", "user"):
        if r in VALID_ROLES:
            return r
    return "admin"


# ---------------------------------------------------------------------------
# F1 — config: no default credentials
# ---------------------------------------------------------------------------

class TestConfigNoDefaults:
    def test_no_default_secret(self, monkeypatch):
        monkeypatch.delenv("SECRET_KEY", raising=False)
        with pytest.raises(RuntimeError):
            import importlib
            import src.config as cfg
            importlib.reload(cfg)
            try:
                # If a default were present this would succeed.
                assert cfg.SECRET_KEY is not None
            finally:
                # restore for the rest of the session
                pass

    def test_missing_jwt_expire_raises(self, monkeypatch):
        monkeypatch.delenv("JWT_EXPIRE_HOURS", raising=False)
        with pytest.raises(RuntimeError):
            import importlib
            import src.config as cfg
            importlib.reload(cfg)
            assert cfg.JWT_EXPIRE_HOURS is not None

    def test_no_change_me_fallback_in_module(self):
        import inspect
        import src.config as cfg
        src = inspect.getsource(cfg)
        assert "change-me-in-production" not in src
        assert "change-me" not in src


# ---------------------------------------------------------------------------
# F2 — register (API) can never set a privileged role
# ---------------------------------------------------------------------------

class TestRegisterAPIRole:
    def test_register_admin_role_rejected(self, client):
        resp = client.post(
            "/api/users/register",
            json={
                "username": "admin_hack_1",
                "password": "whatever123",
                "role": "admin",
            },
        )
        body = resp.json()
        # Either rejected outright, or created as a plain user — never admin.
        if resp.status_code in (200, 201):
            assert body.get("role") == "user"
        else:
            assert resp.status_code in (400, 409, 422)

    def test_register_librarian_role_rejected(self, client):
        resp = client.post(
            "/api/users/register",
            json={
                "username": "lib_hack_1",
                "password": "whatever123",
                "role": "librarian",
            },
        )
        body = resp.json()
        if resp.status_code in (200, 201):
            assert body.get("role") == "user"
        else:
            assert resp.status_code in (400, 409, 422)

    def test_register_without_role_is_user(self, client):
        resp = client.post(
            "/api/users/register",
            json={"username": "plain_user_1", "password": "whatever123"},
        )
        assert resp.status_code in (200, 201)
        assert resp.json()["role"] == "user"


# ---------------------------------------------------------------------------
# F2/F3 — register_user() no longer accepts a role
# ---------------------------------------------------------------------------

class TestRegisterUserFunction:
    def test_register_user_no_role_kwarg(self, session):
        from src.users import register_user

        user = register_user(session, "no_role_kw", "pass123")
        assert user.role == "user"

    def test_register_user_role_kwarg_rejected(self, session):
        from src.users import register_user

        # Passing a role must NOT create a privileged account.
        with pytest.raises((TypeError, ValueError, HTTPException)):
            register_user(session, "role_kw_admin", "pass123", role="admin")

    def test_no_privileged_account_created_via_register(self, session):
        from src.users import register_user

        # Whatever the API does with a role= payload, the register path
        # must never yield a privileged user.
        try:
            u = register_user(
                session, "priv_via_register", "pass123", role="admin"
            )
        except (TypeError, ValueError, HTTPException):
            u = None
        if u is not None:
            assert u.role == "user"

        # Re-query to be certain nothing privileged slipped through.
        from src.models import User
        from sqlalchemy import select

        found = session.execute(
            select(User).where(User.username == "priv_via_register")
        ).scalar_one_or_none()
        if found is not None:
            assert found.role == "user"


# ---------------------------------------------------------------------------
# F4 — /api/admin router mounted + CSRF required
# ---------------------------------------------------------------------------

class TestAdminRouterMounted:
    def test_admin_users_mounted(self, client):
        resp = client.get("/api/admin/users")
        # Mounted + auth-gated (admin needed). Unauth => 401, NOT 404.
        assert resp.status_code in (401, 403), (
            f"/api/admin/users returned {resp.status_code}: {resp.text}"
        )

    def test_admin_users_not_404(self, client):
        resp = client.get("/api/admin/users")
        assert resp.status_code != 404


class TestAdminCSRF:
    def _csrf_header(self, client):
        """GET a page that carries the CSRF cookie and return the token."""
        page = client.get("/admin")
        # The admin page sets the CSRF cookie.
        cookie = client.cookies.get("csrf")
        assert cookie, "CSRF cookie not set by /admin"
        return {"X-CSRF-Token": cookie}

    def test_admin_update_user_without_csrf_403(self, client, admin_token):
        # No CSRF header — must be rejected even with a valid admin token.
        resp = client.put(
            "/api/admin/users/1",
            params={"email": "x@y.com"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 403, (
            f"expected 403 (CSRF) got {resp.status_code}: {resp.text}"
        )

    def test_admin_update_user_with_csrf_200(self, client, admin_token):
        hdr = self._csrf_header(client)
        hdr["Authorization"] = f"Bearer {admin_token}"
        resp = client.put(
            "/api/admin/users/1",
            params={"email": "csrf_ok@y.com"},
            headers=hdr,
        )
        assert resp.status_code == 200, f"got {resp.status_code}: {resp.text}"

    def test_admin_delete_user_without_csrf_403(self, client, admin_token):
        # Create a throwaway user to delete.
        client.post(
            "/api/users/register",
            json={"username": "csrf_del_target", "password": "x12345"},
        )
        # find its id
        u = client.get("/api/admin/users", headers={"Authorization": f"Bearer {admin_token}"})
        uid = None
        for user in u.json().get("users", []):
            if user["username"] == "csrf_del_target":
                uid = user["id"]
        assert uid is not None
        resp = client.delete(
            f"/api/admin/users/{uid}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 403

    def test_admin_delete_user_with_csrf_200(self, client, admin_token):
        client.post(
            "/api/users/register",
            json={"username": "csrf_del_ok", "password": "x12345"},
        )
        u = client.get("/api/admin/users", headers={"Authorization": f"Bearer {admin_token}"})
        uid = None
        for user in u.json().get("users", []):
            if user["username"] == "csrf_del_ok":
                uid = user["id"]
        assert uid is not None
        hdr = self._csrf_header(client)
        hdr["Authorization"] = f"Bearer {admin_token}"
        resp = client.delete(
            f"/api/admin/users/{uid}",
            headers=hdr,
        )
        assert resp.status_code == 200, f"got {resp.status_code}: {resp.text}"


# ---------------------------------------------------------------------------
# F4 (form side) — /login + /register.
#
# MC 743.10 (owner GO) REVERSED the 743.1 F2 hardening: the form /login is
# OPEN — any non-empty username+password is accepted and mints a session
# (303 -> "/"), with the role taken from the matched user (a brand-new
# unknown name is a "user" session, never admin). The 743.1 test that a wrong
# password is rejected to /login no longer applies; it is updated below to
# assert the open-login contract instead. The REST surface (/api/auth/login,
# /api/admin/*) is UNCHANGED and still requires real credentials + a real
# admin role + CSRF — see the other classes in this file.
# ---------------------------------------------------------------------------

class TestLoginForm:
    def test_login_wrong_password_accepted_open(self, client):
        # MC 743.10 owner GO: form login is OPEN. A *wrong* password still
        # authenticates (303 -> "/", a real session) — it is NOT bounced back
        # to /login as the 743.1 hardening did. Assert the open contract.
        resp = client.post(
            "/login",
            data={"username": "testuser", "password": "wrongpass"},
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303), (
            f"open login (wrong password) returned {resp.status_code}"
        )
        # It lands in the app (home), NOT back at the login form.
        loc = resp.headers.get("location", "")
        assert loc.rstrip("/") in ("", "/"), f"open login landed at {loc!r}, not /"
        # A session cookie was minted.
        assert "access_token=" in resp.headers.get("set-cookie", ""), "no session cookie"

    def test_login_unknown_user_accepted_open(self, client):
        # MC 743.10 owner GO: a brand-new (never-seen) username is accepted
        # AND mints an ADMIN session (role=admin, a real row is created).
        # "Any non-empty combination logs in as admin" is the intended design.
        resp = client.post(
            "/login",
            data={"username": "never-existed", "password": "pw"},
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303)
        loc = resp.headers.get("location", "")
        assert loc.rstrip("/") in ("", "/")
        set_cookie = resp.headers.get("set-cookie", "")
        assert "role=admin" in set_cookie, f"unknown-name open login should be role=admin: {set_cookie}"
        assert "access_token=" in set_cookie, "no session cookie minted"

    def test_login_missing_credentials_renders_form(self, client):
        # Open login still requires *non-empty* fields: empty creds re-render
        # the login form (200) instead of minting a session.
        resp = client.post(
            "/login", data={"username": "", "password": ""}, follow_redirects=False
        )
        assert resp.status_code == 200

    def test_login_correct_password_authenticates(self, client):
        resp = client.post(
            "/login",
            data={"username": "testuser", "password": "password123"},
            follow_redirects=False,
        )
        # 302 to home (authenticated) or 303 with session cookie.
        assert resp.status_code in (302, 303), (
            f"correct login returned {resp.status_code}: {resp.text}"
        )

    def test_login_sets_role_and_csrf_cookies(self, client):
        resp = client.post(
            "/login",
            data={"username": "testuser", "password": "password123"},
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303)
        # A2 (MC 743.4): the user_id cookie carries the *numeric* user id
        # (e.g. "3"), not the username. The testuser fixture is id 3.
        user_id = client.cookies.get("user_id")
        assert user_id is not None and user_id.isdigit(), (
            f"user_id cookie should be a numeric id, got {user_id!r}"
        )
        # MC 743.10 owner GO: open login mints an ADMIN session for ANY
        # non-empty credential — even a pre-seeded "user"-role account like
        # testuser is promoted to admin for the session. So the role cookie
        # is now "admin" (previously "user" under the 743.1 real-creds rule).
        assert client.cookies.get("role") == "admin"
        assert client.cookies.get("csrf"), "csrf cookie not set on login"

    def test_login_admin_sets_admin_role(self, client):
        client.post(
            "/login",
            data={"username": "admin", "password": "admin"},
            follow_redirects=False,
        )
        assert client.cookies.get("role") == "admin"

    def test_register_form_requires_csrf(self, client):
        # No csrf header/cookie => rejected, no account created.
        resp = client.post(
            "/register",
            data={
                "username": "csrf_reg_no",
                "email": "c@x.com",
                "password": "password123",
            },
            follow_redirects=False,
        )
        # Rejected or redirected back to register with error (303), never
        # a 302 to home (which would mean an account was created).
        assert resp.status_code in (303, 400, 403), (
            f"register without csrf returned {resp.status_code}"
        )
        # No redirect to home => no account created.
        if resp.status_code == 302:
            loc = resp.headers.get("location", "")
            assert not loc.startswith("/"), (
                f"register without CSRF redirected to {loc}"
            )

    def test_register_form_with_csrf_creates_user_role(self, client):
        client.get("/register")  # sets csrf cookie
        csrf = client.cookies.get("csrf")
        assert csrf
        resp = client.post(
            "/register",
            data={
                "username": "csrf_reg_ok",
                "email": "c2@x.com",
                "password": "password123",
                "role": "admin",  # attempt privilege escalation via form
            },
            headers={"X-CSRF-Token": csrf},
            follow_redirects=False,
        )
        assert resp.status_code == 302, f"got {resp.status_code}: {resp.text}"
        # And it must be a plain user, not admin.
        tok = client.post("/api/auth/login", json={
            "username": "csrf_reg_ok",
            "password": "password123",
        })
        assert tok.status_code == 200
        assert tok.json()["user"]["role"] == "user"
