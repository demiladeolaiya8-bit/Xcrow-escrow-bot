"""BscScan API — detect USDT BEP20 deposits."""
from __future__ import annotations
import aiohttp
from loguru import logger
from config import settings

USDT_BEP20_CONTRACT = "0x55d398326f99059fF775485246999027B3197955"
BEP20_DECIMALS = 18


async def check_bep20_deposit(address: str, min_amount: float) -> dict | None:
    """
    Poll BscScan for incoming USDT BEP20 transfers to `address`.
    Returns the first transaction meeting `min_amount`, or None.
    """
    if not settings.BSCSCAN_API_KEY:
        logger.warning("BSCSCAN_API_KEY not set — skipping BEP20 monitoring")
        return None

    url = "https://api.bscscan.com/api"
    params = {
        "module":          "account",
        "action":          "tokentx",
        "contractaddress": USDT_BEP20_CONTRACT,
        "address":         address,
        "sort":            "desc",
        "apikey":          settings.BSCSCAN_API_KEY,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                data = await resp.json()
    except Exception as e:
        logger.error(f"BscScan request failed: {e}")
        return None

    if data.get("status") != "1":
        return None

    for tx in data.get("result", []):
        try:
            to_addr = tx.get("to", "").lower()
            if to_addr != address.lower():
                continue
            raw_value = int(tx.get("value", "0"))
            amount = raw_value / (10 ** BEP20_DECIMALS)
            confs  = int(tx.get("confirmations", "0"))
            if amount >= min_amount * 0.99 and confs >= settings.CONFIRMATION_BLOCKS:
                return {
                    "tx_hash":  tx.get("hash", ""),
                    "amount":   amount,
                    "from":     tx.get("from", ""),
                    "network":  "USDT_BEP20",
                }
        except Exception:
            continue

    return None
