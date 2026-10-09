from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession
from app.config import settings

engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=20,          # Connections in pool
    max_overflow=10,       # Extra connections when pool full
    pool_recycle=3600,     # Recycle connections after 1 hour
    echo=False             # Set to True for SQL query logging
)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def get_db():
    async with SessionLocal() as session:
        yield session

async def init_db():
    import app.models.tables  # noqa: F401  (registers tables)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
        # create_all never alters existing tables; add columns introduced later.
        if conn.dialect.name == "postgresql":
            from sqlalchemy import text
            for ddl in (
                "ALTER TABLE providers ADD COLUMN IF NOT EXISTS enabled_models JSONB",
                "ALTER TABLE providers ADD COLUMN IF NOT EXISTS default_model VARCHAR",
                "ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS vector_api_key_enc VARCHAR",
                "ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS vector_index_host VARCHAR",
                "ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS rerank_model VARCHAR",
                "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS conversation_history JSONB",
                # agent_kv is a new table — create_all handles it, but belt-and-suspenders:
                """
                CREATE TABLE IF NOT EXISTS agent_kv (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
                    user_id  UUID NOT NULL REFERENCES users(id),
                    key      VARCHAR NOT NULL,
                    value    TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (agent_id, key)
                )
                """,
            ):
                await conn.execute(text(ddl))