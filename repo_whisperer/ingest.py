"""Step 2 — Ingestion.

Walk a target repository, filter to code/text files worth indexing, skip
dependency and build directories, and read each file's contents. The output is
a list of `SourceFile` records that step 3 (chunking) will consume.

Run standalone to sanity-check what a repo ingests to:

    python -m repo_whisperer.ingest <path-to-repo>
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

# File extensions we treat as indexable source. Kept deliberately broad across
# common languages plus config/docs, since a coding tutor benefits from seeing
# build files and READMEs alongside the code itself.
CODE_EXTENSIONS: frozenset[str] = frozenset(
    {
        # General-purpose languages
        ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".c", ".h", ".cpp",
        ".hpp", ".cc", ".cxx", ".cs", ".go", ".rs", ".rb", ".php", ".swift",
        ".kt", ".kts", ".scala", ".clj", ".ex", ".exs", ".erl", ".hs", ".ml",
        ".lua", ".pl", ".pm", ".r", ".dart", ".groovy",
        # Shell / scripting
        ".sh", ".bash", ".zsh", ".fish", ".ps1",
        # Web / markup / styles
        ".html", ".htm", ".css", ".scss", ".sass", ".less", ".vue", ".svelte",
        # Data / config
        ".sql", ".graphql", ".proto", ".json", ".yaml", ".yml", ".toml",
        ".ini", ".cfg", ".env",
        # Docs
        ".md", ".rst", ".txt",
    }
)

# Directory names we never descend into: dependencies, build output, caches,
# version-control internals, and editor metadata.
SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git", ".hg", ".svn",
        "node_modules", "bower_components", "vendor",
        "dist", "build", "out", "bin", "obj", "target",
        ".next", ".nuxt", ".svelte-kit",
        "venv", ".venv", "env", ".env.d",
        "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox",
        ".gradle", ".cache", ".parcel-cache",
        "coverage", ".nyc_output", "htmlcov",
        ".idea", ".vscode",
        "site-packages", ".eggs",
    }
)

# Files larger than this are skipped: usually minified bundles, vendored blobs,
# or generated data that would pollute retrieval with noise.
MAX_FILE_BYTES: int = 1_000_000  # 1 MB


@dataclass
class SourceFile:
    """A single ingested file."""

    path: str          # path relative to the repo root (used for citations)
    abs_path: str      # absolute path on disk
    content: str       # full file text
    num_lines: int     # line count
    size_bytes: int    # size on disk


def _looks_binary(raw: bytes) -> bool:
    """Heuristic: a NUL byte in the first chunk means it's not text."""
    return b"\x00" in raw[:1024]


def iter_source_files(root: str | os.PathLike[str]) -> "list[SourceFile]":
    """Walk `root` and return the source files worth indexing.

    Skips dependency/build directories, non-allowlisted extensions, oversized
    files, binaries, and anything that can't be decoded as UTF-8 text.
    """
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        raise NotADirectoryError(f"Not a directory: {root_path}")

    files: list[SourceFile] = []

    for dirpath, dirnames, filenames in os.walk(root_path):
        # Prune skip-dirs and hidden dirs in place so os.walk won't descend.
        dirnames[:] = [
            d for d in dirnames
            if d not in SKIP_DIRS and not d.startswith(".")
        ]

        for filename in filenames:
            ext = Path(filename).suffix.lower()
            if ext not in CODE_EXTENSIONS:
                continue

            abs_path = Path(dirpath) / filename
            try:
                size = abs_path.stat().st_size
            except OSError:
                continue
            if size > MAX_FILE_BYTES:
                continue

            try:
                raw = abs_path.read_bytes()
            except OSError:
                continue
            if _looks_binary(raw):
                continue

            try:
                content = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue

            rel_path = abs_path.relative_to(root_path).as_posix()
            files.append(
                SourceFile(
                    path=rel_path,
                    abs_path=str(abs_path),
                    content=content,
                    num_lines=content.count("\n") + 1 if content else 0,
                    size_bytes=size,
                )
            )

    # Stable, predictable ordering.
    files.sort(key=lambda f: f.path)
    return files


def _main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m repo_whisperer.ingest <path-to-repo>", file=sys.stderr)
        return 2

    try:
        files = iter_source_files(argv[1])
    except (NotADirectoryError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    total_lines = sum(f.num_lines for f in files)
    total_bytes = sum(f.size_bytes for f in files)

    for f in files:
        print(f"{f.num_lines:>6} lines  {f.size_bytes:>9,} B  {f.path}")

    print(
        f"\n{len(files)} files, {total_lines:,} lines, {total_bytes:,} bytes ingested."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
