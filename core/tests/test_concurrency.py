"""
Real concurrency against Postgres (SQLite's single shared test connection can't run these).
Run with TEST_DATABASE_URL set; skipped otherwise.
"""
import hashlib
import io
import threading

import pytest
from sqlalchemy.orm import sessionmaker

from conftest import TEST_DATABASE_URL
from core import repo
from infrastructure.db import Branch, Chunk, Commit

pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="needs Postgres (set TEST_DATABASE_URL)")

T = "2026-01-01T00:00:00Z"


def race(n, work):
    """Runs work(i) in n threads released at the same instant; returns each result or exception."""
    barrier = threading.Barrier(n)
    results = [None] * n

    def run(i):
        barrier.wait()
        try:
            results[i] = work(i)
        except Exception as e:  # collected, asserted by the caller
            results[i] = e

    threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def stored_file(session):
    """One recorded file to commit against."""
    data = b"shared content"
    h = hashlib.sha256(data).hexdigest()
    repo.save_chunk(session, h, io.BytesIO(data))
    repo.register_file(session, h, len(data), repo.chunker.CHUNKER, [(h, len(data))])
    return h


@pytest.mark.parametrize("racers", [2, 8])
def test_simultaneous_first_commits_exactly_one_wins(db_engine, chunk_store, racers):
    Session = sessionmaker(bind=db_engine)
    with Session() as s:
        f = stored_file(s)

    def push(i):
        with Session() as s:
            return repo.create_commit(s, None, {f"file{i}.txt": f}, "racer", f"push {i}", T)

    results = race(racers, push)
    winners = [r for r in results if isinstance(r, str)]
    assert len(winners) == 1, results
    assert all(isinstance(r, repo.Conflict) for r in results if r is not winners[0]), results
    with Session() as s:
        assert s.get(Branch, "main").commit_hash == winners[0]
        assert s.query(Commit).count() == 1                     # losers rolled back completely


def test_simultaneous_pushes_on_same_parent_exactly_one_wins(db_engine, chunk_store):
    Session = sessionmaker(bind=db_engine)
    with Session() as s:
        f = stored_file(s)
        base = repo.create_commit(s, None, {"base.txt": f}, "a", "base", T)

    def push(i):
        with Session() as s:
            return repo.create_commit(s, base, {f"file{i}.txt": f}, "racer", f"push {i}", T)

    results = race(8, push)
    winners = [r for r in results if isinstance(r, str)]
    assert len(winners) == 1, results
    assert all(isinstance(r, repo.Conflict) for r in results if r is not winners[0]), results
    with Session() as s:
        assert s.get(Branch, "main").commit_hash == winners[0]
        assert s.get(Commit, winners[0]).parent_hash == base


def test_same_chunk_uploaded_simultaneously(db_engine, chunk_store):
    """Many clients uploading the same chunk at once: all succeed, one row, one file on disk."""
    Session = sessionmaker(bind=db_engine)
    data = b"popular chunk" * 50
    h = hashlib.sha256(data).hexdigest()

    def upload(_):
        with Session() as s:
            repo.save_chunk(s, h, io.BytesIO(data))

    results = race(8, upload)
    assert results == [None] * 8, results
    with Session() as s:
        assert s.query(Chunk).filter_by(chunk_hash=h).count() == 1
    assert len(list((chunk_store / h[:2]).iterdir())) == 1      # no temp files left behind
