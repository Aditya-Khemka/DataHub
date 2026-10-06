# Live Manual Demo Guide

This workspace is designed for a live terminal demo where you run each step manually and prove results through CLI output and direct database queries.

Database credentials are centralized in `credentials.json` at the repository root.

Before running the demo, ensure this file exists at the repository root:

```json
{
	"database": {
		"user": "user",
		"password": "password",
		"name": "datahub",
		"host": "db",
		"port": 5432
	}
}
```

## 0. Start From a Clean Slate (Optional but Recommended)

If you want demo-only rows in `log` and `query`, reset Docker volumes first:

```powershell
docker compose down -v
```

## 1. Prepare Infrastructure (Terminal A)

Run these commands from repository root:

PowerShell:

```powershell
./sync_credentials_env.ps1
docker compose up -d --build
docker compose exec dev-env uvicorn api.server:app --host 0.0.0.0 --port 8000
```

cmd:

```bat
sync_credentials_env.cmd
docker compose up -d --build
docker compose exec dev-env uvicorn api.server:app --host 0.0.0.0 --port 8000
```

Keep Terminal A running because it hosts the API.

If port 8000 is already in use, run API on another port such as 8010 and pass that URL explicitly in `push/log/query`.

If you want Terminal B to always use a non-default API URL, set this before running commands:

```bat
set DATAHUB_REMOTE_URL=http://localhost:8010
```

## 2. Open Demo Terminal (Terminal B)

Use one of these options:

### Option A: PowerShell

```powershell
cd live_demo_workspace
. .\load-demo-alias.ps1
```

### Option B: Command Prompt (cmd)

```bat
cd live_demo_workspace
```

In cmd, run the wrapper as `.\datahub` (this folder includes `datahub.cmd`; some Windows setups don't run commands from the current folder without the `.\`).
Use double quotes in cmd for values with spaces, for example: `.\datahub push -m "initial demo"`.

The wrappers fill in the server address (`http://localhost:8000`, or `DATAHUB_REMOTE_URL` if set) for `push`, `pull`, `log` and `query`.

## 3. Manual Demo Flow (Terminal B)

### Step 1: Initialize local DataHub metadata

```powershell
datahub init
```

### Step 2: Push first snapshot

```powershell
datahub push -m "Initial demo snapshot"
```

Expected (all files are new):

```text
Files:  9 new / 9 total
Chunks: 9 uploaded / 9 total
```

### Step 3: Show commit history

```powershell
datahub log
```

### Step 4: Show metadata query result

The CLI extracted stats (row count, column types) from each data file during the push:

```powershell
datahub query "row_count == 8"
```

Expected: `experiments.csv | {'row_count': 8, 'schema': {...}, ...}`

### Step 5: Make a visible change to the CSV

PowerShell:

```powershell
Add-Content .\experiments.csv "exp009,v2.1,CatBoost,0.92,0.08"
```

cmd:

```bat
echo exp009,v2.1,CatBoost,0.92,0.08>>experiments.csv
```

### Step 6: Push second snapshot

```powershell
datahub push -m "Added exp009 after tuning"
```

Expected: only the edited file is uploaded; the other 8 are recognised by their whole-file hash and skipped.

```text
Files:  1 new / 9 total
Chunks: 1 uploaded / 9 total
```

(Files under 4 MiB are a single chunk, so the small CSV is re-uploaded whole. For a big file only the chunk around the edit is uploaded; see the tutorial's 55 MB example in `TUTORIAL.md` section 4.)

### Step 7: Show updated history and query again

```powershell
datahub log
datahub query "row_count == 9"
```

### Step 8: Pull

```powershell
datahub pull
```

Expected: `Already up to date.` (this copy made the latest commit). To show a real pull, with a second copy, push conflicts and the "pull first" rule, follow `TUTORIAL.md` section 7.

## 4. Prove Data Is In PostgreSQL (Terminal C Optional)

From repository root:

PowerShell:

```powershell
docker compose exec db psql -P pager=off -U user -d datahub -c 'SELECT commit_hash, author, message, created_at FROM commit ORDER BY created_at DESC LIMIT 10;'
docker compose exec db psql -P pager=off -U user -d datahub -c 'SELECT target_hash, stats->>''row_count'' AS row_count, stats->>''format'' AS format FROM metadata;'
docker compose exec db psql -P pager=off -U user -d datahub -c 'SELECT id, tree_hash, name, object_hash, object_type FROM tree_entry ORDER BY id DESC LIMIT 20;'
docker compose exec db psql -P pager=off -U user -d datahub -c 'SELECT f.file_hash, f.size_bytes, count(*) AS chunks FROM file f JOIN file_chunk fc ON fc.file_hash = f.file_hash GROUP BY f.file_hash, f.size_bytes;'
docker compose exec db psql -P pager=off -U user -d datahub -c 'SELECT (SELECT sum(size_bytes) FROM file) AS all_file_versions, (SELECT sum(size_bytes) FROM chunk) AS actually_stored;'
```

cmd:

```bat
docker compose exec db psql -P pager=off -U user -d datahub -c "SELECT commit_hash, author, message, created_at FROM commit ORDER BY created_at DESC LIMIT 10;"
docker compose exec db psql -P pager=off -U user -d datahub -c "SELECT target_hash, stats->>'row_count' AS row_count, stats->>'format' AS format FROM metadata;"
docker compose exec db psql -P pager=off -U user -d datahub -c "SELECT id, tree_hash, name, object_hash, object_type FROM tree_entry ORDER BY id DESC LIMIT 20;"
docker compose exec db psql -P pager=off -U user -d datahub -c "SELECT f.file_hash, f.size_bytes, count(*) AS chunks FROM file f JOIN file_chunk fc ON fc.file_hash = f.file_hash GROUP BY f.file_hash, f.size_bytes;"
docker compose exec db psql -P pager=off -U user -d datahub -c "SELECT (SELECT sum(size_bytes) FROM file) AS all_file_versions, (SELECT sum(size_bytes) FROM chunk) AS actually_stored;"
```

## 5. Live Table Website (Terminal D Optional)

This starts a live website that shows every table and all rows, refreshing every 5 seconds.

PowerShell:

```powershell
cd live_demo_workspace
.\run_db_dashboard.ps1
```

cmd:

```bat
cd live_demo_workspace
run_db_dashboard.cmd
```

Open in browser:

```text
http://localhost:8090
```

Optional custom port:

PowerShell:

```powershell
.\run_db_dashboard.ps1 8095
```

cmd:

```bat
run_db_dashboard.cmd 8095
```

## 6. Reset Demo Data (Optional)

If you want a fresh rerun in this folder:

PowerShell:

```powershell
Remove-Item -Recurse -Force .\.datahub
docker compose down -v
```

cmd:

```bat
if exist .datahub rmdir /s /q .datahub
docker compose down -v
```
