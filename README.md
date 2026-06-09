# Repo Whisperer

An AI coding tutor that points at any code repository, lets you explore it, and
teaches the parts you get curious about — at your level, grounded in the real
code, remembering what you've covered across sessions.

It is built in three phases:

- **Phase 1 (DONE):** Text-only RAG Q&A over a local codebase.
- **Phase 2 (DONE):** Tutoring loop — show the code, answer the question, teach
  it Socratically at your level, run follow-ups and a light comprehension check,
  and nudge toward related threads you haven't explored yet.
- **Phase 3 (later):** Screen-aware verification using the vision API.

## What it does

A CLI tool that:

1. Ingests a local code repository.
2. Chunks and embeds the code into a local, persistent vector database.
3. **Answers** questions about the codebase — grounded in the actual retrieved
   code, with file/line citations — using the Anthropic API (`ask`).
4. **Teaches** the threads you get curious about: it shows the code, answers your
   question up front, then offers a guided walkthrough pitched at your level,
   takes in-lesson follow-ups, offers an optional comprehension check, and
   suggests related unexplored threads to wander into next (`explore`).

Embeddings run locally (`sentence-transformers`, `all-MiniLM-L6-v2`), so only the
answering and teaching calls hit the Anthropic API.

## Stack

- Python 3.11+
- Anthropic API (`claude-opus-4-8`) for answering and teaching
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
python -m repo_whisperer explore "how do the collectibles work?"
deactivate                         # when you're done
```

The first command after activating is slow (~10–20s) while the local embedding
model loads — that's normal.

## Usage

Index a repo once, then `ask` quick questions or `explore` to be taught.

```bash
# 1. Index a repository into the local vector store (chroma_db/)
python -m repo_whisperer ingest /path/to/some-repo

# 2a. Ask a grounded, cited question (Phase 1)
python -m repo_whisperer ask "how does X work?"

# 2b. Explore and be taught a thread (Phase 2)
python -m repo_whisperer explore "how do the collectibles work?"
```

The store holds **one repo at a time** — re-running `ingest` rebuilds the
collection from scratch, so switching repos or re-indexing after edits is just
another `ingest`.

### `ask` — a grounded answer

Retrieves the most relevant chunks and prints a single grounded answer that cites
the exact `path:start-end` line ranges it used; the retrieved chunks (with cosine
distances) are printed beneath so you can verify the grounding.

### `explore` — the tutoring loop

`explore` is the Phase 2 experience. For a query it:

1. **Shows the code** — the most relevant chunks for your query, with citations.
2. **Answers your question** directly in a couple of sentences, up front — so a
   "how does X work?" ask is always answered, not gated behind anything.
3. **Offers a deeper lesson.** Accept (a plain "yeah"/"sure" works, not just
   "y") and it teaches the thread in a teaching voice, grounded and cited. If you
   decline, it asks what you'd rather learn instead and teaches that — never a
   dead end.
4. **Teaches at your level.** Pass `--level beginner|intermediate|advanced`
   (default `beginner`); the same accurate, cited content is pitched with more or
   less assumed vocabulary. You can re-pitch mid-lesson by typing `level
   <tier>`.
5. **Takes follow-ups.** After the lesson, ask plain-English follow-ups ("what
   does X mean?", "simpler?") in the same context; press Enter or type `done` to
   move on. Then it offers an optional, low-pressure comprehension check.
6. **Remembers across sessions.** Your level and the threads you've covered
   persist in a local `learning_state.json` sidecar (keyed to the ingested repo).
   The level carries over between runs, and new lessons can cross-reference
   earlier ones. Re-ask a topic you've already covered and it recognizes it,
   offering a **refresher** that builds on what you saw before — no special
   command needed.
7. **Nudges what's next.** When a lesson wraps up, it suggests related threads
   you haven't explored yet (steering away from covered ground). Pick one by
   number, type your own topic, or press Enter to stop — always your call.

Options:

- `ingest`: `--db DIR` (store location), `--window N` / `--overlap M` (chunking),
  `--state FILE` (learning-state sidecar to bind to this repo).
- `ask`: `--db DIR`, `-k N` (number of chunks to retrieve, default 6).
- `explore`: `--db DIR`, `-k N`, `--level beginner|intermediate|advanced`,
  `--state FILE`.
