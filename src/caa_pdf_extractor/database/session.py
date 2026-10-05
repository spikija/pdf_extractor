"""Compatibility aliases for the configured database connection."""
from .connection import SessionLocal, engine

Session = SessionLocal
