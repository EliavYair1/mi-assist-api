import asyncio, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import text
from app.config import settings

client = AsyncOpenAI(api_key=settings.openai_api_key)

CHUNKS = [
    ("safety", "loto", "OSHA 29 CFR 1910.147",
     "Lockout/Tagout 6 steps: 1.Prepare 2.Notify 3.Shutdown 4.Isolate 5.Lock/Tag 6.Verify zero energy. Each worker applies personal lock. Required annual inspection per OSHA 1910.147."),
    ("safety", "confined_space", "OSHA 29 CFR 1910.146",
     "Permit-required confined space: test atmosphere O2 19.5-23.5%, LEL below 10%, toxic below PEL. PPE: full-body harness, supplied-air respirator, multi-gas detector, hard hat ANSI Z89.1."),
    ("ndt", "ut_thickness", "ASME Section V / API 570",
     "UT thickness measurement: clean surface, select 5MHz transducer, calibrate on IIW block, apply couplant, scan grid pattern, record minimum at each CML. Recalibrate every 4 hours."),
    ("api_inspection", "api_570", "API 570 Section 6",
     "API 570 piping inspection intervals: Class 1 high-risk UT every 3 years, Class 2 normal every 5 years, Class 3 low-risk every 10 years. Remaining life = (actual - t_min) / corrosion rate."),
    ("api_inspection", "api_510", "API 510 Section 6",
     "API 510 pressure vessel inspection: external every 5 years, internal every 10 years or half remaining life. Minimum thickness t_min = P*R/(S*E - 0.6*P)."),
]

async def seed():
    engine = create_async_engine(settings.database_url, echo=False)
    SM = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with SM() as db:
        for domain, topic, source, content in CHUNKS:
            res = await db.execute(text("SELECT id FROM knowledge_chunks WHERE source = :s"), {"s": source})
            if res.fetchone():
                print(f"  skip: {source}")
                continue
            print(f"  embedding: {source}...")
            r = await client.embeddings.create(model="text-embedding-3-small", input=content)
            emb = "[" + ",".join(str(x) for x in r.data[0].embedding) + "]"
            await db.execute(text("""
                INSERT INTO knowledge_chunks (content, source, domain, topic, embedding)
                VALUES (:content, :source, :domain, :topic, :emb ::vector)
            """), {"content": content, "source": source, "domain": domain, "topic": topic, "emb": emb})
            await db.commit()
            print(f"  done: {source}")
    await engine.dispose()
    print("Seed complete!")

asyncio.run(seed())