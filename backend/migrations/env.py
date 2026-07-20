# migrations/env.py
#
# This is the standard alembic-generated file with two additions:
#   1. Import Base and models so autogenerate can see our tables
#   2. Read the DB URL from the DATABASE_URL environment variable
#
# Copy these snippets into the file `alembic init` created for you —
# don't overwrite the whole file, just add/replace the marked sections.

import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

# --- ADDITION: make backend/ importable, then import our models ---
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from db.database import Base  # noqa: E402
from db import models  # noqa: E402,F401  (import so tables register on Base.metadata)

config = context.config

# --- ADDITION: override sqlalchemy.url with the env var at runtime ---
config.set_main_option(
    "sqlalchemy.url",
    os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/ecocompute"),
)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# --- ADDITION: point autogenerate at our models' metadata ---
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
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