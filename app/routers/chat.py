from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from datetime import datetime, timezone
import logging

from app.database import get_db
from app.models import User, Conversation, Message
from app.auth import get_current_user
from app.services import usage as usage_svc
from app.services.openai_service import chat_completion

import io
logger = logging.getLogger(__name__)
router = APIRouter()

MAX_HISTORY = 10


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None
    image_base64: str | None = None
    image_type: str | None = None


class ChatResponse(BaseModel):
    reply: str
    conversation_id: str
    usage_remaining: int
    plan: str


@router.post("", response_model=ChatResponse)
async def send_message(
    body: ChatRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # 1. Check daily limit
    usage = await usage_svc.get_usage_today(db, current_user.id)
    allowed, remaining = usage_svc.check_message_limit(usage, current_user.plan)

    if not allowed:
        limit = usage_svc.get_limit(current_user.plan)
        raise HTTPException(
            status_code=429,
            detail={
                "error": "daily_limit_reached",
                "plan": current_user.plan,
                "limit": limit,
                "message": "You've reached your daily question limit. Upgrade to Pro for more.",
                "upgrade_url": "/pricing",
            }
        )

    # 2. Get or create conversation
    conversation = await _get_or_create_conversation(db, current_user, body.conversation_id)

    # 3. Build message history
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.desc())
        .limit(MAX_HISTORY)
    )
    history = list(reversed(result.scalars().all()))

    openai_messages = [
        {"role": msg.role, "content": msg.content}
        for msg in history
    ]

    if body.image_base64 and body.image_type:
        openai_messages.append({
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{body.image_type};base64,{body.image_base64}"
                    }
                },
                {
                    "type": "text",
                    "text": body.message or "Please analyze this image from a safety and inspection perspective."
                }
            ]
        })
    else:
        openai_messages.append({"role": "user", "content": body.message})

    # 4. Call OpenAI
    try:
        reply, tokens = await chat_completion(
            messages=openai_messages,
            user_language=current_user.language_pref,
        )
    except Exception as e:
        logger.error(f"OpenAI error for user {current_user.id}: {e}")
        raise HTTPException(status_code=502, detail="AI service temporarily unavailable. Please try again.")

    # 5. Save messages
    user_msg = Message(
        conversation_id=conversation.id,
        role="user",
        content=body.message,
    )
    assistant_msg = Message(
        conversation_id=conversation.id,
        role="assistant",
        content=reply,
        tokens_used=tokens,
    )
    db.add(user_msg)
    db.add(assistant_msg)

    if not conversation.title:
        conversation.title = body.message[:80] + ("…" if len(body.message) > 80 else "")
    conversation.last_message_at = datetime.now(timezone.utc)

    # 6. Increment usage
    await usage_svc.increment_message_count(db, current_user.id)
    await db.commit()

    new_remaining = max(0, remaining - 1)

    return ChatResponse(
        reply=reply,
        conversation_id=conversation.id,
        usage_remaining=new_remaining,
        plan=current_user.plan,
    )


async def _get_or_create_conversation(
    db: AsyncSession,
    user: User,
    conversation_id: str | None,
) -> Conversation:
    if conversation_id:
        result = await db.execute(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.user_id == user.id,
            )
        )
        conv = result.scalar_one_or_none()
        if conv:
            return conv

    conv = Conversation(user_id=user.id)
    db.add(conv)
    await db.flush()
    return conv


@router.get("/conversations")
async def get_conversations(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.models import Conversation
    result = await db.execute(
        select(Conversation)
        .where(Conversation.user_id == current_user.id)
        .order_by(Conversation.last_message_at.desc())
        .limit(20)
    )
    convs = result.scalars().all()
    return [
        {
            "id": str(c.id),
            "title": c.title or "Untitled",
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "last_message_at": c.last_message_at.isoformat() if c.last_message_at else None,
        }
        for c in convs
    ]


@router.get("/conversations/{conversation_id}/messages")
async def get_conversation_messages(
    conversation_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.models import Conversation
    result = await db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == current_user.id,
        )
    )
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    msgs_result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
    )
    msgs = msgs_result.scalars().all()
    return [
        {
            "role": m.role,
            "content": m.content,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in msgs
    ]



@router.post("/analyze-pdf")
async def analyze_pdf(
    file: UploadFile = File(...),
    question: str = Form(default="Please summarize this document and highlight key safety findings."),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
      logger.info(f"PDF upload: filename={file.filename}, content_type={file.content_type}")

    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    content = await file.read()
    logger.info(f"PDF size: {len(content)} bytes")

    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large. Maximum 20MB.")

    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(content))
        text = ""
        for page in reader.pages:
            text += page.extract_text() or ""
        text = text[:12000]
        logger.info(f"PDF text extracted: {len(text)} chars")
    except Exception as e:
        logger.error(f"PDF read error: {e}")
        raise HTTPException(status_code=400, detail=f"Could not read PDF: {str(e)}")

    if not text.strip():
        raise HTTPException(status_code=400, detail="PDF appears to be empty or image-only.")

    messages = [
        {
            "role": "user",
            "content": f"Document content:\n\n{text}\n\nQuestion: {question}"
        }
    ]

    try:
        reply, tokens = await chat_completion(
            messages=messages,
            user_language=current_user.language_pref,
        )
    except Exception as e:
        logger.error(f"OpenAI error: {e}")
        raise HTTPException(status_code=502, detail="AI service error.")

    conversation = await _get_or_create_conversation(db, current_user, None)
    db.add(Message(conversation_id=conversation.id, role="user", content=f"[PDF: {file.filename}] {question}"))
    db.add(Message(conversation_id=conversation.id, role="assistant", content=reply, tokens_used=tokens))
    await usage_svc.increment_message_count(db, current_user.id)
    await db.commit()

    return {
        "reply": reply,
        "conversation_id": str(conversation.id),
        "filename": file.filename,
        "pages": len(reader.pages),
    }