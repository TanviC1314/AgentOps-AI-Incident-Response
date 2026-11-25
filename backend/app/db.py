from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def make_engine(url: str):
    if url.startswith("sqlite"):
        kwargs: dict = {"connect_args": {"check_same_thread": False}}
        if ":memory:" in url or url == "sqlite://":
            kwargs["poolclass"] = StaticPool
        return create_engine(url, **kwargs)
    return create_engine(url, pool_pre_ping=True)


_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def init_db(url: str | None = None):
    """(Re)initialise the global engine. Tests call this with an isolated URL."""
    global _engine, _SessionLocal
    _engine = make_engine(url or get_settings().database_url)
    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    return _engine


def get_engine():
    if _engine is None:
        init_db()
    return _engine


def session_factory() -> sessionmaker[Session]:
    if _SessionLocal is None:
        init_db()
    return _SessionLocal  # type: ignore[return-value]


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one session per request."""
    session = session_factory()()
    try:
        yield session
    finally:
        session.close()
