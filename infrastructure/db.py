import os
import enum
import sqlite3
from datetime import datetime, timezone
from sqlalchemy import create_engine, Column, String, Integer, BigInteger, Enum, ForeignKey, Text, JSON, select, literal, event, update, UniqueConstraint
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker, declarative_base, relationship, aliased
from sqlalchemy.pool import StaticPool
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from core.objects import format_time

# Allow overriding for test suites, default to in-memory SQLite for seamless testing when Docker is down
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///:memory:")


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, _):
    # SQLite ignores foreign keys unless asked; keep every SQLite engine (incl. test ones) as strict as Postgres
    if isinstance(dbapi_connection, sqlite3.Connection):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")


# Initialize explicit engine
if DATABASE_URL.startswith("sqlite"):
    # StaticPool shares ONE connection across threads so the in-memory DB is visible
    # to FastAPI's worker threads; fine for local dev/tests, use Postgres for anything concurrent.
    engine = create_engine(DATABASE_URL, echo=False, poolclass=StaticPool, connect_args={"check_same_thread": False})
else:
    engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

class ObjectType(str, enum.Enum):
    file = "file"
    tree = "tree"

class Chunk(Base):
    """One content-defined piece of file data, stored once under its SHA-256."""
    __tablename__ = "chunk"
    chunk_hash = Column(String, primary_key=True)
    size_bytes = Column(BigInteger, nullable=False)

class File(Base):
    """A file version, identified by the SHA-256 of its full content."""
    __tablename__ = "file"
    file_hash = Column(String, primary_key=True)
    size_bytes = Column(BigInteger, nullable=False)
    chunker = Column(String, nullable=False)  # e.g. core.chunker.CHUNKER; boundaries only dedupe within one chunker

class FileChunk(Base):
    """The ordered chunk list of a file; offsets are the running sum of chunk sizes."""
    __tablename__ = "file_chunk"
    file_hash = Column(String, ForeignKey("file.file_hash", deferrable=True, initially="DEFERRED"), primary_key=True)
    seq = Column(Integer, primary_key=True)
    chunk_hash = Column(String, ForeignKey("chunk.chunk_hash", deferrable=True, initially="DEFERRED"), nullable=False, index=True)

class Tree(Base):
    """Represents a directory node."""
    __tablename__ = "tree"
    tree_hash = Column(String, primary_key=True)
    entries = relationship("TreeEntry", back_populates="tree")

class TreeEntry(Base):
    """Maps filenames dynamically to underlying Files or sub-Trees."""
    __tablename__ = "tree_entry"
    __table_args__ = (UniqueConstraint("tree_hash", "name"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    tree_hash = Column(String, ForeignKey("tree.tree_hash", deferrable=True, initially="DEFERRED"))
    name = Column(String, nullable=False)
    object_hash = Column(String, nullable=False, index=True) # Polymorphic: matches file_hash OR tree_hash
    object_type = Column(Enum(ObjectType), nullable=False)

    tree = relationship("Tree", back_populates="entries")

class Commit(Base):
    """Repo snapshot states creating a directed graph via lineage."""
    __tablename__ = "commit"
    commit_hash = Column(String, primary_key=True)
    # parent_hash points backward to trace chronological history
    parent_hash = Column(String, ForeignKey("commit.commit_hash", deferrable=True, initially="DEFERRED"), nullable=True) 
    tree_hash = Column(String, ForeignKey("tree.tree_hash", deferrable=True, initially="DEFERRED"), nullable=False)
    author = Column(String, nullable=False)
    message = Column(Text, nullable=False)
    # Stored exactly as hashed (core.objects.format_time) so the commit hash can be recomputed
    created_at = Column(String, nullable=False, default=lambda: format_time(datetime.now(timezone.utc)))

class Branch(Base):
    """Human readable pointers marking specific Commits."""
    __tablename__ = "branch"
    name = Column(String, primary_key=True)
    commit_hash = Column(String, ForeignKey("commit.commit_hash", deferrable=True, initially="DEFERRED"), nullable=False)

class Metadata(Base):
    """Dataset analytics stored persistently regarding a dataset blob or tree."""
    __tablename__ = "metadata"
    id = Column(Integer, primary_key=True, autoincrement=True)
    target_hash = Column(String, nullable=False)
    stats = Column(JSON, nullable=False)

def get_db_session():
    """Yields a SQLAlchemy session to connect to Postgres."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db(test_engine=None):
    """Creates all tables based on declarative base."""
    target_engine = test_engine or engine
    Base.metadata.create_all(bind=target_engine)

def insert_ignore(session, model, rows) -> int:
    """
    Inserts rows, silently skipping any whose primary/unique key already exists
    (e.g. two pushes uploading the same chunk). Returns how many rows were inserted.
    """
    if not rows:
        return 0
    dialect_insert = pg_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
    return session.execute(dialect_insert(model).values(rows).on_conflict_do_nothing()).rowcount

# =======================
# Recursive CTE Query API
# =======================

def get_commit_history(session, start_commit_hash: str):
    """
    Returns an ordered list of commits from `start_commit_hash` backward to the root.
    Utilizes a Recursive Database CTE to traverse parent_hash iteratively.
    """
    # Base case: Retrieve all fields from the selected starting commit natively in SQL
    base_q = select(
        Commit.commit_hash, 
        Commit.parent_hash, 
        Commit.author, 
        Commit.message, 
        Commit.created_at, 
        literal(1).label("depth")
    ).where(Commit.commit_hash == start_commit_hash).cte(name="commit_history", recursive=True)

    # Recursive step: Join previous CTE against the Commit table finding the 'parent'
    recursive_q = select(
        Commit.commit_hash, 
        Commit.parent_hash, 
        Commit.author, 
        Commit.message, 
        Commit.created_at, 
        (base_q.c.depth + 1).label("depth")
    ).join(base_q, Commit.commit_hash == base_q.c.parent_hash)

    # Combine Base and Recursion
    history_cte = base_q.union_all(recursive_q)
    
    # Query the resolved CTE ordered chronologically (shallowest/newest first)
    query = select(history_cte).order_by(history_cte.c.depth.asc())
    return session.execute(query).mappings().all()

def get_tree_closure(session, start_tree_hash: str):
    """
    Returns every unique file_hash referenced deeply inside this Tree or sub-trees.
    Utilizes a Recursive CTE to crawl TreeEntry dynamically inside the DB engine.
    """
    # Base case: the initial folder's raw contents
    base_q = select(
        TreeEntry.tree_hash, 
        TreeEntry.object_hash, 
        TreeEntry.object_type
    ).where(TreeEntry.tree_hash == start_tree_hash).cte(name="tree_closure", recursive=True)

    # Recursive step: Find where inside that folder lies another 'tree' type, and resolve ITS contents
    recursive_q = select(
        TreeEntry.tree_hash, 
        TreeEntry.object_hash, 
        TreeEntry.object_type
    ).join(base_q, TreeEntry.tree_hash == base_q.c.object_hash)\
     .where(base_q.c.object_type == ObjectType.tree)

    closure_cte = base_q.union_all(recursive_q)

    # Filter out sub-folders; only file hashes are returned
    query = select(closure_cte.c.object_hash)\
        .where(closure_cte.c.object_type == ObjectType.file)\
        .distinct()
    
    return session.execute(query).scalars().all()

# =======================
# Branch Pointers Logic
# =======================

def advance_branch(session, branch_name: str, expected_hash, new_hash: str) -> bool:
    """
    Compare-and-swap for a branch pointer: moves it to new_hash only if it still points at
    expected_hash (None = branch must not exist yet). Returns False if someone else moved it.
    Does not commit; the caller commits the whole push atomically.
    """
    if expected_hash is None:
        return insert_ignore(session, Branch, [{"name": branch_name, "commit_hash": new_hash}]) == 1
    stmt = update(Branch)\
        .where(Branch.name == branch_name, Branch.commit_hash == expected_hash)\
        .values(commit_hash=new_hash)
    return session.execute(stmt).rowcount == 1

def get_branch_history(session, branch_name: str):
    """
    Returns the complete recursive lineage specifically tracing down from a Branch Pointer head.
    """
    branch = session.query(Branch).filter_by(name=branch_name).first()
    if not branch:
        raise ValueError(f"Branch '{branch_name}' does not exist.")
    
    return get_commit_history(session, branch.commit_hash)

