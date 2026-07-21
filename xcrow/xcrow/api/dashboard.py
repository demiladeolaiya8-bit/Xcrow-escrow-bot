"""Web admin dashboard — served at /panel."""
from __future__ import annotations
import os
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from config import settings
from database.crud import (
    get_all_deals, get_all_users, get_all_transactions,
    get_all_disputes, get_open_disputes, get_audit_logs,
    get_all_tickets, get_ticket, resolve_ticket,
    count_deals_by_status, count_all_deals, count_users,
    get_total_volume, get_total_fees_earned,
    get_all_settings, set_setting, create_audit_log,
    count_open_tickets, get_deal_by_uid, update_deal,
    get_user, set_user_banned,
)
from database.models import DealStatus, CRYPTO_SYMBOLS

_HERE = os.path.dirname(__file__)
templates = Jinja2Templates(directory=os.path.join(_HERE, "templates"))

dashboard_router = APIRouter()


# ── Auth helpers ────────────────────────────────────────────────────────────

def _is_logged_in(request: Request) -> bool:
    return request.session.get("admin_logged_in") is True


def _require_login(request: Request):
    if not _is_logged_in(request):
        return RedirectResponse("/panel/login", status_code=302)
    return None


def _ctx(request: Request, **extra) -> dict[str, Any]:
    return {"request": request, "now": datetime.utcnow(), **extra}


# ── Login / Logout ──────────────────────────────────────────────────────────

@dashboard_router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if _is_logged_in(request):
        return RedirectResponse("/panel/", status_code=302)
    return templates.TemplateResponse("login.html", _ctx(request, error=None))


@dashboard_router.post("/login", response_class=HTMLResponse)
async def login_post(request: Request, password: str = Form(...)):
    if password == settings.ADMIN_DASHBOARD_PASSWORD:
        request.session["admin_logged_in"] = True
        await create_audit_log("admin", "login", detail="Web dashboard login")
        return RedirectResponse("/panel/", status_code=302)
    return templates.TemplateResponse("login.html", _ctx(request, error="Invalid password"))


@dashboard_router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/panel/login", status_code=302)


# ── Dashboard ─────────────────────────────────────────────────────────────

@dashboard_router.get("/", response_class=HTMLResponse)
async def dashboard_home(request: Request):
    redir = _require_login(request)
    if redir:
        return redir

    total     = await count_all_deals()
    active    = await count_deals_by_status("funded") + await count_deals_by_status("in_delivery")
    completed = await count_deals_by_status("completed")
    cancelled = await count_deals_by_status("cancelled")
    disputed  = await count_deals_by_status("disputed")
    pending   = await count_deals_by_status("step5_pending") + await count_deals_by_status("awaiting_payment")
    releasing = await count_deals_by_status("releasing")
    volume    = await get_total_volume()
    revenue   = await get_total_fees_earned()
    users     = await count_users()
    tickets   = await count_open_tickets()
    recent    = await get_all_deals(limit=8)

    return templates.TemplateResponse("dashboard.html", _ctx(
        request,
        total=total, active=active, completed=completed, cancelled=cancelled,
        disputed=disputed, pending=pending, releasing=releasing,
        volume=volume, revenue=revenue, users=users, tickets=tickets,
        recent=recent, CRYPTO_SYMBOLS=CRYPTO_SYMBOLS,
    ))


# ── Deals ────────────────────────────────────────────────────────────────────

@dashboard_router.get("/deals", response_class=HTMLResponse)
async def deals_page(request: Request, offset: int = 0):
    redir = _require_login(request)
    if redir:
        return redir
    deals = await get_all_deals(limit=25, offset=offset)
    total = await count_all_deals()
    return templates.TemplateResponse("deals.html", _ctx(
        request, deals=deals, offset=offset, total=total,
        CRYPTO_SYMBOLS=CRYPTO_SYMBOLS, DealStatus=DealStatus,
    ))


@dashboard_router.get("/deals/{uid}", response_class=HTMLResponse)
async def deal_detail(request: Request, uid: str):
    redir = _require_login(request)
    if redir:
        return redir
    deal = await get_deal_by_uid(uid.upper())
    if not deal:
        return HTMLResponse("<h2>Deal not found</h2>", status_code=404)
    return templates.TemplateResponse("deal_detail.html", _ctx(
        request, deal=deal, CRYPTO_SYMBOLS=CRYPTO_SYMBOLS, DealStatus=DealStatus,
    ))


@dashboard_router.post("/deals/{uid}/action")
async def deal_action(request: Request, uid: str, action: str = Form(...)):
    redir = _require_login(request)
    if redir:
        return redir
    deal = await get_deal_by_uid(uid.upper())
    if not deal:
        return RedirectResponse("/panel/deals", status_code=302)

    status_map = {
        "release": DealStatus.COMPLETED,
        "refund":  DealStatus.REFUNDED,
        "cancel":  DealStatus.CANCELLED,
    }
    new_status = status_map.get(action)
    if new_status:
        kwargs = {"status": new_status}
        if action == "release":
            kwargs["released_at"] = datetime.utcnow()
        await update_deal(deal.id, **kwargs)
        await create_audit_log("admin_web", f"deal_{action}", target=uid,
                               detail=f"Status → {new_status}")
    return RedirectResponse(f"/panel/deals/{uid}", status_code=302)


# ── Users ─────────────────────────────────────────────────────────────────

@dashboard_router.get("/users", response_class=HTMLResponse)
async def users_page(request: Request, offset: int = 0):
    redir = _require_login(request)
    if redir:
        return redir
    users = await get_all_users(limit=25, offset=offset)
    total = await count_users()
    return templates.TemplateResponse("users.html", _ctx(
        request, users=users, offset=offset, total=total,
    ))


@dashboard_router.post("/users/{user_id}/ban")
async def ban_user(request: Request, user_id: int, banned: str = Form(...)):
    redir = _require_login(request)
    if redir:
        return redir
    is_banned = banned == "1"
    await set_user_banned(user_id, is_banned)
    await create_audit_log("admin_web", "ban_user" if is_banned else "unban_user",
                           target=str(user_id))
    return RedirectResponse("/panel/users", status_code=302)


# ── Payments ──────────────────────────────────────────────────────────────

@dashboard_router.get("/payments", response_class=HTMLResponse)
async def payments_page(request: Request, offset: int = 0):
    redir = _require_login(request)
    if redir:
        return redir
    txs = await get_all_transactions(limit=25, offset=offset)
    return templates.TemplateResponse("payments.html", _ctx(
        request, txs=txs, offset=offset,
    ))


# ── Disputes ──────────────────────────────────────────────────────────────

@dashboard_router.get("/disputes", response_class=HTMLResponse)
async def disputes_page(request: Request, offset: int = 0):
    redir = _require_login(request)
    if redir:
        return redir
    disputes = await get_all_disputes(limit=25, offset=offset)
    return templates.TemplateResponse("disputes.html", _ctx(
        request, disputes=disputes, offset=offset, CRYPTO_SYMBOLS=CRYPTO_SYMBOLS,
    ))


# ── Settings ──────────────────────────────────────────────────────────────

@dashboard_router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    redir = _require_login(request)
    if redir:
        return redir
    all_settings = await get_all_settings()
    return templates.TemplateResponse("settings.html", _ctx(
        request, settings_map=all_settings, saved=False,
    ))


@dashboard_router.post("/settings", response_class=HTMLResponse)
async def settings_save(request: Request):
    redir = _require_login(request)
    if redir:
        return redir
    form = await request.form()
    editable_keys = [
        "fee_percent", "owner_wallet_address", "owner_wallet_network",
        "main_wallet_bsc_eth", "main_wallet_btc",
        "min_escrow_amount", "max_escrow_amount", "required_confirmations",
        "supported_networks",
    ]
    for key in editable_keys:
        val = form.get(key)
        if val is not None:
            await set_setting(key, str(val).strip())
    await create_audit_log("admin_web", "update_settings")
    all_settings = await get_all_settings()
    return templates.TemplateResponse("settings.html", _ctx(
        request, settings_map=all_settings, saved=True,
    ))


# ── Audit logs ────────────────────────────────────────────────────────────

@dashboard_router.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request, offset: int = 0):
    redir = _require_login(request)
    if redir:
        return redir
    logs = await get_audit_logs(limit=50, offset=offset)
    return templates.TemplateResponse("logs.html", _ctx(
        request, logs=logs, offset=offset,
    ))


# ── Support tickets ───────────────────────────────────────────────────────

@dashboard_router.get("/tickets", response_class=HTMLResponse)
async def tickets_page(request: Request, offset: int = 0):
    redir = _require_login(request)
    if redir:
        return redir
    tickets = await get_all_tickets(limit=25, offset=offset)
    return templates.TemplateResponse("tickets.html", _ctx(
        request, tickets=tickets, offset=offset,
    ))


@dashboard_router.post("/tickets/{ticket_id}/resolve")
async def resolve_ticket_web(request: Request, ticket_id: int, reply: str = Form(...)):
    redir = _require_login(request)
    if redir:
        return redir
    await resolve_ticket(ticket_id, reply)
    await create_audit_log("admin_web", "resolve_ticket", target=str(ticket_id))
    return RedirectResponse("/panel/tickets", status_code=302)
