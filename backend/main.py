"""
backend/main.py
================
Skeleton FastAPI app for Day 2-3 infrastructure work (Deepak's task).

This file exists only to prove the docker-compose stack (postgres,
redis, fastapi) runs healthy end-to-end. It intentionally does NOT
implement /ci/{region} or /calculate - those are Paramjeet's Day 2-3
deliverables and should be added to this same file (or split into
routers under backend/) without touching the Docker wiring below.
"""

import os

import redis
from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine, text

app = FastAPI(title="EcoCompute Backend")

DATABASE_URL = os.environ["DATABASE_URL"]
REDIS_URL = os.environ["REDIS_URL"]

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
redis_client = redis.Redis.from_url(REDIS_URL)


@app.get("/")
def root() -> dict:
    return {"message": "EcoCompute backend is up"}


@app.get("/health")
def health() -> dict:
    """Confirms postgres and redis are both reachable from the fastapi container."""
    status = {"postgres": "unknown", "redis": "unknown"}

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        status["postgres"] = "ok"
    except Exception as exc:  # noqa: BLE001
        status["postgres"] = f"error: {exc}"

    try:
        redis_client.ping()
        status["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        status["redis"] = f"error: {exc}"

    if "error" in status["postgres"] or "error" in status["redis"]:
        raise HTTPException(status_code=503, detail=status)
    return status
