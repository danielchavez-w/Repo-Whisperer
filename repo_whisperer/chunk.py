"""Step 3 — Chunking.

Split each ingested file into overlapping line-window chunks. Every chunk
carries the metadata retrieval and citation depend on: source file path and
1-indexed start/end line numbers.

Overlap matters: a relevant function or block that straddles a window boundary
would otherwise be split so that neither chunk contains it whole. Repeating a
few lines between adjacent windows keeps such spans recoverable.

Run standalone to see how a repo chunks:

    python -m repo_whisperer.chunk <path-to-repo> [--window N] [--overlap M]
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

from repo_whisperer.ingest import SourceFile, iter_source_files

# Defaults chosen for the Phase 1 baseline. A window of 40 lines is large
# enough to hold a small function or coherent block, while 10 lines of overlap
# (25%) keeps boundary-straddling code recoverable. Both are tunable per call.
DEFAULT_WINDOW: int = 40
DEFAULT_OVERLAP: int = 10


@dataclass
class Chunk:
    """A single line-window chunk of a source file."""

    id: str            # stable unique id: "<path>:<start>-<end>"
    path: str          # source file path, relative to repo root
    start_line: int    # 1-indexed, inclusive
    end_line: int      # 1-indexed, inclusive
    text: str          # the chunk's source text


def chunk_source_file(
    source: SourceFile,
    window: int = DEFAULT_WINDOW,
    overlap: int = DEFAULT_OVERLAP,
) -> list[Chunk]:
    """Split one `SourceFile` into overlapping line-window chunks.

    A file shorter than `window` yields a single chunk covering the whole file.
    Blank-only / empty files yield nothing.
    """
    if window <= 0:
        raise ValueError("window must be positive")
    if overlap < 0 or overlap >= window:
        raise ValueError("overlap must be in [0, window)")

    lines = source.content.splitlines()
    n = len(lines)
    if n == 0 or not source.content.strip():
        return []

    step = window - overlap
    chunks: list[Chunk] = []

    start = 0
    while start < n:
        end = min(start + window, n)  # exclusive index into `lines`
        text = "\n".join(lines[start:end])
        start_line = start + 1        # 1-indexed, inclusive
        end_line = end                # 1-indexed, inclusive
        chunks.append(
            Chunk(
                id=f"{source.path}:{start_line}-{end_line}",
                path=source.path,
                start_line=start_line,
                end_line=end_line,
                text=text,
            )
        )
        if end == n:  # last window reached the end of the file
            break
        start += step

    return chunks


def chunk_source_files(
    sources: list[SourceFile],
    window: int = DEFAULT_WINDOW,
    overlap: int = DEFAULT_OVERLAP,
) -> list[Chunk]:
    """Chunk a list of `SourceFile`s, preserving their order."""
    chunks: list[Chunk] = []
    for source in sources:
        chunks.extend(chunk_source_file(source, window=window, overlap=overlap))
    return chunks


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m repo_whisperer.chunk",
        description="Ingest a repo and split it into overlapping line-window chunks.",
    )
    parser.add_argument("path", help="path to the repository to chunk")
    parser.add_argument(
        "--window", type=int, default=DEFAULT_WINDOW,
        help=f"chunk size in lines (default: {DEFAULT_WINDOW})",
    )
    parser.add_argument(
        "--overlap", type=int, default=DEFAULT_OVERLAP,
        help=f"overlap between adjacent chunks in lines (default: {DEFAULT_OVERLAP})",
    )
    parser.add_argument(
        "--show", type=int, default=3, metavar="N",
        help="print the first N chunks as a preview (default: 3, 0 to disable)",
    )
    args = parser.parse_args(argv[1:])

    try:
        sources = iter_source_files(args.path)
        chunks = chunk_source_files(sources, window=args.window, overlap=args.overlap)
    except (NotADirectoryError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    for chunk in chunks[: args.show]:
        preview = chunk.text.splitlines()
        head = preview[0] if preview else ""
        print(f"[{chunk.id}]  ({len(preview)} lines)")
        print(f"    {head[:100]}")

    avg = (sum(len(c.text) for c in chunks) / len(chunks)) if chunks else 0
    print(
        f"\n{len(chunks)} chunks from {len(sources)} files "
        f"(window={args.window}, overlap={args.overlap}, avg {avg:.0f} chars/chunk)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
