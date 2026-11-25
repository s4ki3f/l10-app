"""Engine and session factory. One engine per process."""
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from ..settings import database_url


@lru_cache
def get_engine(url: str | None = None) -> Engine:
    return create_engine(url or database_url(), pool_pre_ping=True)


def session_factory(url: str | None = None) -> sessionmaker[Session]:
    return sessionmaker(get_engine(url), expire_on_commit=False)
