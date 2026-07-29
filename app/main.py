from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging

from app.database import engine, Base
from app.routers import auth, chat, usage, billing, upload, knowledge
from app.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: create DB tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("MI Assist API started — tables ready")
    yield
    # Shutdown
    await engine.dispose()
    logger.info("MI Assist API shut down")


app = FastAPI(
    title="MI Assist API",
    description="MetroIntegrity — Safety First. Integrity Always.",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.debug else None,   # hide in production
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://metrointegrity.com", "https://www.metrointegrity.com"],
    allow_credentials=True,
allow_methods=["GET", "POST", "DELETE", "PUT", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(auth.router,    prefix="/v1/auth",    tags=["Auth"])
app.include_router(chat.router,    prefix="/v1/chat",    tags=["Chat"])
app.include_router(usage.router,   prefix="/v1/usage",   tags=["Usage"])
app.include_router(billing.router, prefix="/v1/billing", tags=["Billing"])
app.include_router(upload.router,  prefix="/v1/upload",  tags=["Upload"])
app.include_router(knowledge.router, prefix="/v1/knowledge", tags=["Knowledge"])

@app.get("/health")
async def health():
    return {"status": "ok", "service": "MI Assist API", "version": "1.0.0"}
