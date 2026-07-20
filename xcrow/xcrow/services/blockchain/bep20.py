from __future__ import annotations
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
    return "0x" + "0" * 24 + address.lower().removeprefix("0x")


async def _rpc_call(session: aiohttp.ClientSession, url: str, method: str, params: list) -> object:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
        data = await resp.json(content_type=None)
    if "error" in data:
        raise RuntimeError(f"RPC error: {data['error']}")
    return data["result"]


async def _try_nodes(method: str, params: list) -> object:
    async with aiohttp.ClientSession() as session:
        for url in _BSC_RPC_NODES:
            try:
                return await _rpc_call(session, url, method, params)
            except Exception as exc:
                logger.debug(f"BSC RPC {url} failed ({method}): {exc}")
    raise RuntimeError("All BSC RPC nodes failed")


async def get_current_block() -> int:
    """Return the current BSC block number."""
    latest_hex: str = await _try_nodes("eth_blockNumber", [])
    return int(latest_hex, 16)


async def check_bep20_deposit(
    address: str, min_amount: float, start_block: int = 0
) -> dict | None:
    """
    Scan BSC for USDT BEP20 transfers to `address`.

    Uses eth_getLogs with ERC-20 Transfer event — no API key needed.
    Pass start_block = deal.last_checked_block to avoid rescanning from genesis.

    Returns dict with tx_hash/amount/from/network, or None.
    """
    try:
        latest_block = await get_current_block()

        # Scan last ~2000 blocks (~100 min on BSC) if no cursor set
        if start_block == 0:
            from_block = max(0, latest_block - 2000)
        else:
            from_block = start_block

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
                "topics": [
                    _TRANSFER_TOPIC,
                    None,        # from — any sender
                    padded_to,   # to   — our deposit address only
                ],
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
                    from_raw = log.get("topics", [None, None, None])[1] or "0x"
                    best_tx = {
                        "tx_hash": log.get("transactionHash", ""),
                        "amount":  amount,
                        "from":    "0x" + from_raw[-40:],
                        "network": "USDT_BEP20",
                    }

            if total >= min_amount * 0.99:
                logger.info(
                    f"BEP20 USDT confirmed: {total:.6f} (expected {min_amount}) "
                    f"at {address} tx {best_tx['tx_hash'][:16]}..."
                )
                best_tx["amount"] = total
                return best_tx

        logger.debug(f"BEP20: no qualifying transfer for {address} (blocks {from_block}-{latest_block})")
        return None

    except Exception as exc:
        logger.error(f"check_bep20_deposit error for {address}: {exc}")
        return None
