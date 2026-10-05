import hashlib
import os

import fastcdc

# Chunk boundaries must be identical on every machine, forever.
# Changing any of these (or the fastcdc version) breaks dedup against stored data.
MIN_SIZE = 4 * 1024 * 1024 #4mb
AVG_SIZE = 16 * 1024 * 1024 #16 mb
MAX_SIZE = 64 * 1024 * 1024 #64 mb
CHUNKER = "fastcdc-1.7.0/4M/16M/64M"


def chunk_file(path, min_size=MIN_SIZE, avg_size=AVG_SIZE, max_size=MAX_SIZE):
    """
    Splits a file into content-defined chunks in a single read pass.
    Returns (file_hash, size, [(chunk_hash, offset, length), ...]) in file order.
    Also, it checks and runs only on fastcdc compiled extension, which is faster than the pure Python version.
    """
    if fastcdc.fastcdc.__module__ != "fastcdc.fastcdc_cy":
        raise RuntimeError("fastcdc compiled extension not loaded; use Python 3.8-3.12")

    # an empty file will throw error
    if os.path.getsize(path) == 0:
        return hashlib.sha256(b"").hexdigest(), 0, []

    file_hasher = hashlib.sha256()
    chunks = []
    # fastcdc never closes its map; 
    # consuming the generator here lets free it on return, releasing the Windows file lock. 
    
    for chunk in fastcdc.fastcdc(path, min_size, avg_size, max_size, fat=True):
        file_hasher.update(chunk.data)
        chunks.append((hashlib.sha256(chunk.data).hexdigest(), chunk.offset, chunk.length))

    return file_hasher.hexdigest(), chunks[-1][1] + chunks[-1][2], chunks
