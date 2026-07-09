# Repo Whisperer

An AI coding tutor that points at any code repository, lets you explore it, and
teaches the parts you get curious about — at your level, grounded in the real
code, remembering what you've covered across sessions.

It is built in three phases:

- **Phase 1 (DONE):** Text-only RAG Q&A over a local codebase.
- **Phase 2 (DONE):** Tutoring loop — show the code, answer the question, teach
  it Socratically at your level, run follow-ups and a light comprehension check,
  and nudge toward related threads you haven't explored yet.
- **Phase 3 (in progress):** Screen-aware tutoring via the vision API. The tutor
  can now *see* your editor: highlight any code and it teaches that selection,
  grounding the lesson in both the screenshot and the rest of the ingested repo
  (`look`). And the teach → verify arc is closed: write your own version of a
  pattern in a practice file and `check` helps you get it right — assistance,
  never a grade. Next: closing the loop into the learning memory (mastery,
  refreshers).

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
5. **Looks at your screen** (Phase 3, macOS): takes one announced screenshot of
   your editor and teaches the code you've **highlighted** — grounded in both the
   screenshot (your exact selection) and related chunks pulled from the ingested
   repo, so the lesson can reach off-screen context with citations (`look`).
6. **Helps with code you wrote** (Phase 3): after learning a pattern, write your
   own version in a practice file and run `check <file>` — the tutor re-reads it
   from disk, works out what you were going for, and helps you close the gap at
   your level, comparing against how the real codebase does it (`check`).
7. **Answers side questions about the whole repo**: "which file should I learn
   first for my level?", "what is this project, big picture?" — answered from a
   map of every file in the ingested store, fitted to your level and steered by
   what you've already covered, ending in a ready-to-run `explore` query
   (`guide`).

Embeddings run locally (`sentence-transformers`, `all-MiniLM-L6-v2`), so only the
answering, teaching, and screen-reading calls hit the Anthropic API.

## Stack

- Python 3.11+
- Anthropic API (`claude-opus-4-8`) for answering, teaching, and reading the screen
- ChromaDB for local, persistent vector storage
- `sentence-transformers` (`all-MiniLM-L6-v2`) for local embeddings
- `look` is macOS-only — it uses `screencapture` plus `pyobjc-framework-Quartz`
  for window enumeration and the Screen Recording permission check

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

# 2c. Highlight code in your editor, then have it taught (Phase 3, macOS)
python -m repo_whisperer look

# 2d. Write your own version of a pattern, then get help with it (Phase 3)
python -m repo_whisperer check practice.js

# 2e. Ask a side question about the whole repo / your learning path
python -m repo_whisperer guide "which file should I learn first for my level?"
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

### `look` — teach what you highlight (Phase 3, macOS)

`look` gives the tutor sight. Open a file in your editor, **highlight** the code
you want explained, then run it:

```bash
python -m repo_whisperer look
```

1. **One announced screenshot.** It tells you what it's about to capture and takes
   exactly one frame of your **active editor window** — never continuous, never in
   the background. Nothing is captured unless you run the command. (First use
   prompts for macOS Screen Recording permission; grant it and reopen the app.)
2. **Teaches your highlighted selection** specifically — not a tour of the whole
   file — reading the colored selection straight off the screenshot.
3. **Grounds it in the whole repo.** It uses your selection to retrieve related
   code from the ingested store, so the lesson can reach **off-screen** context —
   "this gets consumed over in `player.js:120-138`," "the geometry is built at
   `collectibles.js:40`" — with `path:start-end` citations, the same way `explore`
   cites. Ingest the repo you're studying first (`ingest <path>`) so it has that
   context; with no repo ingested it falls back to teaching from the screen alone
   and tells you to ingest for full-project context.
4. **Same tutor.** Pitched at your saved level and connected to threads you've
   already covered, in the same voice as `explore`.

If nothing is clearly highlighted, it asks you to highlight the part you want
rather than dumping the whole file.

### `check` — get help with code you wrote (Phase 3)

`check` completes the teach → verify arc — assist-first. Learn a pattern (via
`explore` or `look`), open a practice file, try writing your own version, then:

```bash
python -m repo_whisperer check practice.js
```

1. **You name the file; disk is the truth.** It re-reads your practice file
   straight from disk — no screenshot, no capture, no guessing which file.
2. **It works out what you were going for** — assuming your most recent lesson,
   and announcing that assumption (with no lessons recorded it infers the
   pattern from your code itself).
3. **It assists, never grades.** No verdict, score, or pass/fail — it starts
   from what your attempt already gets right, then helps close the gap: what's
   missing and *why* it matters, pitched at your saved level. An honest
   structured judgment runs underneath to locate where your understanding is,
   but what you see is help, not a report card.
4. **Grounded in the real repo.** Your attempt is used to retrieve how the
   actual codebase solves the same thing, so the help compares against the real
   code with `path:start-end` citations. Practice-style simplifications
   (different names, hardcoded values) are respected — only differences that
   touch the core mechanism come up.

An empty or wrong file gets a friendly note, not a crash. Tweak the file and run
`check` again as many times as you like.

### `guide` — side questions about the whole repo

Every other tutoring command works one thread at a time; `guide` is for the
questions that need the **whole repo in view** — your learning path, the big
picture:

```bash
python -m repo_whisperer guide "which file should I learn first for my level?"
python -m repo_whisperer guide      # defaults to: where should I start?
```

The tutor answers from a **map of every file** in the ingested store (plus
excerpts retrieved for your question, so behavior claims stay cited), fitted to
your saved level — a beginner gets pointed at the small, self-contained entry
point, not the gnarliest core file — and steered by the threads you've already
covered. It names real files only, flags when it's inferring from a filename
rather than reading code, and ends with a ready-to-run `explore` query so the
advice turns directly into a lesson.

Options:

- `ingest`: `--db DIR` (store location), `--window N` / `--overlap M` (chunking),
  `--state FILE` (learning-state sidecar to bind to this repo).
- `ask`: `--db DIR`, `-k N` (number of chunks to retrieve, default 6).
- `explore`: `--db DIR`, `-k N`, `--level beginner|intermediate|advanced`,
  `--state FILE`.
- `look`: `--db DIR`, `-k N` (related chunks to retrieve), `--level
  beginner|intermediate|advanced`, `--state FILE`, `--screen` (capture the whole
  screen instead of the active window), `--window ID` (pin an exact window),
  `--exclude APP` (skip an app when picking the window), `--keep` (keep the
  screenshot file).
- `check`: `--db DIR`, `-k N` (chunks to retrieve for comparison), `--level
  beginner|intermediate|advanced`, `--state FILE`.
- `guide`: `--db DIR`, `-k N` (excerpts to retrieve for grounding), `--level
  beginner|intermediate|advanced`, `--state FILE`.
