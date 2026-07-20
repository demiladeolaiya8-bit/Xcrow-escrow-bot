"""
Detect USDT BEP20 deposits via direct BSC RPC (eth_getLogs).

Why not BscScan?
  - V1 (api.bscscan.com/api)          → hard-blocked, returns NOTOK "deprecated V1"
  - Etherscan V2 (api.etherscan.io/v2) → requires paid plan for chainid=56 (BSC)

Instead we query public BSC RPC nodes directly using eth_getLogs.
No API key required. Multiple endpoints tried in order for resilience.
"""
from __future__ import annotations

import asyncio
from loguru import logger
import aiohttp

USDT_BEP20_CONTRACT = "0x55d398326f99059fF775485246999027B3197955"
BEP20_DECIMALS = 18

# keccak256("Transfer(address,address,uint256)")
_TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

# Public BSC mainnet RPC endpoints — tried in order until one succeeds
_BSC_RPC_NODES = [
    "https://bsc-dataseed1.binance.org/",
    "https://bsc-dataseed2.binance.org/",
    "https://bsc-dataseed3.binance.org/",
    "https://bsc-dataseed4.binance.org/",
    "https://bsc-dataseed1.defibit.io/",
    "https://bsc-dataseed1.ninicoin.io/",
    "https://binance.llamarpc.com",
    "https://bsc.publicnode.com",
]


def _pad_address(address: str) -> str:
    """Pad a 20-byte address to a 32-byte hex topic (0x + 24 zeros + address)."""
    return "0x" + "0" * 24 + address.lower().removeprefix("0x")


async def _rpc_call(session: aiohttp.ClientSession, url: str, method: str, params: list) -> object:
    """Make one JSON-RPC 2.0 call. Raises on HTTP or RPC error."""
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
        data = await resp.json(content_type=None)
    if "error" in data:
        raise RuntimeError(f"RPC error: {data['error']}")
    return data["result"]


async def _try_nodes(method: str, params: list) -> object:
    """Try each RPC node in order, returning the first successful result."""
    async with aiohttp.ClientSession() as session:
        for url in _BSC_RPC_NODES:
            try:
                return await _rpc_call(session, url, method, params)
            except Exception as exc:
                logger.debug(f"BSC RPC {url} failed ({method}): {exc}")
    raise RuntimeError("All BSC RPC nodes failed")


async def check_bep20_deposit(address: str, min_amount: float, start_block: int = 0) -> dict | None:
    """
    Scan the BSC chain for USDT BEP20 transfers to `address`.

    Uses eth_getLogs with the ERC-20 Transfer event filtered to our deposit
    address as the recipient topic. No explorer API key required.

    Args:
        address:     The deposit wallet address to monitor.
        min_amount:  Minimum USDT amount to consider as payment (float, e.g. 10.0).
        start_block: Only scan from this block onward (pass deal.last_checked_block
                     to avoid re-scanning the whole chain on every poll).

    Returns:
        dict with tx_hash / amount / from / network on success, else None.
    """
    try:
        # 1. Get current block so we know where to scan up to
        latest_hex: str = await _try_nodes("eth_blockNumber", [])
        latest_block = int(latest_hex, 16)

        from_block = max(0, start_block)
        if from_block > latest_block:
            return None

        # eth_getLogs has a node-level limit per request (~2 000–5 000 blocks).
        # We chunk into 2 000-block windows so we never exceed it.
        CHUNK = 2_000
        chunks = range(from_block, latest_block + 1, CHUNK)

        padded_to = _pad_address(address)

        for chunk_start in chunks:
            chunk_end = min(chunk_start + CHUNK - 1, latest_block)

            logs = await _try_nodes("eth_getLogs", [{
                "fromBlock": hex(chunk_start),
                "toBlock":   hex(chunk_end),
                "address":   USDT_BEP20_CONTRACT,
                "topics": [
                    _TRANSFER_TOPIC,
                    None,          # from — any sender
                    padded_to,     # to   — our deposit address only
                ],
            }])

            if not logs:
                continue

            total = 0.0
            best_tx: dict | None = None

            for log in logs:
                # data field holds the uint256 token amount (no indexed amount in ERC-20)
                raw = int(log.get("data", "0x0"), 16)
                amount = raw / (10 ** BEP20_DECIMALS)
                total += amount
                if best_tx is None:
                    # topics[1] = from address (padded)
                    from_raw = log.get("topics", [None, None])[1] or "0x"
                    from_addr = "0x" + from_raw[-40:]
                    best_tx = {
                        "tx_hash": log.get("transactionHash", ""),
                        "amount":  amount,
                        "from":    from_addr,
                        "network": "USDT_BEP20",
                    }

            if total >= min_amount * 0.99:
                logger.info(
                    f"✅ BEP20 USDT confirmed: {total:.6f} USDT received "
                    f"(expected {min_amount}) at {address} — tx {best_tx['tx_hash'][:16]}…"
                )
                # Return the first matching tx; caller can re-query total if needed
                best_tx["amount"] = total   # report cumulative total
                return best_tx

        logger.debug(f"BEP20: no qualifying USDT transfer found for {address} "
                     f"(blocks {from_block}–{latest_block})")
        return None

    except Exception as exc:
        logger.error(f"check_bep20_deposit error for {address}: {exc}")
        return None
