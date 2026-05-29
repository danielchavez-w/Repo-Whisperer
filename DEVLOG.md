# Development Log

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
