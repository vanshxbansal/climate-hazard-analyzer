"""MongoDB access. Falls back to an in-memory mongomock when MONGODB_URI is unset."""
from __future__ import annotations

import logging
from functools import lru_cache

from .config import settings

log = logging.getLogger("db")


@lru_cache(maxsize=1)
def get_db():
    if settings.mongodb_uri:
        from pymongo import MongoClient

        client = MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=8000, appname="climate-hazards")
        db = client[settings.mongodb_db]
        log.info("Using MongoDB database %s", settings.mongodb_db)
    else:
        import mongomock

        db = mongomock.MongoClient()[settings.mongodb_db]
        log.warning("MONGODB_URI not set: using in-memory mongomock (data lost on restart)")
    _ensure_indexes(db)
    return db


def _ensure_indexes(db) -> None:
    try:
        db.daily_tmax.create_index([("cell", 1), ("year", 1)], unique=True)
        db.analyses.create_index("created_at", expireAfterSeconds=settings.analysis_ttl_days * 86400)
        db.geocode.create_index("created_at", expireAfterSeconds=90 * 86400)
    except Exception as exc:  # index creation must never take the API down
        log.warning("Index creation failed: %s", exc)
