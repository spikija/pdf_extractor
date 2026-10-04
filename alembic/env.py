from logging.config import fileConfig
from pathlib import Path
import os

from alembic import context
from sqlalchemy import engine_from_config, pool
from dotenv import load_dotenv

from caa_pdf_extractor.database.models import Base


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / ".env"

if not ENV_FILE.exists():
    raise RuntimeError(f".env file not found at: {ENV_FILE}")

load_dotenv(dotenv_path=ENV_FILE, override=True)

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError(
        f"DATABASE_URL was not found in {ENV_FILE}"
    )

config = context.config

# Alembic/ConfigParser treats % specially.
config.set_main_option(
    "sqlalchemy.url",
    DATABASE_URL.replace("%", "%%")
)

if config.config_file_name is not None:
    try:
        fileConfig(config.config_file_name)
    except Exception:
        pass

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
