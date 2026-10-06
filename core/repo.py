import hashlib
import io
import json
import os
from datetime import datetime, timezone

from sqlalchemy import select

from core import chunker
from core.objects import build_trees, commit_hash, format_time, is_valid_hash
from infrastructure.db import Branch, Chunk, Commit, File, FileChunk, Metadata, ObjectType, Tree, TreeEntry, advance_branch, insert_ignore
from storage.engine import chunk_path, get_chunk, put_chunk


class Conflict(Exception):
    """Raised when a commit cannot be created because the branch has moved since the parent."""

def _check_hashes(hashes):
    for h in hashes:
        if not is_valid_hash(h):
            raise ValueError(f"Invalid hash: {h!r}")


def has_files(session, file_hashes):
    """Returns the subset of file_hashes already recorded."""
    _check_hashes(file_hashes)
    return set(session.scalars(select(File.file_hash).where(File.file_hash.in_(set(file_hashes)))))


def missing_chunks(session, chunk_hashes):
    """Returns the chunk hashes not yet stored, in input order, without duplicates."""
    _check_hashes(chunk_hashes)
    stored = set(session.scalars(select(Chunk.chunk_hash).where(Chunk.chunk_hash.in_(set(chunk_hashes)))))
    return [h for h in dict.fromkeys(chunk_hashes) if h not in stored]


def save_chunk(session, chunk_hash, data_stream):
    """Disk first, then the DB row: the DB never lists a chunk that isn't safely on disk."""
    put_chunk(chunk_hash, data_stream)
    insert_ignore(session, Chunk, [{"chunk_hash": chunk_hash, "size_bytes": os.path.getsize(chunk_path(chunk_hash))}])
    session.commit()


MAX_STATS_BYTES = 64 * 1024


def _record_file(session, file_hash, size, chunk_hashes, stats=None):
    if insert_ignore(session, File, [{"file_hash": file_hash, "size_bytes": size, "chunker": chunker.CHUNKER}]):
        insert_ignore(session, FileChunk, [
            {"file_hash": file_hash, "seq": i, "chunk_hash": h} for i, h in enumerate(chunk_hashes)
        ])
    if stats:
        insert_ignore(session, Metadata, [{"target_hash": file_hash, "stats": stats}])  # same content => same stats
    session.commit()


def _check_stats(stats):
    """Stats come from the client: descriptive only, but bound their shape and size."""
    if stats is None:
        return
    if not isinstance(stats, dict):
        raise ValueError("Stats must be a JSON object")
    if len(json.dumps(stats)) > MAX_STATS_BYTES:
        raise ValueError(f"Stats exceed {MAX_STATS_BYTES} bytes")


def register_file(session, file_hash, size, chunker_id, chunks, stats=None):
    """
    Records a file whose chunks a client already uploaded. chunks: [(chunk_hash, length)] in file order.
    Everything is verified first: a wrong record would hand corrupt data to everyone who dedupes against it.
    stats: optional client-computed metadata (row count, schema, metrics) stored against the file hash.
    """
    _check_hashes([file_hash] + [h for h, _ in chunks])
    _check_stats(stats)
    if chunker_id != chunker.CHUNKER:
        raise ValueError(f"Chunker mismatch: client {chunker_id!r}, server {chunker.CHUNKER!r}")
    if has_files(session, [file_hash]):
        return

    lengths = [n for _, n in chunks]
    if sum(lengths) != size:
        raise ValueError("Chunk lengths do not add up to the file size")
    for i, n in enumerate(lengths):
        smallest = 1 if i == len(lengths) - 1 else chunker.MIN_SIZE
        if not smallest <= n <= chunker.MAX_SIZE:
            raise ValueError(f"Chunk {i} has invalid length {n}")

    stored = dict(session.execute(
        select(Chunk.chunk_hash, Chunk.size_bytes).where(Chunk.chunk_hash.in_({h for h, _ in chunks}))
    ).all())
    for h, n in chunks:
        if h not in stored:
            raise ValueError(f"Chunk not uploaded: {h}")
        if stored[h] != n:
            raise ValueError(f"Chunk {h} is {stored[h]} bytes, declared {n}")

    # re-reads the whole file once per new file; fine at this scale, cache per-chunk trust if it becomes the bottleneck
    hasher = hashlib.sha256()
    for h, _ in chunks:
        for piece in get_chunk(h):
            hasher.update(piece)
    if hasher.hexdigest() != file_hash:
        raise ValueError("Chunks do not reassemble to the declared file hash")

    _record_file(session, file_hash, size, [h for h, _ in chunks], stats)


def store_file(session, path):
    """Chunks a file already on this machine and stores only the new chunks. Returns its file hash."""
    file_hash, size, chunks = chunker.chunk_file(path, chunker.MIN_SIZE, chunker.AVG_SIZE, chunker.MAX_SIZE)
    if has_files(session, [file_hash]):
        return file_hash

    missing = set(missing_chunks(session, [h for h, _, _ in chunks]))
    with open(path, "rb") as f:
        for h, offset, length in chunks:
            if h in missing:
                f.seek(offset)
                save_chunk(session, h, io.BytesIO(f.read(length)))  # put_chunk re-verifies, so a file edited mid-store fails loudly
                missing.discard(h)

    _record_file(session, file_hash, size, [h for h, _, _ in chunks])
    return file_hash


def read_file(session, file_hash):
    """Streams a stored file back by joining its chunks in order."""
    if not has_files(session, [file_hash]):
        raise ValueError(f"File with hash {file_hash} does not exist.")
    chunk_hashes = session.scalars(
        select(FileChunk.chunk_hash).where(FileChunk.file_hash == file_hash).order_by(FileChunk.seq)
    ).all()
    for h in chunk_hashes:
        yield from get_chunk(h)


def commit_files(session, commit_hash):
    """Flattens a commit's Merkle tree into {path: file_hash}; None if the commit doesn't exist."""
    _check_hashes([commit_hash])
    commit = session.get(Commit, commit_hash)
    if commit is None:
        return None
    files, stack = {}, [(commit.tree_hash, "")]
    # one query per folder; switch to a recursive CTE that builds paths if deep trees get slow
    while stack:
        tree, prefix = stack.pop()
        for entry in session.scalars(select(TreeEntry).where(TreeEntry.tree_hash == tree)):
            path = prefix + entry.name
            if entry.object_type == ObjectType.tree:
                stack.append((entry.object_hash, path + "/"))
            else:
                files[path] = entry.object_hash
    return commit, files


def file_chunks(session, file_hash):
    """Returns (size, [(chunk_hash, length)] in file order), or None if the file isn't recorded."""
    _check_hashes([file_hash])
    record = session.get(File, file_hash)
    if record is None:
        return None
    rows = session.execute(
        select(FileChunk.chunk_hash, Chunk.size_bytes)
        .join(Chunk, Chunk.chunk_hash == FileChunk.chunk_hash)
        .where(FileChunk.file_hash == file_hash)
        .order_by(FileChunk.seq)
    ).all()
    return record.size_bytes, [(h, n) for h, n in rows]


def create_commit(session, parent, files, author, message, time=None, branch="main"):
    """
    Builds the Merkle trees for {path: file_hash}, records the commit and moves the branch, atomically.
    Raises Conflict if the branch is no longer at `parent`. Returns the commit hash.
    """
    root, trees = build_trees(files)
    unknown = set(files.values()) - has_files(session, list(files.values()))
    if unknown:
        raise ValueError(f"Unknown files: {sorted(unknown)}")

    time = time or format_time(datetime.now(timezone.utc))
    new_hash = commit_hash(root, parent, author, time, message)

    if session.get(Commit, new_hash):
        head = session.get(Branch, branch)
        if head and head.commit_hash == new_hash:
            return new_hash  # a retried push that already succeeded
        raise Conflict(f"Commit {new_hash} exists but is not the head of {branch}")

    try:
        insert_ignore(session, Tree, [{"tree_hash": h} for h in trees])
        insert_ignore(session, TreeEntry, [
            {"tree_hash": h, "name": name, "object_hash": obj_hash, "object_type": ObjectType(obj_type)}
            for h, entries in trees.items() for obj_type, obj_hash, name in entries
        ])
        session.add(Commit(commit_hash=new_hash, parent_hash=parent, tree_hash=root,
                           author=author, message=message, created_at=time))
        if not advance_branch(session, branch, parent, new_hash):
            raise Conflict(f"Branch {branch} has moved since {parent}; pull first")
        session.commit()
    except BaseException:
        session.rollback()
        raise
    return new_hash
