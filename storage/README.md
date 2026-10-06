# Module 2: Storage engine & deduplication
**Contributor:** Aditya Khemka

The content-addressable chunk store on disk ([engine.py](engine.py)). Every chunk is stored once, under its own SHA-256, at:

```text
BLOB_DIR/<first 2 hex chars>/<sha256>        e.g. blobs/9f/9f86d081884c7d65...
```
The two-character folders keep any single directory from holding millions of files.

## Functions
```python
chunk_path(chunk_hash) -> str
    # validates the hash (64 lowercase hex) before building a path: no "../" tricks
put_chunk(expected_hash, stream) -> bool
    # True = newly written, False = already stored
get_chunk(chunk_hash) -> Iterator[bytes]
    # streams a stored chunk back in 1 MiB pieces
```

How `put_chunk` stays safe:
1. Already stored? Return `False` without writing (deduplication).
2. Stream into a **temp file in the destination folder**, hashing as it goes; stop past 64 MiB (`MAX_SIZE` from `core/chunker.py`).
3. Hash mismatch or any error: delete the temp file, raise `ValueError`.
4. Otherwise **atomically rename** (`os.replace`) into place, so a crash never leaves a half-written chunk.
5. Windows: if the rename is blocked because another request is reading the same chunk, it is already stored (same hash, same bytes), so return `False`.

Storage never touches the database. Recording a chunk (`chunk` row) only *after* it is safely on disk is done by `core/repo.py: save_chunk`.

## Configuration
`BLOB_DIR` (default `blobs/`, relative to where the API runs).

## Tests
```powershell
venv\Scripts\python -m pytest storage/tests -v
```
