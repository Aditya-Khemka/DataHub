import io
import os
import pytest
import hashlib
from typing import Generator
import storage.engine as engine
from storage.engine import put_blob, get_blob

@pytest.fixture
def mock_blob_dir(tmp_path, monkeypatch):
    """Mock BLOB_DIR to point to a temporary pytest directory."""
    temp_dir = str(tmp_path / "blobs")
    monkeypatch.setattr(engine, "BLOB_DIR", temp_dir)
    os.makedirs(temp_dir, exist_ok=True)
    return temp_dir

def test_put_blob_new(mock_blob_dir):
    data = b"Hello, DataHub!"
    expected_hash = hashlib.sha256(data).hexdigest()
    
    stream = io.BytesIO(data)
    result_hash = put_blob(stream)
    
    assert result_hash == expected_hash
    # Check if the file physically exists
    assert os.path.exists(os.path.join(mock_blob_dir, expected_hash))
    
    # Check contents
    with open(os.path.join(mock_blob_dir, expected_hash), "rb") as f:
        assert f.read() == data

def test_put_blob_duplicate(mock_blob_dir):
    data = b"This is a duplicate test"
    stream1 = io.BytesIO(data)
    hash1 = put_blob(stream1)
    
    # Store the modification time
    blob_path = os.path.join(mock_blob_dir, hash1)
    mtime_before = os.path.getmtime(blob_path)
    
    # Process identical stream again
    stream2 = io.BytesIO(data)
    hash2 = put_blob(stream2)
    
    assert hash1 == hash2
    mtime_after = os.path.getmtime(blob_path)
    
    # Ensure it wasn't overwritten (mtime should be exactly the same)
    assert mtime_before == mtime_after

def test_get_blob_success(mock_blob_dir):
    data = b"Streaming chunks test data" * 1000  # Make it decent size
    stream = io.BytesIO(data)
    blob_hash = put_blob(stream)
    
    # Read via get_blob generator
    chunk_generator = get_blob(blob_hash)
    assert isinstance(chunk_generator, Generator)
    
    reconstructed_data = b"".join(chunk_generator)
    assert reconstructed_data == data

def test_get_blob_not_found(mock_blob_dir):
    with pytest.raises(ValueError) as exc:
        list(get_blob("nonexistent_hash_12345"))
    assert "nonexistent_hash_12345 does not exist" in str(exc.value)


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
