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

When the learner accepts, the tool **teaches** — explains the code in context,
in a teaching voice, then offers a *light, optional* comprehension invitation,
respecting that the learner is a curious adult, not a student being quizzed. The
tool **remembers** what threads the learner has covered so it can cross-reference
("this uses the same pattern you saw in the audio system earlier") and **nudge**
toward related unexplored threads.

Tone target: a patient expert sitting next to you. Responds to pull, not push.
Specific, not robotic. Invites, doesn't lecture.

### CRITICAL design insight — teach at the LEARNER'S altitude

The learner this tool is built for is a **curious learner, not a senior
developer**. They build systems in plain English and know coding *concepts* at a
basic level — functions, strings, values, conditions, floats, arrays — but they
do NOT fluently know intermediate jargon (e.g. "factory function", "buffer
geometry", "module-level constant", "descriptor object") without it being
explained.

During testing, a lesson taught the content *correctly* but pitched it at a
mid-level developer's vocabulary, and the target learner could not follow it.
**The content was right; the altitude was wrong.** This is the single most
important thing to fix in Phase 2: the tutor must meet the learner where they
are.

Two consequences, both built in Step 3 below:
1. **A depth/level setting** so the learner tells the tutor what altitude to
   teach at, and the SAME accurate, grounded content gets explained with more or
   less assumed vocabulary. At the lowest level, jargon terms must be defined in
   plain English the first time they appear ("a factory function is just a
   function whose job is to build something and hand it back — like a little
   assembly station").
2. **Follow-up questions inside a lesson** so the learner can ask "what does X
   mean?", "explain that simpler", or "give me an example" right there, in the
   same context, without losing the thread. A patient expert lets you ask back.

Teaching at the right altitude is NOT dumbing down. The facts, the citations, and
the grounding contract stay identical. Only the assumed vocabulary changes.

---

## Build order

Build in this order and **stop after each step** for review and a commit.
Do not scaffold all steps at once. Each step reuses the Phase 1 modules
(`ingest`, `chunk`, `store`, `answer`) rather than reimplementing them.

1. **Show-then-offer-to-teach loop — DONE (committed).**
   - Takes a "where is X" query, reuses Phase 1 retrieval to show the relevant
     chunks, then generates a *specific* teaching offer derived from the actual
     retrieved code and captures the learner's accept/decline. Verified working.

2. **Socratic teaching of an accepted thread — DONE (committed).**
   - When the learner accepts, the tool teaches that thread: explains the
     retrieved code in context, builds understanding in layers, lands the answer,
     and honestly admits where the retrieved code runs out instead of inventing.
     Verified working. **Known issue addressed in Step 3:** the lesson assumes too
     much vocabulary for the target learner.

3. **Learner calibration (depth dial + in-lesson follow-ups + light
   comprehension invitation) — BUILD THIS NEXT.**

   This step makes the teaching from Step 2 actually land for the target learner.
   Three pieces, all building on the existing teaching call:

   a. **Depth / level setting.** Let the learner specify their level — e.g. a
      `--level` flag or a quick one-time prompt — with at least three tiers such
      as `beginner` (knows basic concepts, needs jargon defined in plain
      English), `intermediate` (comfortable with common terms), and `advanced`
      (wants to go straight to architecture, minimal hand-holding). The level
      feeds the teaching prompt so the SAME grounded content is pitched at the
      right altitude. At `beginner`, any non-basic term must be defined in plain
      English the first time it is used. Default to `beginner` (the target user),
      and make the current level visible/changeable.

   b. **In-lesson follow-up questions.** After a lesson prints, do NOT just drop
      back to the shell. Let the learner ask plain-English follow-ups in the same
      context — "what does X mean?", "explain that simpler", "give me an example"
      — answered at their chosen level, grounded in the same retrieved chunks,
      until they're satisfied. A simple read-loop is fine. The learner types
      something to exit (e.g. blank line or "done").

   c. **Light comprehension invitation.** Once the learner is done with
      follow-ups, offer an *optional* way to engage: a predict-the-behavior
      question, a small modify-this-code challenge, or an explain-it-back prompt
      — pitched at their level. They can take it or skip it. If taken, evaluate
      supportively and discuss. An invitation, never a pop quiz.

   Verify by teaching the SAME thread (e.g. the orb point system) at `beginner`
   and confirming the target learner can actually follow it, then trying a
   follow-up question and the comprehension invitation.

4. **Learning memory / state.**
   - Persist what threads the learner has covered, their chosen level, and where
     they engaged or struggled (a JSON file or small SQLite table — keep it
     simple). Use it so the tutor can cross-reference earlier threads when
     teaching new ones, and so the level persists across sessions.

5. **"What's next" nudges.**
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

Steps 1 and 2 are built, verified, and committed. Build **step 3 only** (learner
calibration: depth dial + in-lesson follow-ups + light comprehension invitation).
Show the files, then stop and wait for review before moving to step 4.
