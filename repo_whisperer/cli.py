"""CLI — the single entry point for Repo Whisperer.

Ties the engine together into the commands the learner uses:

    python -m repo_whisperer ingest <path-to-repo>     # index a repo
    python -m repo_whisperer ask "<question>"          # grounded, cited answer
    python -m repo_whisperer explore "<where is X>"    # show code + offer to teach
    python -m repo_whisperer look                      # teach the code you highlight
    python -m repo_whisperer check <practice-file>     # help with code you wrote
    python -m repo_whisperer guide ["<question>"]      # whole-repo learning-path advice

`ingest` walks → chunks → embeds → stores a repo (rebuilding the collection);
`ask` retrieves the most relevant chunks and prints a grounded, cited answer
(Phase 1). `explore` is the Phase 2 tutoring loop: it shows the relevant code,
offers to teach it, teaches at the learner's level, and afterwards suggests
related unexplored threads to keep the session going. `look` (Phase 3) gives the
tutor sight: it takes one announced screenshot of your editor and teaches the
code you've highlighted, using the rest of the visible file as context. `check`
(Phase 3) completes teach → verify: after learning a pattern, write your own
version in a practice file and `check` re-reads it from disk and helps you close
the gap — grounded in the real repo, never a grade. `guide` answers the side
questions the thread-scoped commands can't — "which file should I learn first
for my level?" — from a map of the whole ingested repo. `ask`, `explore`,
`look`, `check`, and `guide` default to the `chroma_db/` store, so a typical
session is one `ingest` followed by as many of them as you like.
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
from repo_whisperer.tutor import LEVELS, check, explore, guide, look


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
        k=args.top_k,
        db_dir=args.db,
        state_path=args.state,
        keep=args.keep,
    )
    return 0 if teaching is not None else 1


def _cmd_check(args: argparse.Namespace) -> int:
    assistance = check(
        args.file,
        level=args.level,
        k=args.top_k,
        db_dir=args.db,
        state_path=args.state,
    )
    return 0 if assistance is not None else 1


def _cmd_guide(args: argparse.Namespace) -> int:
    guide(
        args.question,
        level=args.level,
        k=args.top_k,
        db_dir=args.db,
        state_path=args.state,
    )
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
        "--db", default=DEFAULT_DB_DIR, metavar="DIR",
        help=f"ChromaDB store to pull repo context from (default: {DEFAULT_DB_DIR})",
    )
    p_look.add_argument(
        "-k", "--top-k", type=int, default=DEFAULT_TOP_K, dest="top_k",
        help=f"related chunks to retrieve for context (default: {DEFAULT_TOP_K})",
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

    p_check = sub.add_parser(
        "check", help="have the tutor look at code you wrote and help you close the gap",
        description=(
            "Phase 3 verification, assist-first: after learning a pattern "
            "(via `explore` or `look`), write your own version in a practice "
            "file, then run `check <file>`. The tutor re-reads your file "
            "straight from disk (no screenshot), works out what you were going "
            "for — assuming your most recent lesson, and saying so — and helps "
            "you close the gap at your level, comparing against how the real "
            "codebase does it with `path:start-end` citations. Assistance, "
            "never a grade."
        ),
    )
    p_check.add_argument("file", help="path to the practice file you wrote")
    p_check.add_argument(
        "--db", default=DEFAULT_DB_DIR, metavar="DIR",
        help=f"ChromaDB store to compare against (default: {DEFAULT_DB_DIR})",
    )
    p_check.add_argument(
        "-k", "--top-k", type=int, default=DEFAULT_TOP_K, dest="top_k",
        help=f"related chunks to retrieve for comparison (default: {DEFAULT_TOP_K})",
    )
    p_check.add_argument(
        "--level", choices=LEVELS, default=None,
        help="teaching altitude (default: your saved level, else beginner)",
    )
    p_check.add_argument(
        "--state", default=DEFAULT_STATE_PATH, metavar="FILE",
        help=f"learning-state file (default: {DEFAULT_STATE_PATH})",
    )
    p_check.set_defaults(func=_cmd_check)

    p_guide = sub.add_parser(
        "guide", help="ask a side question about the whole repo or your learning path",
        description=(
            "The side-question command: whole-repo, learning-path questions "
            "that the thread-scoped commands can't answer — 'which file "
            "should I learn first for my level?', 'what is this project, big "
            "picture?'. The tutor answers from a map of EVERY file in the "
            "ingested repo (plus excerpts retrieved for your question), fitted "
            "to your saved level and steered by the threads you've already "
            "covered, ending with a ready-to-run `explore` query. With no "
            "question it answers: where should I start?"
        ),
    )
    p_guide.add_argument(
        "question", nargs="?", default="",
        help="your side question, in quotes (default: where should I start?)",
    )
    p_guide.add_argument(
        "--db", default=DEFAULT_DB_DIR, metavar="DIR",
        help=f"ChromaDB store to survey (default: {DEFAULT_DB_DIR})",
    )
    p_guide.add_argument(
        "-k", "--top-k", type=int, default=DEFAULT_TOP_K, dest="top_k",
        help=f"excerpts to retrieve for grounding (default: {DEFAULT_TOP_K})",
    )
    p_guide.add_argument(
        "--level", choices=LEVELS, default=None,
        help="teaching altitude (default: your saved level, else beginner)",
    )
    p_guide.add_argument(
        "--state", default=DEFAULT_STATE_PATH, metavar="FILE",
        help=f"learning-state file (default: {DEFAULT_STATE_PATH})",
    )
    p_guide.set_defaults(func=_cmd_guide)

    args = parser.parse_args(argv)

    try:
        return args.func(args)
    except (NotADirectoryError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
