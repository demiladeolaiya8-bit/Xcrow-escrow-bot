"""Xcrow — entry point. Starts bot + blockchain monitor + admin API."""
from __future__ import annotations
import asyncio
import os
import sys

# ── Create runtime dirs before loguru opens files ─────────────────────────
os.makedirs("logs", exist_ok=True)
os.makedirs("sessions", exist_ok=True)

from loguru import logger

logger.remove()
logger.add(
    sys.stdout,
    level="INFO",
    format="<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | {message}",
    colorize=True,
)
logger.add(
    "logs/xcrow.log",
    level="DEBUG",
    rotation="10 MB",
    retention="14 days",
    compression="gz",
    encoding="utf-8",
)

# ── Validate config immediately — fails fast with a clear message ──────────
from config import settings, validate_settings
validate_settings(settings)

import uvicorn
from database.db import init_db
from bot.bot import create_bot, create_dispatcher
from services.blockchain.monitor import BlockchainMonitor


async def main() -> None:
    logger.info("=" * 55)
    logger.info("  ⚡  XCROW Escrow Bot — starting up")
    logger.info("=" * 55)

    # Init database (creates tables if first run)
    await init_db()
    logger.info("✅  Database ready")

    # Telegram bot
    bot = create_bot()
    dp = create_dispatcher()

    # Blockchain monitor
    monitor = BlockchainMonitor(bot)

    # Admin FastAPI app
    uvicorn_config = uvicorn.Config(
        "api.app:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        log_level="warning",
        access_log=False,
    )
    api_server = uvicorn.Server(uvicorn_config)
    logger.info(f"✅  Admin API → http://{settings.API_HOST}:{settings.API_PORT}/docs")

    # Register bot commands with Telegram
    from bot.commands import set_bot_commands
    await set_bot_commands(bot)
    logger.info("✅  Bot commands registered")

    # Pre-connect Pyrogram so first group creation is instant
    from services.group_creator import start_pyrogram
    await start_pyrogram()

    logger.info("🚀  Bot is live — polling for updates")

    await asyncio.gather(
        dp.start_polling(bot, allowed_updates=[
            "message", "callback_query", "my_chat_member", "chat_member",
        ]),
        api_server.serve(),
        monitor.run(),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Xcrow stopped.")
