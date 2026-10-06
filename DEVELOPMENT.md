# Developing DataHub

How to set up, test and change the code. For *using* DataHub see [TUTORIAL.md](TUTORIAL.md); for the design see [README.md](README.md).

## 1. Two environments

| | Docker (`dev-env` + Postgres) | Local Python 3.11 venv |
|---|---|---|
| Use for | Running the app, demos, **Postgres** tests | Fast edit-test loop (SQLite) |
| Python | 3.11 (`python:3.11-slim` image) | **3.11 exactly** (3.8–3.12 work; 3.13+ do not, see below) |
| Database | PostgreSQL 15 | In-memory SQLite, or the Docker Postgres via `TEST_DATABASE_URL` |

**Why Python 3.11:** `fastcdc` ships its fast compiled extension only for Python 3.8–3.12, and `pandas==2.2.2` has no wheels for newer versions. Without the compiled extension `core/chunker.py` refuses to run (pure-Python chunking measured ~10× slower: about 7 MB/s instead of 71 MB/s).

### Docker setup
```powershell
# credentials.json at the repo root (git-ignored), see TUTORIAL.md §1.1
./sync_credentials_env.ps1          # writes .env (DB_USER, DB_PASSWORD, DATABASE_URL, ...)
docker compose up -d --build
docker compose exec dev-env uvicorn api.server:app --host 0.0.0.0 --port 8000   # the API, when you need it
```
The repository is mounted at `/app`, so code edits are visible in the container immediately. `.dockerignore` keeps `venv/`, `blobs/`, caches and secrets out of the image.

### Local venv setup (Windows)
```powershell
py -3.11 -m venv venv
venv\Scripts\python -m pip install -r requirements.txt
venv\Scripts\python -c "import fastcdc; print(fastcdc.fastcdc.__module__)"   # must print fastcdc.fastcdc_cy
```

## 2. Project layout

```text
core/            chunking (chunker.py), hash formats (objects.py), all server-side logic (repo.py)
infrastructure/  SQLAlchemy models, recursive history queries, branch compare-and-swap (db.py)
storage/         content-addressed chunk store on disk: blobs/<2 hex>/<sha256> (engine.py)
api/             FastAPI endpoints, a thin layer over core/repo.py (server.py)
cli/             click CLI: init, push, pull, log, query (main.py; utils/api.py = HTTP client)
metadata/        stats extraction for CSV / JSON / Parquet (extractor.py)
query/           "metric op value" parser and filter (parser.py)
conftest.py      shared test fixtures (database + chunk store)
live_demo_workspace/, mock_workspace/   demo material
```

Rules that keep the pieces honest:
- **One implementation of hashing**, in `core/`, imported by both the CLI and the server. Never re-implement a hash format elsewhere.
- **The server trusts nothing**: it recomputes tree and commit hashes, re-hashes every chunk, and verifies every file record (`core/repo.py: register_file`).
- **The CLI never touches the database**; it only talks HTTP (`cli/utils/api.py`).
- **No SQL built from strings**: use SQLAlchemy expressions.

## 3. Running the tests

| Where | Command | Expect |
|---|---|---|
| Local, SQLite | `venv\Scripts\python -m pytest` | all pass, **4 skipped** (Postgres-only concurrency tests) |
| Docker, Postgres | see below | all pass, 0 skipped |
| Local, against Docker's Postgres | see below | all pass, 0 skipped |

**Postgres tests** need a separate, throwaway database (create it once per fresh volume):
```powershell
docker compose exec db createdb -U user datahub_test
docker compose exec -e TEST_DATABASE_URL=postgresql://user:password@db:5432/datahub_test dev-env python -m pytest
# or from Windows (port 5432 is published):
$env:TEST_DATABASE_URL = "postgresql://user:password@localhost:5432/datahub_test"; venv\Scripts\python -m pytest
```

> **Never point `TEST_DATABASE_URL` at the app database (`datahub`).** Every test drops and recreates all tables.

### Fixtures (`conftest.py`)
| Fixture | Gives you |
|---|---|
| `db_engine` | A fresh, empty database for this test: in-memory SQLite, or Postgres when `TEST_DATABASE_URL` is set |
| `db_session` | A session on it |
| `chunk_store` | A private chunk folder, and **tiny chunk sizes** (64 B / 256 B / 1 KiB) so chunking tests run fast |

Example:
```python
def test_something(db_session, chunk_store):
    ...
```
API tests use the `client` fixture in `api/tests/test_server.py` (a `TestClient` wired to `db_engine`); CLI tests reuse it and swap the CLI's HTTP session for it.

### What the suite covers
- `core/tests/test_core.py`: chunking round trip, **pinned chunk boundaries**, dedup after an insert, Merkle tree behaviour (slide 3), Unicode names, commit hashing.
- `core/tests/test_repo.py`: storing / verifying files, every rejected forged claim, commit chains, conflicts, retries, stats.
- `core/tests/test_concurrency.py` (Postgres only): simultaneous pushes and uploads (exactly one winner, no half-saved data).
- `infrastructure/`, `storage/`, `api/`, `cli/`, `metadata/`, `query/`: each module's contract, plus full push/pull flows through the real API.

## 4. Conventions

- **Ponytail style:** the simplest thing that works, standard library first, no speculative abstractions. A deliberate shortcut with a known limit gets a comment saying so and naming the upgrade path, e.g. `# ponytail: one query per folder; switch to a recursive CTE if deep trees get slow`.
- **Chunk settings are permanent.** `MIN_SIZE`, `AVG_SIZE`, `MAX_SIZE` in `core/chunker.py` and the `fastcdc` version must not change: new chunks would stop deduplicating against stored data. `test_boundaries_are_pinned` fails if anything moves a boundary. See [README step 9](README.md#step-9-beyond-the-slides-choosing-the-sizes-4--16--64-mib).
- **Hash formats are versioned** (`datahub-tree-v1`, `datahub-commit-v1`). Changing one means a new version string and a migration story, not an edit in place.
- Errors: raise `ValueError` for bad input (becomes HTTP 400) and `repo.Conflict` for "someone pushed first" (HTTP 409).

## 5. Changing the database schema

There are no migrations (decided: no Alembic). After changing a model in `infrastructure/db.py`:

1. Recreate the database, which **deletes all data**:
   ```powershell
   docker compose down -v
   docker compose up -d --build
   docker compose exec db createdb -U user datahub_test    # for Postgres tests
   ```
2. Update `ER_Diagram.mmd` and regenerate the picture:
   ```powershell
   npx -y @mermaid-js/mermaid-cli -i ER_Diagram.mmd -o DataHub_ER_Diagram.png -b white -s 2
   ```
3. Update the table list in [README.md §4](README.md#4-database-structure).

## 6. Adding a dependency

Pin it exactly in `requirements.txt`, reinstall the venv (`pip install -r requirements.txt`) and rebuild the image (`docker compose up -d --build`). Prefer the standard library or an already-installed package.
