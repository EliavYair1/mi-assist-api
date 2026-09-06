from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from datetime import datetime, timezone
import logging
import io

from app.database import get_db
from app.models import User, Conversation, Message
from app.auth import get_current_user
from app.services import usage as usage_svc
from app.services.openai_service import chat_completion

logger = logging.getLogger(__name__)
router = APIRouter()
MAX_HISTORY = 10

IMAGE_TYPES = ("jpg", "jpeg", "png", "webp")
DOC_TYPES = ("pdf", "docx", "xlsx", "csv", "txt", "md", "pptx")


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


SAFETY_KEYWORDS = [
    "safety", "osha", "ndt", "api", "inspection", "hazard", "ppe", "loto",
    "confined space", "hot work", "fall protection", "welding", "pipe", "tank",
    "pressure", "valve", "ultrasonic", "radiograph", "corrosion", "scaffold",
    "permit", "jsa", "hazop", "refinery", "pipeline", "asme", "ansi", "niosh",
    "ladder", "climb", "height", "fall", "hard hat", "harness", "rigging",
    "crane", "excavation", "trench", "electrical", "arc flash", "chemical",
    "toxic", "gas", "h2s", "benzene", "silica", "respirator", "ventilation",
    "emergency", "incident", "accident", "near miss", "risk", "hazard",
    "procedure", "sop", "jha", "work permit", "toolbox", "training",
    "translate", "translation", "summarize", "explain", "what is", "how to",
    "what are", "steps", "dwell", "wait time", "waiting", "time", "duration",
    "בטיחות", "בדיקה", "צנרת", "מיכל", "סכנה", "ציוד מגן", "תרגם", "תרגום",
    "סולם", "טיפוס", "גובה", "נפילה", "רתימה", "כימי", "גז", "נשימה",
    "חירום", "תאונה", "סיכון", "נוהל", "הדרכה", "המתנה", "זמן"
]


def is_safety_related(message: str) -> bool:
    msg_lower = message.lower()
    return any(kw in msg_lower for kw in SAFETY_KEYWORDS)


def check_image_permission(plan: str) -> str | None:
    """Returns error message if plan doesn't allow images, else None."""
    if plan == "free":
        return "Image analysis is not available on the Free plan. Upgrade to Pro or higher to analyze images."
    return None


def check_file_permission(plan: str, ext: str) -> str | None:
    """Returns error message if plan doesn't allow this file type, else None."""
    if ext in IMAGE_TYPES:
        if plan == "free":
            return "Image uploads require Pro plan or higher."
    elif ext in DOC_TYPES:
        if plan in ("free", "pro"):
            return "Document uploads require Expert plan or higher."
    return None


@router.post("", response_model=ChatResponse)
async def send_message(
    body: ChatRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # 1. Usage check
    usage = await usage_svc.get_usage_today(db, current_user.id)
    allowed, remaining = usage_svc.check_message_limit(usage, current_user.plan)
    if not allowed:
        limit = usage_svc.get_limit(current_user.plan)
        raise HTTPException(status_code=429, detail={
            "error": "daily_limit_reached",
            "plan": current_user.plan,
            "limit": limit,
            "message": "You've reached your daily question limit.",
            "upgrade_url": "/pricing"
        })

    # 2. Get/create conversation
    conversation = await _get_or_create_conversation(db, current_user, body.conversation_id)

    # 2a. Plan-based image restrictions
    if body.image_base64:
        err = check_image_permission(current_user.plan)
        if err:
            return ChatResponse(
                reply=err,
                conversation_id=str(conversation.id),
                usage_remaining=remaining,
                plan=current_user.plan,
            )

    # 2b. Domain check — skip if follow-up OR short contextual question
    is_followup = body.conversation_id is not None
    is_short_contextual = len(body.message.strip()) < 60
    if not is_followup and not is_short_contextual and not is_safety_related(body.message) and not body.image_base64:
        return ChatResponse(
            reply="MI Assist is focused on industrial safety, inspections, NDT, API-related guidance, field procedures, industrial compliance, and related field operations. Please ask a question related to one of these areas.",
            conversation_id=str(conversation.id),
            usage_remaining=remaining,
            plan=current_user.plan,
        )

    # 3. History
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.desc())
        .limit(MAX_HISTORY)
    )
    history = list(reversed(result.scalars().all()))
    openai_messages = [{"role": msg.role, "content": msg.content} for msg in history]

    if body.image_base64 and body.image_type:
        openai_messages.append({
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:{body.image_type};base64,{body.image_base64}"}},
                {"type": "text", "text": body.message or "Please analyze this image from a safety and inspection perspective."}
            ]
        })
    else:
        openai_messages.append({"role": "user", "content": body.message})

    try:
        reply, tokens = await chat_completion(messages=openai_messages, user_language=current_user.language_pref)
    except Exception as e:
        logger.error(f"OpenAI error: {e}")
        raise HTTPException(status_code=502, detail="AI service temporarily unavailable.")

    db.add(Message(conversation_id=conversation.id, role="user", content=body.message))
    db.add(Message(conversation_id=conversation.id, role="assistant", content=reply, tokens_used=tokens))
    if not conversation.title:
        conversation.title = body.message[:80]
    conversation.last_message_at = datetime.now(timezone.utc)
    await usage_svc.increment_message_count(db, current_user.id)
    await db.commit()

    return ChatResponse(
        reply=reply,
        conversation_id=str(conversation.id),
        usage_remaining=max(0, remaining - 1),
        plan=current_user.plan
    )


async def _get_or_create_conversation(db, user, conversation_id):
    if conversation_id:
        result = await db.execute(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.user_id == user.id
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
    db: AsyncSession = Depends(get_db)
):
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
            "last_message_at": c.last_message_at.isoformat() if c.last_message_at else None
        }
        for c in convs
    ]


@router.get("/conversations/{conversation_id}/messages")
async def get_conversation_messages(
    conversation_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == current_user.id
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
            "created_at": m.created_at.isoformat() if m.created_at else None
        }
        for m in msgs
    ]


@router.post("/analyze-pdf")
async def analyze_file(
    file: UploadFile = File(...),
    question: str = Form(default="Please summarize this document and highlight key safety findings."),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    content = await file.read()
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large. Maximum 20MB.")

    filename = file.filename or ""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    # Plan-based file restrictions
    err = check_file_permission(current_user.plan, ext)
    if err:
        raise HTTPException(status_code=403, detail=err)

    text = ""

    try:
        if ext == "pdf":
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(content))
            if reader.is_encrypted:
                try: reader.decrypt("")
                except: pass
            for page in reader.pages:
                text += page.extract_text() or ""

        elif ext == "docx":
            import mammoth
            result = mammoth.extract_raw_text(io.BytesIO(content))
            text = result.value

        elif ext in ("txt", "md"):
            text = content.decode("utf-8", errors="ignore")

        elif ext == "csv":
            import csv
            decoded = content.decode("utf-8", errors="ignore")
            reader = csv.reader(decoded.splitlines())
            rows = list(reader)
            text = "\n".join([", ".join(row) for row in rows[:200]])

        elif ext == "xlsx":
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True)
            for sheet in wb.worksheets:
                text += f"\n[Sheet: {sheet.title}]\n"
                for row in sheet.iter_rows(max_row=200, values_only=True):
                    text += ", ".join([str(c) if c is not None else "" for c in row]) + "\n"

        elif ext in IMAGE_TYPES:
            import base64
            b64 = base64.b64encode(content).decode()
            mime = f"image/{'jpeg' if ext in ('jpg', 'jpeg') else ext}"
            from app.services.openai_service import client as openai_client
            response = await openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                        {"type": "text", "text": question}
                    ]
                }],
                max_tokens=1000
            )
            text = response.choices[0].message.content

        else:
            raise HTTPException(status_code=400, detail=f"Unsupported file type: .{ext}")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"File read error: {e}")
        raise HTTPException(status_code=400, detail=f"Could not read file: {str(e)}")

    text = text[:12000]
    if not text.strip():
        raise HTTPException(status_code=400, detail="File appears to be empty.")

    messages = [{"role": "user", "content": f"Document content:\n\n{text}\n\nQuestion: {question}"}]
    try:
        reply, tokens = await chat_completion(messages=messages, user_language=current_user.language_pref)
    except Exception as e:
        logger.error(f"OpenAI error: {e}")
        raise HTTPException(status_code=502, detail="AI service error.")

    conversation = await _get_or_create_conversation(db, current_user, None)
    db.add(Message(conversation_id=conversation.id, role="user", content=f"[FILE: {filename}] {question}"))
    db.add(Message(conversation_id=conversation.id, role="assistant", content=reply, tokens_used=tokens))
    await usage_svc.increment_message_count(db, current_user.id)
    await db.commit()

    return {"reply": reply, "conversation_id": str(conversation.id), "filename": filename}