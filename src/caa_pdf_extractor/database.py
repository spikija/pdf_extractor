"""Legacy module; database configuration lives in database/connection.py."""
from .database.connection import SessionLocal, engine

Session = SessionLocal
