import pytest

from query.parser import build_filter, execute_query
from infrastructure.db import File, Metadata

H1, H2, H3 = ("1" * 64, "2" * 64, "3" * 64)


@pytest.fixture
def session(db_session):
    """Three files with stats (fresh DB per test, see conftest.py; never the app database)."""
    for h, stats in ((H1, {"accuracy": 0.95, "loss": 0.10}),
                     (H2, {"accuracy": 0.80, "loss": 0.30}),
                     (H3, {"accuracy": 0.60, "loss": 0.50})):
        db_session.add(File(file_hash=h, size_bytes=1, chunker="test"))
        db_session.add(Metadata(target_hash=h, stats=stats))
    db_session.commit()
    return db_session


FILES = {"models/a/metrics.json": H1, "models/b/metrics.json": H2, "models/c/metrics.json": H3}


def test_execute_query_accuracy(session):
    results = execute_query(session, build_filter("accuracy > 0.9"), FILES)
    assert results == [("models/a/metrics.json", H1, {"accuracy": 0.95, "loss": 0.10})]


def test_execute_query_loss(session):
    results = execute_query(session, build_filter("loss <= 0.3"), FILES)
    assert [path for path, _, _ in results] == ["models/a/metrics.json", "models/b/metrics.json"]


def test_execute_query_no_result(session):
    assert execute_query(session, build_filter("accuracy > 0.99"), FILES) == []


def test_query_is_scoped_to_the_given_files(session):
    """Files outside the commit don't match; one file stored under two paths is listed under both."""
    files = {"x/metrics.json": H1, "y/copy.json": H1, "z/other.json": H2}
    results = execute_query(session, build_filter("accuracy > 0.7"), files)
    assert [path for path, _, _ in results] == ["x/metrics.json", "y/copy.json", "z/other.json"]
    assert execute_query(session, build_filter("accuracy > 0.9"), {"only/c.json": H3}) == []


def test_invalid_operator(session):
    with pytest.raises(ValueError):
        execute_query(session, build_filter("accuracy ~ 0.9"), FILES)
