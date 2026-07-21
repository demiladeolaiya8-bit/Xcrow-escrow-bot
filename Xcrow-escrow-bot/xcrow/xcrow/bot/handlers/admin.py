"""Admin panel — /admin command and all admin callbacks."""
from __future__ import annotations
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from config import settings
from bot.states import GroupDealStates
from bot.keyboards.kb import admin_main_kb, admin_deal_kb, admin_user_kb, admin_disputes_kb
from database.crud import (
    get_all_deals, get_deal_by_uid, update_deal,
    get_all_users, get_user, set_user_banned,
    get_open_disputes, resolve_dispute, count_deals_by_status,
)
from database.models import DealStatus, CRYPTO_SYMBOLS

router = Router()


def is_admin(user_id: int) -> bool:
    return user_id in settings.admin_id_list


# ── Guard ──────────────────────────────────────────────────────────────────

async def _check_admin(message_or_callback) -> bool:
    uid = (
        message_or_callback.from_user.id
        if hasattr(message_or_callback, "from_user")
        else None
    )
    if not uid or not is_admin(uid):
        if hasattr(message_or_callback, "answer"):
            await message_or_callback.answer("⛔ Admin access only.")
        elif hasattr(message_or_callback, "message"):
            await message_or_callback.answer("⛔ Admin access only.", show_alert=True)
        return False
    return True


# ── /admin ─────────────────────────────────────────────────────────────────

@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    if not await _check_admin(message):
        return
    await message.answer(
        "👑 <b>XCROW Admin Panel</b>",
        reply_markup=admin_main_kb(),
    )


# ── Statistics ──────────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin:stats")
async def cb_admin_stats(callback: CallbackQuery) -> None:
    if not await _check_admin(callback):
        return
    await callback.answer()

    total     = await count_deals_by_status("completed") + await count_deals_by_status("releasing")
    active    = await count_deals_by_status("funded") + await count_deals_by_status("in_delivery")
    pending   = await count_deals_by_status("step5_pending") + await count_deals_by_status("awaiting_payment")
    disputed  = await count_deals_by_status("disputed")
    completed = await count_deals_by_status("completed")

    text = (
        "📊 <b>Statistics</b>\n\n"
        f"💰 Funded/Active:    {active}\n"
        f"⏳ Awaiting Payment: {pending}\n"
        f"⚠️ Disputed:         {disputed}\n"
        f"✅ Completed:        {completed}\n"
    )
    await callback.message.edit_text(text, reply_markup=admin_main_kb())


# ── All Deals ───────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("admin:deals:"))
async def cb_admin_deals(callback: CallbackQuery) -> None:
    if not await _check_admin(callback):
        return
    await callback.answer()

    offset = int(callback.data.split(":")[-1])
    deals = await get_all_deals(limit=10, offset=offset)
    if not deals:
        await callback.message.edit_text("No deals found.", reply_markup=admin_main_kb())
        return

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    for d in deals:
        symbol = CRYPTO_SYMBOLS.get(d.crypto, d.crypto or "?")
        label = f"#{d.deal_uid} · {d.status[:10]} · {d.total_amount or 0:.2f} {symbol}"
        builder.button(text=label, callback_data=f"admin_deal:{d.deal_uid}")
    if offset > 0:
        builder.button(text="⬅️ Prev", callback_data=f"admin:deals:{offset - 10}")
    if len(deals) == 10:
        builder.button(text="➡️ Next", callback_data=f"admin:deals:{offset + 10}")
    builder.button(text="⬅️ Back", callback_data="admin:back")
    builder.adjust(1)

    await callback.message.edit_text(
        f"📋 <b>All Deals</b> (showing {offset + 1}–{offset + len(deals)})",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data.startswith("admin_deal:"))
async def cb_admin_deal_detail(callback: CallbackQuery) -> None:
    if not await _check_admin(callback):
        return
    await callback.answer()

    uid = callback.data.split(":", 1)[1]
    deal = await get_deal_by_uid(uid)
    if not deal:
        await callback.message.edit_text("Deal not found.")
        return

    buyer  = deal.buyer.display_name  if deal.buyer  else "—"
    seller = deal.seller.display_name if deal.seller else "—"
    symbol = CRYPTO_SYMBOLS.get(deal.crypto, deal.crypto or "?") if deal.crypto else "?"

    text = (
        f"📋 <b>Deal #{uid}</b>\n\n"
        f"Status:  {deal.status}\n"
        f"Buyer:   {buyer}\n"
        f"Seller:  {seller}\n"
        f"Item:    {deal.title or '—'}\n"
        f"Amount:  {deal.amount or 0:,.4f} {symbol}\n"
        f"Fee:     {deal.fee_amount or 0:,.4f} {symbol}\n"
        f"Total:   {deal.total_amount or 0:,.4f} {symbol}\n"
        f"Network: {deal.crypto or '—'}\n"
        f"Deposit: <code>{deal.deposit_address or '—'}</code>\n"
        f"TX:      <code>{deal.tx_hash or '—'}</code>\n"
        f"Created: {deal.created_at.strftime('%Y-%m-%d %H:%M')}\n"
        + (f"Notes:   {deal.admin_notes}\n" if deal.admin_notes else "")
    )
    await callback.message.edit_text(text, reply_markup=admin_deal_kb(uid, deal.status))


# ── Admin actions on a deal ──────────────────────────────────────────────────

@router.callback_query(F.data.startswith("admin_release:"))
async def cb_admin_release(callback: CallbackQuery, bot: Bot) -> None:
    if not await _check_admin(callback):
        return
    uid = callback.data.split(":", 1)[1]
    deal = await get_deal_by_uid(uid)
    if not deal:
        await callback.answer("Deal not found.", show_alert=True)
        return
    await callback.answer("✅ Releasing funds…")

    await update_deal(deal.id, status=DealStatus.COMPLETED)
    deal = await get_deal_by_uid(uid)

    from bot.handlers.group import update_pinned
    await update_pinned(bot, deal)

    symbol = CRYPTO_SYMBOLS.get(deal.crypto, deal.crypto or "?")
    if deal.group_id:
        await bot.send_message(
            deal.group_id,
            f"✅ <b>Deal #{uid} — COMPLETED</b>\n\n"
            f"Funds of <b>{deal.amount:,.4f} {symbol}</b> have been released to the seller.\n\n"
            f"Thank you for using XCROW Escrow."
        )
    await callback.message.edit_text(f"✅ Deal {uid} marked as completed. Funds released.", reply_markup=None)


@router.callback_query(F.data.startswith("admin_refund:"))
async def cb_admin_refund(callback: CallbackQuery, bot: Bot) -> None:
    if not await _check_admin(callback):
        return
    uid = callback.data.split(":", 1)[1]
    deal = await get_deal_by_uid(uid)
    if not deal:
        await callback.answer("Deal not found.", show_alert=True)
        return
    await callback.answer("↩️ Refunding…")

    await update_deal(deal.id, status=DealStatus.REFUNDED)
    deal = await get_deal_by_uid(uid)

    from bot.handlers.group import update_pinned
    await update_pinned(bot, deal)

    if deal.group_id:
        await bot.send_message(
            deal.group_id,
            f"↩️ <b>Deal #{uid} — REFUNDED</b>\n\n"
            f"Funds have been refunded to the Buyer.\n"
            f"Support: @{settings.SUPPORT_USERNAME}"
        )
    await callback.message.edit_text(f"↩️ Deal {uid} marked as refunded.", reply_markup=None)


@router.callback_query(F.data.startswith("admin_cancel:"))
async def cb_admin_cancel_deal(callback: CallbackQuery, bot: Bot) -> None:
    if not await _check_admin(callback):
        return
    uid = callback.data.split(":", 1)[1]
    deal = await get_deal_by_uid(uid)
    if not deal:
        await callback.answer("Deal not found.", show_alert=True)
        return
    await callback.answer("❌ Cancelling…")
    await update_deal(deal.id, status=DealStatus.CANCELLED)
    deal = await get_deal_by_uid(uid)

    from bot.handlers.group import update_pinned
    await update_pinned(bot, deal)
    if deal.group_id:
        await bot.send_message(deal.group_id, f"❌ Deal <code>{uid}</code> has been cancelled by admin.")
    await callback.message.edit_text(f"❌ Deal {uid} cancelled.", reply_markup=None)


@router.callback_query(F.data.startswith("admin_note:"))
async def cb_admin_note(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _check_admin(callback):
        return
    uid = callback.data.split(":", 1)[1]
    await callback.answer()
    await state.set_state(GroupDealStates.awaiting_admin_note)
    await state.update_data(deal_uid=uid)
    await callback.message.reply(f"📝 Type your note for deal <code>{uid}</code>:")


@router.message(GroupDealStates.awaiting_admin_note)
async def msg_admin_note(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    uid = data.get("deal_uid")
    if not uid:
        return
    deal = await get_deal_by_uid(uid)
    if not deal:
        await message.reply("Deal not found.")
        await state.clear()
        return
    await update_deal(deal.id, admin_notes=message.text.strip())
    await state.clear()
    await message.reply(f"✅ Note saved for deal <code>{uid}</code>.")


# ── Disputes ────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin:disputes")
async def cb_admin_disputes(callback: CallbackQuery) -> None:
    if not await _check_admin(callback):
        return
    await callback.answer()

    disputes = await get_open_disputes()
    if not disputes:
        await callback.message.edit_text("✅ No open disputes.", reply_markup=admin_main_kb())
        return

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    for d in disputes:
        label = f"#{d.deal.deal_uid} — {d.reason[:30]}"
        builder.button(text=label, callback_data=f"admin_dispute_view:{d.id}:{d.deal.deal_uid}")
    builder.button(text="⬅️ Back", callback_data="admin:back")
    builder.adjust(1)

    await callback.message.edit_text(
        f"⚠️ <b>Open Disputes ({len(disputes)})</b>",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data.startswith("admin_dispute_view:"))
async def cb_admin_dispute_view(callback: CallbackQuery) -> None:
    if not await _check_admin(callback):
        return
    _, dispute_id, uid = callback.data.split(":", 2)
    deal = await get_deal_by_uid(uid)
    if not deal:
        await callback.answer("Deal not found.", show_alert=True)
        return
    await callback.answer()

    buyer  = deal.buyer.display_name  if deal.buyer  else "—"
    seller = deal.seller.display_name if deal.seller else "—"
    text = (
        f"⚠️ <b>Dispute — Deal #{uid}</b>\n\n"
        f"Buyer:  {buyer}\n"
        f"Seller: {seller}\n\n"
        f"Choose action:"
    )
    await callback.message.edit_text(
        text,
        reply_markup=admin_disputes_kb(int(dispute_id), uid),
    )


@router.callback_query(F.data.startswith("admin_dispute_release:"))
async def cb_dispute_release(callback: CallbackQuery, bot: Bot) -> None:
    if not await _check_admin(callback):
        return
    _, dispute_id, uid = callback.data.split(":", 2)
    deal = await get_deal_by_uid(uid)
    if not deal:
        await callback.answer("Not found.", show_alert=True)
        return
    await callback.answer("✅ Releasing to seller…")
    await resolve_dispute(int(dispute_id), callback.from_user.id, "Released to seller by admin.")
    await update_deal(deal.id, status=DealStatus.COMPLETED)
    deal = await get_deal_by_uid(uid)
    from bot.handlers.group import update_pinned
    await update_pinned(bot, deal)
    if deal.group_id:
        await bot.send_message(deal.group_id, f"⚖️ Dispute resolved. Funds released to Seller.")
    await callback.message.edit_text(f"✅ Dispute resolved. Funds released to seller.")


@router.callback_query(F.data.startswith("admin_dispute_refund:"))
async def cb_dispute_refund(callback: CallbackQuery, bot: Bot) -> None:
    if not await _check_admin(callback):
        return
    _, dispute_id, uid = callback.data.split(":", 2)
    deal = await get_deal_by_uid(uid)
    if not deal:
        await callback.answer("Not found.", show_alert=True)
        return
    await callback.answer("↩️ Refunding to buyer…")
    await resolve_dispute(int(dispute_id), callback.from_user.id, "Refunded to buyer by admin.")
    await update_deal(deal.id, status=DealStatus.REFUNDED)
    deal = await get_deal_by_uid(uid)
    from bot.handlers.group import update_pinned
    await update_pinned(bot, deal)
    if deal.group_id:
        await bot.send_message(deal.group_id, f"⚖️ Dispute resolved. Funds refunded to Buyer.")
    await callback.message.edit_text(f"↩️ Dispute resolved. Funds refunded to buyer.")


# ── Users ────────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("admin:users:"))
async def cb_admin_users(callback: CallbackQuery) -> None:
    if not await _check_admin(callback):
        return
    await callback.answer()
    offset = int(callback.data.split(":")[-1])
    users = await get_all_users(limit=10, offset=offset)

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    for u in users:
        name = u.username or u.first_name or str(u.telegram_id)
        banned = " 🚫" if u.is_banned else ""
        builder.button(text=f"{name}{banned}", callback_data=f"admin_user:{u.telegram_id}")
    if offset > 0:
        builder.button(text="⬅️ Prev", callback_data=f"admin:users:{offset - 10}")
    if len(users) == 10:
        builder.button(text="➡️ Next", callback_data=f"admin:users:{offset + 10}")
    builder.button(text="⬅️ Back", callback_data="admin:back")
    builder.adjust(1)

    await callback.message.edit_text(
        f"👥 <b>Users</b> (showing {offset + 1}–{offset + len(users)})",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data.startswith("admin_user:"))
async def cb_admin_user(callback: CallbackQuery) -> None:
    if not await _check_admin(callback):
        return
    await callback.answer()
    uid = int(callback.data.split(":", 1)[1])
    user = await get_user(uid)
    if not user:
        await callback.message.edit_text("User not found.")
        return
    text = (
        f"👤 <b>User: {user.display_name}</b>\n\n"
        f"ID:      <code>{user.telegram_id}</code>\n"
        f"Banned:  {'Yes 🚫' if user.is_banned else 'No ✅'}\n"
        f"Admin:   {'Yes 👑' if user.is_admin else 'No'}\n"
        f"Joined:  {user.created_at.strftime('%Y-%m-%d')}\n"
        + (f"Notes:   {user.notes}\n" if user.notes else "")
    )
    await callback.message.edit_text(text, reply_markup=admin_user_kb(uid, user.is_banned))


@router.callback_query(F.data.startswith("admin_ban:"))
async def cb_admin_ban(callback: CallbackQuery) -> None:
    if not await _check_admin(callback):
        return
    uid = int(callback.data.split(":", 1)[1])
    await set_user_banned(uid, True)
    await callback.answer("🚫 User banned.")
    await callback.message.edit_text(f"🚫 User <code>{uid}</code> has been banned.", reply_markup=None)


@router.callback_query(F.data.startswith("admin_unban:"))
async def cb_admin_unban(callback: CallbackQuery) -> None:
    if not await _check_admin(callback):
        return
    uid = int(callback.data.split(":", 1)[1])
    await set_user_banned(uid, False)
    await callback.answer("✅ User unbanned.")
    await callback.message.edit_text(f"✅ User <code>{uid}</code> has been unbanned.", reply_markup=None)


# ── Back ──────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin:back")
async def cb_admin_back(callback: CallbackQuery) -> None:
    if not await _check_admin(callback):
        return
    await callback.answer()
    await callback.message.edit_text("👑 <b>XCROW Admin Panel</b>", reply_markup=admin_main_kb())
