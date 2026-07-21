"""All database operations for Xcrow."""
from __future__ import annotations
from datetime import datetime
from typing import Optional, List, Dict
from sqlalchemy import select, update, func as sqlfunc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database.models import (
    Deal, DealStatus, Transaction, Dispute, User,
    PlatformSetting, AuditLog, SupportTicket,
)
from database.db import AsyncSessionLocal


# ── Session helper ─────────────────────────────────────────────────────────

async def _s() -> AsyncSession:  # type: ignore[return]
    return AsyncSessionLocal()


# ── Users ──────────────────────────────────────────────────────────────────

async def get_or_create_user(
    telegram_id: int,
    username: str | None = None,
    first_name: str | None = None,
) -> User:
    async with AsyncSessionLocal() as s:
        user = await s.get(User, telegram_id)
        if user is None:
            user = User(telegram_id=telegram_id, username=username, first_name=first_name)
            s.add(user)
        else:
            if username is not None:
                user.username = username
            if first_name is not None:
                user.first_name = first_name
            user.last_active = datetime.utcnow()
        await s.commit()
        await s.refresh(user)
        return user


async def get_user(telegram_id: int) -> User | None:
    async with AsyncSessionLocal() as s:
        return await s.get(User, telegram_id)


async def set_user_banned(telegram_id: int, banned: bool) -> None:
    async with AsyncSessionLocal() as s:
        await s.execute(
            update(User).where(User.telegram_id == telegram_id).values(is_banned=banned)
        )
        await s.commit()


async def set_user_admin(telegram_id: int, is_admin: bool) -> None:
    async with AsyncSessionLocal() as s:
        await s.execute(
            update(User).where(User.telegram_id == telegram_id).values(is_admin=is_admin)
        )
        await s.commit()


async def get_all_users(limit: int = 50, offset: int = 0) -> List[User]:
    async with AsyncSessionLocal() as s:
        result = await s.execute(select(User).order_by(User.created_at.desc()).limit(limit).offset(offset))
        return list(result.scalars().all())


async def count_users() -> int:
    async with AsyncSessionLocal() as s:
        result = await s.execute(select(sqlfunc.count(User.telegram_id)))
        return result.scalar_one() or 0


# ── Deals ──────────────────────────────────────────────────────────────────

async def create_deal(creator_id: int, deal_uid: str) -> Deal:
    async with AsyncSessionLocal() as s:
        deal = Deal(creator_id=creator_id, deal_uid=deal_uid, status=DealStatus.DRAFT)
        s.add(deal)
        await s.commit()
        await s.refresh(deal)
        return deal


async def get_deal_by_uid(uid: str) -> Deal | None:
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(Deal)
            .options(selectinload(Deal.buyer), selectinload(Deal.seller))
            .where(Deal.deal_uid == uid)
        )
        return result.scalar_one_or_none()


async def get_deal_by_group(group_id: int) -> Deal | None:
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(Deal)
            .options(selectinload(Deal.buyer), selectinload(Deal.seller))
            .where(Deal.group_id == group_id)
        )
        return result.scalar_one_or_none()


async def get_deal_by_id(deal_id: int) -> Deal | None:
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(Deal)
            .options(selectinload(Deal.buyer), selectinload(Deal.seller))
            .where(Deal.id == deal_id)
        )
        return result.scalar_one_or_none()


async def update_deal(deal_id: int, **kwargs) -> None:
    async with AsyncSessionLocal() as s:
        await s.execute(update(Deal).where(Deal.id == deal_id).values(**kwargs))
        await s.commit()


async def get_user_deals(user_id: int) -> List[Deal]:
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(Deal)
            .options(selectinload(Deal.buyer), selectinload(Deal.seller))
            .where((Deal.buyer_id == user_id) | (Deal.seller_id == user_id) | (Deal.creator_id == user_id))
            .order_by(Deal.created_at.desc())
            .limit(20)
        )
        return list(result.scalars().all())


async def get_active_deals_for_monitoring() -> List[Deal]:
    """Returns deals waiting for deposit confirmation."""
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(Deal).where(Deal.status.in_([
                DealStatus.STEP5_PENDING,
                DealStatus.AWAITING_PAYMENT,
            ]))
        )
        return list(result.scalars().all())


async def get_all_deals(limit: int = 50, offset: int = 0) -> List[Deal]:
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(Deal)
            .options(selectinload(Deal.buyer), selectinload(Deal.seller))
            .order_by(Deal.created_at.desc())
            .limit(limit).offset(offset)
        )
        return list(result.scalars().all())


async def count_deals_by_status(status: str) -> int:
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(sqlfunc.count(Deal.id)).where(Deal.status == status)
        )
        return result.scalar_one() or 0


async def count_all_deals() -> int:
    async with AsyncSessionLocal() as s:
        result = await s.execute(select(sqlfunc.count(Deal.id)))
        return result.scalar_one() or 0


async def get_total_volume() -> float:
    """Total amount across completed deals (what sellers received)."""
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(sqlfunc.coalesce(sqlfunc.sum(Deal.amount), 0))
            .where(Deal.status == DealStatus.COMPLETED)
        )
        return float(result.scalar_one() or 0)


async def get_total_fees_earned() -> float:
    """Total platform fees from completed deals."""
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(sqlfunc.coalesce(sqlfunc.sum(Deal.fee_amount), 0))
            .where(Deal.status == DealStatus.COMPLETED)
        )
        return float(result.scalar_one() or 0)


async def get_next_wallet_index() -> int:
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(sqlfunc.count(Deal.id)).where(Deal.wallet_index.isnot(None))
        )
        return (result.scalar_one() or 0)


# ── Transactions ───────────────────────────────────────────────────────────

async def create_transaction(
    deal_id: int, tx_hash: str, amount: float, crypto: str,
    from_addr: str | None = None, confirmed: bool = True,
) -> Transaction:
    async with AsyncSessionLocal() as s:
        tx = Transaction(
            deal_id=deal_id, tx_hash=tx_hash, amount=amount,
            crypto=crypto, from_addr=from_addr, confirmed=confirmed,
        )
        s.add(tx)
        await s.commit()
        await s.refresh(tx)
        return tx


async def tx_hash_exists(tx_hash: str) -> bool:
    async with AsyncSessionLocal() as s:
        result = await s.execute(select(Transaction).where(Transaction.tx_hash == tx_hash))
        return result.scalar_one_or_none() is not None


async def get_all_transactions(limit: int = 50, offset: int = 0) -> List[Transaction]:
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(Transaction)
            .options(selectinload(Transaction.deal))
            .order_by(Transaction.created_at.desc())
            .limit(limit).offset(offset)
        )
        return list(result.scalars().all())


# ── Disputes ───────────────────────────────────────────────────────────────

async def create_dispute(deal_id: int, opened_by: int, reason: str) -> Dispute:
    async with AsyncSessionLocal() as s:
        d = Dispute(deal_id=deal_id, opened_by=opened_by, reason=reason)
        s.add(d)
        await s.commit()
        await s.refresh(d)
        return d


async def resolve_dispute(
    dispute_id: int, admin_id: int, resolution: str, status: str = "resolved"
) -> None:
    async with AsyncSessionLocal() as s:
        await s.execute(
            update(Dispute)
            .where(Dispute.id == dispute_id)
            .values(admin_id=admin_id, resolution=resolution, status=status,
                    resolved_at=datetime.utcnow())
        )
        await s.commit()


async def get_open_disputes() -> List[Dispute]:
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(Dispute)
            .options(selectinload(Dispute.deal))
            .where(Dispute.status == "open")
            .order_by(Dispute.created_at.asc())
        )
        return list(result.scalars().all())


async def get_all_disputes(limit: int = 50, offset: int = 0) -> List[Dispute]:
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(Dispute)
            .options(selectinload(Dispute.deal))
            .order_by(Dispute.created_at.desc())
            .limit(limit).offset(offset)
        )
        return list(result.scalars().all())


# ── Platform settings ──────────────────────────────────────────────────────

_DEFAULT_SETTINGS: list[tuple[str, str, str]] = [
    ("fee_percent",           "1.0",                   "Platform fee percentage added on top of deal amount"),
    ("owner_wallet_address",  "",                      "Wallet address where platform fees are sent"),
    ("owner_wallet_network",  "USDT_BEP20",            "Network for the owner fee wallet"),
    ("min_escrow_amount",     "1.0",                   "Minimum deal amount in USDT equivalent"),
    ("max_escrow_amount",     "100000.0",              "Maximum deal amount in USDT equivalent"),
    ("required_confirmations","1",                     "On-chain confirmations required to mark payment confirmed"),
    ("supported_networks",    "USDT_TRC20,USDT_BEP20,ETH,BTC,SOL,TON,LTC", "Comma-separated list of enabled networks"),
]


async def seed_default_settings() -> None:
    """Idempotently insert default settings rows (only if key doesn't exist yet)."""
    async with AsyncSessionLocal() as s:
        for key, value, description in _DEFAULT_SETTINGS:
            existing = await s.get(PlatformSetting, key)
            if existing is None:
                s.add(PlatformSetting(key=key, value=value, description=description))
        await s.commit()


async def get_setting(key: str, default: str = "") -> str:
    async with AsyncSessionLocal() as s:
        row = await s.get(PlatformSetting, key)
        return row.value if row else default


async def set_setting(key: str, value: str) -> None:
    async with AsyncSessionLocal() as s:
        row = await s.get(PlatformSetting, key)
        if row:
            row.value = value
            row.updated_at = datetime.utcnow()
        else:
            s.add(PlatformSetting(key=key, value=value))
        await s.commit()


async def get_all_settings() -> Dict[str, PlatformSetting]:
    async with AsyncSessionLocal() as s:
        result = await s.execute(select(PlatformSetting).order_by(PlatformSetting.key))
        rows = result.scalars().all()
        return {r.key: r for r in rows}


async def get_fee_percent() -> float:
    val = await get_setting("fee_percent", "1.0")
    try:
        return float(val)
    except (ValueError, TypeError):
        return 1.0


async def fee_breakdown(amount: float) -> tuple[float, float]:
    """Async version — reads live fee % from DB. Returns (fee_amount, total_buyer_pays)."""
    pct = await get_fee_percent()
    fee = round(amount * pct / 100, 6)
    return fee, round(amount + fee, 6)


# ── Audit log ──────────────────────────────────────────────────────────────

async def create_audit_log(
    actor: str, action: str,
    target: str | None = None,
    detail: str | None = None,
) -> None:
    async with AsyncSessionLocal() as s:
        s.add(AuditLog(actor=actor, action=action, target=target, detail=detail))
        await s.commit()


async def get_audit_logs(limit: int = 100, offset: int = 0) -> List[AuditLog]:
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit).offset(offset)
        )
        return list(result.scalars().all())


# ── Support tickets ────────────────────────────────────────────────────────

async def create_ticket(
    telegram_id: int | None, username: str | None,
    subject: str, message: str,
) -> SupportTicket:
    async with AsyncSessionLocal() as s:
        t = SupportTicket(
            telegram_id=telegram_id, username=username,
            subject=subject, message=message,
        )
        s.add(t)
        await s.commit()
        await s.refresh(t)
        return t


async def get_all_tickets(limit: int = 50, offset: int = 0) -> List[SupportTicket]:
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(SupportTicket).order_by(SupportTicket.created_at.desc()).limit(limit).offset(offset)
        )
        return list(result.scalars().all())


async def get_ticket(ticket_id: int) -> SupportTicket | None:
    async with AsyncSessionLocal() as s:
        return await s.get(SupportTicket, ticket_id)


async def resolve_ticket(ticket_id: int, reply: str) -> None:
    async with AsyncSessionLocal() as s:
        await s.execute(
            update(SupportTicket)
            .where(SupportTicket.id == ticket_id)
            .values(status="resolved", reply=reply, resolved_at=datetime.utcnow())
        )
        await s.commit()


async def count_open_tickets() -> int:
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(sqlfunc.count(SupportTicket.id)).where(SupportTicket.status == "open")
        )
        return result.scalar_one() or 0
