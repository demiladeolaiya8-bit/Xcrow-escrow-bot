"""Deal endpoints."""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from api.app import verify_api_key
from database.crud import get_all_deals, get_deal_by_uid, update_deal
from database.models import DealStatus

router = APIRouter(dependencies=[Depends(verify_api_key)])


class DealOut(BaseModel):
    id: int
    deal_uid: str
    status: str
    title: Optional[str]
    amount: Optional[float]
    crypto: Optional[str]
    total_amount: float
    deposit_address: Optional[str]
    tx_hash: Optional[str]

    class Config:
        from_attributes = True


@router.get("/", response_model=list[DealOut])
async def list_deals(limit: int = 20, offset: int = 0):
    deals = await get_all_deals(limit=limit, offset=offset)
    return deals


@router.get("/{uid}", response_model=DealOut)
async def get_deal(uid: str):
    deal = await get_deal_by_uid(uid.upper())
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")
    return deal


@router.post("/{uid}/release")
async def release_deal(uid: str):
    deal = await get_deal_by_uid(uid.upper())
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")
    await update_deal(deal.id, status=DealStatus.COMPLETED)
    return {"status": "completed", "deal_uid": uid}


@router.post("/{uid}/refund")
async def refund_deal(uid: str):
    deal = await get_deal_by_uid(uid.upper())
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")
    await update_deal(deal.id, status=DealStatus.REFUNDED)
    return {"status": "refunded", "deal_uid": uid}
