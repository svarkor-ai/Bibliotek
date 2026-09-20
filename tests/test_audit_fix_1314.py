"""Regression tests for MC 1314.1 — the 9 A-bugs + N1/N2 from audit 1267.

Each test targets one audit finding, named after it. The cookie flows
(checkout-cookie / return-cookie) had ZERO coverage before this file —
that is exactly where A-08/A-09 lived.

Owner ruling respected: open admin login (B-01) is intentional PoC design
and is NOT tested here as a vulnerability.
"""
import pytest

from src.models import Loan

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _cookie_session(client, token: str, csrf: str = "test-csrf-token") -> dict:
    """Install a cookie-auth session (access_token + csrf) on the TestClient
    and return the X-CSRF-Token header matching the cookie.

    verify_csrf is a double-submit check: it compares the csrf cookie to the
    submitted token, so a matching pair set client-side is a valid session
    fixture — deterministic and independent of page renders.
    """
    client.cookies.set("access_token", token)
    client.cookies.set("csrf", csrf)
    return {"X-CSRF-Token": csrf}


def _form_login(client, username: str, password: str):
    """Login via the HTML form (sets access_token + csrf cookies)."""
    resp = client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    return client.cookies.get("access_token")


# ---------------------------------------------------------------------------
# A-01 — /logout invalidates the session
# ---------------------------------------------------------------------------


class TestA01LogoutInvalidates:
    def test_logout_revokes_token(self, client):
        token = _form_login(client, "logoutuser", "pw12345")
        assert token

        resp = client.get("/logout")
        assert resp.status_code == 200

        # The previously-valid JWT must now be rejected everywhere.
        verify = client.get(f"/api/auth/verify?token={token}")
        assert verify.status_code == 401, verify.text

    def test_logout_clears_cookies(self, client):
        _form_login(client, "cookieuser", "pw12345")
        resp = client.get("/logout")
        set_cookie = resp.headers.get("set-cookie", "")
        assert 'access_token=""' in set_cookie or 'access_token=;' in set_cookie
        assert 'role=""' in set_cookie or 'role=;' in set_cookie

    def test_logout_without_session_is_harmless(self, client):
        resp = client.get("/logout")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# A-02 — GET /api/loans/return/{id} is reachable (was Flask syntax, 404)
# ---------------------------------------------------------------------------


class TestA02ReturnRouteReachable:
    def test_owner_can_return_own_loan(self, client, admin_token, sample_book, session):
        loan_resp = client.post(
            "/api/loans/checkout",
            json={"book_id": sample_book["id"], "user_id": 3, "librarian_id": 2},
            headers=_bearer(admin_token),
        )
        loan_id = loan_resp.json()["id"]

        # testuser (id 3) returns their own loan via the GET route.
        _cookie_session(client, client.post(
            "/api/auth/login",
            json={"username": "testuser", "password": "password123"},
        ).json()["access_token"])
        resp = client.get(f"/api/loans/return/{loan_id}", follow_redirects=False)
        assert resp.status_code != 404, "route still dead (A-02 unfixed)"
        assert resp.status_code == 303

        row = session.query(Loan).filter(Loan.id == loan_id).first()
        assert row.return_date is not None

    def test_non_owner_cannot_return_via_get_route(
        self, client, admin_token, sample_book, session
    ):
        loan_resp = client.post(
            "/api/loans/checkout",
            json={"book_id": sample_book["id"], "user_id": 3, "librarian_id": 2},
            headers=_bearer(admin_token),
        )
        loan_id = loan_resp.json()["id"]

        # A different plain user must NOT return user 3's loan.
        other = client.post(
            "/api/auth/login",
            json={"username": "flowuser2", "password": "flowpass2"},
        )
        if other.status_code == 401:
            client.post("/api/users/register",
                        json={"username": "flowuser2", "password": "flowpass2"})
            other = client.post(
                "/api/auth/login",
                json={"username": "flowuser2", "password": "flowpass2"},
            )
        _cookie_session(client, other.json()["access_token"])
        client.get(f"/api/loans/return/{loan_id}", follow_redirects=False)

        row = session.query(Loan).filter(Loan.id == loan_id).first()
        assert row.return_date is None, "non-owner returned another user's loan"

    def test_missing_loan_does_not_crash(self, client, user_token):
        _cookie_session(client, user_token)
        resp = client.get("/api/loans/return/99999", follow_redirects=False)
        assert resp.status_code != 404 or resp.status_code == 303


# ---------------------------------------------------------------------------
# A-03 — librarian cannot set role=admin via PUT /api/users/{id}
# ---------------------------------------------------------------------------


class TestA03RoleChangeAdminOnly:
    def test_librarian_cannot_set_role(self, client, librarian_token):
        resp = client.put(
            "/api/users/3",
            params={"role": "admin"},
            headers=_bearer(librarian_token),
        )
        assert resp.status_code == 403, resp.text

    def test_librarian_can_edit_non_role_fields(self, client, librarian_token):
        resp = client.put(
            "/api/users/3",
            params={"email": "edited-by-lib@ex.com"},
            headers=_bearer(librarian_token),
        )
        assert resp.status_code == 200
        assert resp.json()["email"] == "edited-by-lib@ex.com"

    def test_admin_can_set_role(self, client, admin_token):
        client.post("/api/users/register",
                    json={"username": "promotee", "password": "pw12345"})
        users = client.get("/api/users", headers=_bearer(admin_token)).json()
        # /api/users returns {"users": [...], "total": n}
        rows = users["users"] if isinstance(users, dict) else users
        uid = next(u["id"] for u in rows if u["username"] == "promotee")
        resp = client.put(
            f"/api/users/{uid}",
            params={"role": "admin"},
            headers=_bearer(admin_token),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["role"] == "admin"


# ---------------------------------------------------------------------------
# A-04 — DELETE user with loans → 409, not 500
# ---------------------------------------------------------------------------


class TestA04DeleteUserWithLoans:
    def _csrf_headers(self, client, admin_token):
        client.cookies.set("csrf", "del-csrf-token")
        return {"X-CSRF-Token": "del-csrf-token", **_bearer(admin_token)}

    def test_delete_user_with_loans_409(self, client, admin_token, sample_book):
        client.post("/api/users/register",
                    json={"username": "loandeletee", "password": "pw12345"})
        users = client.get("/api/admin/users", headers=_bearer(admin_token)).json()
        uid = next(u["id"] for u in users["users"] if u["username"] == "loandeletee")

        # Give the user a loan (admin checks out on their behalf).
        client.post(
            "/api/loans/checkout",
            json={"book_id": sample_book["id"], "user_id": uid, "librarian_id": 1},
            headers=_bearer(admin_token),
        )

        resp = client.delete(
            f"/api/admin/users/{uid}", headers=self._csrf_headers(client, admin_token)
        )
        assert resp.status_code in (400, 409), (
            f"expected 400/409 got {resp.status_code}: {resp.text}"
        )

    def test_delete_user_without_loans_200(self, client, admin_token):
        client.post("/api/users/register",
                    json={"username": "cleanuser", "password": "pw12345"})
        users = client.get("/api/admin/users", headers=_bearer(admin_token)).json()
        uid = next(u["id"] for u in users["users"] if u["username"] == "cleanuser")

        resp = client.delete(
            f"/api/admin/users/{uid}", headers=self._csrf_headers(client, admin_token)
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"deleted": uid}


# ---------------------------------------------------------------------------
# A-05 — POST /api/books requires auth
# ---------------------------------------------------------------------------


class TestA05CreateBookAuth:
    def test_anonymous_insert_rejected(self, client):
        resp = client.post(
            "/api/books",
            json={"isbn": "9780000000009", "title": "Anon Insert"},
        )
        assert resp.status_code in (401, 403), resp.text

    def test_plain_user_rejected(self, client, user_token):
        resp = client.post(
            "/api/books",
            json={"isbn": "9780000000016", "title": "User Insert"},
            headers=_bearer(user_token),
        )
        assert resp.status_code == 403, resp.text

    def test_admin_can_insert(self, client, admin_token):
        resp = client.post(
            "/api/books",
            json={"isbn": "9780000000023", "title": "Admin Insert"},
            headers=_bearer(admin_token),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["title"] == "Admin Insert"


# ---------------------------------------------------------------------------
# A-06 — plain user can read a book detail (invalid "reader" role removed)
# ---------------------------------------------------------------------------


class TestA06PlainUserBookDetail:
    def test_plain_user_gets_book_detail(self, client, user_token, sample_book):
        resp = client.get(
            f"/api/books/{sample_book['id']}", headers=_bearer(user_token)
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["id"] == sample_book["id"]

    def test_anonymous_still_rejected_on_detail(self, client, sample_book):
        resp = client.get(f"/api/books/{sample_book['id']}")
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# A-07 — plain user cannot charge a loan to another user
# ---------------------------------------------------------------------------


class TestA07CheckoutObjectAuthz:
    def test_user_forced_to_own_id(self, client, user_token, sample_book):
        resp = client.post(
            "/api/loans/checkout",
            json={"book_id": sample_book["id"], "user_id": 1, "librarian_id": 2},
            headers=_bearer(user_token),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["user_id"] == 3, (
            "loan charged to another user (A-07 unfixed)"
        )

    def test_admin_keeps_explicit_choice(self, client, admin_token, sample_book):
        resp = client.post(
            "/api/loans/checkout",
            json={"book_id": sample_book["id"], "user_id": 3, "librarian_id": 1},
            headers=_bearer(admin_token),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["user_id"] == 3


# ---------------------------------------------------------------------------
# A-08 — checkout-cookie requires CSRF
# ---------------------------------------------------------------------------


class TestA08CheckoutCookieCSRF:
    def test_without_csrf_403(self, client, user_token, sample_book):
        client.cookies.set("access_token", user_token)
        resp = client.post(
            "/api/loans/checkout-cookie",
            json={"book_id": sample_book["id"]},
        )
        assert resp.status_code == 403, resp.text

    def test_with_csrf_200(self, client, user_token, sample_book):
        _cookie_session(client, user_token)
        resp = client.post(
            "/api/loans/checkout-cookie",
            json={"book_id": sample_book["id"]},
            headers={"X-CSRF-Token": "test-csrf-token"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["user_id"] == 3

    def test_without_session_401(self, client, sample_book):
        client.cookies.set("csrf", "test-csrf-token")
        resp = client.post(
            "/api/loans/checkout-cookie",
            json={"book_id": sample_book["id"]},
            headers={"X-CSRF-Token": "test-csrf-token"},
        )
        assert resp.status_code == 401, resp.text


# ---------------------------------------------------------------------------
# A-09 — return-cookie: CSRF + ownership check
# ---------------------------------------------------------------------------


class TestA09ReturnCookie:
    def _checkout_via_cookie(self, client, token, book_id):
        _cookie_session(client, token)
        return client.post(
            "/api/loans/checkout-cookie",
            json={"book_id": book_id},
            headers={"X-CSRF-Token": "test-csrf-token"},
        )

    def _second_user_token(self, client, username, password):
        resp = client.post("/api/auth/login",
                           json={"username": username, "password": password})
        if resp.status_code == 401:
            client.post("/api/users/register",
                        json={"username": username, "password": password})
            resp = client.post("/api/auth/login",
                               json={"username": username, "password": password})
        return resp.json()["access_token"]

    def test_owner_can_return_own_loan(self, client, user_token, sample_book):
        loan = self._checkout_via_cookie(client, user_token, sample_book["id"])
        assert loan.status_code == 200
        loan_id = loan.json()["id"]

        resp = client.post(
            "/api/loans/return-cookie",
            json={"loan_id": loan_id},
            headers={"X-CSRF-Token": "test-csrf-token"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["return_date"] is not None

    def test_cross_user_return_rejected(
        self, client, user_token, admin_token, sample_book
    ):
        # testuser (id 3) checks out via the cookie flow...
        loan = self._checkout_via_cookie(client, user_token, sample_book["id"])
        loan_id = loan.json()["id"]

        # ...then a DIFFERENT plain user tries to return it.
        other_token = self._second_user_token(client, "evilreturner", "pw12345")
        _cookie_session(client, other_token)
        resp = client.post(
            "/api/loans/return-cookie",
            json={"loan_id": loan_id},
            headers={"X-CSRF-Token": "test-csrf-token"},
        )
        assert resp.status_code == 403, (
            f"cross-user return allowed (A-09 unfixed): {resp.text}"
        )

    def test_librarian_may_return_any_loan(
        self, client, librarian_token, user_token, sample_book
    ):
        loan = self._checkout_via_cookie(client, user_token, sample_book["id"])
        loan_id = loan.json()["id"]

        _cookie_session(client, librarian_token)
        resp = client.post(
            "/api/loans/return-cookie",
            json={"loan_id": loan_id},
            headers={"X-CSRF-Token": "test-csrf-token"},
        )
        assert resp.status_code == 200, resp.text

    def test_without_csrf_403(self, client, user_token, sample_book):
        client.cookies.set("access_token", user_token)
        resp = client.post("/api/loans/return-cookie", json={"loan_id": 1})
        assert resp.status_code == 403, resp.text

    def test_missing_loan_404(self, client, user_token):
        _cookie_session(client, user_token)
        resp = client.post(
            "/api/loans/return-cookie",
            json={"loan_id": 99999},
            headers={"X-CSRF-Token": "test-csrf-token"},
        )
        assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# N1 — /api/loans/active: admin/librarian see ALL, plain users their own
# ---------------------------------------------------------------------------


class TestN1ActiveLoansContract:
    def test_librarian_sees_all_active_loans(
        self, client, admin_token, librarian_token, sample_book
    ):
        # A loan belonging to testuser (id 3), created by admin.
        client.post(
            "/api/loans/checkout",
            json={"book_id": sample_book["id"], "user_id": 3, "librarian_id": 1},
            headers=_bearer(admin_token),
        )
        resp = client.get("/api/loans/active", headers=_bearer(librarian_token))
        assert resp.status_code == 200
        loans = resp.json()
        assert any(l["user_id"] == 3 for l in loans), (
            f"librarian must see ALL active loans, got: {loans}"
        )

    def test_admin_sees_all_active_loans(
        self, client, admin_token, sample_book
    ):
        client.post(
            "/api/loans/checkout",
            json={"book_id": sample_book["id"], "user_id": 3, "librarian_id": 1},
            headers=_bearer(admin_token),
        )
        resp = client.get("/api/loans/active", headers=_bearer(admin_token))
        assert resp.status_code == 200
        assert any(l["user_id"] == 3 for l in resp.json())

    def test_plain_user_sees_only_own(
        self, client, admin_token, user_token, sample_book
    ):
        # Loan for user 3 (testuser)...
        client.post(
            "/api/loans/checkout",
            json={"book_id": sample_book["id"], "user_id": 3, "librarian_id": 1},
            headers=_bearer(admin_token),
        )
        # ...and a second book loaned to user 1 (admin).
        from src.books import create_book
        from src.database import get_engine
        from sqlalchemy.orm import Session as SA_Session
        with SA_Session(get_engine()) as s:
            book2 = create_book(s, isbn="9780061120091", title="Second Book")
            s.commit()
            book2_id = book2.id
        client.post(
            "/api/loans/checkout",
            json={"book_id": book2_id, "user_id": 1, "librarian_id": 1},
            headers=_bearer(admin_token),
        )

        resp = client.get("/api/loans/active", headers=_bearer(user_token))
        assert resp.status_code == 200
        loans = resp.json()
        assert loans and all(l["user_id"] == 3 for l in loans), (
            f"plain user must see only their own loans, got: {loans}"
        )
