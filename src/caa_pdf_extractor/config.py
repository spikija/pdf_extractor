"""Shared local configuration; environment values take precedence over .env."""
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_environment():
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(PROJECT_ROOT / ".env", override=False)


def database_url():
    load_environment()
    value = os.getenv("DATABASE_URL")
    if not value or not value.strip():
        raise RuntimeError("DATABASE_URL is required for database operations; set it in the environment or optional project .env.")
    return value


def pdf_db_path():
    load_environment()
    return Path(os.getenv("PDF_DB_PATH") or PROJECT_ROOT / "pdf_db")
