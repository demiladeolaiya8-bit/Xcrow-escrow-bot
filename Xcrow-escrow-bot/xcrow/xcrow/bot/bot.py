"""Bot and Dispatcher singletons."""
from __future__ import annotations
import logging
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, ErrorEvent
from aiogram.exceptions import TelegramBadRequest
from config import settings

logger = logging.getLogger(__name__)


async def safe_answer(callback: CallbackQuery, text: str = "", show_alert: bool = False) -> None:
    """Answer a callback query, silently ignoring 'query is too old' errors."""
    try:
        await callback.answer(text, show_alert=show_alert)
    except TelegramBadRequest as e:
        if "query is too old" in str(e) or "query ID is invalid" in str(e):
            logger.debug(f"Stale callback query ignored: {e}")
        else:
            raise

_bot: Bot | None = None
_dp: Dispatcher | None = None


def create_bot() -> Bot:
    global _bot
    if _bot is None:
        _bot = Bot(
            token=settings.BOT_TOKEN,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
    return _bot


async def _stale_query_error_handler(event: ErrorEvent) -> bool:
    """Silently drop 'query is too old' errors so they don't spam logs."""
    exc = event.exception
    if isinstance(exc, TelegramBadRequest):
        msg = str(exc)
        if "query is too old" in msg or "query ID is invalid" in msg:
            logger.debug(f"Dropped stale callback query: {msg}")
            return True   # mark as handled
    return False          # re-raise everything else


def create_dispatcher() -> Dispatcher:
    global _dp
    if _dp is None:
        storage = MemoryStorage()
        _dp = Dispatcher(storage=storage)

        # Swallow stale callback-query errors caused by bot restarts
        _dp.errors.register(_stale_query_error_handler)

        # Register middlewares
        from bot.middlewares.auth import AuthMiddleware
        _dp.message.middleware(AuthMiddleware())
        _dp.callback_query.middleware(AuthMiddleware())

        # Register routers
        from bot.handlers.start import router as start_router
        from bot.handlers.group import router as group_router
        from bot.handlers.admin import router as admin_router
        _dp.include_router(start_router)
        _dp.include_router(group_router)
        _dp.include_router(admin_router)

    return _dp


def get_bot() -> Bot:
    if _bot is None:
        raise RuntimeError("Bot not initialised — call create_bot() first")
    return _bot
