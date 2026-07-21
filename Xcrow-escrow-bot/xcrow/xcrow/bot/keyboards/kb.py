"""All keyboards for Xcrow."""
from __future__ import annotations
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from database.models import CryptoNetwork, CRYPTO_LABELS


# ── /start  ────────────────────────────────────────────────────────────────

def start_kb(support: str, website: str = "") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔐 Create Escrow Group", callback_data="create_escrow")
    builder.button(text="💬 Support", url=f"https://t.me/{support}")
    if website:
        builder.button(text="🌐 Website", url=website)
    builder.adjust(1)
    return builder.as_markup()


# ── Group Step 1 ───────────────────────────────────────────────────────────

def register_seller_kb(deal_uid: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✋ I am the Seller", callback_data=f"reg_seller:{deal_uid}")
    builder.adjust(1)
    return builder.as_markup()


# ── Group Step 2 ───────────────────────────────────────────────────────────

def payout_method_kb(deal_uid: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for net in CryptoNetwork:
        builder.button(text=CRYPTO_LABELS[net], callback_data=f"seller_net:{deal_uid}:{net.value}")
    builder.adjust(1)
    return builder.as_markup()


# ── Group Step 3 ───────────────────────────────────────────────────────────

def register_buyer_kb(deal_uid: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✋ I am the Buyer", callback_data=f"reg_buyer:{deal_uid}")
    builder.adjust(1)
    return builder.as_markup()


# ── Group Step 4 ───────────────────────────────────────────────────────────

def payment_currency_kb(deal_uid: str) -> InlineKeyboardMarkup:
    """Show only networks that the central monitor supports."""
    SUPPORTED = [
        CryptoNetwork.USDT_BEP20,
        CryptoNetwork.USDT_ERC20,
        CryptoNetwork.ETH,
        CryptoNetwork.BTC,
    ]
    builder = InlineKeyboardBuilder()
    for net in SUPPORTED:
        builder.button(text=CRYPTO_LABELS[net], callback_data=f"deal_crypto:{deal_uid}:{net.value}")
    builder.adjust(1)
    return builder.as_markup()


def confirm_deal_kb(deal_uid: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Confirm",        callback_data=f"confirm_deal:{deal_uid}:yes")
    builder.button(text="✏️ Edit Details",   callback_data=f"confirm_deal:{deal_uid}:edit")
    builder.button(text="❌ Cancel Deal",    callback_data=f"confirm_deal:{deal_uid}:cancel")
    builder.adjust(1)
    return builder.as_markup()


# ── Group Step 5 ───────────────────────────────────────────────────────────

def payment_actions_kb(deal_uid: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Check Payment",        callback_data=f"check_payment:{deal_uid}")
    builder.button(text="⚠️ Raise Dispute",        callback_data=f"dispute:{deal_uid}")
    builder.button(text="❌ Cancel Deal",           callback_data=f"cancel_deal:{deal_uid}")
    builder.adjust(1)
    return builder.as_markup()


# ── Funded / Delivery ──────────────────────────────────────────────────────

def delivery_kb(deal_uid: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Confirm Delivery Received", callback_data=f"delivery_ok:{deal_uid}")
    builder.button(text="⚠️ Raise Dispute",             callback_data=f"dispute:{deal_uid}")
    builder.adjust(1)
    return builder.as_markup()


# ── Dispute ────────────────────────────────────────────────────────────────

def dispute_kb(deal_uid: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⚠️ Yes, Raise Dispute", callback_data=f"dispute_confirm:{deal_uid}")
    builder.button(text="⬅️ Back",               callback_data=f"dispute_cancel:{deal_uid}")
    builder.adjust(1)
    return builder.as_markup()


# ── Admin ──────────────────────────────────────────────────────────────────

def admin_main_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📋 All Deals",       callback_data="admin:deals:0")
    builder.button(text="🔥 Open Disputes",   callback_data="admin:disputes")
    builder.button(text="👥 Users",           callback_data="admin:users:0")
    builder.button(text="📊 Statistics",      callback_data="admin:stats")
    builder.adjust(2)
    return builder.as_markup()


def admin_deal_kb(deal_uid: str, deal_status: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if deal_status in ("funded", "in_delivery", "buyer_confirming", "releasing"):
        builder.button(text="✅ Release Funds",  callback_data=f"admin_release:{deal_uid}")
        builder.button(text="↩️ Refund Buyer",  callback_data=f"admin_refund:{deal_uid}")
    if deal_status == "disputed":
        builder.button(text="⚖️ Resolve Dispute", callback_data=f"admin_resolve:{deal_uid}")
    builder.button(text="🚫 Cancel Deal",       callback_data=f"admin_cancel:{deal_uid}")
    builder.button(text="📝 Add Note",          callback_data=f"admin_note:{deal_uid}")
    builder.button(text="⬅️ Back",              callback_data="admin:deals:0")
    builder.adjust(2)
    return builder.as_markup()


def admin_user_kb(telegram_id: int, is_banned: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if is_banned:
        builder.button(text="✅ Unban User",  callback_data=f"admin_unban:{telegram_id}")
    else:
        builder.button(text="🚫 Ban User",   callback_data=f"admin_ban:{telegram_id}")
    builder.button(text="⬅️ Back",           callback_data="admin:users:0")
    builder.adjust(1)
    return builder.as_markup()


def admin_disputes_kb(dispute_id: int, deal_uid: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Release → Seller", callback_data=f"admin_dispute_release:{dispute_id}:{deal_uid}")
    builder.button(text="Refund → Buyer",   callback_data=f"admin_dispute_refund:{dispute_id}:{deal_uid}")
    builder.button(text="⬅️ Back",          callback_data="admin:disputes")
    builder.adjust(2)
    return builder.as_markup()
