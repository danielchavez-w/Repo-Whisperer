# Development Log

## 2026-06-06 — Phase 2, Step 2: Socratic teaching of an accepted thread

When the learner accepts the `explore` offer, we now actually teach the thread
instead of printing a placeholder. Added `teach_thread(query, hits, offer,
model)` to `tutor.py`: one LLM call (reusing `_client` + `build_context`, model =
Phase 1 `ANSWER_MODEL`) that explains how the retrieved code works, in a teaching
voice, building understanding step by step and citing `path:start-end` keys. The
accepted offer is passed in so the teaching stays on the specific thread.

- `explore`'s accept branch now calls `teach_thread` and prints it; the decline
  branch is unchanged.
- `ExploreResult` gained a `teaching: str | None` field, so an accepted+taught
  interaction carries its teaching for later steps (memory in Step 4).
- New constants: `MAX_TEACH_TOKENS = 1536` (teaching is longer than an offer or a
  flat answer, but still bounded) and `TEACH_SYSTEM_PROMPT`.

Scope: the teaching prompt explicitly does NOT quiz or set exercises — the light,
optional comprehension invitation is Step 3, kept separate on purpose. Verified
imports, the new `teaching` field, and that the CLI stays wired; the live
teaching call is verified by running `explore` against a real repo and accepting.

## 2026-06-06 — Phase 2, Step 1: show-then-offer-to-teach

Started Phase 2 (the tutoring layer), built on top of the Phase 1 engine. Added
`repo_whisperer/tutor.py` and a new `explore` CLI command.

`explore "<where is X>"` runs the first tutoring beat:
- **Show me** — reuses Phase 1 retrieval (`answer.retrieve`) to pull and print
  the most relevant chunks (actual code + `path:start-end` citations).
- **Offer the doorway** — one LLM call (`generate_offer`, reusing `_client` and
  `build_context`) produces a single specific, tempting invitation derived from
  the retrieved code, naming a real identifier (e.g. "Want me to walk you
  through how `initRails` builds the two rail meshes?").
- **Capture the choice** — prompts `Learn this? [y/N]` and records accept/decline
  in an `ExploreResult` (query, hits, offer, accepted) for Step 2 to pick up.

Deliberately scoped: this step shows + offers + captures only. The actual
Socratic teaching of an accepted thread is Step 2 — no teaching content yet.

Decisions worth recording:
- Reused Phase 1 throughout (retrieval, context building, Anthropic client); no
  re-implementation. The offer model is the Phase 1 answering model.
- `explore` accepts `decide=False` and an injectable `input_fn` so the
  show+offer path is callable non-interactively (and testable); `EOFError` on
  input is treated as "not now" rather than crashing.
- Verified: CLI registers `explore`, module imports clean, and the show/retrieval
  path renders correctly against the current store. Offer generation (the API
  call) to be verified by running against a real repo before commit.

## 2026-06-03 — Session wrap-up (Phase 1 shipped)

Phase 1 is complete and fully pushed to `origin/main` (through `4eea3b2`). The
whole pipeline works behind two commands:

```bash
python -m repo_whisperer ingest <path-to-repo>
python -m repo_whisperer ask "<question>"
```

**What got done this session:**
- Fixed a broken `.venv` (Intel-Mac PyTorch ceiling) and pinned the stack
  (`375e70e`) so it can't drift again.
- Step 4 — embedding + ChromaDB storage, `store.py` (`ab3b380`).
- Step 5 — retrieval + grounded, cited answers, `answer.py` (`2ec8fb2`).
- Step 6 — `ingest`/`ask` CLI, `cli.py` + `__main__.py` (`4eea3b2`).
- Wired in the Anthropic key via `.env` and verified `claude-opus-4-8` reachable.
- Verified retrieval quality on the Swerve repo and on this repo itself.

**To resume later:** `cd` in, `source .venv/bin/activate`, then `ingest` the repo
you want and `ask` away. The store holds one repo at a time. See the README
Quickstart.

**Possible next directions (all optional, none started):** automated tests; a
relevance/distance threshold so weak chunks are dropped from context; supporting
multiple repos/collections at once; then Phase 2 (tutoring loop) — still out of
scope until explicitly picked up.

## 2026-06-02 — Step 6: CLI wiring (Phase 1 complete)

Added `repo_whisperer/cli.py` and `repo_whisperer/__main__.py`: a single
`python -m repo_whisperer` entry point with the two commands the spec asks for,
dispatching to the functions built in earlier steps.

- `ingest <path>` → `store.ingest_repo` (walk → chunk → embed → store, rebuilding
  the collection). Supports `--db`, `--window`, `--overlap`.
- `ask "<question>"` → `answer.answer_question` (retrieve top-k → grounded, cited
  answer). Supports `--db` and `-k`. Prints the answer followed by the retrieved
  chunks and their cosine distances.
- argparse subcommands; a required subcommand means a bare invocation prints
  usage and exits 2. Path/key/empty-store errors surface as friendly one-liners
  (exit 1) rather than tracebacks.
- Updated `README.md` Usage from a "coming soon" stub to the real commands.

**Verified** end to end: `--help` lists both commands; `ingest .` indexed this
repo (11 files / 42 chunks); `ask` answered a question about this codebase's own
re-ingest idempotency, correctly citing `store.py` line ranges and quoting the
actual code — a clean self-referential check that ingest→retrieve→answer works
through the unified CLI. Bare invocation errors with usage as expected.

**Phase 1 is complete.** Full pipeline: ingest → chunk → embed → ChromaDB →
retrieve → grounded cited answer, driven by `ingest`/`ask`. Phases 2 and 3
remain out of scope.

## 2026-06-02 — Step 5: Retrieval + grounded answer

Added `repo_whisperer/answer.py`: the payoff step — turn a question into a
code-grounded, cited answer. Embed the question with the same `all-MiniLM-L6-v2`
model used for indexing, query the ChromaDB collection for the top-k nearest
chunks, build a prompt that labels each excerpt with its `path:start-end`
citation key, and ask `claude-opus-4-8` to answer using only those excerpts.

- **Grounding contract:** a system prompt restricts the model to the provided
  excerpts, requires inline `path:start-end` citations, and tells it to say so
  (rather than guess) when the excerpts don't cover the question.
- **Retrieval:** `retrieve()` returns `Hit` records (id, path, lines, text,
  cosine distance). Default k=6; `n_results` is clamped to the collection size.
- **Key handling:** the Anthropic client loads `ANTHROPIC_API_KEY` from `.env`
  lazily and errors clearly if it's missing; a missing/empty collection gives an
  actionable "run store first" message instead of a stack trace.
- The CLI prints the answer, then the retrieved chunks with distances so the
  grounding is visible even for citations the model didn't surface.

**Verified** against the Swerve store. "where are the rails made?" → a correct
answer that distinguishes the *visual* rails (`js/rails.js`, `initRails` /
`updateRails`) from the *physics/collision* rails (`js/track.js:151-190`), with
every cited line range present in the retrieved set (no hallucinated
citations). An out-of-scope question ("how does login work?") correctly returns
"there is no user authentication … in the provided excerpts" rather than
inventing one — the RAG grounding holding under a negative case.

**Next — Step 6:** CLI wiring so `ingest <path>` and `ask "<question>"` are
single entry points over `store.ingest_repo` and `answer.answer_question`.

## 2026-06-02 — Step 4: Embedding + ChromaDB storage

Added `repo_whisperer/store.py`: embeds each chunk locally and persists it to a
local, persistent ChromaDB collection — the searchable index step 5 will query.

- **Embedding:** `sentence-transformers` `all-MiniLM-L6-v2` (384-dim, CPU, no API
  cost), loaded once via a cached lazy loader and imported lazily so the module
  doesn't drag in torch unless embedding actually happens. Vectors are
  L2-normalized and the collection uses cosine space, so they pair correctly.
- **Storage:** one collection named `repo` in `chroma_db/` (gitignored). Each
  chunk's stable id (`"<path>:<start>-<end>"`) is the Chroma document id; the
  chunk text is the document; and `path`/`start_line`/`end_line` are stored as
  metadata for step-5 citations. Added in batches of 256 to bound memory.
- **Idempotent re-ingestion:** the collection is dropped and rebuilt on every
  run, so a file that shrank, moved, or was deleted leaves no stale chunks —
  chosen over upsert-by-id because a clean rebuild can't orphan anything.
- Standalone runner ingests a repo and reports the final collection size; warns
  if the stored count ever diverges from the chunk count (id collision guard).

**Verified:** this repo → 8 files / 29 chunks, all 29 stored; a second run stays
at 29 (idempotent, no duplicate-id crash); a fresh process reads 29 back from
disk and a query for "how are files chunked into windows?" returns `chunk.py`
and the task doc's chunking section as top hits. External `Swerve` repo →
18 files / 109 chunks (matches the earlier chunking-step count). `chroma_db/`
stays untracked by git.

**Environment fix (prerequisite):** the `.venv` had drifted to a broken state —
`sentence-transformers` wouldn't import. Root cause is the Intel/x86_64 Mac:
PyTorch's last macOS-x86_64 wheel is **2.2.2**, but unpinned installs had pulled
`transformers 5.x` (needs torch ≥ 2.4) and `numpy 2.x` (breaks torch 2.2.2's
compiled extension). Pinned the stack back down to fit the torch ceiling
(`numpy<2`, `torch==2.2.2`, `transformers>=4.40,<5`, `sentence-transformers
>=3.0,<4`) and documented the reason in `requirements.txt` so it can't drift
again.

## 2026-05-29 — Session wrap-up & next steps

**Done so far (all committed + pushed to `origin/main`):**
- Step 1 — Scaffold (`21c0f3f`)
- Step 2 — Ingestion, `repo_whisperer/ingest.py` (`8923c36`)
- Step 3 — Chunking, `repo_whisperer/chunk.py` (`eec2a52`)

Pipeline verified end-to-end through chunking on this repo and on the external
`/Users/dan/Desktop/Swerve` test repo (18 files → 109 chunks).

**Next session — Step 4: Embedding + ChromaDB storage**
- Add `repo_whisperer/embed.py` (or `store.py`): embed each `Chunk.text` with
  `sentence-transformers` `all-MiniLM-L6-v2` (model loads locally, no API cost).
- Persist to a local ChromaDB collection in `chroma_db/` (already gitignored).
  Use each chunk's `id` (`"<path>:<start>-<end>"`) as the Chroma document id and
  store `path`/`start_line`/`end_line` as metadata for citations.
- Make re-ingestion idempotent (upsert by id, or clear+rebuild the collection)
  so re-running on the same repo doesn't duplicate chunks.
- Provide a standalone runner to embed a repo and report collection size.

**Then:** Step 5 (retrieve top-k + grounded Claude answer with citations,
`claude-opus-4-8`), Step 6 (CLI: `ingest <path>`, `ask "<question>"`).

**Reminders for next session:**
- Run everything via `.venv/bin/python` (deps already installed there).
- Windsurf's integrated terminal was garbling stdout — verify command output by
  writing to a temp file and reading it back if it recurs.
- Optional cleanup: reconcile the cosmetic line-count off-by-one between
  `ingest` (`\n`-count+1) and `chunk` (`splitlines()`) for files ending in a
  trailing newline. Citations are unaffected.

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
