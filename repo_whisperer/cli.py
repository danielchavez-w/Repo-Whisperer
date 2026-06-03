"""Step 6 — CLI.

The single entry point that ties steps 2–5 together into the two commands the
Phase 1 spec asks for:

    python -m repo_whisperer ingest <path-to-repo>
    python -m repo_whisperer ask "<question>"

`ingest` walks → chunks → embeds → stores a repo (rebuilding the collection);
`ask` retrieves the most relevant chunks and prints a grounded, cited answer.
Both default to the `chroma_db/` store, so a typical session is one `ingest`
followed by as many `ask`s as you like.
"""

from __future__ import annotations

import argparse
import sys

from repo_whisperer.answer import DEFAULT_TOP_K, answer_question
from repo_whisperer.store import (
    COLLECTION_NAME,
    DEFAULT_DB_DIR,
    DEFAULT_OVERLAP,
    DEFAULT_WINDOW,
    ingest_repo,
)


def _cmd_ingest(args: argparse.Namespace) -> int:
    print(f"Ingesting {args.path} … (loading the embedder on first run can take a moment)")
    result = ingest_repo(
        args.path, db_dir=args.db, window=args.window, overlap=args.overlap,
    )
    print(
        f"Stored {result.collection_count} chunks "
        f"(from {result.num_chunks} chunked across {result.num_files} files) "
        f"in collection '{COLLECTION_NAME}' at {result.db_dir}."
    )
    print(f'Ready. Ask a question with:  python -m repo_whisperer ask "<question>"')
    return 0


def _cmd_ask(args: argparse.Namespace) -> int:
    if not args.question.strip():
        print("error: question is empty", file=sys.stderr)
        return 1

    answer, hits = answer_question(args.question, k=args.top_k, db_dir=args.db)
    print(answer)
    print("\n" + "-" * 60)
    print(f"Retrieved {len(hits)} chunks (cosine distance, lower = closer):")
    for h in hits:
        print(f"  {h.distance:.3f}  {h.id}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv

    parser = argparse.ArgumentParser(
        prog="python -m repo_whisperer",
        description="Repo Whisperer — ask grounded questions about a local codebase.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser(
        "ingest", help="index a repo into the vector store",
        description="Walk, chunk, embed, and store a repo (rebuilds the collection).",
    )
    p_ingest.add_argument("path", help="path to the repository to ingest")
    p_ingest.add_argument(
        "--db", default=DEFAULT_DB_DIR, metavar="DIR",
        help=f"ChromaDB storage directory (default: {DEFAULT_DB_DIR})",
    )
    p_ingest.add_argument(
        "--window", type=int, default=DEFAULT_WINDOW,
        help=f"chunk size in lines (default: {DEFAULT_WINDOW})",
    )
    p_ingest.add_argument(
        "--overlap", type=int, default=DEFAULT_OVERLAP,
        help=f"overlap between adjacent chunks in lines (default: {DEFAULT_OVERLAP})",
    )
    p_ingest.set_defaults(func=_cmd_ingest)

    p_ask = sub.add_parser(
        "ask", help="ask a question about the ingested repo",
        description="Retrieve relevant chunks and print a grounded, cited answer.",
    )
    p_ask.add_argument("question", help="the question to ask, in quotes")
    p_ask.add_argument(
        "--db", default=DEFAULT_DB_DIR, metavar="DIR",
        help=f"ChromaDB storage directory (default: {DEFAULT_DB_DIR})",
    )
    p_ask.add_argument(
        "-k", "--top-k", type=int, default=DEFAULT_TOP_K, dest="top_k",
        help=f"number of chunks to retrieve (default: {DEFAULT_TOP_K})",
    )
    p_ask.set_defaults(func=_cmd_ask)

    args = parser.parse_args(argv)

    try:
        return args.func(args)
    except (NotADirectoryError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
