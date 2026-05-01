import asyncio, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from app.config import settings

SQL = [
    "CREATE EXTENSION IF NOT EXISTS vector",
    """CREATE TABLE IF NOT EXISTS knowledge_chunks (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        content TEXT NOT NULL,
        source VARCHAR(200) NOT NULL,
        domain VARCHAR(50) NOT NULL,
        topic VARCHAR(100),
        embedding vector(1536),
        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
    )""",
    """CREATE INDEX IF NOT EXISTS idx_chunks_embedding
        ON knowledge_chunks
        USING ivfflat (embedding vector_cosine_ops)
        WITH (lists = 100)"""
]

async def run():
    engine = create_async_engine(settings.database_url, echo=False)
    async with engine.begin() as conn:
        for stmt in SQL:
            await conn.execute(text(stmt))
            print(f"done: {stmt[:40]}...")
    await engine.dispose()
    print("Migration complete!")

asyncio.run(run())