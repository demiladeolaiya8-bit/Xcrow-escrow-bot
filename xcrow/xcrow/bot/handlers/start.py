"""
/start, /create, /menu, /history, /wallet, /calculate,
/escrow_fee, /verify, /feedback, /support, /new_wallet
"""
from __future__ import annotations
import random, string
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext

from config import settings
from bot.keyboards.kb import start_kb
from bot.states import DmStates
from database.crud import (
    get_or_create_user, get_user_deals, get_deal_by_uid, create_deal,
)
from database.models import CRYPTO_LABELS, CryptoNetwork

router = Router()


# ── helpers ────────────────────────────────────────────────────────────────

def gen_uid(length: int = 8) -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=length))


def welcome_text() -> str:
    support = settings.SUPPORT_USERNAME
    lines = [
        "👋 <b>Welcome to XCROW Telegram Escrow</b>\n",
        "Secure cryptocurrency escrow for buyers and sellers.\n",
        "<b>How Escrow Works</b>\n",
        "1. Create an escrow group.",
        "2. Buyer deposits funds into escrow.",
        "3. Seller delivers the item or service.",
        "4. Buyer confirms delivery.",
        "5. Escrow releases the funds.\n",
        "Funds remain locked until the escrow release conditions are met.\n",
        f"Support: @{support}\n",
        'Tap <b>"Create Escrow Group"</b> to begin.',
    ]
    return "\n".join(lines)


# ── /start ─────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    if message.chat.type != "private":
        return  # ignore /start in groups
    await message.answer(
        welcome_text(),
        reply_markup=start_kb(settings.SUPPORT_USERNAME, settings.WEBSITE_URL),
    )


# ── /create  (same as pressing the button) ────────────────────────────────

@router.message(Command("create"))
async def cmd_create(message: Message) -> None:
    if message.chat.type != "private":
        return
    await _start_create_flow(message)


@router.callback_query(F.data == "create_escrow")
async def cb_create_escrow(callback: CallbackQuery) -> None:
    await callback.answer()
    await _start_create_flow(callback.message, edit=True)


async def _start_create_flow(message: Message, edit: bool = False) -> None:
    """Core create-group logic, shared by /create and the button callback."""
    from services.group_creator import create_and_setup_group
    from database.crud import create_deal as db_create_deal, update_deal
    from bot.bot import get_bot

    bot = get_bot()
    uid = gen_uid()

    loading_text = (
        f"⏳ <b>Creating your escrow group…</b>\n\n"
        f"Deal ID: <code>{uid}</code>\n"
        "Please wait a moment."
    )

    if edit:
        await message.edit_text(loading_text)
    else:
        msg = await message.answer(loading_text)
        message = msg  # swap so we can edit it below

    # Create deal record (status=draft until group is created)
    deal = await db_create_deal(creator_id=message.chat.id, deal_uid=uid)

    if not settings.pyrogram_configured:
        # Fallback: instruct user to manually create a group and add the bot
        fallback = (
            f"⚠️ <b>Auto group creation is not configured.</b>\n\n"
            f"To proceed manually:\n"
            f"1. Create a new Telegram group.\n"
            f"2. Add @{settings.BOT_USERNAME} to the group.\n"
            f"3. Send this command in the group:\n\n"
            f"<code>/register {uid}</code>\n\n"
            f"Your Deal ID: <code>{uid}</code>"
        )
        await message.edit_text(fallback)
        return

    try:
        group_id, invite_link = await create_and_setup_group(deal=deal, bot=bot)
        result_text = (
            f"✅ <b>Your Escrow Group is Ready!</b>\n\n"
            f"Deal ID: <code>{uid}</code>\n\n"
            f"<b>Share this invite link</b> with the other party:\n"
            f"{invite_link}\n\n"
            f"Once both parties have joined, follow the steps in the group to complete the deal."
        )
        await message.edit_text(result_text)
    except Exception as e:
        from loguru import logger
        logger.error(f"Group creation failed for deal {uid}: {e}")
        await message.edit_text(
            f"❌ <b>Group creation failed.</b>\n\n"
            f"Error: {e}\n\n"
            f"Please make sure you've run <code>python pyrogram_auth.py</code> on the server first, "
            f"then restart the bot."
        )


# ── /menu ──────────────────────────────────────────────────────────────────

@router.message(Command("menu"))
async def cmd_menu(message: Message) -> None:
    text = (
        "📋 <b>XCROW Commands</b>\n\n"
        "/start — Welcome screen\n"
        "/create — Create a new escrow group\n"
        "/history — Your escrow history\n"
        "/wallet — Your payout wallets\n"
        "/new_wallet — Add a payout wallet\n"
        "/calculate — Calculate escrow fees\n"
        "/escrow_fee — Fee structure\n"
        "/verify — Verify a deal\n"
        "/feedback — Report a bug / request feature\n"
        "/support — Contact support\n"
        "/menu — This list"
    )
    await message.answer(text)


# ── /history ───────────────────────────────────────────────────────────────

@router.message(Command("history"))
async def cmd_history(message: Message) -> None:
    if message.chat.type != "private":
        return
    deals = await get_user_deals(message.from_user.id)
    if not deals:
        await message.answer("You have no escrow deals yet.\n\nUse /create to start one.")
        return

    STATUS_EMOJI = {
        "draft": "📝", "step1_pending": "1️⃣", "step2_pending": "2️⃣",
        "step3_pending": "3️⃣", "step4_pending": "4️⃣", "step5_pending": "⏳",
        "awaiting_payment": "⏳", "funded": "💰", "in_delivery": "🚚",
        "buyer_confirming": "🔍", "releasing": "🔓", "completed": "✅",
        "disputed": "⚠️", "refunded": "↩️", "cancelled": "❌",
    }

    lines = ["📋 <b>Your Escrow History</b>\n"]
    for d in deals:
        emoji = STATUS_EMOJI.get(d.status, "❓")
        role = "Buyer" if d.buyer_id == message.from_user.id else (
               "Seller" if d.seller_id == message.from_user.id else "Creator")
        amount_str = f"{d.total_amount} {d.crypto}" if d.amount else "—"
        lines.append(
            f"{emoji} <b>{d.deal_uid}</b> · {d.status.replace('_', ' ').title()}\n"
            f"   Role: {role} · Amount: {amount_str}\n"
        )

    await message.answer("\n".join(lines))


# ── /wallet ────────────────────────────────────────────────────────────────

@router.message(Command("wallet"))
async def cmd_wallet(message: Message) -> None:
    if message.chat.type != "private":
        return
    deals = await get_user_deals(message.from_user.id)
    wallets = {
        (d.seller_wallet, d.seller_network)
        for d in deals
        if d.seller_id == message.from_user.id and d.seller_wallet
    }
    if not wallets:
        await message.answer(
            "You have no payout wallets saved yet.\n\n"
            "Your wallet is saved automatically when you register as Seller in an escrow group."
        )
        return
    lines = ["💳 <b>Your Saved Payout Wallets</b>\n"]
    for addr, net in wallets:
        lines.append(f"• <b>{net}</b>\n  <code>{addr}</code>\n")
    await message.answer("\n".join(lines))


# ── /new_wallet ────────────────────────────────────────────────────────────

@router.message(Command("new_wallet"))
async def cmd_new_wallet(message: Message) -> None:
    if message.chat.type != "private":
        return
    await message.answer(
        "💳 <b>Add a Payout Wallet</b>\n\n"
        "Your payout wallet is saved automatically when you register as <b>Seller</b> "
        "in an escrow group and enter your wallet address.\n\n"
        "Start a deal with /create to register as seller and add your wallet."
    )


# ── /calculate ─────────────────────────────────────────────────────────────

@router.message(Command("calculate"))
async def cmd_calculate(message: Message) -> None:
    if message.chat.type != "private":
        return
    text = (
        "🧮 <b>Fee Calculator</b>\n\n"
        "Send me an amount to calculate the escrow fee.\n"
        "Example: <code>100</code>\n\n"
        f"Current fee: <b>{settings.ESCROW_FEE_PERCENT}%</b> ({settings.FEE_MODEL.replace('_', ' ').title()})"
    )
    await message.answer(text)


@router.message(F.text.regexp(r"^\d+(\.\d+)?$") & F.chat.type == "private")
async def calc_fee_input(message: Message) -> None:
    try:
        amount = float(message.text.strip())
        fee, total = settings.fee_breakdown(amount)
        model_desc = {
            "buyer_pays":  "Fee added on top — buyer pays deal amount + fee",
            "seller_pays": "Fee deducted — seller receives deal amount minus fee",
            "split":       "Fee split 50/50 between buyer and seller",
        }.get(settings.FEE_MODEL, "")

        text = (
            f"🧮 <b>Fee Breakdown</b>\n\n"
            f"Deal Amount:    <b>{amount:,.2f}</b>\n"
            f"Escrow Fee ({settings.ESCROW_FEE_PERCENT}%): <b>{fee:,.4f}</b>\n"
            f"──────────────────\n"
            f"Buyer Sends:    <b>{total:,.4f}</b>\n"
            f"Seller Receives: <b>{amount:,.4f}</b>\n\n"
            f"ℹ️ {model_desc}"
        )
        await message.answer(text)
    except ValueError:
        pass


# ── /escrow_fee ────────────────────────────────────────────────────────────

@router.message(Command("escrow_fee"))
async def cmd_escrow_fee(message: Message) -> None:
    fee = settings.ESCROW_FEE_PERCENT
    model = settings.FEE_MODEL.replace("_", " ").title()
    text = (
        "💸 <b>XCROW Fee Structure</b>\n\n"
        f"Platform Fee: <b>{fee}%</b> per deal\n"
        f"Fee Model:    <b>{model}</b>\n\n"
        "<b>Supported Networks:</b>\n"
        + "\n".join(f"• {label}" for label in CRYPTO_LABELS.values())
        + "\n\n"
        "The fee is calculated automatically and shown to both parties before payment."
    )
    await message.answer(text)


# ── /verify ────────────────────────────────────────────────────────────────

@router.message(Command("verify"))
async def cmd_verify(message: Message) -> None:
    if message.chat.type != "private":
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "🔍 <b>Verify a Deal</b>\n\n"
            "Send: <code>/verify DEAL_UID</code>\n"
            "Example: <code>/verify AB12CD34</code>"
        )
        return
    uid = args[1].strip().upper()
    deal = await get_deal_by_uid(uid)
    if not deal:
        await message.answer(f"❌ Deal <code>{uid}</code> not found.")
        return
    buyer = deal.buyer.display_name if deal.buyer else "Not registered"
    seller = deal.seller.display_name if deal.seller else "Not registered"
    amount_str = f"{deal.total_amount} {deal.crypto}" if deal.amount else "—"
    text = (
        f"🔍 <b>Deal Verified</b>\n\n"
        f"Deal ID: <code>{deal.deal_uid}</code>\n"
        f"Status:  {deal.status.replace('_', ' ').title()}\n"
        f"Buyer:   {buyer}\n"
        f"Seller:  {seller}\n"
        f"Amount:  {amount_str}\n"
        f"Item:    {deal.title or '—'}\n"
        f"Created: {deal.created_at.strftime('%Y-%m-%d %H:%M UTC')}"
    )
    await message.answer(text)


# ── /feedback ──────────────────────────────────────────────────────────────

@router.message(Command("feedback"))
async def cmd_feedback(message: Message, state: FSMContext) -> None:
    if message.chat.type != "private":
        return
    await state.set_state(DmStates.awaiting_feedback)
    await message.answer(
        "💬 <b>Send Feedback</b>\n\n"
        "Type your feedback, bug report, or feature request.\n"
        "It will be sent directly to the admin team.\n\n"
        "Send /cancel to cancel."
    )


@router.message(DmStates.awaiting_feedback)
async def handle_feedback(message: Message, state: FSMContext) -> None:
    from bot.bot import get_bot
    await state.clear()
    bot = get_bot()
    feedback_text = (
        f"📬 <b>New Feedback</b>\n\n"
        f"From: {message.from_user.mention_html()} (ID: <code>{message.from_user.id}</code>)\n\n"
        f"{message.text}"
    )
    for admin_id in settings.admin_id_list:
        try:
            await bot.send_message(admin_id, feedback_text)
        except Exception:
            pass
    await message.answer("✅ Your feedback has been sent. Thank you!")


# ── /support ───────────────────────────────────────────────────────────────

@router.message(Command("support"))
async def cmd_support(message: Message) -> None:
    text = (
        f"💬 <b>Support</b>\n\n"
        f"Contact our support team: @{settings.SUPPORT_USERNAME}\n\n"
        f"Include your Deal ID in your message for faster assistance."
    )
    await message.answer(text)


# ── /cancel ────────────────────────────────────────────────────────────────

@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    if current:
        await state.clear()
        await message.answer("✅ Cancelled.")
