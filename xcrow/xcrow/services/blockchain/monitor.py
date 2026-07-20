"""
Blockchain monitor — polls all active deals every N seconds
and notifies the group when a deposit is confirmed.
"""
from __future__ import annotations
import asyncio
from loguru import logger
from aiogram import Bot

from config import settings
from database.models import CryptoNetwork, AUTO_MONITOR_NETWORKS
from database.crud import get_active_deals_for_monitoring, tx_hash_exists


class BlockchainMonitor:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def run(self) -> None:
        logger.info(f"🔍 Blockchain monitor started (interval: {settings.MONITOR_INTERVAL_SECONDS}s)")
        while True:
            try:
                await self._check_all_deals()
            except Exception as e:
                logger.error(f"Monitor loop error: {e}")
            await asyncio.sleep(settings.MONITOR_INTERVAL_SECONDS)

    async def _check_all_deals(self) -> None:
        deals = await get_active_deals_for_monitoring()
        if not deals:
            return
        logger.debug(f"Monitoring {len(deals)} active deal(s)…")
        for deal in deals:
            try:
                await check_deal_once(deal, self.bot)
            except Exception as e:
                logger.error(f"Error checking deal {deal.deal_uid}: {e}")


async def check_deal_once(deal, bot: Bot) -> bool:
    """
    Check a single deal for a confirmed deposit.
    Returns True if payment was found.
    """
    if not deal.deposit_address or not deal.crypto:
        return False

    network = deal.crypto
    if network not in AUTO_MONITOR_NETWORKS:
        return False  # Manual confirmation only for BTC/SOL/TON/LTC

    result = None
    amount_to_check = deal.total_amount or 0

    if network == CryptoNetwork.USDT_TRC20:
        from services.blockchain.trc20 import check_trc20_deposit
        result = await check_trc20_deposit(deal.deposit_address, amount_to_check)

    elif network == CryptoNetwork.USDT_BEP20:
        from services.blockchain.bep20 import check_bep20_deposit
        result = await check_bep20_deposit(deal.deposit_address, amount_to_check)

    elif network == CryptoNetwork.ETH:
        from services.blockchain.eth import check_eth_deposit
        result = await check_eth_deposit(deal.deposit_address, amount_to_check)

    if not result:
        return False

    tx_hash = result.get("tx_hash", "")
    amount  = result.get("amount", 0.0)

    # Prevent duplicate processing
    if tx_hash and await tx_hash_exists(tx_hash):
        return False

    logger.info(f"💰 Payment detected for deal {deal.deal_uid}: {amount} ({network}) TX:{tx_hash}")

    from bot.handlers.group import on_payment_confirmed
    await on_payment_confirmed(bot, deal, tx_hash, amount)
    return True
