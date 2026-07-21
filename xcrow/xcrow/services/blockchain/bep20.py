"""
BEP20 USDT deposit detector.

Detection strategy:
1. Ankr BSC RPC (eth_getLogs)  — primary, no API key, very reliable
2. BscScan V2 REST API         — secondary, if BSCSCAN_API_KEY is set
3. Other public BSC RPC nodes  — final fallback
"""
from __future__ import annotations
import aiohttp
from loguru import logger
from config import settings

USDT_BEP20_CONTRACT = "0x55d398326f99059fF775485246999027B3197955"
BEP20_DECIMALS = 18
_TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

# Ankr first — it is the most reliable free BSC RPC and supports eth_getLogs
_BSC_RPC_NODES = [
    "https://rpc.ankr.com/bsc",
    "https://bsc-rpc.publicnode.com",
    "https://1rpc.io/bnb",
    "https://binance.llamarpc.com",
    "https://bsc.publicnode.com",
    "https://bsc-dataseed1.binance.org/",
    "https://bsc-dataseed2.binance.org/",
    "https://bsc-dataseed3.binance.org/",
    "https://bsc-dataseed4.binance.org/",
]


def _pad(address: str) -> str:
    return "0x" + "0" * 24 + address.lower().removeprefix("0x")


# ── Low-level RPC ─────────────────────────────────────────────────────────

async def _rpc(session: aiohttp.ClientSession, url: str, method: str, params: list):
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=12)) as r:
        data = await r.json(content_type=None)
    if "error" in data:
        raise RuntimeError(f"RPC error from {url}: {data['error']}")
    return data["result"]


async def _try_rpc_nodes(method: str, params: list):
    """Try each BSC RPC node in sequence; raise if all fail."""
    async with aiohttp.ClientSession() as session:
        last: Exception = RuntimeError("No RPC nodes configured")
        for url in _BSC_RPC_NODES:
            try:
                return await _rpc(session, url, method, params)
            except Exception as exc:
                last = exc
                logger.debug(f"BSC node {url} failed: {exc}")
    raise last


# ── Block helpers ─────────────────────────────────────────────────────────

async def get_current_block() -> int:
    result: str = await _try_rpc_nodes("eth_blockNumber", [])
    return int(result, 16)


# ── Primary: Ankr / RPC eth_getLogs ──────────────────────────────────────

async def _check_via_rpc(address: str, min_amount: float, start_block: int) -> dict | None:
    latest = await get_current_block()
    from_block = max(0, latest - 2_000) if start_block == 0 else start_block
    if from_block > latest:
        return None

    padded_to = _pad(address)
    CHUNK = 2_000

    async with aiohttp.ClientSession() as session:
        last: Exception = RuntimeError("No nodes")
        for url in _BSC_RPC_NODES:
            try:
                total = 0.0
                best: dict | None = None

                for chunk_start in range(from_block, latest + 1, CHUNK):
                    chunk_end = min(chunk_start + CHUNK - 1, latest)
                    payload = {
                        "jsonrpc": "2.0", "id": 1,
                        "method": "eth_getLogs",
                        "params": [{
                            "fromBlock": hex(chunk_start),
                            "toBlock":   hex(chunk_end),
                            "address":   USDT_BEP20_CONTRACT,
                            "topics":    [_TRANSFER_TOPIC, None, padded_to],
                        }],
                    }
                    async with session.post(
                        url, json=payload, timeout=aiohttp.ClientTimeout(total=15)
                    ) as r:
                        data = await r.json(content_type=None)

                    if "error" in data:
                        raise RuntimeError(data["error"])

                    for log in (data.get("result") or []):
                        raw = int(log.get("data", "0x0"), 16)
                        amt = raw / (10 ** BEP20_DECIMALS)
                        total += amt
                        if best is None:
                            topics = log.get("topics") or []
                            from_raw = topics[1] if len(topics) > 1 else "0x"
                            best = {
                                "tx_hash": log.get("transactionHash", ""),
                                "amount":  amt,
                                "from":    "0x" + (from_raw or "0x")[-40:],
                                "network": "USDT_BEP20",
                            }

                if total >= min_amount * 0.99 and best:
                    best["amount"] = total
                    return best
                return None   # scanned all chunks, no match

            except Exception as exc:
                last = exc
                logger.debug(f"eth_getLogs via {url} failed: {exc}")

    raise last


# ── Secondary: BscScan V2 REST API ───────────────────────────────────────

async def _check_via_bscscan(address: str, min_amount: float) -> dict | None:
    url = "https://api.bscscan.com/v2/api"
    params = {
        "chainid":         "56",
        "module":          "account",
        "action":          "tokentx",
        "contractaddress": USDT_BEP20_CONTRACT,
        "address":         address,
        "sort":            "desc",
        "apikey":          settings.BSCSCAN_API_KEY,
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=20)) as r:
            data = await r.json(content_type=None)

    if data.get("status") != "1":
        msg    = data.get("message", "")
        result = data.get("result", "")
        if "No transactions" in str(result) or msg in ("No transactions found", "No transactions found."):
            return None
        raise RuntimeError(f"BscScan V2: {msg} — {result}")

    total = 0.0
    best: dict | None = None
    for tx in data.get("result", []):
        try:
            if tx.get("to", "").lower() != address.lower():
                continue
            raw = int(tx.get("value", "0"))
            amt = raw / (10 ** BEP20_DECIMALS)
            if int(tx.get("confirmations", "0")) < settings.CONFIRMATION_BLOCKS:
                continue
            total += amt
            if best is None:
                best = {
                    "tx_hash": tx.get("hash", ""),
                    "amount":  amt,
                    "from":    tx.get("from", ""),
                    "network": "USDT_BEP20",
                }
        except Exception:
            continue

    if total >= min_amount * 0.99 and best:
        best["amount"] = total
        return best
    return None


# ── Public entry point ────────────────────────────────────────────────────

async def check_bep20_deposit(
    address: str, min_amount: float, start_block: int = 0
) -> dict | None:
    """
    Detect USDT BEP20 deposit to `address` ≥ `min_amount`.

    Tries Ankr RPC first (most reliable, no API key),
    then BscScan V2 as secondary if BSCSCAN_API_KEY is set.
    Never raises — returns None on any failure.
    """
    # 1. Ankr/RPC — primary
    try:
        result = await _check_via_rpc(address, min_amount, start_block)
        if result:
            logger.info(
                f"✅ BEP20 confirmed (RPC): {result['amount']:.6f} USDT "
                f"→ {address[:10]}… tx {result['tx_hash'][:14]}…"
            )
        return result
    except Exception as rpc_exc:
        logger.warning(f"BEP20 RPC check failed ({rpc_exc}), trying BscScan…")

    # 2. BscScan V2 — secondary
    if settings.BSCSCAN_API_KEY:
        try:
            result = await _check_via_bscscan(address, min_amount)
            if result:
                logger.info(
                    f"✅ BEP20 confirmed (BscScan): {result['amount']:.6f} USDT "
                    f"→ {address[:10]}… tx {result['tx_hash'][:14]}…"
                )
            return result
        except Exception as bsc_exc:
            logger.error(f"BEP20 BscScan check also failed ({bsc_exc})")

    logger.error(f"check_bep20_deposit: all methods failed for {address}")
    return None
