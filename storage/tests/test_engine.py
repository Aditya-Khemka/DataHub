import io
import os
import pytest
import hashlib
from typing import Generator
import storage.engine as engine

@pytest.fixture
def mock_blob_dir(tmp_path, monkeypatch):
    """Mock BLOB_DIR to point to a temporary pytest directory."""
    temp_dir = str(tmp_path / "blobs")
    monkeypatch.setattr(engine, "BLOB_DIR", temp_dir)
    os.makedirs(temp_dir, exist_ok=True)
    return temp_dir

# ---- Content chunks (put_chunk / get_chunk) ----

from storage.engine import put_chunk, get_chunk, chunk_path

def test_put_chunk_roundtrip_and_dedup(mock_blob_dir):
    data = b"chunk payload" * 1000
    h = hashlib.sha256(data).hexdigest()

    assert put_chunk(h, io.BytesIO(data)) is True
    assert os.path.exists(os.path.join(mock_blob_dir, h[:2], h))   # fan-out layout
    assert b"".join(get_chunk(h)) == data
    assert put_chunk(h, io.BytesIO(data)) is False                 # already stored, not rewritten

def test_put_chunk_rejects_wrong_content_without_leftovers(mock_blob_dir):
    h = hashlib.sha256(b"real").hexdigest()
    with pytest.raises(ValueError, match="does not match"):
        put_chunk(h, io.BytesIO(b"tampered"))
    assert os.listdir(os.path.join(mock_blob_dir, h[:2])) == []    # no temp file, no chunk

def test_put_chunk_rejects_oversized(mock_blob_dir, monkeypatch):
    monkeypatch.setattr(engine, "MAX_SIZE", 10)
    data = b"x" * 11
    with pytest.raises(ValueError, match="maximum size"):
        put_chunk(hashlib.sha256(data).hexdigest(), io.BytesIO(data))

def test_chunk_hash_must_be_valid(mock_blob_dir):
    for bad in ("../../etc/passwd", "ABC", "a" * 63):
        with pytest.raises(ValueError, match="Invalid chunk hash"):
            chunk_path(bad)
    with pytest.raises(ValueError, match="does not exist"):
        list(get_chunk("0" * 64))
