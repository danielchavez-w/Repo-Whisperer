# Repo Whisperer

An AI coding tutor that points at any code repository, designs a learning path
through it, watches you work, and verifies your progress in real time.

It is built in three phases:

- **Phase 1 (current):** Text-only RAG Q&A over a local codebase.
- **Phase 2 (later):** Tutoring loop — lesson planning, Socratic explanation,
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

## Usage

> Coming as the CLI is built out (steps 2–6). Planned commands:
>
> ```bash
> repo-whisperer ingest <path-to-repo>
> repo-whisperer ask "how does X work?"
> ```
