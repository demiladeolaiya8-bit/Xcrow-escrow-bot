"""SQLAlchemy ORM models for Xcrow."""
from __future__ import annotations
from datetime import datetime
from enum import Enum as PyEnum
from sqlalchemy import (
    BigInteger, Boolean, DateTime, Float, ForeignKey,
    Integer, String, Text, func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ── Deal status enum ───────────────────────────────────────────────────────
class DealStatus(str, PyEnum):
    DRAFT             = "draft"
    STEP1_PENDING     = "step1_pending"
    STEP2_PENDING     = "step2_pending"
    STEP3_PENDING     = "step3_pending"
    STEP4_PENDING     = "step4_pending"
    STEP5_PENDING     = "step5_pending"
    AWAITING_PAYMENT  = "awaiting_payment"
    FUNDED            = "funded"
    IN_DELIVERY       = "in_delivery"
    BUYER_CONFIRMING  = "buyer_confirming"
    RELEASING         = "releasing"
    COMPLETED         = "completed"
    DISPUTED          = "disputed"
    REFUNDED          = "refunded"
    CANCELLED         = "cancelled"


# ── Crypto enum ────────────────────────────────────────────────────────────
class CryptoNetwork(str, PyEnum):
    USDT_TRC20 = "USDT_TRC20"
    USDT_BEP20 = "USDT_BEP20"
    ETH        = "ETH"
    BTC        = "BTC"
    SOL        = "SOL"
    TON        = "TON"
    LTC        = "LTC"


CRYPTO_LABELS = {
    CryptoNetwork.USDT_TRC20: "USDT (TRC20 · Tron)",
    CryptoNetwork.USDT_BEP20: "USDT (BEP20 · BSC)",
    CryptoNetwork.ETH:        "ETH (Ethereum)",
    CryptoNetwork.BTC:        "BTC (Bitcoin)",
    CryptoNetwork.SOL:        "SOL (Solana)",
    CryptoNetwork.TON:        "TON",
    CryptoNetwork.LTC:        "LTC (Litecoin)",
}

CRYPTO_SYMBOLS = {
    CryptoNetwork.USDT_TRC20: "USDT",
    CryptoNetwork.USDT_BEP20: "USDT",
    CryptoNetwork.ETH:        "ETH",
    CryptoNetwork.BTC:        "BTC",
    CryptoNetwork.SOL:        "SOL",
    CryptoNetwork.TON:        "TON",
    CryptoNetwork.LTC:        "LTC",
}

# Networks we can auto-monitor (others require admin manual confirmation)
AUTO_MONITOR_NETWORKS = {CryptoNetwork.USDT_TRC20, CryptoNetwork.USDT_BEP20, CryptoNetwork.ETH}


# ── Tables ─────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    telegram_id:  Mapped[int]           = mapped_column(BigInteger, primary_key=True)
    username:     Mapped[str | None]    = mapped_column(String(64))
    first_name:   Mapped[str | None]    = mapped_column(String(128))
    is_banned:    Mapped[bool]          = mapped_column(Boolean, default=False)
    is_admin:     Mapped[bool]          = mapped_column(Boolean, default=False)
    created_at:   Mapped[datetime]      = mapped_column(DateTime, server_default=func.now())
    last_active:  Mapped[datetime]      = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    notes:        Mapped[str | None]    = mapped_column(Text)

    deals_as_buyer:  Mapped[list["Deal"]] = relationship("Deal", foreign_keys="Deal.buyer_id",  back_populates="buyer")
    deals_as_seller: Mapped[list["Deal"]] = relationship("Deal", foreign_keys="Deal.seller_id", back_populates="seller")

    @property
    def display_name(self) -> str:
        if self.username:
            return f"@{self.username}"
        return self.first_name or str(self.telegram_id)


class Deal(Base):
    __tablename__ = "deals"

    id:              Mapped[int]         = mapped_column(Integer, primary_key=True, autoincrement=True)
    deal_uid:        Mapped[str]         = mapped_column(String(12), unique=True, nullable=False)
    group_id:        Mapped[int | None]  = mapped_column(BigInteger, unique=True)
    creator_id:      Mapped[int]         = mapped_column(BigInteger, nullable=False)

    buyer_id:        Mapped[int | None]  = mapped_column(BigInteger, ForeignKey("users.telegram_id"))
    seller_id:       Mapped[int | None]  = mapped_column(BigInteger, ForeignKey("users.telegram_id"))

    buyer:           Mapped["User | None"] = relationship("User", foreign_keys=[buyer_id],  back_populates="deals_as_buyer")
    seller:          Mapped["User | None"] = relationship("User", foreign_keys=[seller_id], back_populates="deals_as_seller")

    # Deal details
    title:           Mapped[str | None]  = mapped_column(String(512))
    amount:          Mapped[float | None] = mapped_column(Float)
    crypto:          Mapped[str | None]  = mapped_column(String(32))
    fee_percent:     Mapped[float]       = mapped_column(Float, default=1.0)   # snapshot at deal creation
    fee_amount:      Mapped[float]       = mapped_column(Float, default=0.0)
    total_amount:    Mapped[float]       = mapped_column(Float, default=0.0)

    # Payment
    deposit_address: Mapped[str | None]  = mapped_column(String(128))
    wallet_index:    Mapped[int | None]  = mapped_column(Integer)
    tx_hash:         Mapped[str | None]  = mapped_column(String(128))

    # Seller payout
    seller_wallet:   Mapped[str | None]  = mapped_column(String(128))
    seller_network:  Mapped[str | None]  = mapped_column(String(32))

    # Status
    status:          Mapped[str]         = mapped_column(String(32), default=DealStatus.DRAFT)

    # Pinned message
    pinned_msg_id:   Mapped[int | None]  = mapped_column(BigInteger)

    # Block cursor — prevents BEP20/ETH monitor from rescanning from genesis
    last_checked_block: Mapped[int]      = mapped_column(Integer, default=0)

    # Timestamps
    created_at:      Mapped[datetime]    = mapped_column(DateTime, server_default=func.now())
    funded_at:       Mapped[datetime | None] = mapped_column(DateTime)
    released_at:     Mapped[datetime | None] = mapped_column(DateTime)

    admin_notes:     Mapped[str | None]  = mapped_column(Text)

    transactions:    Mapped[list["Transaction"]] = relationship("Transaction", back_populates="deal")
    disputes:        Mapped[list["Dispute"]]     = relationship("Dispute", back_populates="deal")


class Transaction(Base):
    __tablename__ = "transactions"

    id:          Mapped[int]    = mapped_column(Integer, primary_key=True, autoincrement=True)
    deal_id:     Mapped[int]    = mapped_column(Integer, ForeignKey("deals.id"), nullable=False)
    tx_hash:     Mapped[str]    = mapped_column(String(128), nullable=False)
    amount:      Mapped[float]  = mapped_column(Float, nullable=False)
    crypto:      Mapped[str]    = mapped_column(String(32), nullable=False)
    from_addr:   Mapped[str | None] = mapped_column(String(128))
    confirmed:   Mapped[bool]   = mapped_column(Boolean, default=False)
    created_at:  Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    deal: Mapped["Deal"] = relationship("Deal", back_populates="transactions")


class Dispute(Base):
    __tablename__ = "disputes"

    id:          Mapped[int]       = mapped_column(Integer, primary_key=True, autoincrement=True)
    deal_id:     Mapped[int]       = mapped_column(Integer, ForeignKey("deals.id"), nullable=False)
    opened_by:   Mapped[int]       = mapped_column(BigInteger, nullable=False)
    reason:      Mapped[str]       = mapped_column(Text, nullable=False)
    status:      Mapped[str]       = mapped_column(String(32), default="open")
    admin_id:    Mapped[int | None]  = mapped_column(BigInteger)
    resolution:  Mapped[str | None]  = mapped_column(Text)
    created_at:  Mapped[datetime]  = mapped_column(DateTime, server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)

    deal: Mapped["Deal"] = relationship("Deal", back_populates="disputes")


class PlatformSetting(Base):
    """Key-value table for admin-configurable platform settings."""
    __tablename__ = "platform_settings"

    key:         Mapped[str]          = mapped_column(String(64), primary_key=True)
    value:       Mapped[str]          = mapped_column(Text, nullable=False)
    description: Mapped[str | None]   = mapped_column(String(256))
    updated_at:  Mapped[datetime]     = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class AuditLog(Base):
    """Immutable log of all admin actions."""
    __tablename__ = "audit_logs"

    id:         Mapped[int]          = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor:      Mapped[str]          = mapped_column(String(128), nullable=False)   # "admin_web", "bot_monitor", etc.
    action:     Mapped[str]          = mapped_column(String(128), nullable=False)
    target:     Mapped[str | None]   = mapped_column(String(256))                   # deal_uid, user_id, etc.
    detail:     Mapped[str | None]   = mapped_column(Text)
    created_at: Mapped[datetime]     = mapped_column(DateTime, server_default=func.now())


class SupportTicket(Base):
    """User-submitted support tickets."""
    __tablename__ = "support_tickets"

    id:          Mapped[int]          = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int | None]   = mapped_column(BigInteger)
    username:    Mapped[str | None]   = mapped_column(String(64))
    subject:     Mapped[str]          = mapped_column(String(256), nullable=False)
    message:     Mapped[str]          = mapped_column(Text, nullable=False)
    status:      Mapped[str]          = mapped_column(String(32), default="open")   # open | resolved | closed
    reply:       Mapped[str | None]   = mapped_column(Text)
    created_at:  Mapped[datetime]     = mapped_column(DateTime, server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
