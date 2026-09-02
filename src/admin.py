"""Admin routes for Bibliotek (mounted LIVE by MC 743.1, S1).

Six /admin routes, CSRF-hardened per MC 361.14 / 361.16: every admin POST
mutator verifies a double-submit CSRF token before touching the DB, and every
admin GET sets a per-session ``csrf_token`` cookie (exposed to templates via
``_template_context``).

Role gate (I2): anonymous -> 302 /login; logged-in non-admin -> 403.

Staff accounts are created by ``register_user`` (forces role ``"user"``) then
promoted with ``update_user``; a public self-register can never mint staff.

The CSRF primitives live in ``src.app`` and are reused here (one
implementation, no duplication). Double-submit pattern is stateless.

Routes:
    GET  /admin                     -> admin_dashboard.html
    GET  /admin/lantagare           -> admin_lantagare.html
    POST /admin/lantagare/{user_id} -> 303 -> /admin/lantagare   (C8, +CSRF)
    GET  /admin/personal            -> admin_personal.html
    POST /admin/personal            -> 303 -> /admin/personal   (C11, +CSRF)
    POST /admin/personal/{user_id}  -> 303 -> /admin/personal   (C12, +CSRF)
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

STAFF_ROLES: tuple[str, ...] = ("librarian", "admin", "staff")

from src.csrf import (
    generate_csrf_token,
    set_csrf_cookie,
    verify_csrf,
)


def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(url=path, status_code=303)


def _require_admin(request: Request) -> Optional[dict]:
    cu = getattr(request.state, "current_user", None)
    if cu is None:
        return None
    if cu.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    return cu


def _attach_csrf(response: Any, token: str) -> Any:
    # The cookie name must be "csrf" (the name the admin JS and the test
    # client read). Use the shared helper so naming is centralised in
    # src.csrf (MC 743.1, F4). set_csrf_cookie mutates in place and returns
    # None, so we must return *response* — not the helper's return value —
    # or the route returns None and FastAPI serialises an empty body (the
    # MC 743.4 "CSRF cookie not set" bug).
    set_csrf_cookie(response, token)
    return response


def _q(text: str) -> str:
    import urllib.parse
    return urllib.parse.quote(text)


def _req_csrf_error(request: Request, back_path: str) -> RedirectResponse:
    return _redirect(back_path + "?error=" + _q("Säkerhetstoken saknas eller är felaktig. Försök igen."))


def build_admin_router(
    templates: Jinja2Templates,
    template_context: Callable[[Request], dict[str, Any]],
    get_session_cm: Callable,
) -> APIRouter:
    from src.circulation import get_overdue_loans, get_user_loans
    from src.users import list_users, register_user, update_user

    router = APIRouter(tags=["admin"])

    @router.get("/admin", include_in_schema=False)
    async def admin_dashboard(request: Request) -> Any:
        # Issue the CSRF cookie BEFORE the admin check: the token is a
        # per-session random value whose *issuance* needs no auth — only its
        # *use* on a mutating endpoint does. This lets the admin JS (and the
        # test client) fetch a valid token from GET /admin without an admin
        # session cookie (the test holds the admin identity as a Bearer token).
        csrf_token = generate_csrf_token()
        cu = _require_admin(request)
        if cu is None:
            resp = RedirectResponse(url="/login", status_code=302)
            return _attach_csrf(resp, csrf_token)
        with get_session_cm() as db:
            user_count = len(list_users(db, role_filter="user"))
            overdue_count = len(get_overdue_loans(db))
        ctx = {**template_context(request), "csrf_token": csrf_token,
               "user_count": user_count, "overdue_count": overdue_count}
        resp = templates.TemplateResponse(request=request, name="admin_dashboard.html", context=ctx)
        return _attach_csrf(resp, csrf_token)

    @router.get("/admin/lantagare", include_in_schema=False)
    async def admin_lantagare(request: Request, error: Optional[str] = None, success: Optional[str] = None) -> Any:
        cu = _require_admin(request)
        if cu is None:
            return RedirectResponse(url="/login", status_code=302)
        with get_session_cm() as db:
            rows = []
            for u in list_users(db, role_filter="user"):
                active_loans = len(get_user_loans(db, u.id, active_only=True))
                rows.append({"id": u.id, "username": u.username, "email": u.email, "created_at": u.created_at, "active_loans": active_loans})
        token = generate_csrf_token()
        ctx = {**template_context(request), "csrf_token": token, "rows": rows}
        if error: ctx["error"] = error
        if success: ctx["success"] = success
        resp = templates.TemplateResponse(request=request, name="admin_lantagare.html", context=ctx)
        return _attach_csrf(resp, token)

    @router.get("/admin/personal", include_in_schema=False)
    async def admin_personal(request: Request, error: Optional[str] = None, success: Optional[str] = None) -> Any:
        cu = _require_admin(request)
        if cu is None:
            return RedirectResponse(url="/login", status_code=302)
        with get_session_cm() as db:
            staff = [{"id": u.id, "username": u.username, "email": u.email, "role": u.role, "created_at": u.created_at} for u in list_users(db) if u.role in STAFF_ROLES]
        token = generate_csrf_token()
        ctx = {**template_context(request), "csrf_token": token, "staff": staff}
        if error: ctx["error"] = error
        if success: ctx["success"] = success
        resp = templates.TemplateResponse(request=request, name="admin_personal.html", context=ctx)
        return _attach_csrf(resp, token)

    # ------------------------------------------------------------------
    # POST /admin/lantagare/{user_id} — edit borrower (C8, 303 -> GET, +CSRF)
    # ------------------------------------------------------------------
    @router.post("/admin/lantagare/{user_id}", include_in_schema=False)
    async def admin_lantagare_edit(request: Request, user_id: int) -> Any:
        cu = _require_admin(request)
        if cu is None:
            return RedirectResponse(url="/login", status_code=302)

        form = await request.form()
        if not await verify_csrf(request, form.get("csrf_token")):
            return _req_csrf_error(request, "/admin/lantagare")

        username = form.get("username", "").strip()
        email = form.get("email", "").strip() or None
        new_password = form.get("new_password", "") or None

        if username and len(username) < 3:
            return _redirect("/admin/lantagare?error=" + _q("Anv\u00e4ndarnamn m\u00e5ste vara minst 3 tecken"))

        try:
            with get_session_cm() as db:
                kwargs = {"email": email}
                if username:
                    kwargs["username"] = username
                update_user(db, user_id, **kwargs)
                if new_password:
                    update_user(db, user_id, password=new_password)
        except HTTPException as exc:
            return _redirect("/admin/lantagare?error=" + _q(str(exc.detail)))
        except Exception as exc:
            return _redirect("/admin/lantagare?error=" + _q(str(exc)))

        return _redirect("/admin/lantagare?success=" + _q("\u00c4ndringarna sparades"))

    # ------------------------------------------------------------------
    # POST /admin/personal — create staff (C11, 303 -> GET, +CSRF)
    # ------------------------------------------------------------------
    @router.post("/admin/personal", include_in_schema=False)
    async def admin_personal_create(request: Request) -> Any:
        cu = _require_admin(request)
        if cu is None:
            return RedirectResponse(url="/login", status_code=302)

        form = await request.form()
        if not await verify_csrf(request, form.get("csrf_token")):
            return _req_csrf_error(request, "/admin/personal")

        username = form.get("username", "").strip()
        password = form.get("password", "")
        email = form.get("email", "").strip() or None
        role = form.get("role", "").strip()

        if not username or not password:
            return _redirect("/admin/personal?error=" + _q("Anv\u00e4ndarnamn och l\u00f6senord kr\u00e4vs"))
        if role not in STAFF_ROLES:
            return _redirect("/admin/personal?error=" + _q("Ogiltig roll"))
        if len(password) < 8:
            return _redirect("/admin/personal?error=" + _q("L\u00f6senordet m\u00e5ste vara minst 8 tecken"))

        try:
            with get_session_cm() as db:
                new_user = register_user(db, username=username, password=password, email=email)
                update_user(db, new_user.id, role=role)
        except HTTPException as exc:
            return _redirect("/admin/personal?error=" + _q(str(exc.detail)))
        except Exception as exc:
            return _redirect("/admin/personal?error=" + _q(str(exc)))

        return _redirect("/admin/personal?success=" + _q("Personal skapades"))

    # ------------------------------------------------------------------
    # POST /admin/personal/self — self password reset (C12b, 303 -> GET, +CSRF)
    # ------------------------------------------------------------------
    @router.post("/admin/personal/self", include_in_schema=False)
    async def admin_personal_self(request: Request) -> Any:
        cu = _require_admin(request)
        if cu is None:
            return RedirectResponse(url="/login", status_code=302)

        form = await request.form()
        if not await verify_csrf(request, form.get("csrf_token")):
            return _req_csrf_error(request, "/admin/personal")

        new_password = form.get("password", "") or form.get("new_password", "")
        if len(new_password) < 8:
            return _redirect("/admin/personal?error=" + _q("L\u00f6senordet m\u00e5ste vara minst 8 tecken"))

        try:
            with get_session_cm() as db:
                update_user(db, cu["user_id"], password=new_password)
        except HTTPException as exc:
            return _redirect("/admin/personal?error=" + _q(str(exc.detail)))
        except Exception as exc:
            return _redirect("/admin/personal?error=" + _q(str(exc)))

        return _redirect("/admin/personal?success=" + _q("L\u00f6senordet byttes"))

    # ------------------------------------------------------------------
    # POST /admin/personal/{user_id} — edit staff (C12, 303 -> GET, +CSRF)
    # ------------------------------------------------------------------
    @router.post("/admin/personal/{user_id}", include_in_schema=False)
    async def admin_personal_edit(request: Request, user_id: int) -> Any:
        cu = _require_admin(request)
        if cu is None:
            return RedirectResponse(url="/login", status_code=302)

        form = await request.form()
        if not await verify_csrf(request, form.get("csrf_token")):
            return _req_csrf_error(request, "/admin/personal")

        email = form.get("email", "").strip() or None
        role = form.get("role", "").strip()
        new_password = form.get("new_password", "") or None

        if role not in STAFF_ROLES:
            return _redirect("/admin/personal?error=" + _q("Ogiltig roll"))

        try:
            with get_session_cm() as db:
                update_user(db, user_id, email=email, role=role)
                if new_password:
                    update_user(db, user_id, password=new_password)
        except HTTPException as exc:
            return _redirect("/admin/personal?error=" + _q(str(exc.detail)))
        except Exception as exc:
            return _redirect("/admin/personal?error=" + _q(str(exc)))

        return _redirect("/admin/personal?success=" + _q("\u00c4ndringarna sparades"))

    return router
