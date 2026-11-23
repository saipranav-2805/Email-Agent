import contextlib
import logging
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from app.config import settings
from app.db.models import Base

logger = logging.getLogger(__name__)

engine: AsyncEngine
async_session_factory: async_sessionmaker[AsyncSession]


def get_normalized_database_url(url: str) -> str:
    """Normalizes DATABASE_URL to use async drivers (asyncpg for postgres, aiosqlite for sqlite)."""
    if not url:
        return "sqlite+aiosqlite:///./placement_agent.db"

    clean_url = url.strip()
    if clean_url.startswith("postgres://"):
        clean_url = clean_url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif clean_url.startswith("postgresql://") and not clean_url.startswith("postgresql+asyncpg://"):
        clean_url = clean_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif clean_url.startswith("sqlite://") and not clean_url.startswith("sqlite+aiosqlite://"):
        clean_url = clean_url.replace("sqlite://", "sqlite+aiosqlite://", 1)

    return clean_url


def setup_database_engine(db_url: str) -> AsyncEngine:
    global engine, async_session_factory
    normalized_url = get_normalized_database_url(db_url)

    connect_args = {}
    if "sqlite" in normalized_url:
        connect_args["check_same_thread"] = False

    try:
        engine = create_async_engine(
            normalized_url,
            echo=False,
            future=True,
            connect_args=connect_args,
        )
        async_session_factory = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        logger.info(f"Initialized database engine with: {normalized_url.split('@')[-1] if '@' in normalized_url else normalized_url}")
        return engine
    except Exception as e:
        logger.error(f"Failed to initialize database engine for {normalized_url}: {e}")
        # Fallback to local SQLite
        fallback_url = "sqlite+aiosqlite:///./placement_agent.db"
        logger.warning(f"Falling back to local SQLite: {fallback_url}")
        engine = create_async_engine(
            fallback_url,
            echo=False,
            future=True,
            connect_args={"check_same_thread": False},
        )
        async_session_factory = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        return engine


# Initialize engine at module level
engine = setup_database_engine(settings.DATABASE_URL)


async def init_db():
    """Initializes database tables, with safety fallback to SQLite on connection failure."""
    global engine, async_session_factory
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to connect to primary DB ({settings.DATABASE_URL}): {e}")
        logger.info("Falling back to local SQLite database...")
        fallback_url = "sqlite+aiosqlite:///./placement_agent.db"
        engine = create_async_engine(
            fallback_url,
            echo=False,
            future=True,
            connect_args={"check_same_thread": False},
        )
        async_session_factory = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Fallback SQLite database initialized successfully.")


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for FastAPI route handlers."""
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@contextlib.asynccontextmanager
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Context manager for standalone background tasks, poller, and scheduler."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
