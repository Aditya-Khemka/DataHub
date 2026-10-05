import hashlib
import random
import unicodedata

import pytest

from core.chunker import chunk_file
from core.objects import build_trees, commit_hash, format_time, tree_hash

SMALL = dict(min_size=1024, avg_size=4096, max_size=16384)
H = lambda s: hashlib.sha256(s.encode()).hexdigest()


def write(tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data)
    return str(path)


def test_chunks_hash_and_reassemble(tmp_path):
    data = random.Random(0).randbytes(200_000)
    file_hash, size, chunks = chunk_file(write(tmp_path, "f", data), **SMALL)

    assert file_hash == hashlib.sha256(data).hexdigest()
    assert size == len(data)
    assert b"".join(data[o:o + n] for _, o, n in chunks) == data
    assert all(h == hashlib.sha256(data[o:o + n]).hexdigest() for h, o, n in chunks)


def test_boundaries_are_pinned(tmp_path):
    # Fails if a fastcdc upgrade or parameter change moves boundaries (breaks dedup with stored data)
    data = random.Random(0).randbytes(200_000)
    _, _, chunks = chunk_file(write(tmp_path, "f", data), **SMALL)
    assert H("".join(h for h, _, _ in chunks)) == PINNED_CHUNKS


def test_insert_near_start_reuses_most_chunks(tmp_path):
    data = random.Random(0).randbytes(200_000)
    edited = data[:1000] + b"inserted row\n" * 10 + data[1000:]
    _, _, before = chunk_file(write(tmp_path, "a", data), **SMALL)
    _, _, after = chunk_file(write(tmp_path, "b", edited), **SMALL)

    reused = {h for h, _, _ in after} & {h for h, _, _ in before}
    assert len(reused) >= len(after) - 2


def test_empty_file(tmp_path):
    assert chunk_file(write(tmp_path, "e", b"")) == (hashlib.sha256(b"").hexdigest(), 0, [])


def test_merkle_tree_slide_3():
    v1 = {"left/A.txt": H("A"), "left/B.txt": H("B"), "right/C.txt": H("C"), "right/D.txt": H("D")}
    v2 = {**v1, "right/C.txt": H("C1")}
    root1, trees1 = build_trees(v1)
    root2, trees2 = build_trees(v2)

    assert root1 != root2
    # Only the changed folder and the root are new; the untouched folder is shared
    assert len(set(trees2) - set(trees1)) == 2
    # Input order does not matter
    assert build_trees(dict(reversed(list(v1.items()))))[0] == root1


def test_identical_folders_stored_once():
    root, trees = build_trees({"a/x.csv": H("x"), "b/x.csv": H("x")})
    assert len(trees) == 2  # one shared subfolder + root


def test_unicode_forms_hash_the_same():
    nfc, nfd = unicodedata.normalize("NFC", "café.csv"), unicodedata.normalize("NFD", "café.csv")
    assert nfc != nfd
    assert build_trees({nfc: H("x")})[0] == build_trees({nfd: H("x")})[0]


@pytest.mark.parametrize("files", [
    {"a": H("1"), "a/b": H("2")},   # file and folder at the same path
    {"../x": H("1")},
    {"a//b": H("1")},
    {"x": "not-a-hash"},
])
def test_invalid_trees_rejected(files):
    with pytest.raises(ValueError):
        build_trees(files)


def test_empty_root_is_valid():
    root, trees = build_trees({})
    assert root == tree_hash([])[0] and trees == {root: []}


def test_commit_hash_links_parent_and_validates():
    from datetime import datetime, timezone

    tree = H("tree")
    t = format_time(datetime(2026, 10, 6, 12, 0, 0, 999, tzinfo=timezone.utc))
    assert t == "2026-10-06T12:00:00Z"

    first = commit_hash(tree, None, "alice", t, "init")
    second = commit_hash(tree, first, "alice", t, "init")
    assert first != second  # same content, different parent -> different commit

    for bad in [("bad", None, "a", t), (tree, "bad", "a", t), (tree, None, "a\nb", t),
                (tree, None, "a", "2026-10-06 12:00:00")]:
        with pytest.raises(ValueError):
            commit_hash(*bad, "msg")


# 51 chunks of random.Random(0).randbytes(200_000) with SMALL sizes, fastcdc 1.7.0
PINNED_CHUNKS = "6a033e2c6a92f23185e6150dd7a942c0da8a3a3656e602e760b24f08a9d60c3e"
