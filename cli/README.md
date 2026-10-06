# Module 3: Client CLI
**Owner:** Kedar Medishetty

The `datahub` command line ([main.py](main.py)). It runs where the data is, talks to the server **only over HTTP** ([utils/api.py](utils/api.py)) and never touches the database. Step-by-step usage is in [TUTORIAL.md](../TUTORIAL.md).

```text
python -m cli.main init
python -m cli.main push  <server> [-m MESSAGE] [--author NAME]
python -m cli.main pull  <server> [--force]
python -m cli.main log   <server>
python -m cli.main query <server> "accuracy > 0.9"
```

## Local state: `.datahub/`
- `config.json`: created by `init`.
- `HEAD`: the commit this copy last pushed or pulled. It is the **parent** of the next push and the "base" `pull` compares against. Written only after a push or pull fully succeeds.

## `push`
1. Scan files (`utils/file_scanner.py`: `/` paths, sorted; skips `.datahub`, `.git`, `__pycache__`, `venv`, `.venv`).
2. Chunk and hash every file (`core/chunker.py`).
3. `/files/exists`: skip files the server has. `/chunks/missing`: upload only missing chunks, each once.
4. Extract stats for new files (`metadata/extractor.py`) and register files with `/files/`.
5. `/commit/` with parent = `HEAD`. A 409 means someone pushed first: run `pull`.

## `pull`
Per path, compares **remote**, **HEAD** (what you last had) and **your disk**:

| Situation | Result |
|---|---|
| Remote didn't change it | Left exactly as you have it (edits and deletions survive) |
| Your disk already matches the remote | Skipped |
| You changed it **and** the remote changed it | Conflict: nothing is touched, files listed; `--force` takes the remote's |
| Otherwise | Downloaded or deleted to match the remote |

Downloads go chunk by chunk into a temp file; every chunk hash and the whole-file hash are checked before an atomic swap. Paths that would resolve outside the folder are refused.

## Errors
Server errors are shown with the server's message and exit with code 1.

## Tests
```powershell
venv\Scripts\python -m pytest cli/tests -v
```
They run the real CLI against the real API in-process (fresh database per test).
