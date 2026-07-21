"""
BEP20 USDT deposit detector.

Detection strategy (RPC-only — BscScan blocked on most datacenter IPs):
  Tries each RPC node in sequence until one returns valid eth_getLogs data.
  Nodes that ban getLogs (Ankr -32005) are skipped automatically.
"""
from __future__ import annotations
import aiohttp
from loguru import logger
from config import settings

USDT_BEP20_CONTRACT = "0x55d398326f99059fF775485246999027B3197955"
BEP20_DECIMALS      = 18
_TRANSFER_TOPIC     = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

# Ordered by reliability for datacenter/VPS IPs and getLogs support.
# Ankr excluded — free tier blocks eth_getLogs with -32005.
# BscScan excluded — blocks cloud IPs, returns HTML instead of JSON.
_RPC_NODES = [
    "https://bsc-rpc.publicnode.com",         # PublicNode — reliable, allows getLogs
    "https://bsc.publicnode.com",
    "https://1rpc.io/bnb",                    # 1RPC — privacy-first, no rate limit on getLogs
    "https://binance.llamarpc.com",           # LlamaRPC
    "https://rpc-bsc.48.club",               # 48 Club — BSC validator pool
    "https://bsc-dataseed1.binance.org/",
    "https://bsc-dataseed2.binance.org/",
    "https://bsc-dataseed3.binance.org/",
    "https://bsc-dataseed4.binance.org/",
    "https://bsc-dataseed1.defibit.io/",
    "https://bsc-dataseed1.ninicoin.io/",
    "https://bsc-dataseed2.defibit.io/",
    "https://bsc-dataseed3.defibit.io/",
    "https://bsc-dataseed2.ninicoin.io/",
]


def _pad(addr: str) -> str:
    return "0x" + "0" * 24 + addr.lower().removeprefix("0x")


# ── RPC helpers ───────────────────────────────────────────────────────────

async def _rpc_post(session: aiohttp.ClientSession, url: str, method: str, params: list):
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as r:
        data = await r.json(content_type=None)
    err = data.get("error")
    if err:
        code = err.get("code") if isinstance(err, dict) else 0
        # -32005 = limit exceeded — skip this node immediately
        raise RuntimeError(f"RPC {code}: {err}")
    return data["result"]


async def get_current_block() -> int:
    async with aiohttp.ClientSession() as session:
        for url in _RPC_NODES:
            try:
                hex_num: str = await _rpc_post(session, url, "eth_blockNumber", [])
                return int(hex_num, 16)
            except Exception as e:
                logger.debug(f"blockNumber failed {url}: {e}")
    raise RuntimeError("All RPC nodes failed for eth_blockNumber")


async def _check_via_rpc(address: str, min_amount: float, start_block: int) -> dict | None:
    latest     = await get_current_block()
    from_block = max(0, latest - 2_000) if start_block == 0 else start_block
    if from_block > latest:
        return None

    padded_to = _pad(address)
    CHUNK     = 2_000

    async with aiohttp.ClientSession() as session:
        for url in _RPC_NODES:
            try:
                total = 0.0
                best: dict | None = None

                for cs in range(from_block, latest + 1, CHUNK):
                    ce   = min(cs + CHUNK - 1, latest)
                    logs = await _rpc_post(session, url, "eth_getLogs", [{
                        "fromBlock": hex(cs),
                        "toBlock":   hex(ce),
                        "address":   USDT_BEP20_CONTRACT,
                        "topics":    [_TRANSFER_TOPIC, None, padded_to],
                    }])
                    for log in (logs or []):
                        raw = int(log.get("data", "0x0"), 16)
                        amt = raw / (10 ** BEP20_DECIMALS)
                        total += amt
                        if best is None:
                            topics   = log.get("topics") or []
                            from_raw = topics[1] if len(topics) > 1 else "0x"
                            best     = {
                                "tx_hash": log.get("transactionHash", ""),
                                "amount":  amt,
                                "from":    "0x" + (from_raw or "0x")[-40:],
                                "network": "USDT_BEP20",
                            }

                if total >= min_amount * 0.99 and best:
                    best["amount"] = total
                    return best
                return None      # node worked but no payment found

            except Exception as exc:
                logger.debug(f"eth_getLogs via {url} failed: {exc}")
                continue

    return None   # all nodes failed — no crash, just no result


# ── Public entry point ────────────────────────────────────────────────────

async def check_bep20_deposit(
    address: str, min_amount: float, start_block: int = 0
) -> dict | None:
    """
    Detect USDT BEP20 deposit ≥ min_amount to address.
    Uses RPC eth_getLogs across multiple public BSC nodes.
    Never raises — returns None on any failure.
    """
    try:
        result = await _check_via_rpc(address, min_amount, start_block)
        if result:
            logger.info(
                f"✅ BEP20 confirmed: {result['amount']:.6f} USDT "
                f"→ {address[:10]}… tx {result['tx_hash'][:14]}…"
            )
        return result
    except Exception as exc:
        logger.error(f"check_bep20_deposit failed for {address}: {exc}")
        return None
