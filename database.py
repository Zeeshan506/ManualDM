import os
from typing import Generator

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

load_dotenv()

RAW_DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./webhook_events.db")


def _with_sslmode(url: str) -> str:
    """Ensure Postgres connections use SSL when talking to Supabase."""
    if not url.startswith("postgresql"):
        return url
    if "sslmode=" in url:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}sslmode=require"


DATABASE_URL = _with_sslmode(RAW_DATABASE_URL)

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db() -> Generator:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create tables for dev/sqlite; safe no-op for existing Postgres schema."""
    import models  # noqa: F401 ensures models are registered with Base metadata

    # Only run create_all for convenience; prod should rely on migrations.
    Base.metadata.create_all(bind=engine)
# *** End Patch