"""Xcrow — centralised configuration. All values read from .env at startup."""
from __future__ import annotations
from typing import List
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── Telegram bot ──────────────────────────────────────────────────────
    BOT_TOKEN: str = ""
    BOT_USERNAME: str = "XcrowVouchBot"

    # ── Admins ────────────────────────────────────────────────────────────
    ADMIN_IDS: str = ""          # comma-separated telegram numeric IDs
    SUPPORT_USERNAME: str = "XcrowBotsupport"
    WEBSITE_URL: str = ""

    # ── Database ──────────────────────────────────────────────────────────
    # Docker Compose default; override in .env if running bare
    DATABASE_URL: str = "postgresql+asyncpg://xcrow:xcrowpass@postgres:5432/xcrow"

    # ── Blockchain APIs ───────────────────────────────────────────────────
    TRONGRID_API_KEY: str = ""   # trongrid.io
    BSCSCAN_API_KEY: str = ""    # bscscan.com
    ETHERSCAN_API_KEY: str = ""  # etherscan.io

    # ── HD wallet ─────────────────────────────────────────────────────────
    HD_MNEMONIC: str = ""
    WALLET_PASSPHRASE: str = ""

    # ── Pyrogram (auto group creation) ────────────────────────────────────
    API_ID: str = ""
    API_HASH: str = ""
    PHONE_NUMBER: str = ""
    SESSION_NAME: str = "sessions/xcrow_user"

    # ── Admin REST API ────────────────────────────────────────────────────
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    API_SECRET_KEY: str = "xcrow-change-in-production"

    # ── Escrow settings ───────────────────────────────────────────────────
    ESCROW_FEE_PERCENT: float = 1.0
    # buyer_pays  → fee added on top, buyer pays deal_amount + fee
    # seller_pays → fee deducted, seller receives deal_amount - fee
    # split       → fee split 50/50
    FEE_MODEL: str = "buyer_pays"

    # ── Monitoring ────────────────────────────────────────────────────────
    MONITOR_INTERVAL_SECONDS: int = 30
    CONFIRMATION_BLOCKS: int = 1

    # ── Misc ──────────────────────────────────────────────────────────────
    LOG_LEVEL: str = "INFO"

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    @property
    def admin_id_list(self) -> List[int]:
        if not self.ADMIN_IDS:
            return []
        return [int(x.strip()) for x in self.ADMIN_IDS.split(",") if x.strip().isdigit()]

    @property
    def pyrogram_configured(self) -> bool:
        return bool(self.API_ID and self.API_HASH and self.PHONE_NUMBER)

    def fee_breakdown(self, amount: float) -> tuple[float, float]:
        """Return (fee_amount, total_buyer_pays) for a given deal amount."""
        fee = round(amount * self.ESCROW_FEE_PERCENT / 100, 6)
        if self.FEE_MODEL == "buyer_pays":
            return fee, round(amount + fee, 6)
        return fee, amount  # seller_pays / split: buyer just sends amount


def validate_settings(s: Settings) -> None:
    errors: list[str] = []
    if not s.BOT_TOKEN:
        errors.append("BOT_TOKEN is missing")
    if not s.ADMIN_IDS:
        errors.append("ADMIN_IDS is missing")
    if not s.HD_MNEMONIC:
        errors.append("HD_MNEMONIC is missing — run: python pyrogram_auth.py --mnemonic")
    if errors:
        from loguru import logger
        logger.error("❌  Configuration errors found in .env:")
        for e in errors:
            logger.error(f"    • {e}")
        raise SystemExit(1)


settings = Settings()
