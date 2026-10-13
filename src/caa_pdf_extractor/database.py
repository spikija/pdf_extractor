"""Legacy module; database configuration lives in database/connection.py."""
from .database.connection import SessionLocal, get_engine

Session = SessionLocal


def __getattr__(name):
    if name == "engine":
        return get_engine()
    raise AttributeError(name)
