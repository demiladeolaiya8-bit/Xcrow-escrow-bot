"""
Auto-release transfer service.

NEW (main-wallet mode):
  auto_release_from_main_wallet() — sends directly from your SafePal wallet
  using MAIN_WALLET_PRIVATE_KEY in .env. No gas-feeding step needed.
  Supports: USDT_BEP20, USDT_ERC20, ETH, BTC (BTC = admin notification only).

LEGACY (kept for TRC20 and backwards-compat):
  auto_release_deal() — original HD-wallet sweep flow (TRC20 still uses this).
"""
from __future__ import annotations
import asyncio
import re
from datetime import datetime
from loguru import logger

from config import settings
from database.models import CryptoNetwork, CRYPTO_SYMBOLS, DealStatus
from database.crud import update_deal, get_setting, create_audit_log

# ── Constants ──────────────────────────────────────────────────────────────
USDT_BEP20_CONTRACT  = "0x55d398326f99059fF775485246999027B3197955"
USDT_ERC20_CONTRACT  = "0xdAC17F958D2ee523a2206206994597C13D831ec7"
USDT_TRC20_CONTRACT  = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"

_ERC20_TRANSFER_SELECTOR = bytes.fromhex("a9059cbb")

_BSC_RPCS = [
    "https://bsc-rpc.publicnode.com",
    "https://bsc.publicnode.com",
    "https://1rpc.io/bnb",
    "https://binance.llamarpc.com",
    "https://bsc-dataseed1.binance.org/",
    "https://bsc-dataseed2.binance.org/",
    "https://bsc-dataseed3.binance.org/",
]

_ETH_RPCS = [
    "https://eth.publicnode.com",
    "https://1rpc.io/eth",
    "https://ethereum.publicnode.com",
    "https://rpc.ankr.com/eth",
]


# ── Key sanitization ───────────────────────────────────────────────────────

def _sanitize_key(key: str) -> str:
    k = re.sub(r'\s+', '', key)
    if k.lower().startswith('0x'):
        k = k[2:]
    k = re.sub(r'[^0-9a-fA-F]', '', k)
    if len(k) > 64:
        k = k[-64:]
    if len(k) != 64:
        raise ValueError(
            f"Private key has {len(k)} hex chars after sanitization (expected 64). "
            "Check MAIN_WALLET_PRIVATE_KEY in your .env."
        )
    return '0x' + k


# ── Raw JSON-RPC helper ────────────────────────────────────────────────────

def _rpc_sync_urllib(url: str, method: str, params: list):
    import json
    from urllib.request import Request, urlopen
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req  = Request(url, data=body, headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=15) as r:
        data = json.loads(r.read())
    if "error" in data:
        raise RuntimeError(f"RPC error: {data['error']}")
    return data["result"]


async def _rpc(rpcs: list[str], method: str, params: list):
    import aiohttp
    connector = aiohttp.TCPConnector(ssl=False)
    last: Exception = RuntimeError("all RPCs failed")

    try:
        async with aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as session:
            for url in rpcs:
                try:
                    async with session.post(url, json={
                        "jsonrpc": "2.0", "id": 1,
                        "method": method, "params": params,
                    }) as r:
                        data = await r.json(content_type=None)
                    if "error" in data:
                        raise RuntimeError(f"RPC error: {data['error']}")
                    return data["result"]
                except Exception as e:
                    logger.debug(f"RPC {url} failed: {e}")
                    last = e
    except Exception as e:
        last = e

    loop = asyncio.get_event_loop()
    for url in rpcs:
        try:
            result = await loop.run_in_executor(None, _rpc_sync_urllib, url, method, params)
            return result
        except Exception as e:
            last = e

    raise RuntimeError(f"All RPCs failed: {last}")


def _erc20_transfer_data(to_address: str, amount_raw: int) -> str:
    to_padded  = to_address.lower().replace("0x", "").zfill(64)
    amt_padded = hex(amount_raw)[2:].zfill(64)
    return "0x" + _ERC20_TRANSFER_SELECTOR.hex() + to_padded + amt_padded


def _checksum(address: str) -> str:
    from eth_utils import to_checksum_address
    return to_checksum_address(address)


# ── BSC / ETH on-chain helpers ─────────────────────────────────────────────

async def _get_nonce(rpcs, address: str) -> int:
    result = await _rpc(rpcs, "eth_getTransactionCount", [address, "pending"])
    return int(result, 16)


async def _get_gas_price(rpcs, multiplier: float = 1.15, min_gwei: int = 3) -> int:
    result = await _rpc(rpcs, "eth_gasPrice", [])
    price  = int(int(result, 16) * multiplier)
    return max(price, min_gwei * 10**9)


async def _broadcast_tx(rpcs, signed_tx_bytes: bytes) -> str:
    raw_hex = "0x" + bytes(signed_tx_bytes).hex()
    return await _rpc(rpcs, "eth_sendRawTransaction", [raw_hex])


def _sign_tx(private_key: str, tx: dict) -> bytes:
    from eth_account import Account
    signed = Account.sign_transaction(tx, private_key)
    return signed.rawTransaction


async def _send_native(rpcs, chain_id: int, private_key: str, to: str, amount_wei: int) -> str:
    from eth_account import Account
    acct      = Account.from_key(private_key)
    nonce     = await _get_nonce(rpcs, acct.address)
    gas_price = await _get_gas_price(rpcs)
    tx = {
        "to":       _checksum(to),
        "value":    amount_wei,
        "gas":      21_000,
        "gasPrice": gas_price,
        "nonce":    nonce,
        "chainId":  chain_id,
    }
    raw = _sign_tx(private_key, tx)
    return await _broadcast_tx(rpcs, raw)


async def _send_erc20(rpcs, chain_id: int, contract: str,
                      private_key: str, to: str, amount_raw: int,
                      decimals_18: bool = True) -> str:
    from eth_account import Account
    acct      = Account.from_key(private_key)
    nonce     = await _get_nonce(rpcs, acct.address)
    gas_price = await _get_gas_price(rpcs)
    tx = {
        "to":       _checksum(contract),
        "value":    0,
        "gas":      120_000,
        "gasPrice": gas_price,
        "nonce":    nonce,
        "chainId":  chain_id,
        "data":     _erc20_transfer_data(_checksum(to), amount_raw),
    }
    raw = _sign_tx(private_key, tx)
    return await _broadcast_tx(rpcs, raw)


# ── Public send helpers ────────────────────────────────────────────────────

async def _send_bnb(private_key: str, to: str, amount_bnb: float) -> str:
    wei = int(amount_bnb * 10**18)
    return await _send_native(_BSC_RPCS, 56, private_key, to, wei)


async def _send_bep20_usdt(private_key: str, to: str, amount: float) -> str:
    raw = int(amount * 10**18)    # BEP20 USDT = 18 decimals
    return await _send_erc20(_BSC_RPCS, 56, USDT_BEP20_CONTRACT, private_key, to, raw)


async def _send_eth_native(private_key: str, to: str, amount: float) -> str:
    wei = int(amount * 10**18)
    return await _send_native(_ETH_RPCS, 1, private_key, to, wei)


async def _send_erc20_usdt(private_key: str, to: str, amount: float) -> str:
    raw = int(amount * 10**6)    # ERC20 USDT = 6 decimals
    return await _send_erc20(_ETH_RPCS, 1, USDT_ERC20_CONTRACT, private_key, to, raw)


# ── Tron helpers (legacy TRC20) ────────────────────────────────────────────

def _tron_key_bytes(private_key: str) -> bytes:
    k = re.sub(r'[^0-9a-fA-F]', '', re.sub(r'^0[xX]', '', private_key.strip()))
    if len(k) > 64:
        k = k[-64:]
    return bytes.fromhex(k.zfill(64))


def _tron_address_from_key(private_key: str) -> str:
    from tronpy.keys import PrivateKey
    pk = PrivateKey(_tron_key_bytes(private_key))
    return pk.public_key.to_base58check_address()


async def _send_trx(private_key: str, to: str, amount_trx: float) -> str:
    from tronpy import AsyncTron
    from tronpy.keys import PrivateKey
    pk        = PrivateKey(_tron_key_bytes(private_key))
    from_addr = pk.public_key.to_base58check_address()
    sun       = int(amount_trx * 1_000_000)
    async with AsyncTron() as client:
        txb    = client.trx.transfer(from_addr, to, sun)
        txn    = await txb.build()
        txn    = txn.sign(pk)
        result = await txn.broadcast()
        return result.get("txid", "")


async def _send_trc20_usdt(private_key: str, to: str, amount: float) -> str:
    from tronpy import AsyncTron
    from tronpy.keys import PrivateKey
    pk    = PrivateKey(_tron_key_bytes(private_key))
    owner = pk.public_key.to_base58check_address()
    sun   = int(amount * 1_000_000)
    async with AsyncTron() as client:
        contract = await client.get_contract(USDT_TRC20_CONTRACT)
        txn = (
            await contract.functions.transfer(to, sun)
        ).with_owner(owner).fee_limit(10_000_000)
        txn    = txn.sign(pk)
        result = await txn.broadcast()
        await result.wait()
        return result.get("txid", "")


# ══════════════════════════════════════════════════════════════════════════
#  NEW: Auto-release from main wallet (central-wallet mode)
# ══════════════════════════════════════════════════════════════════════════

async def auto_release_from_main_wallet(deal, bot) -> bool:
    """
    Release funds from the main SafePal wallet to the seller.

    Flow:
      1. Read MAIN_WALLET_PRIVATE_KEY from .env
      2. Send deal.amount to seller on seller_network
      3. If payout wallet ≠ main wallet, send fee there too
      4. Mark deal COMPLETED, notify all parties

    BTC: auto-release not supported — admin receives notification to release manually.
    """
    from bot.handlers.group import notify_admins

    uid    = deal.deal_uid
    net    = deal.seller_network or deal.crypto
    symbol = CRYPTO_SYMBOLS.get(net, "USDT")

    if not deal.seller_wallet:
        await notify_admins(bot, f"❌ Release {uid}: seller_wallet not set — manual release needed.")
        return False

    raw_key = (settings.MAIN_WALLET_PRIVATE_KEY or "").strip()
    if not raw_key:
        owner_wallet = await get_setting("owner_wallet_address", "")
        await notify_admins(
            bot,
            f"🔔 <b>Manual Release Required — {uid}</b>\n\n"
            f"MAIN_WALLET_PRIVATE_KEY not set in .env.\n\n"
            f"Seller: <code>{deal.seller_wallet}</code> ({net})\n"
            f"Amount: {deal.amount:,.6f} {symbol}\n"
            f"Dashboard: /panel/deals/{uid}",
        )
        return False

    try:
        key = _sanitize_key(raw_key)
    except ValueError as e:
        await notify_admins(bot, f"❌ Release {uid}: bad MAIN_WALLET_PRIVATE_KEY — {e}")
        return False

    # BTC: manual only
    if net == CryptoNetwork.BTC:
        await notify_admins(
            bot,
            f"🔔 <b>Manual BTC Release — {uid}</b>\n\n"
            f"BTC auto-release not supported yet.\n\n"
            f"Please send <b>{deal.amount:.8f} BTC</b> manually to:\n"
            f"<code>{deal.seller_wallet}</code>\n\n"
            f"Then mark complete in dashboard: /panel/deals/{uid}",
        )
        return False

    owner_wallet  = await get_setting("owner_wallet_address", settings.MAIN_WALLET_BSC_ETH)
    owner_network = await get_setting("owner_wallet_network", "USDT_BEP20")

    seller_tx = ""
    fee_tx    = ""

    try:
        logger.info(f"Auto-release {uid}: {deal.amount} {symbol} → {deal.seller_wallet} on {net}")

        # ── Step 1: Pay seller ─────────────────────────────────────────────
        if net == CryptoNetwork.USDT_BEP20:
            seller_tx = await _send_bep20_usdt(key, deal.seller_wallet, deal.amount)
        elif net == CryptoNetwork.USDT_ERC20:
            seller_tx = await _send_erc20_usdt(key, deal.seller_wallet, deal.amount)
        elif net == CryptoNetwork.ETH:
            seller_tx = await _send_eth_native(key, deal.seller_wallet, deal.amount)
        elif net == CryptoNetwork.USDT_TRC20:
            # TRC20: use TRC gas key
            raw_trc = (settings.GAS_WALLET_TRC_PRIVATE_KEY or settings.GAS_WALLET_PRIVATE_KEY or "").strip()
            if raw_trc:
                seller_tx = await _send_trc20_usdt(raw_trc, deal.seller_wallet, deal.amount)
            else:
                raise RuntimeError("TRC20 key not configured — set GAS_WALLET_TRC_PRIVATE_KEY")
        else:
            raise RuntimeError(f"Network {net} not supported for auto-release")

        logger.info(f"Auto-release {uid}: seller TX = {seller_tx}")

        # ── Step 2: Platform fee (skip if fee wallet = main wallet or fee = 0) ──
        if (deal.fee_amount and deal.fee_amount > 0 and owner_wallet and
                owner_wallet.lower() != settings.MAIN_WALLET_BSC_ETH.lower()):
            try:
                await asyncio.sleep(3)
                if net in (CryptoNetwork.USDT_BEP20, CryptoNetwork.USDT_ERC20):
                    if net == CryptoNetwork.USDT_BEP20:
                        fee_tx = await _send_bep20_usdt(key, owner_wallet, deal.fee_amount)
                    else:
                        fee_tx = await _send_erc20_usdt(key, owner_wallet, deal.fee_amount)
                elif net == CryptoNetwork.ETH:
                    fee_tx = await _send_eth_native(key, owner_wallet, deal.fee_amount)
                logger.info(f"Auto-release {uid}: fee TX = {fee_tx}")
            except Exception as fee_err:
                logger.warning(f"Auto-release {uid}: fee transfer failed ({fee_err}) — seller already paid")

        # ── Step 3: Mark completed ─────────────────────────────────────────
        await update_deal(deal.id, status=DealStatus.COMPLETED, released_at=datetime.utcnow())
        await create_audit_log(
            actor="bot_auto_release",
            action="auto_released_main_wallet",
            target=uid,
            detail=f"SellerTX:{seller_tx} FeeTX:{fee_tx} Amount:{deal.amount} {symbol}",
        )

        # ── Step 4: Notify all parties ─────────────────────────────────────
        if deal.group_id:
            try:
                await bot.send_message(
                    deal.group_id,
                    f"✅ <b>Funds Released!</b>\n\n"
                    f"<b>{deal.amount:,.4f} {symbol}</b> sent to seller.\n"
                    f"TX: <code>{seller_tx[:20]}…</code>\n\n"
                    f"🏁 Deal <code>{uid}</code> is now <b>completed</b>. Thank you!",
                )
            except Exception:
                pass

        if deal.seller_id:
            try:
                await bot.send_message(
                    deal.seller_id,
                    f"💸 <b>Payment Sent to You!</b>\n\n"
                    f"Deal: <code>{uid}</code>\n"
                    f"Amount: <b>{deal.amount:,.4f} {symbol}</b>\n"
                    f"Wallet: <code>{deal.seller_wallet}</code>\n"
                    f"TX: <code>{seller_tx}</code>\n\n"
                    f"Funds should arrive within minutes.",
                )
            except Exception:
                pass

        if deal.buyer_id:
            try:
                await bot.send_message(
                    deal.buyer_id,
                    f"✅ <b>Deal Completed!</b>\n\n"
                    f"Deal: <code>{uid}</code>\n"
                    f"Payment of <b>{deal.amount:,.4f} {symbol}</b> has been released to the seller.\n\n"
                    f"Thank you for using Xcrow!",
                )
            except Exception:
                pass

        await notify_admins(
            bot,
            f"✅ <b>Auto-release done — {uid}</b>\n"
            f"Seller TX: <code>{seller_tx}</code>\n"
            f"Fee TX:    <code>{fee_tx or 'N/A (same wallet)'}</code>",
        )
        return True

    except Exception as exc:
        logger.error(f"Auto-release FAILED for {uid}: {exc}")
        await notify_admins(
            bot,
            f"❌ <b>Auto-release FAILED — {uid}</b>\n\n"
            f"Error: {exc}\n\n"
            f"⚠️ Manual release required!\n"
            f"Seller: <code>{deal.seller_wallet}</code> ({net})\n"
            f"Amount: {deal.amount:,.6f} {symbol}\n"
            f"Dashboard: /panel/deals/{uid}",
        )
        return False


# ══════════════════════════════════════════════════════════════════════════
#  LEGACY: Original HD-wallet sweep (kept for TRC20 compatibility)
# ══════════════════════════════════════════════════════════════════════════

async def auto_release_deal(deal, bot) -> bool:
    """Legacy HD-wallet auto-release. Used for TRC20 deals and old deposit-address deals."""
    # If private key is set, use the new main-wallet flow instead
    if settings.MAIN_WALLET_PRIVATE_KEY:
        return await auto_release_from_main_wallet(deal, bot)

    from services.wallet import wallet_service
    from bot.handlers.group import notify_admins

    uid    = deal.deal_uid
    net    = deal.crypto
    symbol = CRYPTO_SYMBOLS.get(net, "USDT")

    if not deal.seller_wallet:
        await notify_admins(bot, f"❌ Auto-release {uid}: no seller_wallet — manual release needed.")
        return False

    _raw_bsc = (settings.GAS_WALLET_PRIVATE_KEY or "").strip()
    _raw_trc = (settings.GAS_WALLET_TRC_PRIVATE_KEY or "").strip() or _raw_bsc

    try:
        bsc_gas_key = _sanitize_key(_raw_bsc) if _raw_bsc else ""
    except ValueError as e:
        await notify_admins(bot, f"❌ Auto-release {uid}: bad GAS_WALLET_PRIVATE_KEY — {e}")
        return False

    try:
        trc_gas_key = _sanitize_key(_raw_trc) if _raw_trc else ""
    except ValueError as e:
        await notify_admins(bot, f"❌ Auto-release {uid}: bad GAS_WALLET_TRC_PRIVATE_KEY — {e}")
        return False

    gas_key = trc_gas_key if net == CryptoNetwork.USDT_TRC20 else bsc_gas_key
    if not gas_key:
        await notify_admins(bot, f"🔔 Manual release needed — {uid}: no gas key configured.")
        return False

    try:
        deposit_key = wallet_service.derive_private_key(net, deal.wallet_index)
    except Exception as e:
        await notify_admins(bot, f"❌ Auto-release {uid}: key derivation failed — {e}")
        return False

    owner_wallet  = await get_setting("owner_wallet_address", "")
    seller_network = deal.seller_network or deal.crypto
    seller_tx = ""

    try:
        if net == CryptoNetwork.USDT_BEP20:
            await _send_bnb(gas_key, deal.deposit_address, 0.002)
            await asyncio.sleep(8)
            seller_tx = await _send_bep20_usdt(deposit_key, deal.seller_wallet, deal.amount)
        elif net == CryptoNetwork.USDT_TRC20:
            tron_deposit = _tron_address_from_key(deposit_key)
            await _send_trx(gas_key, tron_deposit, 20)
            await asyncio.sleep(6)
            seller_tx = await _send_trc20_usdt(deposit_key, deal.seller_wallet, deal.amount)

        await update_deal(deal.id, status=DealStatus.COMPLETED, released_at=datetime.utcnow())
        await create_audit_log("bot_auto_release", "auto_released_legacy", target=uid,
                               detail=f"SellerTX:{seller_tx} Amount:{deal.amount} {symbol}")

        if deal.group_id:
            try:
                await bot.send_message(
                    deal.group_id,
                    f"✅ <b>Funds Released!</b>\n<b>{deal.amount:,.4f} {symbol}</b> sent to seller.\n"
                    f"TX: <code>{seller_tx[:20]}…</code>",
                )
            except Exception:
                pass
        return True

    except Exception as exc:
        logger.error(f"Legacy auto-release FAILED for {uid}: {exc}")
        await notify_admins(bot, f"❌ Legacy auto-release FAILED — {uid}: {exc}")
        return False
