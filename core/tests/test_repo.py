import hashlib
import io
import random

import pytest
from core import chunker, repo
from infrastructure.db import Branch, Chunk, get_branch_history


@pytest.fixture
def session(db_session, chunk_store):
    """Fresh DB + chunk folder per test with tiny chunk sizes (see conftest.py)."""
    return db_session


def write(tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data)
    return str(path)


DATA = random.Random(1).randbytes(20_000)


def test_store_and_read_roundtrip_with_dedup(session, tmp_path):
    f1 = repo.store_file(session, write(tmp_path, "v1", DATA))
    assert f1 == hashlib.sha256(DATA).hexdigest()
    assert b"".join(repo.read_file(session, f1)) == DATA
    chunks_v1 = session.query(Chunk).count()

    repo.store_file(session, write(tmp_path, "copy", DATA))       # same content again
    assert session.query(Chunk).count() == chunks_v1

    v2 = DATA[:5000] + b"rows inserted near the top" + DATA[5000:]
    f2 = repo.store_file(session, write(tmp_path, "v2", v2))      # slide 11: only the edited region is new
    assert b"".join(repo.read_file(session, f2)) == v2
    assert session.query(Chunk).count() - chunks_v1 <= 3


def test_empty_file(session, tmp_path):
    f = repo.store_file(session, write(tmp_path, "empty", b""))
    assert b"".join(repo.read_file(session, f)) == b""


def test_register_file_from_uploaded_chunks(session, tmp_path):
    file_hash, size, chunks = chunker.chunk_file(write(tmp_path, "f", DATA), 64, 256, 1024)
    assert repo.missing_chunks(session, [h for h, _, _ in chunks]) == list(dict.fromkeys(h for h, _, _ in chunks))
    for h, offset, length in chunks:
        repo.save_chunk(session, h, io.BytesIO(DATA[offset:offset + length]))
    assert repo.missing_chunks(session, [h for h, _, _ in chunks]) == []

    declared = [(h, length) for h, _, length in chunks]
    repo.register_file(session, file_hash, size, chunker.CHUNKER, declared)
    assert b"".join(repo.read_file(session, file_hash)) == DATA


def test_register_file_rejects_bad_claims(session, tmp_path):
    file_hash, size, chunks = chunker.chunk_file(write(tmp_path, "f", DATA), 64, 256, 1024)
    for h, offset, length in chunks:
        repo.save_chunk(session, h, io.BytesIO(DATA[offset:offset + length]))
    tiny = hashlib.sha256(b"tiny").hexdigest()
    repo.save_chunk(session, tiny, io.BytesIO(b"tiny"))
    declared = [(h, length) for h, _, length in chunks]
    fake = "0" * 64  # not yet recorded, so register_file doesn't short-circuit

    cases = [
        (dict(chunker_id="other"), "Chunker mismatch"),
        (dict(size=size + 1), "do not add up"),
        (dict(chunks=[(tiny, 4)] + declared, size=size + 4), "invalid length"),       # undersized non-last chunk
        (dict(chunks=declared + [("f" * 64, 100)], size=size + 100), "not uploaded"),
        (dict(chunks=[(declared[0][0], declared[0][1] - 1)] + declared[1:], size=size - 1), "declared"),
        (dict(), "do not reassemble"),                                                # chunks fine, file hash wrong
    ]
    for overrides, error in cases:
        args = dict(file_hash=fake, size=size, chunker_id=chunker.CHUNKER, chunks=declared) | overrides
        with pytest.raises(ValueError, match=error):
            repo.register_file(session, **args)
    assert not repo.has_files(session, [fake])


def test_commit_chain_conflicts_and_retry(session, tmp_path):
    f1 = repo.store_file(session, write(tmp_path, "a", b"first"))
    f2 = repo.store_file(session, write(tmp_path, "b", b"second"))
    t = "2026-01-01T00:00:00Z"

    c1 = repo.create_commit(session, None, {"x/data.csv": f1, "y/data.csv": f1}, "me", "init", t)  # identical folders
    c2 = repo.create_commit(session, c1, {"x/data.csv": f2}, "me", "update", t)
    assert [r["commit_hash"] for r in get_branch_history(session, "main")] == [c2, c1]

    assert repo.create_commit(session, c1, {"x/data.csv": f2}, "me", "update", t) == c2   # retry -> same result
    with pytest.raises(repo.Conflict):
        repo.create_commit(session, c1, {"x/data.csv": f1}, "other", "late push", t)      # stale parent
    with pytest.raises(repo.Conflict):
        repo.create_commit(session, None, {"z": f1}, "other", "second root", t)           # branch already exists
    assert session.get(Branch, "main").commit_hash == c2

    with pytest.raises(ValueError, match="Unknown files"):
        repo.create_commit(session, c2, {"x/data.csv": "f" * 64}, "me", "missing file", t)
