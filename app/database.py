from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.config import settings
import asyncpg


async def init_connection(conn):
    """Disable prepared statement cache on every new connection."""
    await conn.execute("SET statement_timeout = '30s'")


async def create_pool():
    return await asyncpg.create_pool(
        dsn=settings.database_url.replace("postgresql+asyncpg://", "postgresql://"),
        statement_cache_size=0,
        min_size=2,
        max_size=10,
    )


engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    execution_options={"compiled_cache": {}},
    connect_args={"statement_cache_size": 0},
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise