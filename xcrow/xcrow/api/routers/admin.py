"""Admin endpoints."""
from __future__ import annotations
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from api.app import verify_api_key
from database.crud import get_all_users, set_user_banned, count_deals_by_status

router = APIRouter(dependencies=[Depends(verify_api_key)])


@router.get("/stats")
async def stats():
    return {
        "active":    await count_deals_by_status("funded") + await count_deals_by_status("in_delivery"),
        "pending":   await count_deals_by_status("step5_pending"),
        "disputed":  await count_deals_by_status("disputed"),
        "completed": await count_deals_by_status("completed"),
    }


class BanRequest(BaseModel):
    banned: bool


@router.post("/users/{user_id}/ban")
async def ban_user(user_id: int, req: BanRequest):
    await set_user_banned(user_id, req.banned)
    return {"user_id": user_id, "banned": req.banned}
