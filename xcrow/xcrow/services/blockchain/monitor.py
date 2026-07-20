"""
Blockchain monitor — polls all active deals every N seconds.
Tracks last_checked_block per deal to avoid rescanning from genesis.
Auto-reconnects with exponential backoff on network failures.
"""
from __future__ import annotations
import asyncio
from loguru import logger
from aiogram import Bot

from config import settings
from database.models import CryptoNetwork, AUTO_MONITOR_NETWORKS
from database.crud import get_active_deals_for_monitoring, tx_hash_exists, update_deal


class BlockchainMonitor:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self._consecutive_errors = 0

    async def run(self) -> None:
        logger.info(f"🔍 Blockchain monitor started (interval: {settings.MONITOR_INTERVAL_SECONDS}s)")
        while True:
            try:
                await self._check_all_deals()
                self._consecutive_errors = 0
            except Exception as e:
                self._consecutive_errors += 1
                wait = min(60, 5 * self._consecutive_errors)
                logger.error(f"Monitor loop error (#{self._consecutive_errors}): {e}. Retrying in {wait}s.")
                await asyncio.sleep(wait)
                continue
            await asyncio.sleep(settings.MONITOR_INTERVAL_SECONDS)

    async def _check_all_deals(self) -> None:
        deals = await get_active_deals_for_monitoring()
        if not deals:
            return
        logger.debug(f"Monitoring {len(deals)} active deal(s)…")
        tasks = [check_deal_once(deal, self.bot) for deal in deals]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for deal, result in zip(deals, results):
            if isinstance(result, Exception):
                logger.error(f"Error checking deal {deal.deal_uid}: {result}")


async def check_deal_once(deal, bot: Bot) -> bool:
    """Check one deal for a confirmed deposit. Returns True if payment processed."""
    if not deal.deposit_address or not deal.crypto:
        return False

    network = deal.crypto
    if network not in AUTO_MONITOR_NETWORKS:
        return False  # BTC/SOL/TON/LTC require manual confirmation

    result = None
    amount_to_check = deal.total_amount or 0
    start_block = deal.last_checked_block or 0

    if network == CryptoNetwork.USDT_TRC20:
        from services.blockchain.trc20 import check_trc20_deposit
        result = await check_trc20_deposit(deal.deposit_address, amount_to_check)

    elif network == CryptoNetwork.USDT_BEP20:
        from services.blockchain.bep20 import check_bep20_deposit, get_current_block
        result = await check_bep20_deposit(deal.deposit_address, amount_to_check, start_block)
        # Advance the block cursor so next poll only scans new blocks
        try:
            current = await get_current_block()
            if current > start_block:
                await update_deal(deal.id, last_checked_block=current)
        except Exception:
            pass

    elif network == CryptoNetwork.ETH:
        from services.blockchain.eth import check_eth_deposit
        result = await check_eth_deposit(deal.deposit_address, amount_to_check)

    if not result:
        return False

    tx_hash = result.get("tx_hash", "")
    amount  = result.get("amount", 0.0)

    if tx_hash and await tx_hash_exists(tx_hash):
        return False  # Already processed

    logger.info(f"💰 Payment detected for deal {deal.deal_uid}: {amount} ({network}) TX:{tx_hash}")

    from bot.handlers.group import on_payment_confirmed
    await on_payment_confirmed(bot, deal, tx_hash, amount)
    return True
