"""Admin endpoints (JSON API)."""
from __future__ import annotations
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from api.app import verify_api_key
from database.crud import (
    get_all_users, set_user_banned, count_deals_by_status,
    get_total_volume, get_total_fees_earned, count_all_deals, count_users,
    get_setting, get_all_settings,
)

router = APIRouter(dependencies=[Depends(verify_api_key)])


@router.get("/stats")
async def stats():
    return {
        "total_deals":  await count_all_deals(),
        "active":       await count_deals_by_status("funded") + await count_deals_by_status("in_delivery"),
        "pending":      await count_deals_by_status("step5_pending") + await count_deals_by_status("awaiting_payment"),
        "releasing":    await count_deals_by_status("releasing"),
        "disputed":     await count_deals_by_status("disputed"),
        "completed":    await count_deals_by_status("completed"),
        "cancelled":    await count_deals_by_status("cancelled"),
        "refunded":     await count_deals_by_status("refunded"),
        "total_users":  await count_users(),
        "total_volume": await get_total_volume(),
        "revenue":      await get_total_fees_earned(),
        "fee_percent":  await get_setting("fee_percent", "1.0"),
        "owner_wallet": await get_setting("owner_wallet_address", ""),
    }


@router.get("/settings")
async def get_platform_settings():
    s = await get_all_settings()
    return {k: v.value for k, v in s.items()}


class BanRequest(BaseModel):
    banned: bool


@router.post("/users/{user_id}/ban")
async def ban_user(user_id: int, req: BanRequest):
    await set_user_banned(user_id, req.banned)
    return {"user_id": user_id, "banned": req.banned}
