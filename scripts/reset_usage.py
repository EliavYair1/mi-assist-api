#!/usr/bin/env python3
"""
MI Assist — Daily usage reset script
Runs at midnight UTC via cron:
  0 0 * * * /var/www/mi-assist-api/venv/bin/python /var/www/mi-assist-api/scripts/reset_usage.py

Or set up with APScheduler inside the app (see notes below).
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import date, timedelta
from sqlalchemy import delete
from app.database import AsyncSessionLocal
from app.models import UsageDaily


async def reset_old_usage():
    """Delete usage records older than 7 days (keeps DB lean, today's records untouched)."""
    cutoff = date.today() - timedelta(days=7)
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            delete(UsageDaily).where(UsageDaily.date < cutoff)
        )
        await db.commit()
        deleted = result.rowcount
        print(f"[{date.today()}] Deleted {deleted} old usage records (older than {cutoff})")


if __name__ == "__main__":
    asyncio.run(reset_old_usage())
