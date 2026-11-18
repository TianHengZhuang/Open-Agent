from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from openagent.core.config import get_settings
from openagent.db.sqlite_compat import dedupe_index_names
from openagent.db.models.base import Base


engine = None
async_session_maker = None


def is_sqlite_url(url: str) -> bool:
    """True for the zero-dependency local database backend."""
    return url.startswith("sqlite")


def init_db() -> None:
    global engine, async_session_maker
    settings = get_settings()
    url = settings.DATABASE_URL

    if is_sqlite_url(url):
        # Local/dev SQLite: no server, no Docker. `check_same_thread=False`
        # is required because aiosqlite runs the connection on its own thread.
        engine = create_async_engine(
            url,
            echo=settings.is_development,
            connect_args={"check_same_thread": False},
        )
    else:
        engine = create_async_engine(
            url,
            echo=settings.is_development,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=20,
            # Recycle connections so stale/forked server-side closes never
            # poison the pool; fail fast instead of queueing forever when
            # the pool is exhausted (surfaces as 503, not a hung request).
            pool_recycle=3600,
            pool_timeout=30,
        )
    async_session_maker = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )



async def ensure_schema() -> None:
    """Create tables straight from the models (SQLite/local mode).

    Production uses Alembic migrations against PostgreSQL; the zero-dependency
    local SQLite file instead bootstraps its schema from the model metadata so
    ``pnpm dev`` works with no migration step.
    """
    if engine is None:
        init_db()
    dedupe_index_names(Base.metadata)
    settings = get_settings()
    if is_sqlite_url(settings.DATABASE_URL):
        # Make sure the parent directory of a file-based SQLite database exists.
        url = settings.DATABASE_URL
        db_path = url.split("///", 1)[-1] if "///" in url else ""
        if db_path and db_path != ":memory:":
            Path(db_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db() -> AsyncSession:
    if async_session_maker is None:
        init_db()
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def close_db() -> None:
    global engine
    if engine:
        await engine.dispose()
        engine = None
