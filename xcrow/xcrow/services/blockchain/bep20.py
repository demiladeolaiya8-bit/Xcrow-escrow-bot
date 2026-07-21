"""
BEP20 USDT deposit detector.

Strategy (in order):
1. BscScan REST API  — if BSCSCAN_API_KEY is set (most reliable, no RPC needed)
2. eth_getLogs via public BSC RPC nodes — fallback when no API key
"""
from __future__ import annotations
import aiohttp
from loguru import logger
from config import settings

USDT_BEP20_CONTRACT = "0x55d398326f99059fF775485246999027B3197955"
BEP20_DECIMALS = 18

# keccak256("Transfer(address,address,uint256)")
_TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

# Public BSC RPC nodes — ordered by reliability; tried in sequence
_BSC_RPC_NODES = [
    "https://rpc.ankr.com/bsc",                           # Ankr — most reliable public node
    "https://bsc-rpc.publicnode.com",
    "https://1rpc.io/bnb",
    "https://binance.llamarpc.com",
    "https://bsc.publicnode.com",
    "https://bsc-dataseed1.binance.org/",
    "https://bsc-dataseed2.binance.org/",
    "https://bsc-dataseed3.binance.org/",
    "https://bsc-dataseed4.binance.org/",
    "https://bsc-dataseed1.defibit.io/",
    "https://bsc-dataseed1.ninicoin.io/",
    "https://endpoints.omniatech.io/v1/bsc/mainnet/public",
]


def _pad_address(address: str) -> str:
    return "0x" + "0" * 24 + address.lower().removeprefix("0x")


# ── BscScan API approach (primary) ────────────────────────────────────────

async def _bscscan_get_transfers(
    session: aiohttp.ClientSession, address: str
) -> list[dict]:
    """Fetch USDT BEP20 transfers to `address` via BscScan token-tx API."""
    url = "https://api.bscscan.com/api"
    params = {
        "module":          "account",
        "action":          "tokentx",
        "contractaddress": USDT_BEP20_CONTRACT,
        "address":         address,
        "sort":            "desc",
        "apikey":          settings.BSCSCAN_API_KEY or "YourApiKeyToken",
    }
    async with session.get(
        url, params=params, timeout=aiohttp.ClientTimeout(total=20)
    ) as resp:
        data = await resp.json(content_type=None)

    if data.get("status") != "1":
        msg = data.get("message", "")
        result = data.get("result", "")
        # "No transactions found" is normal — not an error
        if "No transactions" in str(result) or msg == "No transactions found":
            return []
        raise RuntimeError(f"BscScan API: {msg} — {result}")

    return data.get("result", [])


async def _check_via_bscscan(address: str, min_amount: float) -> dict | None:
    """Use BscScan API to detect USDT BEP20 deposit."""
    async with aiohttp.ClientSession() as session:
        txs = await _bscscan_get_transfers(session, address)

    total = 0.0
    best_tx: dict | None = None

    for tx in txs:
        try:
            to_addr = tx.get("to", "").lower()
            if to_addr != address.lower():
                continue
            raw = int(tx.get("value", "0"))
            amount = raw / (10 ** BEP20_DECIMALS)
            confirmations = int(tx.get("confirmations", "0"))
            if confirmations < settings.CONFIRMATION_BLOCKS:
                continue
            total += amount
            if best_tx is None:
                best_tx = {
                    "tx_hash": tx.get("hash", ""),
                    "amount":  amount,
                    "from":    tx.get("from", ""),
                    "network": "USDT_BEP20",
                }
        except Exception:
            continue

    if total >= min_amount * 0.99 and best_tx:
        best_tx["amount"] = total
        return best_tx
    return None


# ── RPC / eth_getLogs approach (fallback) ────────────────────────────────

async def _rpc_call(
    session: aiohttp.ClientSession, url: str, method: str, params: list
) -> object:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    async with session.post(
        url, json=payload, timeout=aiohttp.ClientTimeout(total=12)
    ) as resp:
        data = await resp.json(content_type=None)
    if "error" in data:
        raise RuntimeError(f"RPC error: {data['error']}")
    return data["result"]


async def _try_nodes(method: str, params: list) -> object:
    """Try each BSC RPC node in order; raise only if all fail."""
    async with aiohttp.ClientSession() as session:
        last_exc: Exception = RuntimeError("No nodes configured")
        for url in _BSC_RPC_NODES:
            try:
                return await _rpc_call(session, url, method, params)
            except Exception as exc:
                last_exc = exc
                logger.debug(f"BSC RPC {url} failed ({method}): {exc}")
    raise RuntimeError(f"All BSC RPC nodes failed. Last error: {last_exc}")


async def get_current_block() -> int:
    """Return current BSC block number."""
    latest_hex: str = await _try_nodes("eth_blockNumber", [])
    return int(latest_hex, 16)


async def _check_via_rpc(
    address: str, min_amount: float, start_block: int
) -> dict | None:
    """Use eth_getLogs via public RPC nodes to detect USDT BEP20 deposit."""
    latest_block = await get_current_block()

    from_block = max(0, latest_block - 2000) if start_block == 0 else start_block
    if from_block > latest_block:
        return None

    CHUNK = 2_000
    padded_to = _pad_address(address)

    for chunk_start in range(from_block, latest_block + 1, CHUNK):
        chunk_end = min(chunk_start + CHUNK - 1, latest_block)

        logs = await _try_nodes("eth_getLogs", [{
            "fromBlock": hex(chunk_start),
            "toBlock":   hex(chunk_end),
            "address":   USDT_BEP20_CONTRACT,
            "topics":    [_TRANSFER_TOPIC, None, padded_to],
        }])

        if not logs:
            continue

        total = 0.0
        best_tx: dict | None = None

        for log in logs:
            raw = int(log.get("data", "0x0"), 16)
            amount = raw / (10 ** BEP20_DECIMALS)
            total += amount
            if best_tx is None:
                from_raw = (log.get("topics") or [None, None, None])[1] or "0x"
                best_tx = {
                    "tx_hash": log.get("transactionHash", ""),
                    "amount":  amount,
                    "from":    "0x" + from_raw[-40:],
                    "network": "USDT_BEP20",
                }

        if total >= min_amount * 0.99 and best_tx:
            best_tx["amount"] = total
            return best_tx

    return None


# ── Public entry point ────────────────────────────────────────────────────

async def check_bep20_deposit(
    address: str, min_amount: float, start_block: int = 0
) -> dict | None:
    """
    Detect a USDT BEP20 deposit to `address` of at least `min_amount`.

    Tries BscScan API first (if BSCSCAN_API_KEY is set), then falls back
    to direct RPC eth_getLogs.  Returns a result dict or None.
    """
    try:
        if settings.BSCSCAN_API_KEY:
            logger.debug(f"BEP20: checking {address} via BscScan API")
            result = await _check_via_bscscan(address, min_amount)
            if result:
                logger.info(
                    f"BEP20 USDT confirmed (BscScan): {result['amount']:.6f} "
                    f"at {address} tx {result['tx_hash'][:16]}…"
                )
            return result

        logger.debug(f"BEP20: no API key — falling back to RPC for {address}")
        result = await _check_via_rpc(address, min_amount, start_block)
        if result:
            logger.info(
                f"BEP20 USDT confirmed (RPC): {result['amount']:.6f} "
                f"at {address} tx {result['tx_hash'][:16]}…"
            )
        return result

    except Exception as exc:
        logger.error(f"check_bep20_deposit error for {address}: {exc}")
        return None
