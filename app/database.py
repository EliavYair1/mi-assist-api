from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.config import settings


def get_engine():
    url = settings.database_url
    kwargs = dict(
        echo=settings.debug,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
    )
    # Supabase pooler requires statement_cache_size=0
    kwargs["connect_args"] = {"statement_cache_size": 0}
    return create_async_engine(url, **kwargs)


engine = get_engine()

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