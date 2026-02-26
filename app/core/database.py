import os
from pathlib import Path
from typing import Generator

from alembic import command
from alembic.config import Config
from alembic.util.exc import CommandError
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

load_dotenv()

RAW_DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./webhook_events.db")


def _with_sslmode(url: str) -> str:
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


def _is_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _is_postgres_url(url: str) -> bool:
    return url.startswith("postgresql")


def _alembic_config() -> Config:
    project_root = Path(__file__).resolve().parents[2]
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "migrations"))
    config.set_main_option("sqlalchemy.url", DATABASE_URL)
    return config


def _run_postgres_migrations() -> None:
    config = _alembic_config()

    command.upgrade(config, "head")

    try:
        command.check(config)
        return
    except CommandError as exc:
        if "New upgrade operations detected" not in str(exc):
            raise

    command.revision(config, message="auto schema sync", autogenerate=True)
    command.upgrade(config, "head")


def init_db() -> None:
    import app.db.models  # noqa: F401 ensures models are registered with Base metadata

    if _is_postgres_url(DATABASE_URL):
        auto_apply = _is_truthy(os.getenv("AUTO_APPLY_MIGRATIONS", "true"))
        if auto_apply:
            _run_postgres_migrations()
        else:
            command.upgrade(_alembic_config(), "head")
        return

    Base.metadata.create_all(bind=engine)
