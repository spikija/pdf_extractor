"""Compatibility exports; use the canonical database package."""
from .database import Base, Session, get_engine


def __getattr__(name):
    if name == "engine":
        return get_engine()
    raise AttributeError(name)
