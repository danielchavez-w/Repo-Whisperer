# Development Log

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
