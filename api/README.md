# Module 4: API gateway
**Owner:** Romir Shetty

The FastAPI server ([server.py](server.py)): a thin HTTP layer over [core/repo.py](../core/repo.py). It is the trust boundary: everything a client sends is validated and verified before it is stored. The full endpoint table is in [README §5](../README.md#5-api-reference).

## Endpoints
| Endpoint | Calls |
|---|---|
| `POST /files/exists`, `POST /chunks/missing` | `repo.has_files`, `repo.missing_chunks` (≤ 10,000 hashes per call) |
| `PUT /chunks/{hash}` | Streams the body into a temp buffer (spills to disk past 8 MiB; **413** past 64 MiB), then `repo.save_chunk` |
| `POST /files/` | `repo.register_file` for each file (with optional `stats`) |
| `POST /commit/` | `repo.create_commit`: the server builds the trees itself from `{path: file_hash}` |
| `GET /branches/{name}`, `GET /commits/{hash}`, `GET /files/{hash}`, `GET /chunks/{hash}` | Read side, used by `pull` (chunks are streamed) |
| `GET /log` | `get_branch_history(main)` |
| `POST /query/` | Parses the query and filters `main`'s latest commit |

## Error mapping
| Raised | HTTP |
|---|---|
| `ValueError` (bad hash, failed verification, bad stats, ...) | 400 |
| `repo.Conflict` (`main` moved since the client's parent) | 409 |
| Pydantic validation (malformed body, too many hashes) | 422 |
| Unknown commit / file / chunk / branch | 404 |
| Chunk body over 64 MiB | 413 |

## Run
```powershell
docker compose exec dev-env uvicorn api.server:app --host 0.0.0.0 --port 8000
```

## Tests
```powershell
venv\Scripts\python -m pytest api/tests -v
```
The `client` fixture wires a `TestClient` to a fresh database (`dependency_overrides`) and a private chunk folder.
