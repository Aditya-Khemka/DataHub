"""
Shared test fixtures.

Database: in-memory SQLite by default. Set TEST_DATABASE_URL (e.g.
postgresql://user:password@localhost:5432/datahub_test) to run the same tests on Postgres.
Never point it at a database you care about: every test drops and recreates all tables.
"""
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import storage.engine as storage
from core import chunker
from infrastructure.db import Base

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


@pytest.fixture
def db_engine():
    """A fresh, empty database per test."""
    if TEST_DATABASE_URL:
        engine = create_engine(TEST_DATABASE_URL)
        Base.metadata.drop_all(engine)  # leftovers from an interrupted run
    else:
        engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    yield engine
    if TEST_DATABASE_URL:
        Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def db_session(db_engine):
    Session = sessionmaker(bind=db_engine)
    with Session() as s:
        yield s


@pytest.fixture
def chunk_store(tmp_path, monkeypatch):
    """Private chunk folder + tiny chunk sizes (fastcdc minimums) so tests stay fast."""
    monkeypatch.setattr(storage, "BLOB_DIR", str(tmp_path / "blobs"))
    monkeypatch.setattr(chunker, "MIN_SIZE", 64)
    monkeypatch.setattr(chunker, "AVG_SIZE", 256)
    monkeypatch.setattr(chunker, "MAX_SIZE", 1024)
    return tmp_path / "blobs"
