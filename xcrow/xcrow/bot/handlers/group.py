"""
Group escrow workflow — Steps 1 through 5 and all group-level callbacks.
"""
from __future__ import annotations
import re
from datetime import datetime
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, ChatMemberUpdated
from aiogram.filters import Command, ChatMemberUpdatedFilter, JOIN_TRANSITION
from aiogram.fsm.context import FSMContext

from config import settings
from bot.states import GroupDealStates
from bot.keyboards.kb import (
    register_seller_kb, payout_method_kb, register_buyer_kb,
    payment_currency_kb, confirm_deal_kb, payment_actions_kb,
    delivery_kb, dispute_kb,
)
from database.crud import (
    get_deal_by_group, get_deal_by_uid, update_deal,
    create_transaction, tx_hash_exists, create_dispute,
    fee_breakdown as db_fee_breakdown, get_fee_percent,
    create_audit_log, get_setting,
)
from database.models import DealStatus, CryptoNetwork, CRYPTO_LABELS, CRYPTO_SYMBOLS, AUTO_MONITOR_NETWORKS

router = Router()


# ══════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════

def format_pinned(deal) -> str:
    buyer  = deal.buyer.display_name  if deal.buyer  else "❓ Not registered"
    seller = deal.seller.display_name if deal.seller else "❓ Not registered"
    item   = deal.title or "❓ Not set"

    if deal.amount:
        symbol = CRYPTO_SYMBOLS.get(deal.crypto, deal.crypto or "")
        amount_str = f"{deal.amount:,.4f} {symbol}"
        fee_str    = f"{deal.fee_amount:,.4f} {symbol} ({deal.fee_percent:.2f}%)"
        total_str  = f"{deal.total_amount:,.4f} {symbol}"
    else:
        amount_str = fee_str = total_str = "❓ Not set"

    deposit_str = f"<code>{deal.deposit_address}</code>" if deal.deposit_address else "❓ Not generated"

    STATUS_MAP = {
        DealStatus.DRAFT:            "📝 Setting up group…",
        DealStatus.STEP1_PENDING:    "1️⃣ Waiting for Seller to register",
        DealStatus.STEP2_PENDING:    "2️⃣ Waiting for Seller payout details",
        DealStatus.STEP3_PENDING:    "3️⃣ Waiting for Buyer to register",
        DealStatus.STEP4_PENDING:    "4️⃣ Collecting deal information",
        DealStatus.STEP5_PENDING:    "⏳ Awaiting payment",
        DealStatus.AWAITING_PAYMENT: "⏳ Awaiting payment",
        DealStatus.FUNDED:           "💰 Escrow funded — Seller delivering",
        DealStatus.IN_DELIVERY:      "🚚 Delivery in progress",
        DealStatus.BUYER_CONFIRMING: "🔍 Waiting for buyer to confirm receipt",
        DealStatus.RELEASING:        "🔓 Releasing funds to seller",
        DealStatus.COMPLETED:        "✅ Deal completed",
        DealStatus.DISPUTED:         "⚠️ DISPUTED — Admin reviewing",
        DealStatus.REFUNDED:         "↩️ Refunded to buyer",
        DealStatus.CANCELLED:        "❌ Cancelled",
    }
    status_str = STATUS_MAP.get(deal.status, deal.status)

    return (
        f"📋 <b>XCROW ESCROW — Deal #{deal.deal_uid}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Buyer:</b>   {buyer}\n"
        f"👤 <b>Seller:</b>  {seller}\n"
        f"📦 <b>Item:</b>    {item}\n"
        f"💰 <b>Amount:</b>  {amount_str}\n"
        f"💸 <b>Fee:</b>     {fee_str}\n"
        f"📨 <b>Total:</b>   {total_str}\n"
        f"📍 <b>Deposit:</b> {deposit_str}\n"
        f"📊 <b>Status:</b>  {status_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ Powered by XCROW Escrow"
    )


async def update_pinned(bot: Bot, deal) -> None:
    if not (deal.group_id and deal.pinned_msg_id):
        return
    try:
        await bot.edit_message_text(
            chat_id=deal.group_id,
            message_id=deal.pinned_msg_id,
            text=format_pinned(deal),
        )
    except Exception:
        pass


async def notify_admins(bot: Bot, text: str) -> None:
    for admin_id in settings.admin_id_list:
        try:
            await bot.send_message(admin_id, text)
        except Exception:
            pass


def validate_address(address: str, network: str) -> bool:
    """Basic regex validation for crypto addresses."""
    address = address.strip()
    patterns = {
        CryptoNetwork.USDT_TRC20: r"^T[A-Za-z1-9]{33}$",
        CryptoNetwork.USDT_BEP20: r"^0x[0-9a-fA-F]{40}$",
        CryptoNetwork.ETH:        r"^0x[0-9a-fA-F]{40}$",
        CryptoNetwork.BTC:        r"^(1|3)[A-HJ-NP-Za-km-z1-9]{25,34}$|^bc1[a-z0-9]{39,59}$",
        CryptoNetwork.SOL:        r"^[1-9A-HJ-NP-Za-km-z]{32,44}$",
        CryptoNetwork.TON:        r"^(EQ|UQ)[A-Za-z0-9_-]{46}$|^[A-Za-z0-9_-]{48}$",
        CryptoNetwork.LTC:        r"^[LM3][A-Za-z0-9]{26,33}$|^ltc1[a-z0-9]{39,59}$",
    }
    pat = patterns.get(network)
    if not pat:
        return len(address) > 10
    return bool(re.match(pat, address))


# ══════════════════════════════════════════════════════════════════════════
#  BOT ADDED TO GROUP (via my_chat_member update)
# ══════════════════════════════════════════════════════════════════════════

@router.my_chat_member(ChatMemberUpdatedFilter(member_status_changed=JOIN_TRANSITION))
async def bot_added_to_group(update: ChatMemberUpdated, bot: Bot) -> None:
    if update.chat.type not in ("group", "supergroup"):
        return
    group_id = update.chat.id
    deal = await get_deal_by_group(group_id)
    if deal is None:
        return
    if deal.pinned_msg_id:
        return


# ══════════════════════════════════════════════════════════════════════════
#  /register — manual fallback when Pyrogram is not configured
# ══════════════════════════════════════════════════════════════════════════

@router.message(Command("register"), F.chat.type.in_({"group", "supergroup"}))
async def cmd_register(message: Message, bot: Bot) -> None:
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.reply("Usage: <code>/register DEAL_UID</code>")
        return
    uid = args[1].strip().upper()
    deal = await get_deal_by_uid(uid)
    if not deal:
        await message.reply(f"❌ Deal <code>{uid}</code> not found.")
        return
    if deal.group_id and deal.group_id != message.chat.id:
        await message.reply("❌ This deal is already linked to a different group.")
        return

    await update_deal(deal.id, group_id=message.chat.id, status=DealStatus.STEP1_PENDING)
    deal = await get_deal_by_uid(uid)

    try:
        pinned = await bot.send_message(message.chat.id, format_pinned(deal))
        await bot.pin_chat_message(message.chat.id, pinned.message_id, disable_notification=True)
        await update_deal(deal.id, pinned_msg_id=pinned.message_id)
        deal = await get_deal_by_uid(uid)
    except Exception:
        pass

    await _send_step1(bot, message.chat.id, deal)


# ══════════════════════════════════════════════════════════════════════════
#  STEP 1 — Register Seller
# ══════════════════════════════════════════════════════════════════════════

async def _send_step1(bot: Bot, chat_id: int, deal) -> None:
    await bot.send_message(
        chat_id,
        f"<b>Step 1 of 5 — Register Seller</b>\n\n"
        f"Deal: <code>{deal.deal_uid}</code>\n\n"
        f"Who is the <b>Seller</b>? (the person receiving payment)\n\n"
        f"Tap the button below to register as Seller.",
        reply_markup=register_seller_kb(deal.deal_uid),
    )


@router.callback_query(F.data.startswith("reg_seller:"))
async def cb_reg_seller(callback: CallbackQuery, bot: Bot) -> None:
    uid = callback.data.split(":", 1)[1]
    deal = await get_deal_by_uid(uid)
    if not deal:
        await callback.answer("Deal not found.", show_alert=True)
        return

    if deal.status != DealStatus.STEP1_PENDING:
        await callback.answer("Seller already registered.", show_alert=True)
        return

    user = callback.from_user
    await callback.answer()

    from database.crud import get_or_create_user
    await get_or_create_user(user.id, user.username, user.first_name)
    await update_deal(deal.id, seller_id=user.id, status=DealStatus.STEP2_PENDING)
    deal = await get_deal_by_uid(uid)

    await callback.message.edit_text(
        f"✅ <b>{user.mention_html()}</b> has been registered as <b>Seller</b>."
    )
    await update_pinned(bot, deal)
    await _send_step2(bot, callback.message.chat.id, deal)


# ══════════════════════════════════════════════════════════════════════════
#  STEP 2 — Seller payout details
# ══════════════════════════════════════════════════════════════════════════

async def _send_step2(bot: Bot, chat_id: int, deal) -> None:
    await bot.send_message(
        chat_id,
        f"<b>Step 2 of 5 — Seller Payout Details</b>\n\n"
        f"<b>Seller</b>: {deal.seller.display_name}\n\n"
        f"Select the network you want to receive payment on:",
        reply_markup=payout_method_kb(deal.deal_uid),
    )


@router.callback_query(F.data.startswith("seller_net:"))
async def cb_seller_network(callback: CallbackQuery, state: FSMContext) -> None:
    _, uid, network = callback.data.split(":", 2)
    deal = await get_deal_by_uid(uid)
    if not deal:
        await callback.answer("Deal not found.", show_alert=True)
        return
    if deal.status != DealStatus.STEP2_PENDING:
        await callback.answer("Already past this step.", show_alert=True)
        return
    if callback.from_user.id != deal.seller_id:
        await callback.answer("Only the Seller can select the payout network.", show_alert=True)
        return

    await callback.answer()
    label = CRYPTO_LABELS.get(network, network)
    await callback.message.edit_text(
        f"✅ Network selected: <b>{label}</b>\n\n"
        f"Now <b>type your {label} wallet address</b> in this chat.\n"
        f"(The address where you want to receive payment)"
    )

    await update_deal(deal.id, seller_network=network)
    await state.set_state(GroupDealStates.awaiting_seller_address)
    await state.update_data(deal_uid=uid)


@router.message(GroupDealStates.awaiting_seller_address, F.chat.type.in_({"group", "supergroup"}))
async def msg_seller_address(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    uid = data.get("deal_uid")
    if not uid:
        return
    deal = await get_deal_by_uid(uid)
    if not deal:
        return
    if message.from_user.id != deal.seller_id:
        return

    address = message.text.strip() if message.text else ""

    if not validate_address(address, deal.seller_network):
        await message.reply(
            f"❌ <b>Invalid address</b> for {CRYPTO_LABELS.get(deal.seller_network, deal.seller_network)}.\n\n"
            f"Please check the address and try again."
        )
        return

    await state.clear()
    await update_deal(deal.id, seller_wallet=address, status=DealStatus.STEP3_PENDING)
    deal = await get_deal_by_uid(uid)

    await message.reply(
        f"✅ Payout address saved:\n<code>{address}</code>\n\n"
        f"Network: <b>{CRYPTO_LABELS.get(deal.seller_network, deal.seller_network)}</b>"
    )
    await update_pinned(bot, deal)
    await _send_step3(bot, message.chat.id, deal)


# ══════════════════════════════════════════════════════════════════════════
#  STEP 3 — Register Buyer
# ══════════════════════════════════════════════════════════════════════════

async def _send_step3(bot: Bot, chat_id: int, deal) -> None:
    await bot.send_message(
        chat_id,
        f"<b>Step 3 of 5 — Register Buyer</b>\n\n"
        f"Who is the <b>Buyer</b>? (the person making payment)\n\n"
        f"Tap the button below to register as Buyer.",
        reply_markup=register_buyer_kb(deal.deal_uid),
    )


@router.callback_query(F.data.startswith("reg_buyer:"))
async def cb_reg_buyer(callback: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    uid = callback.data.split(":", 1)[1]
    deal = await get_deal_by_uid(uid)
    if not deal:
        await callback.answer("Deal not found.", show_alert=True)
        return
    if deal.status != DealStatus.STEP3_PENDING:
        await callback.answer("Buyer already registered.", show_alert=True)
        return
    if callback.from_user.id == deal.seller_id:
        await callback.answer("The seller cannot also be the buyer.", show_alert=True)
        return

    user = callback.from_user
    await callback.answer()

    from database.crud import get_or_create_user
    await get_or_create_user(user.id, user.username, user.first_name)
    await update_deal(deal.id, buyer_id=user.id, status=DealStatus.STEP4_PENDING)
    deal = await get_deal_by_uid(uid)

    await callback.message.edit_text(
        f"✅ <b>{user.mention_html()}</b> has been registered as <b>Buyer</b>."
    )
    await update_pinned(bot, deal)

    await state.set_state(GroupDealStates.awaiting_deal_description)
    await state.update_data(deal_uid=uid)

    await _send_step4_description(bot, callback.message.chat.id, deal)


# ══════════════════════════════════════════════════════════════════════════
#  STEP 4 — Deal Information
# ══════════════════════════════════════════════════════════════════════════

async def _send_step4_description(bot: Bot, chat_id: int, deal) -> None:
    await bot.send_message(
        chat_id,
        f"<b>Step 4 of 5 — Deal Information</b>\n\n"
        f"<b>Buyer</b> ({deal.buyer.display_name}), please describe the item or service:\n\n"
        f"(Type a brief description, e.g. \"Website redesign — 5 pages\")"
    )


@router.message(GroupDealStates.awaiting_deal_description, F.chat.type.in_({"group", "supergroup"}))
async def msg_deal_description(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    uid = data.get("deal_uid")
    if not uid:
        return
    deal = await get_deal_by_uid(uid)
    if not deal or message.from_user.id != deal.buyer_id:
        return

    description = message.text.strip() if message.text else ""
    if len(description) < 3:
        await message.reply("Please provide a meaningful description (at least 3 characters).")
        return

    await update_deal(deal.id, title=description)
    await state.set_state(GroupDealStates.awaiting_deal_amount)

    await message.reply(
        f"✅ Description saved: <i>{description}</i>\n\n"
        f"Now enter the <b>agreed amount</b> (numbers only, e.g. <code>150.50</code>):"
    )


@router.message(GroupDealStates.awaiting_deal_amount, F.chat.type.in_({"group", "supergroup"}))
async def msg_deal_amount(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    uid = data.get("deal_uid")
    if not uid:
        return
    deal = await get_deal_by_uid(uid)
    if not deal or message.from_user.id != deal.buyer_id:
        return

    text = message.text.strip().replace(",", ".") if message.text else ""
    try:
        amount = float(text)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.reply("❌ Invalid amount. Enter a positive number, e.g. <code>100.50</code>")
        return

    await update_deal(deal.id, amount=amount)
    await state.clear()

    deal = await get_deal_by_uid(uid)
    await message.reply(
        f"✅ Amount saved: <b>{amount}</b>\n\n"
        f"Now select the <b>payment currency / network</b>:",
        reply_markup=payment_currency_kb(uid),
    )


@router.callback_query(F.data.startswith("deal_crypto:"))
async def cb_deal_crypto(callback: CallbackQuery, bot: Bot) -> None:
    _, uid, network = callback.data.split(":", 2)
    deal = await get_deal_by_uid(uid)
    if not deal:
        await callback.answer("Deal not found.", show_alert=True)
        return
    if deal.status != DealStatus.STEP4_PENDING:
        await callback.answer("Already past this step.", show_alert=True)
        return
    if callback.from_user.id != deal.buyer_id:
        await callback.answer("Only the Buyer selects the payment currency.", show_alert=True)
        return

    # Read live fee % from DB, snapshot it onto the deal
    fee_pct = await get_fee_percent()
    fee, total = await db_fee_breakdown(deal.amount)
    symbol = CRYPTO_SYMBOLS.get(network, network)

    await update_deal(
        deal.id,
        crypto=network,
        fee_percent=fee_pct,
        fee_amount=fee,
        total_amount=total,
    )
    deal = await get_deal_by_uid(uid)
    await callback.answer()

    summary = (
        f"📋 <b>Deal Summary — #{uid}</b>\n\n"
        f"📦 <b>Item:</b>    {deal.title}\n"
        f"💰 <b>Amount:</b>  {deal.amount:,.4f} {symbol}\n"
        f"💸 <b>Fee:</b>     {fee:,.4f} {symbol} ({fee_pct:.2f}%)\n"
        f"📨 <b>Total:</b>   {total:,.4f} {symbol}  ← Buyer sends this\n"
        f"🏦 <b>Network:</b> {CRYPTO_LABELS.get(network, network)}\n"
        f"👤 <b>Seller receives:</b> {deal.amount:,.4f} {symbol}\n\n"
        f"<b>Both parties must review and confirm.</b>"
    )

    await callback.message.edit_text(summary, reply_markup=confirm_deal_kb(uid))


@router.callback_query(F.data.startswith("confirm_deal:"))
async def cb_confirm_deal(callback: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    parts = callback.data.split(":")
    uid, action = parts[1], parts[2]
    deal = await get_deal_by_uid(uid)
    if not deal:
        await callback.answer("Deal not found.", show_alert=True)
        return

    user = callback.from_user
    if user.id not in (deal.buyer_id, deal.seller_id):
        await callback.answer("Only the buyer or seller can confirm.", show_alert=True)
        return

    if action == "cancel":
        await update_deal(deal.id, status=DealStatus.CANCELLED)
        deal = await get_deal_by_uid(uid)
        await callback.answer("Deal cancelled.")
        await callback.message.edit_text(f"❌ Deal <code>{uid}</code> has been cancelled.")
        await update_pinned(bot, deal)
        return

    if action == "edit":
        await callback.answer("Please restart deal details entry.")
        await update_deal(deal.id, title=None, amount=None, crypto=None,
                          fee_amount=0, total_amount=0, status=DealStatus.STEP4_PENDING)
        deal = await get_deal_by_uid(uid)
        await state.set_state(GroupDealStates.awaiting_deal_description)
        await state.update_data(deal_uid=uid)
        await callback.message.edit_text("Details reset. Buyer, please type the description again:")
        return

    if action == "yes":
        await callback.answer("✅ Confirmed!")
        await _finalize_step4_and_show_payment(bot, callback, deal)


async def _finalize_step4_and_show_payment(bot: Bot, callback: CallbackQuery, deal) -> None:
    from services.wallet import wallet_service
    from services.qr import generate_qr_bytes

    uid = deal.deal_uid
    wallet_index = await _get_next_wallet_index()
    network = deal.crypto

    try:
        deposit_address = wallet_service.derive_address(network, wallet_index)
    except Exception as e:
        await callback.message.reply(
            f"❌ Failed to generate deposit address: {e}\n"
            "Contact support: @" + settings.SUPPORT_USERNAME
        )
        return

    await update_deal(
        deal.id,
        wallet_index=wallet_index,
        deposit_address=deposit_address,
        status=DealStatus.STEP5_PENDING,
    )
    deal = await get_deal_by_uid(uid)
    await update_pinned(bot, deal)

    symbol  = CRYPTO_SYMBOLS.get(network, network)
    label   = CRYPTO_LABELS.get(network, network)
    total   = deal.total_amount
    is_auto = network in AUTO_MONITOR_NETWORKS

    text = (
        f"<b>Step 5 of 5 — Escrow Payment</b>\n\n"
        f"📦 <b>Item:</b>    {deal.title}\n"
        f"💰 <b>Amount:</b>  {deal.amount:,.4f} {symbol}\n"
        f"💸 <b>Fee ({deal.fee_percent:.2f}%):</b>  {deal.fee_amount:,.4f} {symbol}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📨 <b>SEND EXACTLY:</b>\n"
        f"<code>{total:,.4f} {symbol}</code>\n\n"
        f"🔗 <b>TO THIS ADDRESS:</b>\n"
        f"<code>{deposit_address}</code>\n\n"
        f"🌐 <b>Network:</b> {label}\n\n"
        f"{'🤖 Payment is monitored automatically.' if is_auto else '⚠️ Manual confirmation required. Contact support after sending.'}\n\n"
        f"⚠️ Send the <b>exact amount</b> to the <b>correct network</b>."
    )

    try:
        from aiogram.types import BufferedInputFile
        qr_bytes = generate_qr_bytes(deposit_address)
        await bot.send_photo(
            callback.message.chat.id,
            photo=BufferedInputFile(qr_bytes, filename="deposit_qr.png"),
            caption=f"📷 QR Code for deposit address\n<code>{deposit_address}</code>",
        )
    except Exception:
        pass

    await bot.send_message(
        callback.message.chat.id,
        text,
        reply_markup=payment_actions_kb(uid),
    )

    await notify_admins(
        bot,
        f"💼 <b>Deal {uid} awaiting payment</b>\n"
        f"Amount: {total} {symbol}\n"
        f"Address: <code>{deposit_address}</code>",
    )


async def _get_next_wallet_index() -> int:
    from database.crud import get_next_wallet_index
    return await get_next_wallet_index()


# ══════════════════════════════════════════════════════════════════════════
#  STEP 5 — Manual payment check
# ══════════════════════════════════════════════════════════════════════════

@router.callback_query(F.data.startswith("check_payment:"))
async def cb_check_payment(callback: CallbackQuery, bot: Bot) -> None:
    uid = callback.data.split(":", 1)[1]
    deal = await get_deal_by_uid(uid)
    if not deal:
        await callback.answer("Deal not found.", show_alert=True)
        return

    if deal.status not in (DealStatus.STEP5_PENDING, DealStatus.AWAITING_PAYMENT):
        await callback.answer("Deal is not awaiting payment.", show_alert=True)
        return

    await callback.answer("🔄 Checking blockchain…")

    from services.blockchain.monitor import check_deal_once
    paid = await check_deal_once(deal, bot)

    if not paid:
        symbol = CRYPTO_SYMBOLS.get(deal.crypto, deal.crypto or "?")
        await callback.message.reply(
            f"⏳ Payment of <b>{deal.total_amount:,.4f} {symbol}</b> not detected yet.\n\n"
            f"Please allow a few minutes for the transaction to confirm on-chain."
        )


# ══════════════════════════════════════════════════════════════════════════
#  Cancel deal
# ══════════════════════════════════════════════════════════════════════════

@router.callback_query(F.data.startswith("cancel_deal:"))
async def cb_cancel_deal(callback: CallbackQuery, bot: Bot) -> None:
    uid = callback.data.split(":", 1)[1]
    deal = await get_deal_by_uid(uid)
    if not deal:
        await callback.answer("Deal not found.", show_alert=True)
        return
    if callback.from_user.id not in (deal.buyer_id, deal.seller_id):
        await callback.answer("Only a party to this deal can cancel it.", show_alert=True)
        return
    if deal.status in (DealStatus.FUNDED, DealStatus.IN_DELIVERY, DealStatus.COMPLETED):
        await callback.answer("Deal is funded or completed — contact admin to cancel.", show_alert=True)
        return

    await callback.answer()
    await update_deal(deal.id, status=DealStatus.CANCELLED)
    deal = await get_deal_by_uid(uid)
    await callback.message.edit_reply_markup(reply_markup=None)
    await bot.send_message(callback.message.chat.id, f"❌ Deal <code>{uid}</code> has been cancelled.")
    await update_pinned(bot, deal)


# ══════════════════════════════════════════════════════════════════════════
#  After funding — Buyer confirms delivery
# ══════════════════════════════════════════════════════════════════════════

@router.callback_query(F.data.startswith("delivery_ok:"))
async def cb_delivery_ok(callback: CallbackQuery, bot: Bot) -> None:
    uid = callback.data.split(":", 1)[1]
    deal = await get_deal_by_uid(uid)
    if not deal:
        await callback.answer("Deal not found.", show_alert=True)
        return
    if callback.from_user.id != deal.buyer_id:
        await callback.answer("Only the Buyer can confirm delivery.", show_alert=True)
        return
    if deal.status not in (DealStatus.FUNDED, DealStatus.IN_DELIVERY, DealStatus.BUYER_CONFIRMING):
        await callback.answer("Deal is not in the delivery stage.", show_alert=True)
        return

    await callback.answer("✅ Delivery confirmed!")
    await update_deal(deal.id, status=DealStatus.RELEASING)
    deal = await get_deal_by_uid(uid)

    await callback.message.edit_reply_markup(reply_markup=None)
    symbol = CRYPTO_SYMBOLS.get(deal.crypto, deal.crypto or "?")

    await bot.send_message(
        callback.message.chat.id,
        f"✅ <b>Buyer has confirmed delivery!</b>\n\n"
        f"Funds are now being released to the seller.\n\n"
        f"Seller wallet: <code>{deal.seller_wallet}</code>\n"
        f"Network: {CRYPTO_LABELS.get(deal.seller_network, deal.seller_network)}\n"
        f"Amount: <b>{deal.amount:,.4f} {symbol}</b>\n\n"
        f"⚡ Processing automatically…",
    )
    await update_pinned(bot, deal)

    # ── Auto-release in background ────────────────────────────────────────
    import asyncio
    from services.transfer import auto_release_deal
    asyncio.create_task(auto_release_deal(deal, bot))


# ══════════════════════════════════════════════════════════════════════════
#  Disputes
# ══════════════════════════════════════════════════════════════════════════

@router.callback_query(F.data.startswith("dispute:"))
async def cb_dispute(callback: CallbackQuery) -> None:
    uid = callback.data.split(":", 1)[1]
    deal = await get_deal_by_uid(uid)
    if not deal:
        await callback.answer("Deal not found.", show_alert=True)
        return
    if callback.from_user.id not in (deal.buyer_id, deal.seller_id):
        await callback.answer("Only deal parties can raise a dispute.", show_alert=True)
        return
    await callback.answer()
    await callback.message.reply(
        f"⚠️ <b>Raise a Dispute?</b>\n\n"
        f"Deal: <code>{uid}</code>\n\n"
        f"This will freeze the deal and notify our admin team.\n"
        f"Only raise a dispute if the other party is not cooperating.",
        reply_markup=dispute_kb(uid),
    )


@router.callback_query(F.data.startswith("dispute_cancel:"))
async def cb_dispute_cancel(callback: CallbackQuery) -> None:
    await callback.answer("Dispute cancelled.")
    await callback.message.delete()


@router.callback_query(F.data.startswith("dispute_confirm:"))
async def cb_dispute_confirm(callback: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    uid = callback.data.split(":", 1)[1]
    deal = await get_deal_by_uid(uid)
    if not deal:
        await callback.answer("Deal not found.", show_alert=True)
        return

    await callback.answer()
    await state.set_state(GroupDealStates.awaiting_dispute_reason)
    await state.update_data(deal_uid=uid)
    await callback.message.edit_text(
        f"⚠️ <b>Dispute for Deal #{uid}</b>\n\n"
        f"Please describe the issue in detail.\n"
        f"Type your reason in this chat now:"
    )


@router.message(GroupDealStates.awaiting_dispute_reason, F.chat.type.in_({"group", "supergroup"}))
async def msg_dispute_reason(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    uid = data.get("deal_uid")
    if not uid:
        return
    deal = await get_deal_by_uid(uid)
    if not deal:
        return
    if message.from_user.id not in (deal.buyer_id, deal.seller_id):
        return

    reason = message.text.strip() if message.text else "(no reason given)"
    await state.clear()

    await create_dispute(deal.id, message.from_user.id, reason)
    await update_deal(deal.id, status=DealStatus.DISPUTED)
    deal = await get_deal_by_uid(uid)

    await message.reply(
        f"⚠️ <b>Dispute Raised</b>\n\n"
        f"Deal: <code>{uid}</code>\n"
        f"Reason: {reason}\n\n"
        f"Our admin team has been notified and will review this shortly.\n"
        f"Support: @{settings.SUPPORT_USERNAME}"
    )
    await update_pinned(bot, deal)

    await notify_admins(
        bot,
        f"🚨 <b>DISPUTE — Deal {uid}</b>\n\n"
        f"Raised by: {message.from_user.mention_html()} ({message.from_user.id})\n\n"
        f"Reason: {reason}\n\n"
        f"Use /admin to review.",
    )


# ══════════════════════════════════════════════════════════════════════════
#  on_payment_confirmed — called by blockchain monitor
# ══════════════════════════════════════════════════════════════════════════

async def on_payment_confirmed(bot: Bot, deal, tx_hash: str, amount: float) -> None:
    """Called externally when a deposit is confirmed on-chain."""
    await create_transaction(deal.id, tx_hash, amount, deal.crypto, confirmed=True)
    await update_deal(deal.id, status=DealStatus.FUNDED, tx_hash=tx_hash, funded_at=datetime.utcnow())
    deal = await get_deal_by_uid(deal.deal_uid)

    await update_pinned(bot, deal)

    symbol = CRYPTO_SYMBOLS.get(deal.crypto, deal.crypto or "?")
    owner_wallet  = await get_setting("owner_wallet_address", "")
    owner_network = await get_setting("owner_wallet_network", "USDT_BEP20")

    # Immutable audit record
    await create_audit_log(
        actor="bot_monitor",
        action="payment_confirmed",
        target=deal.deal_uid,
        detail=f"TX:{tx_hash} Amount:{amount} {symbol}",
    )

    # Group notification
    if deal.group_id:
        await bot.send_message(
            deal.group_id,
            f"💰 <b>Escrow Funded!</b>\n\n"
            f"Deposit confirmed: <b>{amount:,.4f} {symbol}</b>\n"
            f"Transaction: <code>{tx_hash[:20]}…</code>\n\n"
            f"<b>Seller</b> ({deal.seller.display_name if deal.seller else '?'}): "
            f"You may now proceed with delivery.\n\n"
            f"Once delivered, the Buyer will confirm receipt to release funds.",
            reply_markup=delivery_kb(deal.deal_uid),
        )

    # Private DM to buyer
    if deal.buyer_id:
        try:
            await bot.send_message(
                deal.buyer_id,
                f"✅ <b>Your escrow payment was received!</b>\n\n"
                f"Deal: <code>{deal.deal_uid}</code>\n"
                f"Amount: <b>{amount:,.4f} {symbol}</b>\n"
                f"TX: <code>{tx_hash[:20]}…</code>\n\n"
                f"The seller has been notified and will proceed with delivery. "
                f"You'll be asked to confirm receipt once delivery is complete.",
            )
        except Exception:
            pass

    # Private DM to seller
    if deal.seller_id:
        try:
            await bot.send_message(
                deal.seller_id,
                f"💼 <b>Funds are in escrow — proceed with delivery!</b>\n\n"
                f"Deal: <code>{deal.deal_uid}</code>\n"
                f"You will receive: <b>{deal.amount:,.4f} {symbol}</b>\n"
                f"To: <code>{deal.seller_wallet or '—'}</code>\n\n"
                f"Once the buyer confirms receipt, funds will be released to you.",
            )
        except Exception:
            pass

    # Admin DM with full release checklist
    fee_str = f"{deal.fee_amount:,.6f} {symbol}" if deal.fee_amount else "—"
    await notify_admins(
        bot,
        f"💰 <b>Deal {deal.deal_uid} funded — Release Details</b>\n\n"
        f"Buyer:  {deal.buyer.display_name if deal.buyer else '?'}\n"
        f"Seller: {deal.seller.display_name if deal.seller else '?'}\n\n"
        f"Deposited: {amount:,.6f} {symbol}\n"
        f"TX: <code>{tx_hash}</code>\n\n"
        f"📤 <b>When releasing:</b>\n"
        f"→ Send <b>{deal.amount:,.6f} {symbol}</b> to seller:\n"
        f"   <code>{deal.seller_wallet or '—'}</code> ({deal.seller_network or '—'})\n"
        f"→ Send fee <b>{fee_str}</b> to owner wallet:\n"
        f"   <code>{owner_wallet or 'NOT SET — configure at /panel/settings'}</code> ({owner_network})\n\n"
        f"View in dashboard: /panel/deals/{deal.deal_uid}",
    )
