# Core: chunking, hashing and repository logic
**Owner:** shared

Everything that must behave identically on the client and the server lives here, so the CLI and the API can never disagree about a hash. See [How it works](../README.md#1-how-it-works-step-by-step) for the ideas behind it.

## `core/chunker.py`: content-defined chunking

```python
MIN_SIZE = 4 MiB; AVG_SIZE = 16 MiB; MAX_SIZE = 64 MiB
CHUNKER = "fastcdc-1.7.0/4M/16M/64M"

chunk_file(path) -> (file_hash, size, [(chunk_hash, offset, length), ...])
```
- Splits a file with FastCDC and hashes **every chunk and the whole file** (SHA-256) in a single read pass.
- Empty files return `(sha256(b""), 0, [])` (fastcdc cannot memory-map an empty file).
- Refuses to run if the compiled fastcdc extension isn't loaded (Python 3.8–3.12 only).
- The settings are **permanent**: see [choosing the sizes](../README.md#step-9-choosing-the-sizes-4--16--64-mib). Tests patch them to tiny values through the `chunk_store` fixture.

## `core/objects.py`: hash formats (the Merkle tree)

```python
is_valid_hash(h) -> bool                          # 64 lowercase hex chars
tree_hash(entries) -> (hash, sorted_entries)      # entries: [(type "file"|"tree", hash, name)]
build_trees({"dir/a.csv": file_hash}) -> (root_hash, {tree_hash: entries})
format_time(datetime) -> "YYYY-MM-DDTHH:MM:SSZ"
commit_hash(tree, parent, author, time, message) -> hash
```
- Names are Unicode NFC-normalised; `""`, `.`, `..` and names containing `/`, newline or NUL are rejected.
- Identical folders produce one tree; an empty folder set still has a valid root.

## `core/repo.py`: everything the server does

Each function is one unit of work: it commits, or rolls back.

| Function | Does |
|---|---|
| `has_files(session, hashes)` / `missing_chunks(session, hashes)` | Batched existence checks |
| `save_chunk(session, hash, stream)` | Chunk to disk first (verified), then its DB row |
| `register_file(session, file_hash, size, chunker_id, chunks, stats=None)` | Records a client-uploaded file **after verifying** chunker id, per-chunk sizes, declared vs stored lengths, total size, and that the chunks re-assemble to `file_hash`; stores optional stats (≤ 64 KB JSON object) |
| `store_file(session, path)` | Chunks a file on the server's own disk, storing only new chunks |
| `read_file(session, file_hash)` | Streams a file back from its chunks |
| `commit_files(session, commit_hash)` | A commit's `{path: file_hash}` |
| `file_chunks(session, file_hash)` | A file's size and ordered chunk list |
| `create_commit(session, parent, files, author, message, time)` | Builds the trees, records the commit and moves `main` atomically. Raises `Conflict` if `main` moved (HTTP 409); a retried successful commit returns the same hash |

## Tests
```powershell
venv\Scripts\python -m pytest core/tests
```
`test_core.py` (no database), `test_repo.py`, and `test_concurrency.py` (Postgres only; see [DEVELOPMENT.md](../DEVELOPMENT.md#3-running-the-tests)).
