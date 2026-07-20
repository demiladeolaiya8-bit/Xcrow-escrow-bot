"""Bot and Dispatcher singletons."""
from __future__ import annotations
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from config import settings

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


def create_dispatcher() -> Dispatcher:
    global _dp
    if _dp is None:
        storage = MemoryStorage()
        _dp = Dispatcher(storage=storage)

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
