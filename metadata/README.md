# Module 5: Metadata extraction
**Owner:** Saurabh Kumar

Turns a data file into a small JSON dict of stats ([extractor.py](extractor.py)). It runs **in the CLI** during `push`, for new files only; the stats travel with `POST /files/` and are stored against the file's content hash.

```python
extract_metrics(file_path: str, mime_type: str) -> dict
```

| Format | Returned | How (memory stays small) |
|---|---|---|
| CSV | `row_count`, `schema`, `columns`, `format` | Column types from a 100-row sample; rows by counting newlines in 1 MiB blocks |
| JSON, flat object (`metrics.json`) | `{"format": "json", ...the file's own keys...}` | Loaded as-is, so `accuracy`, `loss`, ... become queryable |
| JSON, table-like (list of records, dict of lists) | `row_count`, `schema`, `columns`, `format` | Loaded into a DataFrame |
| Parquet | `row_count`, `schema`, `columns`, `format` | **Footer only** (`pq.read_metadata`); the data is never loaded |
| Anything else | `{"status": "unknown_format", ...}` | Not sent to the server |
| Unreadable file | `{"status": "failed", "error": ...}` | Not sent to the server |

Rules:
- **Never crash the caller:** every failure is caught and returned as `status: failed`.
- **Read-only:** files are only opened for reading.
- Known approximation: CSV quoted fields containing newlines over-count rows (noted in the code).

## Tests
```powershell
venv\Scripts\python -m pytest metadata/tests -v
```
