"""TronGrid API — detect USDT TRC20 deposits."""
from __future__ import annotations
import aiohttp
from loguru import logger
from config import settings

USDT_TRC20_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
TRON_DECIMALS = 6


async def check_trc20_deposit(address: str, min_amount: float) -> dict | None:
    """
    Poll TronGrid for incoming USDT TRC20 transfers to `address`.
    Returns the first unprocessed transaction meeting `min_amount`, or None.
    """
    if not settings.TRONGRID_API_KEY:
        logger.warning("TRONGRID_API_KEY not set — skipping TRC20 monitoring")
        return None

    url = f"https://api.trongrid.io/v1/accounts/{address}/transactions/trc20"
    params = {
        "limit": 20,
        "contract_address": USDT_TRC20_CONTRACT,
        "only_to": "true",
    }
    headers = {"TRON-PRO-API-KEY": settings.TRONGRID_API_KEY}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    logger.warning(f"TronGrid returned {resp.status} for {address}")
                    return None
                data = await resp.json()
    except Exception as e:
        logger.error(f"TronGrid request failed: {e}")
        return None

    transactions = data.get("data", [])
    for tx in transactions:
        try:
            to_addr = tx.get("to", "")
            if to_addr.lower() != address.lower():
                continue
            raw_value = int(tx.get("value", "0"))
            amount = raw_value / (10 ** TRON_DECIMALS)
            if amount >= min_amount * 0.99:  # 1% tolerance
                return {
                    "tx_hash":  tx.get("transaction_id", ""),
                    "amount":   amount,
                    "from":     tx.get("from", ""),
                    "network":  "USDT_TRC20",
                }
        except Exception:
            continue

    return None
