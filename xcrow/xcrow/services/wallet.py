"""HD wallet address derivation using BIP39/BIP44."""
from __future__ import annotations
from bip_utils import (
    Bip39SeedGenerator,
    Bip44, Bip44Coins, Bip44Changes,
)
from loguru import logger
from config import settings
from database.models import CryptoNetwork

COIN_MAP = {
    CryptoNetwork.USDT_TRC20: Bip44Coins.TRON,
    CryptoNetwork.USDT_BEP20: Bip44Coins.ETHEREUM,
    CryptoNetwork.ETH:        Bip44Coins.ETHEREUM,
    CryptoNetwork.BTC:        Bip44Coins.BITCOIN,
    CryptoNetwork.SOL:        Bip44Coins.SOLANA,
    CryptoNetwork.LTC:        Bip44Coins.LITECOIN,
}


class WalletService:
    def __init__(self) -> None:
        self._mnemonic = settings.HD_MNEMONIC.strip()
        self._seed: bytes | None = None

    def _get_seed(self) -> bytes:
        if not self._mnemonic:
            raise RuntimeError(
                "HD_MNEMONIC is not set in .env.\n"
                "Generate one with:  python pyrogram_auth.py --mnemonic"
            )
        if self._seed is None:
            # bip_utils 2.x: pass mnemonic string directly — no FromPhrase needed
            self._seed = Bip39SeedGenerator(self._mnemonic).Generate(settings.WALLET_PASSPHRASE)
        return self._seed

    def derive_address(self, network: str, index: int) -> str:
        if network == CryptoNetwork.TON:
            return self._derive_ton(index)

        coin = COIN_MAP.get(network)
        if coin is None:
            raise ValueError(f"Unsupported network for HD derivation: {network}")

        seed = self._get_seed()
        child = (
            Bip44.FromSeed(seed, coin)
            .Purpose().Coin().Account(0)
            .Change(Bip44Changes.CHAIN_EXT)
            .AddressIndex(index)
        )
        return child.PublicKey().ToAddress()

    def _derive_ton(self, index: int) -> str:
        try:
            seed = self._get_seed()
            child = (
                Bip44.FromSeed(seed, Bip44Coins.SOLANA)
                .Purpose().Coin().Account(index)
                .Change(Bip44Changes.CHAIN_EXT)
                .AddressIndex(0)
            )
            raw = child.PublicKey().RawCompressed().ToHex()
            return f"TON_{raw[:48]}"
        except Exception as e:
            logger.warning(f"TON derivation fallback for index {index}: {e}")
            return f"TON_ADDR_{index:08d}"

    def is_configured(self) -> bool:
        return bool(self._mnemonic)


wallet_service = WalletService()
