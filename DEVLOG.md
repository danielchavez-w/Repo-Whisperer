# Development Log

## 2026-05-29 — Step 3: Chunking

Added `repo_whisperer/chunk.py`: splits each `SourceFile` into overlapping
line-window `Chunk` records for step 4 to embed.

- **Window + overlap:** default 40-line windows with 10-line (25%) overlap,
  both tunable per call / via `--window` / `--overlap`. Overlap keeps a block
  that straddles a window boundary recoverable in at least one whole chunk.
- **Metadata:** each chunk carries `path` and **1-indexed, inclusive**
  `start_line`/`end_line`, plus a stable id `"<path>:<start>-<end>"` (the
  citation key and Chroma document id later).
- **Edge cases:** files shorter than the window yield a single whole-file
  chunk; the final window always ends exactly on the last line (no tiny
  redundant tail); empty / whitespace-only files yield nothing.
- Standalone runner previews the first N chunks and prints a count/avg-size
  summary.

**Verified** with a controlled 100-line case: window=40/overlap=10 produces
windows `(1-40), (31-70), (61-100)` — correct 30-line step, 1-indexing, and
exact end alignment. Real repos chunk cleanly (e.g. this repo → 86 chunks).

**Decision:** `all-MiniLM-L6-v2` truncates at ~256 tokens, so a dense 40-line
window may exceed the embedder's window and be partially truncated for
*retrieval*. Acceptable for the Phase 1 baseline and easy to tune down later;
the full chunk text is still what gets handed to Claude for answering.

**Note:** the IDE's integrated terminal was doubling/garbling stdout this
session, so test results were verified by writing to a file and reading it
back rather than trusting the on-screen terminal output.

## 2026-05-29 — Step 2: Ingestion

Added `repo_whisperer/ingest.py`: walks a target repo and returns `SourceFile`
records (relative path, abs path, content, line count, size) for step 3 to
chunk.

- **Extension allowlist** (`CODE_EXTENSIONS`): broad coverage of common
  languages plus config and docs (`.md`, `.rst`, `.txt`), since a tutor
  benefits from seeing build files and READMEs next to the code.
- **Directory pruning** (`SKIP_DIRS`): drops dependency/build/cache/VCS/editor
  dirs (`node_modules`, `dist`, `build`, `.git`, `__pycache__`, etc.) by
  mutating `os.walk`'s `dirnames` in place so they're never descended.
  Hidden dirs (dotfolders) are skipped too.
- **Noise guards:** skips files >1 MB (minified/generated blobs), files with
  NUL bytes (binaries), and anything that isn't valid UTF-8.
- Output is sorted by path for stable, reproducible ordering.
- Runnable standalone via `python -m repo_whisperer.ingest <path>`, which
  prints a per-file and total line/byte summary — lets the step be verified
  before the real CLI exists (step 6).

**Decision:** relative POSIX paths are stored as the citation key now, so
file/line references stay stable and platform-independent downstream.

## 2026-05-29 — Step 1: Project scaffold

Initialized the Repo Whisperer Phase 1 project.

- Created the package directory `repo_whisperer/` (modules for ingestion,
  chunking, embedding, retrieval, and the CLI will land in steps 2–6).
- `requirements.txt`: `anthropic`, `chromadb`, `sentence-transformers`,
  `python-dotenv`.
- `.gitignore`: excludes `.env` and the local `chroma_db/` vector store, since
  the DB is regenerable and the key is a secret.
- `.env.example` as the template for the Anthropic API key; the real `.env`
  stays untracked.
- `README.md` stub describing the three-phase vision and Phase 1 scope.

**Decisions worth recording:**

- Embeddings run locally via `sentence-transformers` so heavy iteration during
  Phase 1 costs nothing — only question answering calls the Anthropic API.
- The Chroma store lives in `chroma_db/` and is gitignored; it is treated as a
  rebuildable artifact, not source.
