"""CLI — the single entry point for Repo Whisperer.

Ties the engine together into the commands the learner uses:

    python -m repo_whisperer ingest <path-to-repo>     # index a repo
    python -m repo_whisperer ask "<question>"          # grounded, cited answer
    python -m repo_whisperer explore "<where is X>"    # show code + offer to teach
    python -m repo_whisperer look                      # teach the code you highlight

`ingest` walks → chunks → embeds → stores a repo (rebuilding the collection);
`ask` retrieves the most relevant chunks and prints a grounded, cited answer
(Phase 1). `explore` is the Phase 2 tutoring loop: it shows the relevant code,
offers to teach it, teaches at the learner's level, and afterwards suggests
related unexplored threads to keep the session going. `look` (Phase 3) gives the
tutor sight: it takes one announced screenshot of your editor and teaches the
code you've highlighted, using the rest of the visible file as context. `ask`,
`explore`, and `look` default to the `chroma_db/` store, so a typical session is
one `ingest` followed by as many of them as you like.
"""

from __future__ import annotations

import argparse
import sys

from repo_whisperer import learning
from repo_whisperer.answer import DEFAULT_TOP_K, answer_question
from repo_whisperer.learning import DEFAULT_STATE_PATH
from repo_whisperer.store import (
    COLLECTION_NAME,
    DEFAULT_DB_DIR,
    DEFAULT_OVERLAP,
    DEFAULT_WINDOW,
    ingest_repo,
)
from repo_whisperer.tutor import LEVELS, explore, look


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

    # Tie the learning memory to this repo. Switching to a different repo clears
    # stale covered threads (their citations no longer exist) but keeps the level.
    before = len(learning.load_state(args.state).threads)
    state = learning.note_repo_ingested(args.path, path=args.state)
    if before and not state.threads:
        print("Note: this is a different repo, so prior learning history was cleared.")

    print("Ready. Two ways to dig in:")
    print('  Learn it step by step:   python -m repo_whisperer explore "<topic>"')
    print('  Ask a quick question:    python -m repo_whisperer ask "<question>"')
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


def _cmd_explore(args: argparse.Namespace) -> int:
    if not args.query.strip():
        print("error: query is empty", file=sys.stderr)
        return 1

    explore(
        args.query, k=args.top_k, db_dir=args.db,
        level=args.level, state_path=args.state,
    )
    return 0


def _cmd_look(args: argparse.Namespace) -> int:
    teaching = look(
        mode="screen" if args.screen else "window",
        window_id=args.window_id,
        exclude=set(args.exclude),
        level=args.level,
        state_path=args.state,
        keep=args.keep,
    )
    return 0 if teaching is not None else 1


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
    p_ingest.add_argument(
        "--state", default=DEFAULT_STATE_PATH, metavar="FILE",
        help=f"learning-state file to bind to this repo (default: {DEFAULT_STATE_PATH})",
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

    p_explore = sub.add_parser(
        "explore", help="show relevant code, then offer to teach how it works",
        description=(
            "Phase 2 tutoring loop: retrieve and show the most relevant code "
            "for a 'where is X' query, offer to teach it, teach at your level "
            "with follow-ups, then suggest related unexplored threads to "
            "wander into next (you always decide)."
        ),
    )
    p_explore.add_argument("query", help="what you want to see, in quotes")
    p_explore.add_argument(
        "--db", default=DEFAULT_DB_DIR, metavar="DIR",
        help=f"ChromaDB storage directory (default: {DEFAULT_DB_DIR})",
    )
    p_explore.add_argument(
        "-k", "--top-k", type=int, default=DEFAULT_TOP_K, dest="top_k",
        help=f"number of chunks to retrieve (default: {DEFAULT_TOP_K})",
    )
    p_explore.add_argument(
        "--level", choices=LEVELS, default=None,
        help="teaching altitude — how much jargon to assume "
             "(default: your saved level, else beginner)",
    )
    p_explore.add_argument(
        "--state", default=DEFAULT_STATE_PATH, metavar="FILE",
        help=f"learning-state file (default: {DEFAULT_STATE_PATH})",
    )
    p_explore.set_defaults(func=_cmd_explore)

    p_look = sub.add_parser(
        "look", help="look at the code on your screen and teach the highlighted part",
        description=(
            "Phase 3 screen-aware teaching: take one announced screenshot of your "
            "active editor window and teach the code you've HIGHLIGHTED, using the "
            "rest of the visible file as context — at your saved level, in the same "
            "tutor voice. Opt-in and one-shot: nothing is captured unless you run "
            "this. Highlight the function (or lines) you want explained first."
        ),
    )
    p_look.add_argument(
        "--screen", action="store_true",
        help="capture the whole screen instead of the active editor window",
    )
    p_look.add_argument(
        "--window", type=int, metavar="ID", dest="window_id",
        help="capture an exact window id "
             "(see `python -m repo_whisperer.capture --list`)",
    )
    p_look.add_argument(
        "--exclude", action="append", default=[], metavar="APP",
        help="extra app/owner name to skip when picking the window (repeatable)",
    )
    p_look.add_argument(
        "--level", choices=LEVELS, default=None,
        help="teaching altitude (default: your saved level, else beginner)",
    )
    p_look.add_argument(
        "--keep", action="store_true",
        help="keep the screenshot file instead of deleting it after the lesson",
    )
    p_look.add_argument(
        "--state", default=DEFAULT_STATE_PATH, metavar="FILE",
        help=f"learning-state file (default: {DEFAULT_STATE_PATH})",
    )
    p_look.set_defaults(func=_cmd_look)

    args = parser.parse_args(argv)

    try:
        return args.func(args)
    except (NotADirectoryError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
