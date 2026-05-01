from datetime import date, timezone, datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models import UsageDaily
from app.config import PLAN_LIMITS


async def get_usage_today(db: AsyncSession, user_id: str) -> UsageDaily:
    today = date.today()
    result = await db.execute(
        select(UsageDaily).where(
            UsageDaily.user_id == user_id,
            UsageDaily.date == today,
        )
    )
    usage = result.scalar_one_or_none()
    if not usage:
        usage = UsageDaily(user_id=user_id, date=today, message_count=0, upload_count=0)
        db.add(usage)
        await db.flush()
    return usage


async def increment_message_count(db: AsyncSession, user_id: str) -> UsageDaily:
    usage = await get_usage_today(db, user_id)
    usage.message_count += 1
    return usage


async def increment_upload_count(db: AsyncSession, user_id: str) -> UsageDaily:
    usage = await get_usage_today(db, user_id)
    usage.upload_count += 1
    return usage


def get_limit(plan: str) -> int:
    return PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])


def check_message_limit(usage: UsageDaily, plan: str) -> tuple[bool, int]:
    """Returns (is_allowed, remaining)"""
    limit = get_limit(plan)
    remaining = max(0, limit - usage.message_count)
    return remaining > 0, remaining
