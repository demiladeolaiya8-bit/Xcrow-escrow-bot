"""
Pyrogram-based group creation service.
Creates a Telegram supergroup, adds the bot as admin, and returns the invite link.
"""
from __future__ import annotations
import asyncio
from loguru import logger
from config import settings

_pyrogram_client = None


async def _get_client():
    """Return (and start if needed) the Pyrogram user client."""
    global _pyrogram_client
    if _pyrogram_client is not None:
        return _pyrogram_client

    if not settings.pyrogram_configured:
        raise RuntimeError("Pyrogram not configured. Set API_ID, API_HASH, PHONE_NUMBER in .env")

    from pyrogram import Client
    client = Client(
        settings.SESSION_NAME,
        api_id=int(settings.API_ID),
        api_hash=settings.API_HASH,
    )
    await client.start()
    _pyrogram_client = client
    logger.info("✅  Pyrogram client started")
    return client


async def start_pyrogram() -> bool:
    """Called at startup to pre-connect the Pyrogram client. Returns True on success."""
    if not settings.pyrogram_configured:
        logger.warning("Pyrogram not configured — auto group creation disabled")
        return False
    try:
        await _get_client()
        return True
    except Exception as e:
        logger.error(f"Pyrogram startup failed: {e}")
        logger.error("Run  python pyrogram_auth.py  once on your server to create a session file")
        return False


async def stop_pyrogram() -> None:
    global _pyrogram_client
    if _pyrogram_client:
        try:
            await _pyrogram_client.stop()
        except Exception:
            pass
        _pyrogram_client = None


async def create_and_setup_group(deal, bot) -> tuple[int, str]:
    """
    1. Create a Telegram supergroup
    2. Add the bot as admin
    3. Pin the initial status message
    4. Return (group_id, invite_link)
    """
    from database.crud import update_deal
    from database.models import DealStatus
    from bot.handlers.group import format_pinned
    from bot.keyboards.kb import register_seller_kb

    client = await _get_client()
    uid = deal.deal_uid

    # Create the supergroup
    logger.info(f"Creating Telegram group for deal {uid}…")
    chat = await client.create_supergroup(f"🔐 Xcrow · {uid}")
    group_id = chat.id
    logger.info(f"Group created: {group_id}")

    # Add bot to the group
    try:
        await client.add_chat_members(group_id, settings.BOT_USERNAME)
        logger.info(f"Bot @{settings.BOT_USERNAME} added to group {group_id}")
    except Exception as e:
        logger.warning(f"Could not add bot to group: {e}")

    # Give bot admin rights (pin messages, delete messages)
    await asyncio.sleep(1)  # brief pause so Telegram registers the bot
    try:
        await client.promote_chat_member(
            group_id,
            settings.BOT_USERNAME,
            can_manage_chat=True,
            can_pin_messages=True,
            can_delete_messages=True,
            can_invite_users=True,
        )
        logger.info(f"Bot promoted to admin in group {group_id}")
    except Exception as e:
        logger.warning(f"Could not promote bot to admin: {e}")

    # Create invite link
    link_obj = await client.create_chat_invite_link(group_id)
    invite_link = link_obj.invite_link
    logger.info(f"Invite link: {invite_link}")

    # Update deal in DB with group_id
    await update_deal(deal.id, group_id=group_id, status=DealStatus.STEP1_PENDING)

    # Reload deal so format_pinned has the right data
    from database.crud import get_deal_by_id
    updated_deal = await get_deal_by_id(deal.id)

    # Bot sends the pinned status message
    await asyncio.sleep(1)
    try:
        pinned_msg = await bot.send_message(group_id, format_pinned(updated_deal))
        await bot.pin_chat_message(group_id, pinned_msg.message_id, disable_notification=True)
        await update_deal(deal.id, pinned_msg_id=pinned_msg.message_id)
        updated_deal = await get_deal_by_id(deal.id)
    except Exception as e:
        logger.warning(f"Could not send/pin initial message: {e}")

    # Send Step 1 prompt
    try:
        await bot.send_message(
            group_id,
            f"<b>Step 1 of 5 — Register Seller</b>\n\n"
            f"Deal: <code>{uid}</code>\n\n"
            f"Who is the <b>Seller</b>? (the person receiving payment)\n\n"
            f"Tap the button below to register as Seller.",
            reply_markup=register_seller_kb(uid),
        )
    except Exception as e:
        logger.warning(f"Could not send Step 1 message: {e}")

    return group_id, invite_link
