"""
backend/db/database.py

Database connection setup. Reads the connection string from an environment
variable so credentials never get hardcoded/committed.

Set this in a local .env file (NOT committed to git — add .env to .gitignore):
    DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@localhost:5432/ecocompute
"""

import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/ecocompute",
)

engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """FastAPI dependency: yields a DB session and closes it after use."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()