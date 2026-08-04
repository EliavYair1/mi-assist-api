import logging
import uuid
from fastapi import APIRouter, Depends, HTTPException, Form
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from openai import AsyncOpenAI

from app.database import get_db
from app.auth import get_current_user
from app.models import User
from app.config import settings
from fastapi import UploadFile, File
import io

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
async def upload_pdf_knowledge(
    source: str = Form(...),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Only PDF files allowed")

    content = await file.read()

    try:
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(content))
        text_content = ""
        for page in reader.pages:
            text_content += page.extract_text() + "\n"
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read PDF: {str(e)}")

    if not text_content.strip():
        raise HTTPException(status_code=400, detail="PDF appears to be empty or scanned")

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
    return {"success": True, "source": source, "chunks": len(chunks)}
    
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