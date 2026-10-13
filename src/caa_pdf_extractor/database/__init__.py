from .models import Base
from .connection import SessionLocal, get_engine

Session = SessionLocal
__all__ = ["Base", "Session", "SessionLocal", "engine"]


def __getattr__(name):
    if name == "engine":
        return get_engine()
    raise AttributeError(name)
