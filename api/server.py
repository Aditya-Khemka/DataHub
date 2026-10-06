import os
import tempfile
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import storage.engine as storage
from core import chunker, repo
from infrastructure.db import SessionLocal, Branch, get_branch_history, init_db
from query.parser import build_filter, execute_query

# Initialize database schemas
init_db()

app = FastAPI(title="DataHub Node API")

MAX_BATCH = 10_000  # hashes per existence check


# Dependency to get DB session
def get_db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# every ValueError becomes a 400; narrow to a dedicated exception if internal bugs start surfacing as 400s
@app.exception_handler(ValueError)
async def bad_request(_, exc):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(repo.Conflict)
async def conflict(_, exc):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


class HashList(BaseModel):
    hashes: List[str] = Field(max_length=MAX_BATCH)


class ChunkRef(BaseModel):
    hash: str
    length: int


class FileRecord(BaseModel):
    file_hash: str
    size: int
    chunker: str
    chunks: List[ChunkRef]
    stats: Optional[Dict[str, Any]] = None  # client-computed metadata (row count, schema, metrics)


class FilesPayload(BaseModel):
    files: List[FileRecord]


class CommitPayload(BaseModel):
    parent_hash: Optional[str] = None
    files: Dict[str, str]  # {"dir/name.csv": file_hash}
    author: str
    message: str
    time: str  # core.objects.format_time(); part of the hash, so a retry reproduces the same commit


@app.post("/chunks/missing")
def chunks_missing(payload: HashList, session: Session = Depends(get_db_session)):
    """Which of these chunks does the server still need? One call instead of one per chunk."""
    return {"missing": repo.missing_chunks(session, payload.hashes)}


@app.put("/chunks/{chunk_hash}")
async def upload_chunk(chunk_hash: str, request: Request, session: Session = Depends(get_db_session)):
    """Raw chunk bytes as the body. Size is capped, so an oversized upload never fills RAM."""
    buffer = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
    size = 0
    async for piece in request.stream():
        size += len(piece)
        if size > chunker.MAX_SIZE:
            buffer.close()
            raise HTTPException(status_code=413, detail=f"Chunk exceeds {chunker.MAX_SIZE} bytes")
        buffer.write(piece)
    buffer.seek(0)
    with buffer:
        await run_in_threadpool(repo.save_chunk, session, chunk_hash, buffer)
    return {"stored": chunk_hash}


@app.post("/files/exists")
def files_exist(payload: HashList, session: Session = Depends(get_db_session)):
    """Which of these whole files are already recorded? Lets a client skip chunking unchanged files."""
    return {"existing": sorted(repo.has_files(session, payload.hashes))}


@app.post("/files/")
def register_files(payload: FilesPayload, session: Session = Depends(get_db_session)):
    """Records files as ordered chunk lists; every claim is verified against the stored chunks."""
    for f in payload.files:
        repo.register_file(session, f.file_hash, f.size, f.chunker, [(c.hash, c.length) for c in f.chunks], f.stats)
    return {"registered": [f.file_hash for f in payload.files]}


@app.post("/commit/")
def create_commit(payload: CommitPayload, session: Session = Depends(get_db_session)):
    """The server builds the Merkle trees from the path list itself; 409 if main moved since parent_hash."""
    commit_hash = repo.create_commit(
        session, payload.parent_hash, payload.files, payload.author, payload.message, payload.time
    )
    return {"status": "success", "commit_hash": commit_hash}


@app.get("/branches/{name}")
def get_branch(name: str, session: Session = Depends(get_db_session)):
    """Current head of a branch; clients use it as the parent of their next commit."""
    branch = session.get(Branch, name)
    if branch is None:
        raise HTTPException(status_code=404, detail=f"Branch '{name}' does not exist")
    return {"name": branch.name, "commit_hash": branch.commit_hash}


@app.get("/commits/{commit_hash}")
def get_commit(commit_hash: str, session: Session = Depends(get_db_session)):
    """A commit plus its flattened file list {path: file_hash}; what pull needs to rebuild a folder."""
    found = repo.commit_files(session, commit_hash)
    if found is None:
        raise HTTPException(status_code=404, detail=f"Commit {commit_hash} does not exist")
    commit, files = found
    return {"commit_hash": commit.commit_hash, "parent_hash": commit.parent_hash, "author": commit.author,
            "message": commit.message, "created_at": commit.created_at, "files": files}


@app.get("/files/{file_hash}")
def get_file(file_hash: str, session: Session = Depends(get_db_session)):
    """A file's ordered chunk list."""
    found = repo.file_chunks(session, file_hash)
    if found is None:
        raise HTTPException(status_code=404, detail=f"File {file_hash} does not exist")
    size, chunks = found
    return {"file_hash": file_hash, "size": size, "chunks": [{"hash": h, "length": n} for h, n in chunks]}


@app.get("/chunks/{chunk_hash}")
def download_chunk(chunk_hash: str):
    """Raw chunk bytes, streamed. Clients verify the hash themselves."""
    if not os.path.exists(storage.chunk_path(chunk_hash)):  # chunk_path rejects malformed hashes (400)
        raise HTTPException(status_code=404, detail=f"Chunk {chunk_hash} does not exist")
    return StreamingResponse(storage.get_chunk(chunk_hash), media_type="application/octet-stream")


@app.get("/log")
async def get_log(session: Session = Depends(get_db_session)):
    try:
        history = get_branch_history(session, "main")
        return {"history": [{"commit_hash": r.commit_hash, "author": r.author, "message": r.message, "created_at": r.created_at} for r in history]}
    except ValueError:
        return {"history": []}

@app.post("/query/")
def query_metadata(payload: dict, session: Session = Depends(get_db_session)):
    """Filters the files of main's latest commit by a stat, e.g. 'accuracy > 0.9'."""
    query_string = payload.get("query")
    if not query_string:
         raise HTTPException(status_code=400, detail="Query string is required")
    head = session.get(Branch, "main")
    if head is None:
        return {"commit_hash": None, "results": []}
    _, files = repo.commit_files(session, head.commit_hash)
    try:
        ast = build_filter(query_string)
        results = execute_query(session, ast, files)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"commit_hash": head.commit_hash,
            "results": [{"path": path, "file_hash": h, "stats": stats} for path, h, stats in results]}
