# Repo Whisperer

An AI coding tutor that points at any code repository, designs a learning path
through it, watches you work, and verifies your progress in real time.

It is built in three phases:

- **Phase 1 (DONE):** Text-only RAG Q&A over a local codebase.
- **Phase 2 (current):** Tutoring loop — lesson planning, Socratic explanation,
  comprehension checkpoints.
- **Phase 3 (later):** Screen-aware verification using the vision API.

## Phase 1 — what it does

A CLI tool that:

1. Ingests a local code repository.
2. Chunks and embeds the code into a local, persistent vector database.
3. Answers questions about the codebase in the terminal — grounded in the
   actual retrieved code, with file/line citations — using the Anthropic API.

Embeddings run locally (`sentence-transformers`, `all-MiniLM-L6-v2`), so only
question answering hits the Anthropic API.

## Stack

- Python 3.11+
- Anthropic API (`claude-opus-4-8`) for answering
- ChromaDB for local, persistent vector storage
- `sentence-transformers` (`all-MiniLM-L6-v2`) for local embeddings

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then add your ANTHROPIC_API_KEY
```

## Quickstart

```bash
cd /path/to/Repo-Whisperer
source .venv/bin/activate          # deps live in the venv — this step is required
python -m repo_whisperer ingest /path/to/some-repo
python -m repo_whisperer ask "how does X work?"
deactivate                         # when you're done
```

The first `ask` after activating is slow (~10–20s) while the local embedding
model loads — that's normal.

## Usage

Two commands: index a repo once, then ask as many questions as you like.

```bash
# 1. Index a repository into the local vector store (chroma_db/)
python -m repo_whisperer ingest /path/to/some-repo

# 2. Ask grounded questions about it
python -m repo_whisperer ask "how does X work?"
```

Each answer is grounded in the retrieved code and cites the exact
`path:start-end` line ranges it used; the retrieved chunks (with cosine
distances) are printed beneath the answer so you can verify the grounding.

The store holds **one repo at a time** — re-running `ingest` rebuilds the
collection from scratch, so switching repos or re-indexing after edits is just
another `ingest`.

Options:

- `ingest`: `--db DIR` (store location), `--window N` / `--overlap M` (chunking).
- `ask`: `--db DIR`, `-k N` (number of chunks to retrieve, default 6).
