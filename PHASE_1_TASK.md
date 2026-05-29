# Repo Whisperer — Phase 1 Task

## What this project is

Repo Whisperer is an AI coding tutor that points at any GitHub repo, designs a
learning path through it, watches the user's screen as they work, and verifies
their progress in real time. It is built in three phases:

- **Phase 1 (this task):** Text-only RAG Q&A over a local codebase.
- **Phase 2 (later):** Tutoring loop — lesson planning, Socratic explanation,
  comprehension checkpoints.
- **Phase 3 (later):** Screen-aware verification using the vision API.

**Do NOT build Phase 2 or Phase 3 in this task.** Keep Phase 1 tightly scoped.
Phase 1 is the foundation — if retrieval quality is solid, the later phases are
additions on top. If it's shaky, nothing above it works.

---

## Phase 1 goal

A CLI tool that:
1. Ingests a local code repository.
2. Chunks and embeds the code into a local, persistent vector database.
3. Answers questions about the codebase in the terminal — grounded in the
   actual retrieved code, with file/line citations — using the Anthropic API.

---

## Stack

- **Language:** Python 3.11+
- **Answering model:** Anthropic API, model string `claude-opus-4-8`
  - If this string errors, list available models / check docs.anthropic.com
    and use the current Opus string.
- **Vector DB:** ChromaDB — local, persistent, no cloud.
- **Embeddings:** `sentence-transformers` with `all-MiniLM-L6-v2`
  - Runs locally on the Mac. No API cost for embeddings — only question
    answering hits the Anthropic API. This keeps Phase 1 cheap during heavy
    iteration.
- **Chunking:** Simple line-window chunks with overlap. `tree-sitter` /
  AST-aware chunking is OUT OF SCOPE for Phase 1 — we go AST-aware in a later
  iteration once the baseline works.

---

## Build order

Build in this order and **stop after each step** for review and a commit.
Do not scaffold all six steps at once.

1. **Project scaffold**
   - Repo structure, `requirements.txt`, `.gitignore` (ignore the Chroma db
     directory and `.env`), `README.md` stub, and `DEVLOG.md` initialized with
     a dated first entry. `.env` for the Anthropic API key.

2. **Ingestion**
   - Walk a target repo path, filter to code files (sensible extension
     allowlist; skip `node_modules`, `.git`, build/dist dirs), read contents.

3. **Chunking**
   - Line-window chunks with overlap. Each chunk tagged with metadata:
     file path, start line, end line.

4. **Embedding + storage**
   - Embed chunks with sentence-transformers, store in persistent ChromaDB
     with the metadata.

5. **Retrieval + answer**
   - Take a question, embed it, retrieve top-k chunks, build a prompt that
     includes the retrieved code plus file/line citations, send to Claude,
     print a grounded answer that cites which files/lines it used.

6. **CLI wiring**
   - Simple commands: `ingest <path>` and `ask "<question>"`.

---

## Working principles (follow these)

- **Commit after every working step**, with descriptive commit messages
  written as full sentences explaining the *why*, not just the *what*.
- **One thing at a time.** Build a step, let the user run it, then move on.
- **Complete files, not diffs.** When delivering a file, give the entire file —
  never a "replace this function" snippet.
- **Log as you go.** After each step, add a short dated entry to `DEVLOG.md`
  describing what was built and any decision worth recording.

---

## Start here

Build **step 1 only** (project scaffold). Show the structure and files, then
stop and wait for review before moving to step 2.
