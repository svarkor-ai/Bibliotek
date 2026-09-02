"""MC 743.4 (T2, lane A) - the user_id plumbing + the missing web endpoints.

A2: the API login returns the user id (the client and the admin API key
    users on id, not username).
A3: REST admin CRUD with CSRF works (PUT/DELETE /api/admin/users/<id>).
C8a: the web borrower-edit form persists changes (email).
C8b: the web staff-edit form persists changes (role).
C11: POST /admin/personal (create staff) is reachable and persists.
C12: POST /admin/personal/self (password reset) is reachable and persists.

All tests use the same conftest fixtures (client/session/admin_token) as the
rest of the suite, so they run against a fresh in-memory SQLite DB per test.

Seeded users (conftest._seed_users):
  id=1 admin  (role=admin,     password="admin")
  id=2 librarian (role=librarian, password="librarian")
  id=3 testuser (role=user,    password="password123")
"""

from __future__ import annotations

import pytest

# Test credential literals, namespaced by MC id (743.13 F4): the literals used
# below are module constants so no other test file can silently reuse them,
# and so a stale credential cannot match an unrelated seed value.
MC743_13_STAFF_PASS = "staffpass743"
MC743_13_RESET_PASS = "brandnewpass743"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _csrf_token_from_login(client):
    """Log in via the web form and return the csrf cookie set by the response."""
    resp = client.post(
        "/login",
        data={"username": "admin", "password": "admin"},
        follow_redirects=False,
    )
    assert resp.status_code == 303, f"login failed: {resp.status_code}"
    token = client.cookies.get("csrf")
    assert token, "csrf cookie not set after login"
    return token


def _admin_headers(admin_token: str, csrf: str | None = None) -> dict:
    h = {"Authorization": f"Bearer {admin_token}"}
    if csrf:
        h["X-CSRF-Token"] = csrf
    return h


# ---------------------------------------------------------------------------
# A2 - API login returns the user id
# ---------------------------------------------------------------------------

def test_api_login_returns_id_field(client):
    """POST /api/auth/login returns a user object with an `id` key."""
    resp = client.post(
        "/api/auth/login",
        json={"username": "testuser", "password": "password123"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "user" in body, f"login response missing 'user': {body}"
    assert "id" in body["user"], f"login user object missing 'id': {body}"
    assert body["user"]["id"] == 3, f"expected id=3, got {body['user']['id']}"
    assert body["user"]["username"] == "testuser"


def test_api_login_id_matches_db_row(client, session):
    """The id in the login response matches the actual DB row."""
    from src.models import User

    resp = client.post(
        "/api/auth/login",
        json={"username": "librarian", "password": "librarian"},
    )
    assert resp.status_code == 200
    body = resp.json()
    row = session.query(User).filter(User.username == "librarian").first()
    assert row is not None
    assert body["user"]["id"] == row.id


# ---------------------------------------------------------------------------
# A3 - REST admin CRUD with CSRF works
# ---------------------------------------------------------------------------

def test_admin_rest_put_with_csrf_updates_email(client, admin_token):
    """PUT /api/admin/users/3 with CSRF token updates the email."""
    csrf = _csrf_token_from_login(client)
    resp = client.put(
        "/api/admin/users/3",
        json={"email": "updated@ex.com"},
        headers=_admin_headers(admin_token, csrf),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("email") == "updated@ex.com", f"PUT did not persist email: {body}"


def test_admin_rest_delete_with_csrf_deletes_user(client, admin_token, session):
    """DELETE /api/admin/users/<id> with CSRF token deletes the user."""
    from src.users import register_user
    from src.models import User

    victim = register_user(session, "victim743", "victimpass743")
    session.commit()
    victim_id = victim.id

    csrf = _csrf_token_from_login(client)
    resp = client.delete(
        f"/api/admin/users/{victim_id}",
        headers=_admin_headers(admin_token, csrf),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json().get("deleted") == victim_id
    row = session.query(User).filter(User.id == victim_id).first()
    assert row is None, "user was not actually deleted"


def test_admin_rest_delete_without_csrf_is_403(client, admin_token):
    """DELETE without a CSRF token is rejected with 403."""
    resp = client.delete("/api/admin/users/3", headers=_admin_headers(admin_token))
    assert resp.status_code == 403, f"expected 403, got {resp.status_code}: {resp.text}"


# ---------------------------------------------------------------------------
# C8a - web borrower edit persists
# ---------------------------------------------------------------------------

def test_web_borrower_edit_persists_email(client, session):
    """POST /admin/lantagare/3 with a valid CSRF token updates the email."""
    from src.models import User

    csrf = _csrf_token_from_login(client)
    resp = client.post(
        "/admin/lantagare/3",
        data={"email": "borrower_new@ex.com", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert resp.status_code == 303, f"expected 303, got {resp.status_code}: {resp.text[:300]}"
    loc = resp.headers.get("location", "")
    assert "error=" not in loc, f"redirect went to error page: {loc}"
    row = session.query(User).filter(User.id == 3).first()
    assert row is not None
    assert row.email == "borrower_new@ex.com", f"email not persisted: {row.email}"


# ---------------------------------------------------------------------------
# C8b - web staff edit persists
# ---------------------------------------------------------------------------

def test_web_staff_edit_persists_role(client, session):
    """POST /admin/personal/2 with a valid CSRF token updates the role.

    User id 2 is "librarian" (role=librarian). We promote them to admin.
    """
    from src.models import User

    csrf = _csrf_token_from_login(client)
    resp = client.post(
        "/admin/personal/2",
        data={"role": "admin", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert resp.status_code == 303, f"expected 303, got {resp.status_code}: {resp.text[:300]}"
    loc = resp.headers.get("location", "")
    assert "error=" not in loc, f"redirect went to error page: {loc}"
    row = session.query(User).filter(User.id == 2).first()
    assert row is not None
    assert row.role == "admin", f"role not persisted: {row.role}"


# ---------------------------------------------------------------------------
# C11 - POST /admin/personal (create staff) is reachable
# ---------------------------------------------------------------------------

def test_web_staff_create_reachable(client, session):
    """POST /admin/personal with a valid CSRF token creates a staff account."""
    from src.models import User

    csrf = _csrf_token_from_login(client)
    resp = client.post(
        "/admin/personal",
        data={
            "username": "newstaff743",
            "password": MC743_13_STAFF_PASS,
            "email": "newstaff@ex.com",
            "role": "staff",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303, f"expected 303, got {resp.status_code}: {resp.text[:300]}"
    loc = resp.headers.get("location", "")
    assert "error=" not in loc, f"redirect went to error page: {loc}"
    row = session.query(User).filter(User.username == "newstaff743").first()
    assert row is not None, "staff user was not created"
    assert row.role == "staff"


# ---------------------------------------------------------------------------
# C12 - POST /admin/personal/self (password reset) is reachable
# ---------------------------------------------------------------------------

def test_web_self_password_reset_reachable(client, session):
    """POST /admin/personal/self with a valid CSRF token resets the password."""
    from src.models import User
    from src.auth import check_password

    csrf = _csrf_token_from_login(client)
    resp = client.post(
        "/admin/personal/self",
        data={
            "password": MC743_13_RESET_PASS,
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303, f"expected 303, got {resp.status_code}: {resp.text[:300]}"
    loc = resp.headers.get("location", "")
    assert "error=" not in loc, f"redirect went to error page: {loc}"
    admin_row = session.query(User).filter(User.username == "admin").first()
    assert admin_row is not None
    assert check_password(MC743_13_RESET_PASS, admin_row.password_hash), "password was not updated"
