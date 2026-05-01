from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import date, datetime, timezone

from app.database import get_db
from app.models import User
from app.auth import get_current_user
from app.services.usage import get_usage_today, get_limit

router = APIRouter()


@router.get("")
async def get_usage(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    usage = await get_usage_today(db, current_user.id)
    limit = get_limit(current_user.plan)
    remaining = max(0, limit - usage.message_count)

    # Next reset: midnight UTC
    now = datetime.now(timezone.utc)
    tomorrow = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    from datetime import timedelta
    tomorrow += timedelta(days=1)

    return {
        "plan": current_user.plan,
        "plan_status": current_user.plan_status,
        "used_today": usage.message_count,
        "limit": limit,
        "remaining": remaining,
        "resets_at": tomorrow.isoformat(),
        "date": str(date.today()),
        "uploads_used": usage.upload_count,
    }
