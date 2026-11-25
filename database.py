"""Database engine and session lifecycle.

The default remains SQLite for backwards compatibility during the staged refactor.
Set DATABASE_URL to a SQLAlchemy URL when deploying with a supported driver.
"""
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.engine import make_url

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./opensigweave.db")
if DATABASE_URL.startswith("postgresql"):
    postgres_password = os.environ.get("POSTGRES_PASSWORD", "")
    if not postgres_password or postgres_password.lower().startswith(("replace_", "changeme")):
        raise RuntimeError("Set a generated POSTGRES_PASSWORD before using PostgreSQL")
    if make_url(DATABASE_URL).password != postgres_password:
        raise RuntimeError("The password in DATABASE_URL must match POSTGRES_PASSWORD")
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return value


pool_options = {}
if DATABASE_URL.startswith("postgresql"):
    # All limits apply PER Uvicorn worker: plan for total database connections.
    pool_options = {
        "pool_size": _bounded_int("DB_POOL_SIZE", 5, 1, 50),
        "max_overflow": _bounded_int("DB_MAX_OVERFLOW", 10, 0, 50),
        "pool_timeout": _bounded_int("DB_POOL_TIMEOUT", 10, 1, 120),
        "pool_recycle": _bounded_int("DB_POOL_RECYCLE", 1800, 60, 86400),
    }

engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
    **pool_options,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """Yield one SQLAlchemy session per request and always close it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
