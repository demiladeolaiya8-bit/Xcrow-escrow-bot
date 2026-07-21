"""
TRC20 USDT deposit detector.

Strategy (in order):
1. TronGrid API    — if TRONGRID_API_KEY is set (most reliable)
2. Tronscan API    — free public API, no key required (fallback)
"""
from __future__ import annotations
import aiohttp
from loguru import logger
from config import settings

USDT_TRC20_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
TRON_DECIMALS = 6


def _parse_trongrid_txs(transactions: list, address: str, min_amount: float) -> dict | None:
    for tx in transactions:
        try:
            to_addr = tx.get("to", "")
            if to_addr.lower() != address.lower():
                continue
            raw_value = int(tx.get("value", "0"))
            amount = raw_value / (10 ** TRON_DECIMALS)
            if amount >= min_amount * 0.99:
                return {
                    "tx_hash": tx.get("transaction_id", ""),
                    "amount":  amount,
                    "from":    tx.get("from", ""),
                    "network": "USDT_TRC20",
                }
        except Exception:
            continue
    return None


async def _check_via_trongrid(address: str, min_amount: float) -> dict | None:
    url = f"https://api.trongrid.io/v1/accounts/{address}/transactions/trc20"
    params = {
        "limit":            20,
        "contract_address": USDT_TRC20_CONTRACT,
        "only_to":          "true",
    }
    headers = {"TRON-PRO-API-KEY": settings.TRONGRID_API_KEY}
    async with aiohttp.ClientSession() as session:
        async with session.get(
            url, params=params, headers=headers,
            timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            if resp.status != 200:
                raise RuntimeError(f"TronGrid returned HTTP {resp.status}")
            data = await resp.json(content_type=None)
    return _parse_trongrid_txs(data.get("data", []), address, min_amount)


async def _check_via_tronscan(address: str, min_amount: float) -> dict | None:
    """Public Tronscan API — no API key needed."""
    url = "https://apilist.tronscanapi.com/api/token_trc20/transfers"
    params = {
        "limit":            20,
        "start":            0,
        "toAddress":        address,
        "token":            USDT_TRC20_CONTRACT,
        "filterTokenValue": 0,
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(
            url, params=params,
            timeout=aiohttp.ClientTimeout(total=20)
        ) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Tronscan returned HTTP {resp.status}")
            data = await resp.json(content_type=None)

    for tx in data.get("token_transfers", []):
        try:
            to_addr = tx.get("toAddress", "")
            if to_addr.lower() != address.lower():
                continue
            raw_value = int(tx.get("quant", "0"))
            amount = raw_value / (10 ** TRON_DECIMALS)
            if amount >= min_amount * 0.99:
                return {
                    "tx_hash": tx.get("transactionHash", ""),
                    "amount":  amount,
                    "from":    tx.get("transferFromAddress", ""),
                    "network": "USDT_TRC20",
                }
        except Exception:
            continue
    return None


async def check_trc20_deposit(address: str, min_amount: float) -> dict | None:
    """
    Detect a USDT TRC20 deposit to `address` of at least `min_amount`.

    Uses TronGrid if TRONGRID_API_KEY is set, otherwise falls back to
    the public Tronscan API (no key required).
    """
    # Primary: TronGrid
    if settings.TRONGRID_API_KEY:
        try:
            result = await _check_via_trongrid(address, min_amount)
            if result:
                logger.info(
                    f"TRC20 USDT confirmed (TronGrid): {result['amount']:.6f} "
                    f"at {address} tx {result['tx_hash'][:16]}…"
                )
            return result
        except Exception as exc:
            logger.warning(f"TronGrid failed ({exc}), trying Tronscan fallback…")

    # Fallback: Tronscan public API
    try:
        result = await _check_via_tronscan(address, min_amount)
        if result:
            logger.info(
                f"TRC20 USDT confirmed (Tronscan): {result['amount']:.6f} "
                f"at {address} tx {result['tx_hash'][:16]}…"
            )
        return result
    except Exception as exc:
        logger.error(f"check_trc20_deposit error for {address}: {exc}")
        return None
