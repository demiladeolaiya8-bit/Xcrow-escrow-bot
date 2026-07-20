"""All database operations for Xcrow."""
from __future__ import annotations
from datetime import datetime
from typing import Optional, List
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database.models import Deal, DealStatus, Transaction, Dispute, User
from database.db import AsyncSessionLocal


# ── Session helper ─────────────────────────────────────────────────────────

async def _s() -> AsyncSession:  # type: ignore[return]
    """Return a fresh session via context manager."""
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
    from sqlalchemy import func as sqlfunc
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(sqlfunc.count(Deal.id)).where(Deal.status == status)
        )
        return result.scalar_one() or 0


async def get_next_wallet_index() -> int:
    """Get the next unique HD wallet derivation index."""
    from sqlalchemy import func as sqlfunc
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
