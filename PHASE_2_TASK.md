# Repo Whisperer — Phase 2 Task

## What this project is

Repo Whisperer is an AI coding tutor that points at any repo, lets a learner
explore it, teaches the parts they get curious about, and (later) verifies their
progress by watching their screen. It is built in three phases:

- **Phase 1 (DONE):** Text-only RAG Q&A over a local codebase. The engine
  ingests a repo, chunks it, embeds it into ChromaDB, retrieves by meaning, and
  answers questions with grounded, cited answers. Commands: `ingest <path>` and
  `ask "<question>"`.
- **Phase 2 (THIS TASK):** The tutoring layer. Turn the question-answerer into a
  patient expert that teaches the code the learner gets curious about.
- **Phase 3 (LATER):** Screen-aware verification — the tutor watches the screen
  and checks work in real time.

**Do NOT build Phase 3 in this task. Do NOT build a GUI/web UI in this task.**
Phase 2 is built on top of the existing Phase 1 engine, reusing its retrieval.
Keep it tightly scoped, one step at a time.

---

## The Phase 2 design (read this before scoping any step)

The learning model is **learner-driven, explore-then-teach** — NOT a fixed
curriculum. The flow has two beats:

1. **Beat 1 — "show me":** The learner asks where something is (this is the
   existing Phase 1 retrieval). The tool shows the relevant code chunks.
2. **Beat 2 — "teach me":** Having SEEN the code, the learner gets curious about
   how it works. The tool's job is to **offer the doorway** — right after showing
   the chunks, it ends with a *specific, tempting* invitation to go deeper (e.g.
   "Want me to walk you through how `initRails` builds the two rail meshes?"),
   generated from the actual retrieved code, not a generic template. The learner
   decides: accept and get taught, or wave it off and keep exploring.

The bridge between *finding* and *learning* is the tool's job, not the learner's.
The learner should never need to know a magic phrase to trigger tutoring — they
look, the tool offers, they choose.

When the learner accepts, the tool **teaches Socratically** — explains the code
in context, in a teaching voice, then offers a *light, optional* comprehension
invitation (predict / modify / explain-back), respecting that the learner is a
curious adult, not a student being quizzed. The tool **remembers** what threads
the learner has covered so it can cross-reference ("this uses the same pattern
you saw in the audio system earlier") and **nudge** toward related unexplored
threads.

Tone target: a patient expert sitting next to you. Responds to pull, not push.
Specific, not robotic. Invites, doesn't lecture.

---

## Build order

Build in this order and **stop after each step** for review and a commit.
Do not scaffold all steps at once. Each step reuses the Phase 1 modules
(`ingest`, `chunk`, `store`, `answer`) rather than reimplementing them.

1. **Show-then-offer-to-teach loop**
   - Take a "where is X" query, reuse Phase 1 retrieval to show the relevant
     chunks, then generate a *specific* teaching offer derived from the actual
     retrieved code (name the function/concept, make it tempting). The learner
     can accept or decline. This step is just: show + smart offer + capture the
     accept/decline. No teaching content yet.

2. **Socratic teaching of an accepted thread**
   - When the learner accepts the offer, teach that thread: explain the retrieved
     code in context, in a teaching voice (not a flat answer dump). Stay grounded
     in the actual code, cite line ranges, and build understanding step by step.

3. **Light comprehension invitation**
   - After teaching, offer an *optional* way to engage: a predict-the-behavior
     question, a small modify-this-code challenge, or an explain-it-back prompt.
     The learner can take it or skip it. If taken, evaluate their response
     supportively and discuss. Keep it respectful of a curious adult — an
     invitation, never a pop quiz.

4. **Learning memory / state**
   - Persist what threads the learner has covered and where they engaged or
     struggled (a JSON file or small SQLite table — keep it simple). Use it so
     the tutor can cross-reference earlier threads when teaching new ones.

5. **"What's next" nudges**
   - Based on what was just learned and what's still unexplored, suggest related
     threads the learner might be curious about next. Suggestions only — the
     learner always decides. Pull, with gentle nudges.

**Out of scope for Phase 2:** the browse-and-point UI that lets a learner
visually spot a feature to get curious about, and any screen-watching. Those
come later (UI step or Phase 3). Phase 2 proves the *teaching capability* works
in a plain interface first.

---

## Working principles (follow these)

- **Reuse Phase 1, don't rebuild it.** Retrieval, embedding, and storage already
  exist and are tested. Phase 2 builds on top.
- **Commit after every working step**, with descriptive commit messages written
  as full sentences explaining the *why*.
- **One thing at a time.** Build a step, let the user run and verify it, then
  move on. Stop after each step.
- **Complete files, not diffs.** When delivering a file, give the entire file.
- **Test before commit.** Each step gets verified by the user (run it against a
  real repo — e.g. Swerve or Astra) before it is committed.
- **Log as you go.** After each step, add a short dated entry to `DEVLOG.md`
  describing what was built and any decision worth recording.
- The store holds one repo at a time (Phase 1 behavior) — that constraint carries
  forward; no need to change it in Phase 2.

---

## Start here

Build **step 1 only** (show-then-offer-to-teach). Show the structure and the
files, then stop and wait for review before moving to step 2.
