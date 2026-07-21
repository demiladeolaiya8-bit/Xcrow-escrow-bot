"""
BEP20 USDT deposit detector.

Detection strategy:
1. BscScan V2 REST API  — primary (official BSC data, key in .env)
2. Public RPC eth_getLogs — fallback (Ankr excluded — it bans getLogs on free tier)
"""
from __future__ import annotations
import asyncio
import json
import aiohttp
from loguru import logger
from config import settings

USDT_BEP20_CONTRACT = "0x55d398326f99059fF775485246999027B3197955"
BEP20_DECIMALS      = 18
_TRANSFER_TOPIC     = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

# Ankr intentionally excluded — its free tier rejects eth_getLogs with -32005
_RPC_NODES = [
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
]


def _pad(addr: str) -> str:
    return "0x" + "0" * 24 + addr.lower().removeprefix("0x")


# ── BscScan V2 (primary) ──────────────────────────────────────────────────

async def _bscscan_fetch(session: aiohttp.ClientSession, address: str) -> list[dict]:
    """Call BscScan V2 tokentx endpoint. Retries once on empty/non-JSON body."""
    url    = "https://api.bscscan.com/v2/api"
    params = {
        "chainid":         "56",
        "module":          "account",
        "action":          "tokentx",
        "contractaddress": USDT_BEP20_CONTRACT,
        "address":         address,
        "sort":            "desc",
        "apikey":          settings.BSCSCAN_API_KEY,
    }

    for attempt in (1, 2):           # retry once on empty body
        try:
            async with session.get(
                url, params=params, timeout=aiohttp.ClientTimeout(total=25)
            ) as resp:
                text = await resp.text()

            if not text or not text.strip():
                logger.warning(f"BscScan empty body (attempt {attempt}), retrying…")
                await asyncio.sleep(2)
                continue

            data = json.loads(text)
        except json.JSONDecodeError as e:
            logger.warning(f"BscScan bad JSON (attempt {attempt}): {e} | body: {text[:120]}")
            await asyncio.sleep(2)
            continue

        status = data.get("status")
        msg    = data.get("message", "")
        result = data.get("result", "")

        if status == "1":
            return result or []

        # "No transactions found" is normal — not an error
        if "No transactions" in str(result) or "No transactions" in msg:
            return []

        raise RuntimeError(f"BscScan V2: {msg} — {result}")

    raise RuntimeError("BscScan returned empty/invalid body after retry")


async def _check_via_bscscan(address: str, min_amount: float) -> dict | None:
    async with aiohttp.ClientSession() as session:
        txs = await _bscscan_fetch(session, address)

    total = 0.0
    best: dict | None = None
    for tx in txs:
        try:
            if tx.get("to", "").lower() != address.lower():
                continue
            raw = int(tx.get("value", "0"))
            amt = raw / (10 ** BEP20_DECIMALS)
            if int(tx.get("confirmations", "0")) < max(1, settings.CONFIRMATION_BLOCKS):
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


# ── RPC eth_getLogs (fallback) ────────────────────────────────────────────

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
    Never raises — returns None on any failure.
    """
    # 1. BscScan V2 — primary
    if settings.BSCSCAN_API_KEY:
        try:
            result = await _check_via_bscscan(address, min_amount)
            if result:
                logger.info(
                    f"✅ BEP20 confirmed (BscScan): {result['amount']:.6f} USDT "
                    f"→ {address[:10]}… tx {result['tx_hash'][:14]}…"
                )
            return result
        except Exception as exc:
            logger.warning(f"BscScan V2 failed ({exc}), falling back to RPC…")

    # 2. RPC eth_getLogs — fallback
    try:
        result = await _check_via_rpc(address, min_amount, start_block)
        if result:
            logger.info(
                f"✅ BEP20 confirmed (RPC): {result['amount']:.6f} USDT "
                f"→ {address[:10]}… tx {result['tx_hash'][:14]}…"
            )
        return result
    except Exception as exc:
        logger.error(f"check_bep20_deposit: RPC also failed for {address}: {exc}")
        return None
