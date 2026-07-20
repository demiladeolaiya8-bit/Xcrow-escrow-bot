"""Etherscan API — detect ETH deposits."""
from __future__ import annotations
import aiohttp
from loguru import logger
from config import settings

ETH_DECIMALS = 18


async def check_eth_deposit(address: str, min_amount: float) -> dict | None:
    """
    Poll Etherscan for incoming ETH transfers to `address`.
    Returns the first transaction meeting `min_amount`, or None.
    """
    if not settings.ETHERSCAN_API_KEY:
        logger.warning("ETHERSCAN_API_KEY not set — skipping ETH monitoring")
        return None

    url = "https://api.etherscan.io/api"
    params = {
        "module":  "account",
        "action":  "txlist",
        "address": address,
        "sort":    "desc",
        "apikey":  settings.ETHERSCAN_API_KEY,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                data = await resp.json()
    except Exception as e:
        logger.error(f"Etherscan request failed: {e}")
        return None

    if data.get("status") != "1":
        return None

    for tx in data.get("result", []):
        try:
            to_addr = tx.get("to", "").lower()
            if to_addr != address.lower():
                continue
            if tx.get("isError", "0") == "1":
                continue
            raw_value = int(tx.get("value", "0"))
            amount    = raw_value / (10 ** ETH_DECIMALS)
            confs     = int(tx.get("confirmations", "0"))
            if amount >= min_amount * 0.99 and confs >= settings.CONFIRMATION_BLOCKS:
                return {
                    "tx_hash":  tx.get("hash", ""),
                    "amount":   amount,
                    "from":     tx.get("from", ""),
                    "network":  "ETH",
                }
        except Exception:
            continue

    return None
