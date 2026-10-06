# Module 1: Database schema & lineage
**Owner:** Abinav Kiran

SQLAlchemy models for the Merkle tree, file chunk lists and history, plus the queries that walk them ([db.py](db.py)). The full table reference and diagram are in [README §4](../README.md#4-database-structure).

## Models
| Model | Table | Role |
|---|---|---|
| `Branch` | `branch` | Named pointer to the newest commit (`main`) |
| `Commit` | `commit` | Snapshot: root tree + parent commit; `created_at` stored exactly as hashed |
| `Tree`, `TreeEntry` | `tree`, `tree_entry` | Folders and their entries (`object_type` = `file` or `tree`); unique `(tree_hash, name)` |
| `File` | `file` | One file version (SHA-256 of its content) + the chunker that produced it |
| `FileChunk` | `file_chunk` | The file's ordered chunk list (`file_hash`, `seq`, `chunk_hash`) |
| `Chunk` | `chunk` | One stored chunk (bytes on disk in `blobs/`) |
| `Metadata` | `metadata` | Stats of a file version (`target_hash` = file hash) |

All foreign keys are **deferred** (checked at commit), so related rows can be inserted in any order within a transaction. On SQLite, foreign keys are switched on for every connection.

## Functions
```python
get_commit_history(session, commit_hash)   # recursive CTE: commit -> root, newest first, with depth
get_branch_history(session, name)          # history starting at a branch head; ValueError if no branch
get_tree_closure(session, tree_hash)       # recursive CTE: every file hash under a tree
advance_branch(session, name, expected, new) -> bool
    # compare-and-swap: moves the branch only if it still points at `expected`
    # (expected=None creates it); never commits; False means "someone pushed first"
insert_ignore(session, Model, rows) -> int
    # INSERT ... ON CONFLICT DO NOTHING (SQLite or Postgres); returns rows actually inserted
init_db()                                  # create_all; schema changes = recreate the DB (no migrations)
```

## Configuration
`DATABASE_URL` (default: in-memory SQLite). In Docker it points at the `db` container (`.env`, generated from `credentials.json`).

## Tests
```powershell
venv\Scripts\python -m pytest infrastructure/tests -v
```
