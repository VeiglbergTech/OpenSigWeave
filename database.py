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

engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
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
