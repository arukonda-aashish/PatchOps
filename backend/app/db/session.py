"""Async SQLAlchemy session factory"""
import os
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

_is_worker = os.getenv("IS_CELERY_WORKER", "false").lower() == "true"

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    **({} if _is_worker else {"pool_size": 10, "max_overflow": 20}),
    poolclass=NullPool if _is_worker else None,
    pool_pre_ping=not _is_worker,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    pass


async def init_db():
    """Create tables if they don't exist (Alembic handles migrations in prod)"""
    from app.models import all_models  # noqa – imports trigger registration
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
