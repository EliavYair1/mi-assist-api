import logging
import uuid
import io
from fastapi import APIRouter, Depends, HTTPException, Form, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from openai import AsyncOpenAI

from app.database import get_db
from app.auth import get_current_user
from app.models import User
from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()
client = AsyncOpenAI(api_key=settings.openai_api_key)


async def create_embedding(text_content: str) -> list:
    response = await client.embeddings.create(
        model="text-embedding-3-small",
        input=text_content
    )
    return response.data[0].embedding


@router.post("/add-text")
async def add_text_knowledge(
    content: str = Form(...),
    source: str = Form(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not content.strip():
        raise HTTPException(status_code=400, detail="Content is empty")
    embedding = await create_embedding(content)
    await db.execute(
        text("""
            INSERT INTO knowledge_chunks (id, content, source, embedding, domain)
            VALUES (:id, :content, :source, :embedding, :domain)
        """),
        {
            "id": str(uuid.uuid4()),
            "content": content,
            "source": source,
            "embedding": str(embedding),
            "domain": "safety",
        }
    )
    await db.commit()
    return {"success": True, "source": source}


@router.post("/upload-pdf")
async def upload_file_knowledge(
    source: str = Form(...),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    content = await file.read()
    filename = file.filename or ""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    text_content = ""

    try:
        if ext == "pdf":
            import pypdf
            reader = pypdf.PdfReader(io.BytesIO(content))
            for page in reader.pages:
                text_content += page.extract_text() + "\n"

        elif ext == "docx":
            import mammoth
            result = mammoth.extract_raw_text(io.BytesIO(content))
            text_content = result.value

        elif ext in ("txt", "md"):
            text_content = content.decode("utf-8", errors="ignore")

        elif ext == "csv":
            import csv
            decoded = content.decode("utf-8", errors="ignore")
            reader = csv.reader(decoded.splitlines())
            rows = list(reader)
            text_content = "\n".join([", ".join(row) for row in rows[:500]])

        elif ext == "xlsx":
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True)
            for sheet in wb.worksheets:
                text_content += f"\n[Sheet: {sheet.title}]\n"
                for row in sheet.iter_rows(max_row=500, values_only=True):
                    text_content += ", ".join([str(c) if c is not None else "" for c in row]) + "\n"

        elif ext in ("jpg", "jpeg", "png", "webp"):
            import base64
            b64 = base64.b64encode(content).decode()
            mime = f"image/{'jpeg' if ext in ('jpg', 'jpeg') else ext}"
            response = await client.chat.completions.create(
                model="gpt-4o",
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                        {"type": "text", "text": "Describe this image in detail for an industrial safety knowledge base. Extract all text, labels, measurements, warnings, and technical information visible."}
                    ]
                }],
                max_tokens=1000
            )
            text_content = response.choices[0].message.content

        else:
            raise HTTPException(status_code=400, detail=f"Unsupported file type: .{ext}")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read file: {str(e)}")

    if not text_content.strip():
        raise HTTPException(status_code=400, detail="File appears to be empty.")

    chunk_size = 5000
    chunks = [text_content[i:i+chunk_size] for i in range(0, len(text_content), chunk_size)]
    chunks = [c for c in chunks if len(c.strip()) > 50]

    for i, chunk in enumerate(chunks):
        embedding = await create_embedding(chunk)
        chunk_source = f"{source} (part {i+1})" if len(chunks) > 1 else source
        await db.execute(
            text("""
                INSERT INTO knowledge_chunks (id, content, source, embedding, domain)
                VALUES (:id, :content, :source, :embedding, :domain)
            """),
            {
                "id": str(uuid.uuid4()),
                "content": chunk,
                "source": chunk_source,
                "embedding": str(embedding),
                "domain": "safety",
            }
        )

    await db.commit()
    return {"success": True, "source": source, "chunks": len(chunks), "file_type": ext}


@router.get("/list")
async def list_knowledge(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        text("SELECT id, source, LEFT(content, 100) as preview FROM knowledge_chunks ORDER BY source")
    )
    rows = result.fetchall()
    return [{"id": str(r.id), "source": r.source, "preview": r.preview} for r in rows]


@router.get("/search")
async def search_knowledge(
    q: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    embedding = await create_embedding(q)
    result = await db.execute(
        text("""
            SELECT id, source, LEFT(content, 150) as preview,
                   1 - (embedding <=> CAST(:embedding AS vector)) as score
            FROM knowledge_chunks
            ORDER BY embedding <=> CAST(:embedding AS vector)
            LIMIT 10
        """),
        {"embedding": str(embedding)}
    )
    rows = result.fetchall()
    return [{"id": str(r.id), "source": r.source, "preview": r.preview, "score": round(r.score, 3)} for r in rows]


@router.get("/{chunk_id}")
async def get_knowledge(
    chunk_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        text("SELECT id, source, content FROM knowledge_chunks WHERE id = :id"),
        {"id": chunk_id}
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    return {"id": str(row.id), "source": row.source, "content": row.content}


@router.put("/{chunk_id}")
async def update_knowledge(
    chunk_id: str,
    content: str = Form(...),
    source: str = Form(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    embedding = await create_embedding(content)
    await db.execute(
        text("""
            UPDATE knowledge_chunks
            SET content = :content, source = :source, embedding = :embedding
            WHERE id = :id
        """),
        {
            "content": content,
            "source": source,
            "embedding": str(embedding),
            "id": chunk_id,
        }
    )
    await db.commit()
    return {"success": True}


@router.delete("/{chunk_id}")
async def delete_knowledge(
    chunk_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await db.execute(
        text("DELETE FROM knowledge_chunks WHERE id = :id"),
        {"id": chunk_id}
    )
    await db.commit()
    return {"success": True, "deleted": chunk_id}