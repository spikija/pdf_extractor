"""Compatibility aliases for the configured database connection."""
from .connection import SessionLocal, get_engine

Session = SessionLocal


def __getattr__(name):
    if name == "engine":
        return get_engine()
    raise AttributeError(name)
