"""Step 4 — Embedding + ChromaDB storage.

Take the chunks produced by step 3, embed each one locally with
`sentence-transformers` (`all-MiniLM-L6-v2`, no API cost), and persist them to a
local, persistent ChromaDB collection. Each chunk's stable id
(`"<path>:<start>-<end>"`) becomes the Chroma document id, and its path/line
range is stored as metadata so step 5 can cite exactly where an answer came
from.

Re-ingesting the same repo is idempotent: the collection is rebuilt from
scratch each time, so changed or deleted files never leave stale chunks behind.

Run standalone to embed a repo and report the resulting collection size:

    python -m repo_whisperer.store <path-to-repo> [--db DIR] [--window N] [--overlap M]
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import chromadb
from chromadb.config import Settings

from repo_whisperer.chunk import (
    Chunk,
    DEFAULT_OVERLAP,
    DEFAULT_WINDOW,
    chunk_source_files,
)
from repo_whisperer.ingest import iter_source_files

# Local embedding model. 384-dim, runs on CPU, no API cost — chosen for cheap
# iteration during Phase 1. The query at retrieval time must use this same model.
EMBED_MODEL: str = "all-MiniLM-L6-v2"

# Where the persistent vector store lives (gitignored, treated as rebuildable).
DEFAULT_DB_DIR: str = "chroma_db"

# A single collection holds the currently-ingested repo's chunks.
COLLECTION_NAME: str = "repo"

# Cosine distance pairs with the L2-normalized embeddings we produce below.
COLLECTION_METADATA = {"hnsw:space": "cosine"}

# Add to Chroma in batches to bound peak memory on large repos.
ADD_BATCH: int = 256


@dataclass
class IngestResult:
    """Summary of one ingest run, for the CLI to report."""

    num_files: int
    num_chunks: int
    collection_count: int
    db_dir: str


@lru_cache(maxsize=1)
def _load_embedder():
    """Load the sentence-transformers model once and reuse it.

    Imported lazily so that merely importing this module (e.g. for the dataclass
    or constants) doesn't pull in torch and load model weights.
    """
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(EMBED_MODEL)


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a list of texts into L2-normalized vectors (one per text)."""
    if not texts:
        return []
    model = _load_embedder()
    vectors = model.encode(
        texts,
        batch_size=64,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return vectors.tolist()


def get_client(db_dir: str | Path = DEFAULT_DB_DIR) -> "chromadb.api.ClientAPI":
    """Open (or create) the persistent ChromaDB store at `db_dir`."""
    return chromadb.PersistentClient(
        path=str(db_dir),
        settings=Settings(anonymized_telemetry=False),
    )


def _fresh_collection(client) -> "chromadb.api.models.Collection.Collection":
    """Return an empty collection, dropping any previous one of the same name.

    Rebuilding from scratch keeps re-ingestion idempotent: a file that shrank,
    moved, or was deleted can't leave orphaned chunks behind.
    """
    existing = {c.name for c in client.list_collections()}
    if COLLECTION_NAME in existing:
        client.delete_collection(COLLECTION_NAME)
    return client.create_collection(
        name=COLLECTION_NAME,
        metadata=COLLECTION_METADATA,
    )


def store_chunks(chunks: list[Chunk], collection, batch: int = ADD_BATCH) -> int:
    """Embed and add `chunks` to `collection`. Returns the count added."""
    for i in range(0, len(chunks), batch):
        window = chunks[i : i + batch]
        collection.add(
            ids=[c.id for c in window],
            embeddings=embed_texts([c.text for c in window]),
            documents=[c.text for c in window],
            metadatas=[
                {
                    "path": c.path,
                    "start_line": c.start_line,
                    "end_line": c.end_line,
                }
                for c in window
            ],
        )
    return len(chunks)


def ingest_repo(
    repo_path: str | Path,
    db_dir: str | Path = DEFAULT_DB_DIR,
    window: int = DEFAULT_WINDOW,
    overlap: int = DEFAULT_OVERLAP,
) -> IngestResult:
    """Ingest → chunk → embed → store a repo, rebuilding the collection.

    Returns an `IngestResult` summarizing files, chunks, and the final
    collection size as reported by ChromaDB.
    """
    sources = iter_source_files(repo_path)
    chunks = chunk_source_files(sources, window=window, overlap=overlap)

    client = get_client(db_dir)
    collection = _fresh_collection(client)
    if chunks:
        store_chunks(chunks, collection)

    return IngestResult(
        num_files=len(sources),
        num_chunks=len(chunks),
        collection_count=collection.count(),
        db_dir=str(Path(db_dir).resolve()),
    )


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m repo_whisperer.store",
        description="Ingest, chunk, embed, and store a repo in persistent ChromaDB.",
    )
    parser.add_argument("path", help="path to the repository to ingest")
    parser.add_argument(
        "--db", default=DEFAULT_DB_DIR, metavar="DIR",
        help=f"ChromaDB storage directory (default: {DEFAULT_DB_DIR})",
    )
    parser.add_argument(
        "--window", type=int, default=DEFAULT_WINDOW,
        help=f"chunk size in lines (default: {DEFAULT_WINDOW})",
    )
    parser.add_argument(
        "--overlap", type=int, default=DEFAULT_OVERLAP,
        help=f"overlap between adjacent chunks in lines (default: {DEFAULT_OVERLAP})",
    )
    args = parser.parse_args(argv[1:])

    try:
        print(f"Ingesting {args.path} … (loading embedder on first run can take a moment)")
        result = ingest_repo(
            args.path, db_dir=args.db, window=args.window, overlap=args.overlap,
        )
    except (NotADirectoryError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(
        f"Stored {result.collection_count} chunks "
        f"(from {result.num_chunks} chunked across {result.num_files} files) "
        f"in collection '{COLLECTION_NAME}' at {result.db_dir}."
    )
    if result.collection_count != result.num_chunks:
        print(
            f"warning: collection count ({result.collection_count}) != chunk count "
            f"({result.num_chunks}); some ids may have collided.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
