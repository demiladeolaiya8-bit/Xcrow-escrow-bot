"""
Auto-release transfer service.

When buyer confirms delivery the bot:
1. Feeds a small amount of gas (BNB / TRX) into the deposit address
2. Derives the deposit address private key from the HD mnemonic
3. Sends deal.amount USDT from the deposit address to the seller
4. Sends deal.fee_amount USDT from the deposit address to the owner wallet
5. Marks the deal COMPLETED and notifies all parties

Requires in .env:
  GAS_WALLET_PRIVATE_KEY      = SafePal → BNB Chain private key  (needs BNB for gas)
  GAS_WALLET_TRC_PRIVATE_KEY  = SafePal → Tron private key       (needs TRX for energy)

Supported networks for auto-release: USDT_BEP20, USDT_TRC20, ETH
BTC / SOL / TON / LTC still require manual admin release.
"""
from __future__ import annotations
import asyncio
from datetime import datetime
from loguru import logger

from config import settings
from database.models import CryptoNetwork, CRYPTO_SYMBOLS, CRYPTO_LABELS, DealStatus
from database.crud import update_deal, get_setting, create_audit_log

# ── Constants ─────────────────────────────────────────────────────────────
USDT_BEP20_CONTRACT = "0x55d398326f99059fF775485246999027B3197955"
USDT_TRC20_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"

_BSC_RPCS = [
    "https://bsc-rpc.publicnode.com",
    "https://bsc.publicnode.com",
    "https://1rpc.io/bnb",
    "https://binance.llamarpc.com",
    "https://rpc-bsc.48.club",
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

_ERC20_ABI = [{
    "inputs": [
        {"name": "_to",    "type": "address"},
        {"name": "_value", "type": "uint256"},
    ],
    "name": "transfer",
    "outputs": [{"name": "", "type": "bool"}],
    "stateMutability": "nonpayable",
    "type": "function",
}]


# ── Web3 helpers (BSC + ETH) ──────────────────────────────────────────────

async def _w3(rpcs: list[str]):
    from web3 import AsyncWeb3
    from web3.middleware import ExtraDataToPOAMiddleware
    last = RuntimeError("no rpcs")
    for url in rpcs:
        try:
            w = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(url, request_kwargs={"timeout": 12}))
            w.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
            _ = await asyncio.wait_for(w.eth.block_number, timeout=10)
            return w
        except Exception as e:
            last = e
    raise last


async def _send_bnb(gas_private_key: str, to: str, amount_bnb: float) -> str:
    """Send BNB from gas wallet to deposit address to cover gas."""
    from web3 import AsyncWeb3
    w3 = await _w3(_BSC_RPCS)
    acct = w3.eth.account.from_key(gas_private_key)
    nonce = await w3.eth.get_transaction_count(acct.address)
    gas_price = int((await w3.eth.gas_price) * 1.15)
    tx = {
        "to": AsyncWeb3.to_checksum_address(to),
        "value": w3.to_wei(amount_bnb, "ether"),
        "gas": 21_000,
        "gasPrice": gas_price,
        "nonce": nonce,
        "chainId": 56,
    }
    signed = acct.sign_transaction(tx)
    h = await w3.eth.send_raw_transaction(signed.raw_transaction)
    return "0x" + h.hex()


async def _send_bep20_usdt(private_key: str, to: str, amount: float) -> str:
    """Transfer USDT BEP20 from private_key's address."""
    from web3 import AsyncWeb3
    w3 = await _w3(_BSC_RPCS)
    acct = w3.eth.account.from_key(private_key)
    contract = w3.eth.contract(
        address=AsyncWeb3.to_checksum_address(USDT_BEP20_CONTRACT),
        abi=_ERC20_ABI,
    )
    amount_raw = int(amount * 10 ** 18)
    nonce = await w3.eth.get_transaction_count(acct.address)
    gas_price = int((await w3.eth.gas_price) * 1.15)
    tx = await contract.functions.transfer(
        AsyncWeb3.to_checksum_address(to), amount_raw
    ).build_transaction({
        "from": acct.address,
        "nonce": nonce,
        "gasPrice": gas_price,
        "gas": 100_000,
        "chainId": 56,
    })
    signed = acct.sign_transaction(tx)
    h = await w3.eth.send_raw_transaction(signed.raw_transaction)
    return "0x" + h.hex()


async def _send_eth(private_key: str, to: str, amount: float) -> str:
    """Transfer native ETH."""
    from web3 import AsyncWeb3
    w3 = await _w3(_ETH_RPCS)
    acct = w3.eth.account.from_key(private_key)
    nonce = await w3.eth.get_transaction_count(acct.address)
    gas_price = int((await w3.eth.gas_price) * 1.15)
    tx = {
        "to": AsyncWeb3.to_checksum_address(to),
        "value": w3.to_wei(amount, "ether"),
        "gas": 21_000,
        "gasPrice": gas_price,
        "nonce": nonce,
        "chainId": 1,
    }
    signed = acct.sign_transaction(tx)
    h = await w3.eth.send_raw_transaction(signed.raw_transaction)
    return "0x" + h.hex()


# ── Tron helpers ──────────────────────────────────────────────────────────

def _tron_address_from_key(private_key_hex: str) -> str:
    from tronpy.keys import PrivateKey
    pk = PrivateKey(bytes.fromhex(private_key_hex.lstrip("0x")))
    return pk.public_key.to_base58check_address()


async def _send_trx(gas_private_key: str, to: str, amount_trx: float) -> str:
    """Send TRX from gas wallet to deposit address for energy."""
    from tronpy import AsyncTron
    from tronpy.keys import PrivateKey
    pk = PrivateKey(bytes.fromhex(gas_private_key.lstrip("0x")))
    from_addr = pk.public_key.to_base58check_address()
    sun = int(amount_trx * 1_000_000)
    async with AsyncTron() as client:
        txn = client.trx.transfer(from_addr, to, sun).build()
        txn = txn.sign(pk)
        result = await txn.broadcast()
        return result.get("txid", "")


async def _send_trc20_usdt(private_key_hex: str, to: str, amount: float) -> str:
    """Transfer USDT TRC20."""
    from tronpy import AsyncTron
    from tronpy.keys import PrivateKey
    pk = PrivateKey(bytes.fromhex(private_key_hex.lstrip("0x")))
    owner = pk.public_key.to_base58check_address()
    sun = int(amount * 1_000_000)
    async with AsyncTron() as client:
        contract = await client.get_contract(USDT_TRC20_CONTRACT)
        txn = (
            await contract.functions.transfer(to, sun)
        ).with_owner(owner).fee_limit(10_000_000)
        txn = txn.sign(pk)
        result = await txn.broadcast()
        await result.wait()
        return result.get("txid", "")


# ── Main auto-release entry point ─────────────────────────────────────────

async def auto_release_deal(deal, bot) -> bool:
    """
    Automatically release escrow funds to seller when buyer confirms delivery.

    BSC / ETH:
      1. Gas wallet sends BNB/ETH to deposit address
      2. Bot sweeps USDT from deposit address → seller (+ fee → owner wallet)

    TRC20:
      1. Gas wallet sends TRX to deposit address
      2. Bot sweeps USDT TRC20 from deposit address → seller (+ fee → owner wallet)

    Falls back to manual admin notification if GAS_WALLET_PRIVATE_KEY is not set
    or any step fails.
    """
    from services.wallet import wallet_service
    from bot.handlers.group import notify_admins

    uid    = deal.deal_uid
    net    = deal.crypto
    symbol = CRYPTO_SYMBOLS.get(net, "USDT")

    # ── Guards ────────────────────────────────────────────────────────────
    if not deal.seller_wallet:
        await notify_admins(bot, f"❌ Auto-release {uid}: no seller_wallet. Manual release needed.")
        return False

    if not wallet_service.is_configured():
        await notify_admins(bot, f"❌ Auto-release {uid}: HD_MNEMONIC missing. Manual release needed.")
        return False

    # Pick the right gas wallet key per network
    # BSC/ETH → GAS_WALLET_PRIVATE_KEY  (SafePal → BNB Chain)
    # TRC20   → GAS_WALLET_TRC_PRIVATE_KEY (SafePal → Tron), falls back to BSC key
    bsc_gas_key = settings.GAS_WALLET_PRIVATE_KEY.strip()
    trc_gas_key = (settings.GAS_WALLET_TRC_PRIVATE_KEY or "").strip() or bsc_gas_key

    gas_key = trc_gas_key if net == CryptoNetwork.USDT_TRC20 else bsc_gas_key

    if not gas_key:
        logger.warning(f"No gas wallet key configured — manual release for deal {uid}")
        owner_wallet  = await get_setting("owner_wallet_address", "")
        await notify_admins(
            bot,
            f"🔔 <b>Manual Release Required — Deal {uid}</b>\n\n"
            f"Set GAS_WALLET_PRIVATE_KEY (and GAS_WALLET_TRC_PRIVATE_KEY for Tron) "
            f"in .env to enable auto-release.\n\n"
            f"Seller: <code>{deal.seller_wallet}</code> ({deal.seller_network})\n"
            f"Amount: {deal.amount:,.6f} {symbol}\n"
            f"Fee:    {deal.fee_amount:,.6f} {symbol} → <code>{owner_wallet or 'NOT SET'}</code>\n\n"
            f"Dashboard: /panel/deals/{uid}",
        )
        return False

    # ── Derive deposit address private key ────────────────────────────────
    try:
        deposit_key = wallet_service.derive_private_key(net, deal.wallet_index)
    except Exception as e:
        await notify_admins(bot, f"❌ Auto-release {uid}: key derivation failed — {e}")
        return False

    # ── Owner wallet ──────────────────────────────────────────────────────
    owner_wallet  = await get_setting("owner_wallet_address", "")
    owner_network = await get_setting("owner_wallet_network", "USDT_BEP20")

    seller_tx: str = ""
    fee_tx:    str = ""

    try:
        # ── Step 1: Feed gas into deposit address ─────────────────────────
        if net == CryptoNetwork.USDT_BEP20:
            logger.info(f"Auto-release {uid}: sending BNB gas to deposit address…")
            await _send_bnb(gas_key, deal.deposit_address, 0.002)
            await asyncio.sleep(8)   # Wait ~2 BSC blocks

        elif net == CryptoNetwork.USDT_TRC20:
            logger.info(f"Auto-release {uid}: sending TRX gas to deposit address…")
            tron_deposit = _tron_address_from_key(deposit_key)
            await _send_trx(gas_key, tron_deposit, 20)
            await asyncio.sleep(6)   # Wait ~2 Tron blocks

        elif net == CryptoNetwork.ETH:
            logger.info(f"Auto-release {uid}: sending ETH gas to deposit address…")
            await _send_eth(gas_key, deal.deposit_address, 0.001)
            await asyncio.sleep(20)  # ETH blocks are slower

        else:
            raise RuntimeError(f"Network {net} not supported for auto-release")

        # ── Step 2: Send USDT to seller ───────────────────────────────────
        logger.info(f"Auto-release {uid}: sending {deal.amount} {symbol} to seller {deal.seller_wallet}")

        if net == CryptoNetwork.USDT_BEP20:
            seller_tx = await _send_bep20_usdt(deposit_key, deal.seller_wallet, deal.amount)
        elif net == CryptoNetwork.USDT_TRC20:
            seller_tx = await _send_trc20_usdt(deposit_key, deal.seller_wallet, deal.amount)
        elif net == CryptoNetwork.ETH:
            seller_tx = await _send_eth(deposit_key, deal.seller_wallet, deal.amount)

        logger.info(f"Auto-release {uid}: seller TX = {seller_tx}")

        # ── Step 3: Send fee to owner wallet ──────────────────────────────
        if deal.fee_amount and deal.fee_amount > 0 and owner_wallet:
            try:
                await asyncio.sleep(3)   # brief pause between transactions
                if owner_network == CryptoNetwork.USDT_BEP20:
                    fee_key = wallet_service.derive_private_key(CryptoNetwork.USDT_BEP20, deal.wallet_index)
                    fee_tx = await _send_bep20_usdt(fee_key, owner_wallet, deal.fee_amount)
                elif owner_network == CryptoNetwork.USDT_TRC20:
                    fee_key = wallet_service.derive_private_key(CryptoNetwork.USDT_TRC20, deal.wallet_index)
                    fee_tx = await _send_trc20_usdt(fee_key, owner_wallet, deal.fee_amount)
                logger.info(f"Auto-release {uid}: fee TX = {fee_tx}")
            except Exception as fee_err:
                logger.warning(f"Auto-release {uid}: fee transfer failed ({fee_err}) — seller still paid")

        # ── Step 4: Mark completed ────────────────────────────────────────
        await update_deal(deal.id, status=DealStatus.COMPLETED, released_at=datetime.utcnow())
        await create_audit_log(
            actor="bot_auto_release",
            action="auto_released",
            target=uid,
            detail=f"SellerTX:{seller_tx} FeeTX:{fee_tx} Amount:{deal.amount} {symbol}",
        )

        # ── Step 5: Notify everyone ───────────────────────────────────────
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
                    f"Funds should arrive within a few minutes.",
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
            f"Fee TX:    <code>{fee_tx or 'N/A'}</code>",
        )
        return True

    except Exception as exc:
        logger.error(f"Auto-release FAILED for {uid}: {exc}")
        await notify_admins(
            bot,
            f"❌ <b>Auto-release FAILED — {uid}</b>\n\n"
            f"Error: {exc}\n\n"
            f"⚠️ Manual release required!\n"
            f"Seller: <code>{deal.seller_wallet}</code> ({deal.seller_network})\n"
            f"Amount: {deal.amount:,.6f} {symbol}\n"
            f"Dashboard: /panel/deals/{uid}",
        )
        return False
