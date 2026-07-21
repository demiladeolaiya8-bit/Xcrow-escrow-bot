"""
Auto-register every user on first contact.
Block banned users silently.
"""
from __future__ import annotations
from typing import Any, Awaitable, Callable
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject
from database.crud import get_or_create_user


class AuthMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = None

        if isinstance(event, Message) and event.from_user:
            user = event.from_user
        elif isinstance(event, CallbackQuery) and event.from_user:
            user = event.from_user

        if user:
            db_user = await get_or_create_user(
                telegram_id=user.id,
                username=user.username,
                first_name=user.first_name,
            )
            # Block banned users
            if db_user.is_banned:
                return
            data["db_user"] = db_user

        return await handler(event, data)
