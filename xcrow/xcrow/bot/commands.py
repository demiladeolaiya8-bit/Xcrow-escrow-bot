"""Register slash commands with Telegram (shown in the command menu)."""
from aiogram import Bot
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats


COMMANDS = [
    BotCommand(command="start",       description="Welcome & create escrow group"),
    BotCommand(command="create",      description="Create a new escrow group"),
    BotCommand(command="history",     description="View your escrow history"),
    BotCommand(command="wallet",      description="View your payout wallets"),
    BotCommand(command="new_wallet",  description="Add a payout wallet"),
    BotCommand(command="calculate",   description="Calculate escrow fees"),
    BotCommand(command="escrow_fee",  description="View fee structure"),
    BotCommand(command="verify",      description="Verify a deal or wallet"),
    BotCommand(command="feedback",    description="Report a bug or request a feature"),
    BotCommand(command="support",     description="Contact support"),
    BotCommand(command="menu",        description="Show all commands"),
]


async def set_bot_commands(bot: Bot) -> None:
    await bot.set_my_commands(COMMANDS, scope=BotCommandScopeAllPrivateChats())
