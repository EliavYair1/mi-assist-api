from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
import boto3
import uuid
import logging

from app.database import get_db
from app.models import User
from app.auth import get_current_user
from app.services.usage import get_usage_today, increment_upload_count
from app.config import settings, UPLOAD_PLANS

logger = logging.getLogger(__name__)
router = APIRouter()

ALLOWED_TYPES = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


def get_r2_client():
    return boto3.client(
        "s3",
        endpoint_url=settings.r2_endpoint,
        aws_access_key_id=settings.r2_access_key,
        aws_secret_access_key=settings.r2_secret_key,
        region_name="auto",
    )


@router.post("")
async def upload_file(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Plan check
    if current_user.plan not in UPLOAD_PLANS:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "plan_upgrade_required",
                "message": "File upload requires Pro+ or Team plan.",
                "upgrade_url": "/pricing",
            }
        )

    # File type check
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {file.content_type}")

    # Size check
    content = await file.read()
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(status_code=413, detail=f"File too large. Maximum {settings.max_upload_mb}MB.")

    # Monthly upload limit for Pro+ (Team has same limit per user)
    usage = await get_usage_today(db, current_user.id)
    if usage.upload_count >= settings.upload_limit_pro_plus:
        raise HTTPException(
            status_code=429,
            detail={"error": "monthly_upload_limit_reached", "limit": settings.upload_limit_pro_plus}
        )

    # Upload to R2/S3
    ext = ALLOWED_TYPES[file.content_type]
    file_key = f"uploads/{current_user.id}/{uuid.uuid4()}{ext}"
    try:
        r2 = get_r2_client()
        r2.put_object(
            Bucket=settings.r2_bucket,
            Key=file_key,
            Body=content,
            ContentType=file.content_type,
        )
    except Exception as e:
        logger.error(f"R2 upload error: {e}")
        raise HTTPException(status_code=502, detail="File storage unavailable. Please try again.")

    # Increment upload counter
    await increment_upload_count(db, current_user.id)
    await db.commit()

    return {
        "file_id": file_key,
        "filename": file.filename,
        "content_type": file.content_type,
        "size_bytes": len(content),
        "uploads_remaining": max(0, settings.upload_limit_pro_plus - usage.upload_count - 1),
        "message": "File uploaded successfully. Include the file_id in your /chat message to analyze it.",
    }
