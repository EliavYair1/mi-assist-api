from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
import httpx

from app.database import get_db
from app.models import User
from app.auth import create_jwt, get_current_user
from app.config import settings

router = APIRouter()


class ExchangeRequest(BaseModel):
    wp_user_id: int
    wp_nonce: str
    email: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    plan: str
    usage_remaining: int


@router.post("/exchange", response_model=TokenResponse)
async def exchange_wp_session(body: ExchangeRequest, db: AsyncSession = Depends(get_db)):
    """
    WordPress calls this after login.
    We validate the nonce against the WP REST API,
    then return a JWT for the MI Assist chat UI.
    """
    # 1. Validate WP nonce
    await _verify_wp_nonce(body.wp_nonce, body.wp_user_id)

    # 2. Get or create user in our DB
    result = await db.execute(select(User).where(User.wp_user_id == body.wp_user_id))
    user = result.scalar_one_or_none()

    if not user:
        user = User(wp_user_id=body.wp_user_id, email=body.email, plan="free")
        db.add(user)
        await db.commit()
        await db.refresh(user)

    # 3. Calculate remaining usage today
    from app.services.usage import get_usage_today, get_limit
    usage = await get_usage_today(db, user.id)
    limit = get_limit(user.plan)
    remaining = max(0, limit - usage.message_count)

    # 4. Issue JWT
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


async def _verify_wp_nonce(nonce: str, wp_user_id: int):
    """
    Call WordPress REST API to verify the nonce is valid
    and belongs to the claimed user.
    """
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(
                f"{settings.wp_site_url}/wp-json/mi-assist/v1/verify-nonce",
                params={"nonce": nonce, "user_id": wp_user_id},
                headers={"X-MI-Secret": settings.wp_api_secret},
            )
        if r.status_code != 200 or not r.json().get("valid"):
            raise HTTPException(status_code=401, detail="Invalid WordPress session")
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Cannot reach WordPress for auth validation")
