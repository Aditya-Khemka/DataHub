import hashlib
import random

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import storage.engine as storage
from api.server import app, get_db_session
from core import chunker
from core.objects import build_trees, commit_hash
from infrastructure.db import Base

T = "2026-01-01T00:00:00Z"


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Fresh DB and chunk folder per test; tiny chunk sizes so tests stay fast."""
    monkeypatch.setattr(storage, "BLOB_DIR", str(tmp_path / "blobs"))
    monkeypatch.setattr(chunker, "MIN_SIZE", 64)
    monkeypatch.setattr(chunker, "AVG_SIZE", 256)
    monkeypatch.setattr(chunker, "MAX_SIZE", 1024)
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    def session_override():
        with Session() as s:
            yield s

    app.dependency_overrides[get_db_session] = session_override
    yield TestClient(app)
    app.dependency_overrides.clear()


def push_file(client, tmp_path, data):
    """What the CLI will do for one file: chunk, ask what's missing, upload only that, register."""
    path = tmp_path / "upload"
    path.write_bytes(data)
    file_hash, size, chunks = chunker.chunk_file(str(path), 64, 256, 1024)

    missing = client.post("/chunks/missing", json={"hashes": [h for h, _, _ in chunks]}).json()["missing"]
    for h, offset, length in chunks:
        if h in missing:
            assert client.put(f"/chunks/{h}", content=data[offset:offset + length]).status_code == 200
            missing.remove(h)

    record = {"file_hash": file_hash, "size": size, "chunker": chunker.CHUNKER,
              "chunks": [{"hash": h, "length": n} for h, _, n in chunks]}
    assert client.post("/files/", json={"files": [record]}).status_code == 200
    return file_hash, len({h for h, _, _ in chunks})


def test_full_push_flow(client, tmp_path):
    data = random.Random(2).randbytes(20_000)
    f1, _ = push_file(client, tmp_path, data)
    assert client.post("/files/exists", json={"hashes": [f1, "0" * 64]}).json() == {"existing": [f1]}
    assert client.get("/branches/main").status_code == 404

    files = {"data/train.csv": f1}
    c1 = client.post("/commit/", json={"parent_hash": None, "files": files, "author": "me", "message": "v1", "time": T}).json()["commit_hash"]
    assert c1 == commit_hash(build_trees(files)[0], None, "me", T, "v1")   # client and server agree on the hash
    assert client.get("/branches/main").json() == {"name": "main", "commit_hash": c1}

    # Second version: rows inserted near the top -> only a few chunks are new on the server
    v2 = data[:5000] + b"new rows" + data[5000:]
    path = tmp_path / "v2"; path.write_bytes(v2)
    _, _, chunks = chunker.chunk_file(str(path), 64, 256, 1024)
    assert len(client.post("/chunks/missing", json={"hashes": [h for h, _, _ in chunks]}).json()["missing"]) <= 3
    f2, _ = push_file(client, tmp_path, v2)

    c2 = client.post("/commit/", json={"parent_hash": c1, "files": {"data/train.csv": f2}, "author": "me", "message": "v2", "time": T}).json()["commit_hash"]
    assert [c["commit_hash"] for c in client.get("/log").json()["history"]] == [c2, c1]


def test_commit_conflict_and_retry(client, tmp_path):
    f1, _ = push_file(client, tmp_path, b"some data" * 50)
    body = {"parent_hash": None, "files": {"a.csv": f1}, "author": "me", "message": "first", "time": T}
    c1 = client.post("/commit/", json=body).json()["commit_hash"]

    assert client.post("/commit/", json=body).json()["commit_hash"] == c1                        # retry -> same commit
    assert client.post("/commit/", json=body | {"message": "someone else"}).status_code == 409    # main already exists
    assert client.post("/commit/", json=body | {"files": {"a.csv": "f" * 64}}).status_code == 400  # unknown file
    assert client.post("/commit/", json={"files": {}}).status_code == 422                       # malformed body


def test_chunk_upload_rejections(client):
    good = b"x" * 100
    h = hashlib.sha256(good).hexdigest()

    assert client.put(f"/chunks/{h}", content=b"tampered").status_code == 400          # content != hash
    assert client.put("/chunks/not-a-hash", content=good).status_code == 400           # invalid hash
    assert client.put(f"/chunks/{h}", content=b"x" * 2000).status_code == 413          # over MAX_SIZE (patched to 1024)
    assert client.post("/chunks/missing", json={"hashes": ["0" * 64] * 10_001}).status_code == 422  # batch too large

    bad_record = {"file_hash": h, "size": 100, "chunker": chunker.CHUNKER, "chunks": [{"hash": h, "length": 100}]}
    assert client.post("/files/", json={"files": [bad_record]}).status_code == 400     # chunk never uploaded
