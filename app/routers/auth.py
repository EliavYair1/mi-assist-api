from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
import httpx
import re

from app.database import get_db
from app.models import User
from app.auth import create_jwt, get_current_user
from app.config import settings

router = APIRouter()


class ExchangeRequest(BaseModel):
    wp_user_id: int
    wp_nonce: str
    email: str
    plan: str = "free"


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    plan: str
    usage_remaining: int


def normalize_plan(plan: str) -> str:
    mapping = {
        "free": "free",
        "pro": "pro",
        "expert": "pro_plus",
        "team": "team",
    }
    return mapping.get(plan.lower(), "free")


@router.post("/exchange", response_model=TokenResponse)
async def exchange_wp_session(body: ExchangeRequest, db: AsyncSession = Depends(get_db)):
    wp_data = await _verify_wp_nonce(body.wp_nonce, body.wp_user_id)
    wp_plan = wp_data.get("plan", "free")

    result = await db.execute(select(User).where(User.wp_user_id == body.wp_user_id))
    user = result.scalar_one_or_none()

    normalized_plan = normalize_plan(wp_plan)

    if not user:
        user = User(wp_user_id=body.wp_user_id, email=body.email, plan=normalized_plan)
        db.add(user)
    else:
        user.plan = normalized_plan

    await db.commit()
    await db.refresh(user)

    from app.services.usage import get_usage_today, get_limit
    usage = await get_usage_today(db, user.id)
    limit = get_limit(user.plan)
    remaining = max(0, limit - usage.message_count)

    token = create_jwt(user.id, user.wp_user_id, user.plan)

    return TokenResponse(
        access_token=token,
        expires_in=settings.jwt_expiry_minutes * 60,
        plan=user.plan,
        usage_remaining=remaining,
    )


@router.get("/me")
async def get_me(current_user: User = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "email": current_user.email,
        "plan": current_user.plan,
        "plan_status": current_user.plan_status,
        "language_pref": current_user.language_pref,
    }


async def _verify_wp_nonce(nonce: str, wp_user_id: int) -> dict:
    if not nonce or not wp_user_id:
        raise HTTPException(status_code=401, detail="Invalid WordPress session")

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                f"{settings.wp_site_url}/wp-json/mi-assist/v1/verify-nonce",
                params={"nonce": nonce, "user_id": wp_user_id},
                headers={"X-MI-Secret": settings.wp_api_secret},
            )
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Cannot reach WordPress")

    if response.status_code not in (200, 202):
        raise HTTPException(status_code=401, detail="Invalid WordPress session")

    try:
        data = response.json()
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid WordPress session")

    if not data.get("valid"):
        raise HTTPException(status_code=401, detail="Invalid WordPress session")

    return data