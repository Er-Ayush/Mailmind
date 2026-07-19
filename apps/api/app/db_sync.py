"""Synchronous DB access for Celery workers (Celery tasks are sync-world)."""

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

sync_engine = create_engine(get_settings().sync_database_url, pool_pre_ping=True)

SyncSession = sessionmaker(bind=sync_engine, expire_on_commit=False, class_=Session)
