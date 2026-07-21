"""
ETH deposit detector.

Strategy (in order):
1. Etherscan API   — if ETHERSCAN_API_KEY is set
2. Etherscan free  — public endpoint (rate-limited but works without key)
3. Public Ethereum RPC — eth_getBalance / eth_getLogs fallback
"""
from __future__ import annotations
import aiohttp
from loguru import logger
from config import settings

ETH_DECIMALS = 18
USDT_ERC20_CONTRACT = "0xdAC17F958D2ee523a2206206994597C13D831ec7"
_TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

_ETH_RPC_NODES = [
    "https://rpc.ankr.com/eth",
    "https://eth.publicnode.com",
    "https://1rpc.io/eth",
    "https://ethereum.publicnode.com",
    "https://eth-mainnet.public.blastapi.io",
]


def _pad_address(address: str) -> str:
    return "0x" + "0" * 24 + address.lower().removeprefix("0x")


async def _etherscan_txlist(address: str, api_key: str) -> list[dict]:
    # V2 endpoint — required as of mid-2025 (V1 is deprecated)
    url = "https://api.etherscan.io/v2/api"
    params = {
        "chainid": "1",       # Ethereum mainnet
        "module":  "account",
        "action":  "txlist",
        "address": address,
        "sort":    "desc",
        "apikey":  api_key,
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            data = await resp.json(content_type=None)
    if data.get("status") != "1":
        msg = data.get("message", "")
        result = data.get("result", "")
        if "No transactions" in str(result):
            return []
        raise RuntimeError(f"Etherscan: {msg} — {result}")
    return data.get("result", [])


async def _check_via_etherscan(address: str, min_amount: float, api_key: str) -> dict | None:
    txs = await _etherscan_txlist(address, api_key)
    for tx in txs:
        try:
            if tx.get("to", "").lower() != address.lower():
                continue
            if tx.get("isError", "0") == "1":
                continue
            raw_value = int(tx.get("value", "0"))
            amount = raw_value / (10 ** ETH_DECIMALS)
            confs = int(tx.get("confirmations", "0"))
            if amount >= min_amount * 0.99 and confs >= settings.CONFIRMATION_BLOCKS:
                return {
                    "tx_hash": tx.get("hash", ""),
                    "amount":  amount,
                    "from":    tx.get("from", ""),
                    "network": "ETH",
                }
        except Exception:
            continue
    return None


async def _rpc_call(session: aiohttp.ClientSession, url: str, method: str, params: list) -> object:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=12)) as resp:
        data = await resp.json(content_type=None)
    if "error" in data:
        raise RuntimeError(f"RPC: {data['error']}")
    return data["result"]


async def _try_eth_nodes(method: str, params: list) -> object:
    async with aiohttp.ClientSession() as session:
        last_exc: Exception = RuntimeError("No nodes")
        for url in _ETH_RPC_NODES:
            try:
                return await _rpc_call(session, url, method, params)
            except Exception as exc:
                last_exc = exc
                logger.debug(f"ETH RPC {url} failed: {exc}")
    raise RuntimeError(f"All ETH RPC nodes failed: {last_exc}")


async def _check_via_rpc(address: str, min_amount: float) -> dict | None:
    """Check ETH balance change via public RPC (last 2000 blocks)."""
    latest_hex: str = await _try_eth_nodes("eth_blockNumber", [])
    latest = int(latest_hex, 16)
    from_block = max(0, latest - 500)  # ~1.5 hours on ETH

    padded_to = _pad_address(address)
    logs = await _try_eth_nodes("eth_getLogs", [{
        "fromBlock": hex(from_block),
        "toBlock":   hex(latest),
        "address":   USDT_ERC20_CONTRACT,
        "topics":    [_TRANSFER_TOPIC, None, padded_to],
    }])

    total = 0.0
    best_tx: dict | None = None
    for log in (logs or []):
        try:
            raw = int(log.get("data", "0x0"), 16)
            amount = raw / (10 ** ETH_DECIMALS)
            total += amount
            if best_tx is None:
                from_raw = (log.get("topics") or [None, None, None])[1] or "0x"
                best_tx = {
                    "tx_hash": log.get("transactionHash", ""),
                    "amount":  amount,
                    "from":    "0x" + from_raw[-40:],
                    "network": "ETH",
                }
        except Exception:
            continue

    if total >= min_amount * 0.99 and best_tx:
        best_tx["amount"] = total
        return best_tx
    return None


async def check_eth_deposit(address: str, min_amount: float) -> dict | None:
    """
    Detect ETH deposit to `address` of at least `min_amount`.

    Uses Etherscan if ETHERSCAN_API_KEY is set, then public Etherscan,
    then falls back to direct RPC.
    """
    # Primary: Etherscan with key
    if settings.ETHERSCAN_API_KEY:
        try:
            result = await _check_via_etherscan(address, min_amount, settings.ETHERSCAN_API_KEY)
            if result:
                logger.info(f"ETH confirmed (Etherscan): {result['amount']:.6f} tx {result['tx_hash'][:16]}…")
            return result
        except Exception as exc:
            logger.warning(f"Etherscan failed ({exc}), trying RPC fallback…")

    # Fallback: public RPC
    try:
        result = await _check_via_rpc(address, min_amount)
        if result:
            logger.info(f"ETH confirmed (RPC): {result['amount']:.6f} tx {result['tx_hash'][:16]}…")
        return result
    except Exception as exc:
        logger.error(f"check_eth_deposit error for {address}: {exc}")
        return None
