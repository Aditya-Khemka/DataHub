import hashlib
import os
import tempfile
from typing import BinaryIO, Generator

from core.chunker import MAX_SIZE
from core.objects import is_valid_hash

BLOB_DIR = os.getenv("BLOB_DIR", "blobs/")
CHUNK_SIZE = 8192          # I/O buffer size for put_blob/get_blob
READ_SIZE = 1024 * 1024    # I/O buffer size for content chunks (up to MAX_SIZE each)

# Ensure the BLOB_DIR exists
os.makedirs(BLOB_DIR, exist_ok=True)


def chunk_path(chunk_hash: str) -> str:
    """blobs/ab/abcdef... ; fan-out by the first 2 hex chars keeps directories small."""
    if not is_valid_hash(chunk_hash):
        raise ValueError(f"Invalid chunk hash: {chunk_hash!r}")
    return os.path.join(BLOB_DIR, chunk_hash[:2], chunk_hash)


def put_chunk(expected_hash: str, data_stream: BinaryIO) -> bool:
    """
    Stores one content chunk under its SHA-256, verifying the bytes match expected_hash.
    Writes to a temp file and atomically renames it, so a crash never leaves a partial chunk.
    Returns True if newly written, False if it was already stored.
    """
    final_path = chunk_path(expected_hash)
    if os.path.exists(final_path):
        return False

    folder = os.path.dirname(final_path)
    os.makedirs(folder, exist_ok=True)
    # Temp file in the same folder so os.replace is an atomic rename on the same filesystem
    fd, tmp_path = tempfile.mkstemp(dir=folder, suffix=".tmp")
    try:
        hasher = hashlib.sha256()
        size = 0
        with os.fdopen(fd, "wb") as f:
            while piece := data_stream.read(READ_SIZE):
                size += len(piece)
                if size > MAX_SIZE:
                    raise ValueError(f"Chunk exceeds maximum size of {MAX_SIZE} bytes")
                hasher.update(piece)
                f.write(piece)

        if hasher.hexdigest() != expected_hash:
            raise ValueError(f"Chunk content does not match hash {expected_hash}")

        try:
            os.replace(tmp_path, final_path)
        except PermissionError:
            # Windows: the target exists and is open by a reader. Same hash => same bytes, so it's already stored.
            if not os.path.exists(final_path):
                raise
            os.remove(tmp_path)
            return False
        return True
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def get_chunk(chunk_hash: str) -> Generator[bytes, None, None]:
    """Streams a stored chunk back in READ_SIZE pieces."""
    path = chunk_path(chunk_hash)
    if not os.path.exists(path):
        raise ValueError(f"Chunk with hash {chunk_hash} does not exist.")
    with open(path, "rb") as f:
        while piece := f.read(READ_SIZE):
            yield piece

def put_blob(data_stream: BinaryIO) -> str:
    """Reads a data stream, hashes via SHA-256 in 2 passes, writes locally, returns the hash."""
    hasher = hashlib.sha256()
    
    # Pass 1: Read the stream chunk by chunk to compute the SHA-256 hash without loading into RAM
    while True:
        chunk = data_stream.read(CHUNK_SIZE)
        if not chunk:
            break
        hasher.update(chunk)
        
    final_hash = hasher.hexdigest()
    blob_path = os.path.join(BLOB_DIR, final_hash)
    
    # If the blob already exists, simply return the hash (Deduplication)
    if os.path.exists(blob_path):
        return final_hash
        
    # Pass 2: Reset the stream and write the payload to disk
    data_stream.seek(0)
    with open(blob_path, "wb") as f:
        while True:
            chunk = data_stream.read(CHUNK_SIZE)
            if not chunk:
                break
            f.write(chunk)
            
    return final_hash

def get_blob(blob_hash: str) -> Generator[bytes, None, None]:
    """Returns an iterator streaming chunks of the stored file payload securely."""
    blob_path = os.path.join(BLOB_DIR, blob_hash)
    
    if not os.path.exists(blob_path):
        raise ValueError(f"Blob with hash {blob_hash} does not exist.")
        
    # Open file and yield chunks as a generator
    with open(blob_path, "rb") as f:
        while True:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            yield chunk
