"""
Central payment monitor — watches the main escrow wallet across all networks.

Instead of deriving per-deal deposit addresses, all payments go to one wallet.
Each deal has a unique total_amount (random cents added) so we can match
incoming transactions to the correct deal automatically.

Networks monitored:
  • USDT BEP20 (BSC)        — 0xB79fdeaCc172846a7BE52fdd04E8491424304d37
  • USDT ERC20 (Ethereum)   — 0xB79fdeaCc172846a7BE52fdd04E8491424304d37
  • ETH native (Ethereum)   — 0xB79fdeaCc172846a7BE52fdd04E8491424304d37
  • BTC (Bitcoin)           — bc1qkda0dmyde93v72kd0ant00kpf2d3d99h5w9d78

Auto-reconnects with exponential backoff on any failure.
"""
from __future__ import annotations
import asyncio
from loguru import logger
from aiogram import Bot

from config import settings
from database.models import CryptoNetwork
from database.crud import (
    get_active_deals_for_monitoring, tx_hash_exists,
    find_deal_by_amount, get_setting,
)

# ── Per-network last-scanned block (in-memory; resets on restart, safe) ────
_LAST_BSC_BLOCK: int = 0
_LAST_ETH_BLOCK: int = 0
_SEEN_BTC_TX: set[str] = set()          # BTC has no blocks in our flow — track seen txids


class CentralMonitor:
    """Polls the main wallet on all networks every MONITOR_INTERVAL_SECONDS."""

    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self._errors = 0

    async def run(self) -> None:
        logger.info(
            f"🔍 Central wallet monitor started "
            f"(interval: {settings.MONITOR_INTERVAL_SECONDS}s)"
        )
        while True:
            try:
                await self._tick()
                self._errors = 0
            except Exception as exc:
                self._errors += 1
                wait = min(120, 10 * self._errors)
                logger.error(
                    f"Monitor error (#{self._errors}): {exc}. "
                    f"Retrying in {wait}s."
                )
                await asyncio.sleep(wait)
                continue
            await asyncio.sleep(settings.MONITOR_INTERVAL_SECONDS)

    async def _tick(self) -> None:
        deals = await get_active_deals_for_monitoring()
        if not deals:
            logger.debug("Central monitor: no active deals — idle.")
            return

        logger.debug(f"Central monitor: {len(deals)} pending deal(s) — scanning wallets…")

        # Read wallet addresses from DB settings (allows admin to change them)
        bsc_eth_wallet = await get_setting("main_wallet_bsc_eth", settings.MAIN_WALLET_BSC_ETH)
        btc_wallet     = await get_setting("main_wallet_btc",     settings.MAIN_WALLET_BTC)
        required_confs = int(await get_setting("required_confirmations", str(settings.CONFIRMATION_BLOCKS)))

        results = await asyncio.gather(
            _scan_bsc_usdt(bsc_eth_wallet, deals, self.bot, required_confs),
            _scan_eth_usdt(bsc_eth_wallet, deals, self.bot, required_confs),
            _scan_eth_native(bsc_eth_wallet, deals, self.bot, required_confs),
            _scan_btc(btc_wallet, deals, self.bot, required_confs),
            return_exceptions=True,
        )
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                net = ["BSC-USDT", "ETH-USDT", "ETH-native", "BTC"][i]
                logger.warning(f"Central monitor [{net}] error: {r}")


# ── BSC USDT BEP20 ─────────────────────────────────────────────────────────

async def _scan_bsc_usdt(wallet: str, deals: list, bot: Bot, required_confs: int) -> None:
    global _LAST_BSC_BLOCK
    if not any(d.crypto == CryptoNetwork.USDT_BEP20 for d in deals):
        return

    import aiohttp
    USDT_CONTRACT   = "0x55d398326f99059fF775485246999027B3197955"
    TRANSFER_TOPIC  = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
    DECIMALS        = 18
    RPCS = [
        "https://bsc-rpc.publicnode.com",
        "https://bsc.publicnode.com",
        "https://1rpc.io/bnb",
        "https://binance.llamarpc.com",
        "https://bsc-dataseed1.binance.org/",
        "https://bsc-dataseed2.binance.org/",
    ]

    async def _rpc(method, params):
        async with aiohttp.ClientSession() as s:
            for url in RPCS:
                try:
                    async with s.post(url, json={"jsonrpc":"2.0","id":1,"method":method,"params":params},
                                      timeout=aiohttp.ClientTimeout(total=12)) as r:
                        data = await r.json(content_type=None)
                    if "error" not in data:
                        return data["result"]
                except Exception as e:
                    logger.debug(f"BSC RPC {url}: {e}")
        raise RuntimeError("All BSC RPCs failed")

    latest = int(await _rpc("eth_blockNumber", []), 16)
    from_block = max(0, latest - 2000) if _LAST_BSC_BLOCK == 0 else _LAST_BSC_BLOCK
    _LAST_BSC_BLOCK = latest

    padded_to = "0x" + "0" * 24 + wallet.lower().removeprefix("0x")

    async with aiohttp.ClientSession() as session:
        for url in RPCS:
            try:
                async with session.post(url, json={"jsonrpc":"2.0","id":1,"method":"eth_getLogs","params":[{
                    "fromBlock": hex(from_block),
                    "toBlock":   hex(latest),
                    "address":   USDT_CONTRACT,
                    "topics":    [TRANSFER_TOPIC, None, padded_to],
                }]}, timeout=aiohttp.ClientTimeout(total=15)) as r:
                    data = await r.json(content_type=None)
                if "error" in data:
                    continue
                logs = data.get("result") or []
                break
            except Exception as e:
                logger.debug(f"BSC getLogs {url}: {e}")
                logs = []

    for log in logs:
        tx_hash = log.get("transactionHash", "")
        if not tx_hash or await tx_hash_exists(tx_hash):
            continue

        raw    = int(log.get("data", "0x0"), 16)
        amount = raw / (10 ** DECIMALS)
        block  = int(log.get("blockNumber", "0x0"), 16)
        confs  = latest - block

        topics   = log.get("topics") or []
        from_raw = topics[1] if len(topics) > 1 else "0x"
        from_addr = "0x" + from_raw[-40:]

        logger.info(
            f"📥 BSC USDT: {amount:.6f} USDT from {from_addr[:12]}… "
            f"tx {tx_hash[:14]}… confs={confs}"
        )

        if confs < required_confs:
            logger.debug(f"BSC USDT {tx_hash[:10]}: only {confs}/{required_confs} confs — waiting")
            continue

        deal = await find_deal_by_amount(amount, CryptoNetwork.USDT_BEP20)
        if not deal:
            logger.debug(f"BSC USDT {amount:.6f}: no matching pending deal")
            continue

        from bot.handlers.group import on_payment_confirmed
        await on_payment_confirmed(bot, deal, tx_hash, amount, from_addr=from_addr, confirmations=confs)


# ── ETH USDT ERC20 ─────────────────────────────────────────────────────────

async def _scan_eth_usdt(wallet: str, deals: list, bot: Bot, required_confs: int) -> None:
    global _LAST_ETH_BLOCK
    if not any(d.crypto == CryptoNetwork.USDT_ERC20 for d in deals):
        return

    import aiohttp
    USDT_CONTRACT  = "0xdAC17F958D2ee523a2206206994597C13D831ec7"
    TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
    DECIMALS       = 6   # USDT ERC20 uses 6 decimals on Ethereum mainnet
    RPCS = [
        "https://eth.publicnode.com",
        "https://1rpc.io/eth",
        "https://ethereum.publicnode.com",
        "https://rpc.ankr.com/eth",
    ]

    async def _rpc(method, params):
        async with aiohttp.ClientSession() as s:
            for url in RPCS:
                try:
                    async with s.post(url, json={"jsonrpc":"2.0","id":1,"method":method,"params":params},
                                      timeout=aiohttp.ClientTimeout(total=15)) as r:
                        data = await r.json(content_type=None)
                    if "error" not in data:
                        return data["result"]
                except Exception as e:
                    logger.debug(f"ETH RPC {url}: {e}")
        raise RuntimeError("All ETH RPCs failed")

    latest = int(await _rpc("eth_blockNumber", []), 16)
    from_block = max(0, latest - 500) if _LAST_ETH_BLOCK == 0 else _LAST_ETH_BLOCK
    _LAST_ETH_BLOCK = latest

    padded_to = "0x" + "0" * 24 + wallet.lower().removeprefix("0x")

    async with aiohttp.ClientSession() as session:
        for url in RPCS:
            try:
                async with session.post(url, json={"jsonrpc":"2.0","id":1,"method":"eth_getLogs","params":[{
                    "fromBlock": hex(from_block),
                    "toBlock":   hex(latest),
                    "address":   USDT_CONTRACT,
                    "topics":    [TRANSFER_TOPIC, None, padded_to],
                }]}, timeout=aiohttp.ClientTimeout(total=15)) as r:
                    data = await r.json(content_type=None)
                if "error" in data:
                    continue
                logs = data.get("result") or []
                break
            except Exception as e:
                logger.debug(f"ETH getLogs {url}: {e}")
                logs = []

    for log in logs:
        tx_hash = log.get("transactionHash", "")
        if not tx_hash or await tx_hash_exists(tx_hash):
            continue

        raw    = int(log.get("data", "0x0"), 16)
        amount = raw / (10 ** DECIMALS)
        block  = int(log.get("blockNumber", "0x0"), 16)
        confs  = latest - block

        topics   = log.get("topics") or []
        from_raw = topics[1] if len(topics) > 1 else "0x"
        from_addr = "0x" + from_raw[-40:]

        logger.info(f"📥 ETH USDT: {amount:.6f} USDT tx {tx_hash[:14]}… confs={confs}")

        if confs < required_confs:
            continue

        deal = await find_deal_by_amount(amount, CryptoNetwork.USDT_ERC20)
        if not deal:
            logger.debug(f"ETH USDT {amount:.6f}: no matching pending deal")
            continue

        from bot.handlers.group import on_payment_confirmed
        await on_payment_confirmed(bot, deal, tx_hash, amount, from_addr=from_addr, confirmations=confs)


# ── ETH native ─────────────────────────────────────────────────────────────

async def _scan_eth_native(wallet: str, deals: list, bot: Bot, required_confs: int) -> None:
    if not any(d.crypto == CryptoNetwork.ETH for d in deals):
        return
    # ETH native deposits are detected via Etherscan API or block scanning.
    # For simplicity we use the existing check_eth_deposit per deal here.
    from services.blockchain.eth import check_eth_deposit
    from bot.handlers.group import on_payment_confirmed

    eth_deals = [d for d in deals if d.crypto == CryptoNetwork.ETH]
    for deal in eth_deals:
        try:
            result = await check_eth_deposit(wallet, deal.total_amount or 0)
            if not result:
                continue
            tx_hash = result.get("tx_hash", "")
            if not tx_hash or await tx_hash_exists(tx_hash):
                continue
            amount   = result.get("amount", 0.0)
            from_addr = result.get("from", "")
            logger.info(f"📥 ETH native: {amount:.6f} ETH for deal {deal.deal_uid}")
            await on_payment_confirmed(bot, deal, tx_hash, amount, from_addr=from_addr, confirmations=required_confs)
        except Exception as exc:
            logger.warning(f"ETH native check error for {deal.deal_uid}: {exc}")


# ── BTC ────────────────────────────────────────────────────────────────────

async def _scan_btc(wallet: str, deals: list, bot: Bot, required_confs: int) -> None:
    global _SEEN_BTC_TX
    if not wallet or not any(d.crypto == CryptoNetwork.BTC for d in deals):
        return

    import aiohttp
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://blockstream.info/api/address/{wallet}/txs",
                timeout=aiohttp.ClientTimeout(total=20)
            ) as r:
                txs = await r.json(content_type=None)
    except Exception as exc:
        logger.warning(f"BTC monitor: Blockstream API error: {exc}")
        return

    for tx in (txs or []):
        txid  = tx.get("txid", "")
        confs = tx.get("status", {}).get("block_height", None)
        if not txid or txid in _SEEN_BTC_TX:
            continue
        if await tx_hash_exists(txid):
            _SEEN_BTC_TX.add(txid)
            continue

        # Count confirmations — if unconfirmed, block_height is None
        confirmed_at = tx.get("status", {}).get("block_height")
        if confirmed_at is None:
            continue  # unconfirmed mempool tx

        # Get tip height to compute confirmations
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get("https://blockstream.info/api/blocks/tip/height",
                                  timeout=aiohttp.ClientTimeout(total=10)) as r:
                    tip = int(await r.text())
            tx_confs = tip - confirmed_at + 1
        except Exception:
            tx_confs = 1

        if tx_confs < required_confs:
            logger.debug(f"BTC {txid[:10]}: {tx_confs}/{required_confs} confs — waiting")
            continue

        # Sum outputs to our wallet
        total_received = 0.0
        for vout in tx.get("vout", []):
            addr = vout.get("scriptpubkey_address", "")
            if addr.lower() == wallet.lower():
                total_received += vout.get("value", 0) / 1e8  # satoshis → BTC

        if total_received <= 0:
            continue

        # Get sender address from first input (best effort)
        from_addr = ""
        for vin in tx.get("vin", []):
            prev = vin.get("prevout", {})
            from_addr = prev.get("scriptpubkey_address", "")
            if from_addr:
                break

        logger.info(f"📥 BTC: {total_received:.8f} BTC txid {txid[:14]}… confs={tx_confs}")

        deal = await find_deal_by_amount(total_received, CryptoNetwork.BTC, tolerance=0.0001)
        if not deal:
            logger.debug(f"BTC {total_received:.8f}: no matching pending deal")
            _SEEN_BTC_TX.add(txid)
            continue

        from bot.handlers.group import on_payment_confirmed
        await on_payment_confirmed(bot, deal, txid, total_received, from_addr=from_addr, confirmations=tx_confs)
        _SEEN_BTC_TX.add(txid)
