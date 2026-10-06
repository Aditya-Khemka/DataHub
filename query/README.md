# Module 6: Query language
**Owner:** Pashuvula Niranand Reddy

A tiny filter language over file stats ([parser.py](parser.py)): `"<metric> <operator> <number>"`, e.g. `accuracy > 0.9`, `row_count >= 1000000`.

```python
build_filter("accuracy > 0.9") -> {"metric": "accuracy", "operator": ">", "value": 0.9}
    # ValueError unless exactly three parts and a numeric value

execute_query(session, ast, files) -> [(path, file_hash, stats), ...]
    # files: {path: file_hash}, normally the files of main's latest commit
```

- Operators: `>`, `<`, `>=`, `<=`, `==`. Anything else is a `ValueError` (HTTP 400).
- The metric is read from the JSON `stats` column with SQLAlchemy expressions (`Metadata.stats[metric].as_float()`). **No SQL is built from strings**, so input like `accuracy > 0.9; DROP TABLE commit` is just an invalid query.
- Results are listed by **path**, sorted; a file stored under two paths appears under both.
- The filter runs in SQL; narrowing to the commit's files happens in Python, which avoids SQLite's limit on bound parameters for big commits.

Used by `POST /query/` and `datahub query`.

## Tests
```powershell
venv\Scripts\python -m pytest query/tests -v
```
