"""One lazy engine and session factory, shared by all compatibility imports."""
from functools import lru_cache
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from ..config import database_url


@lru_cache(maxsize=1)
def get_engine():
    return create_engine(database_url(), pool_pre_ping=True, hide_parameters=True,
                         connect_args={"connect_timeout": 10})


@lru_cache(maxsize=1)
def _session_factory():
    return sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)


class _LazySessionFactory:
    def __call__(self, **kwargs):
        return _session_factory()(**kwargs)

    def begin(self):
        return _session_factory().begin()


SessionLocal = _LazySessionFactory()


def __getattr__(name):
    if name == "engine":
        return get_engine()
    raise AttributeError(name)
