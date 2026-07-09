# Development Log

## 2026-07-09 — Step 4: the `check` command — assist-oriented verification, disk-only

Phase 3's arc completes: everything before this teaches; `check` looks at code
the **learner** wrote and helps them get it right. The reframe that shaped the
whole step: **assist, not grade.** `check` is the tutor helping with what the
learner was trying to do — "you've got the structure, here's what's missing and
why" — never a proctor with a report card. The judge schema still runs
underneath (that's how the tutor locates where the understanding is), but its
verdict is a **private signal**, translated into patient gap-closing help; the
learner never sees a verdict, score, or pass/fail.

The v1 flow, lean and explicit:

- **The learner names the file:** `check <practice-file>`. No inference, no
  guessing.
- **Disk-for-truth:** the file is re-read straight from disk. No capture, no
  vision call anywhere in `check` — naming the file already carries the intent
  signal a screenshot would have provided.
- **What they were going for** defaults to the most recently taught thread in
  `learning_state.json` (by timestamp — re-teaching refreshes a record in
  place, so list order alone isn't recency), and the assumption is announced.
  With no lessons recorded, the tutor infers the pattern from the code itself.
- **Grounded in the real repo:** the attempt's own text is the retrieval query
  through the existing `answer.retrieve(...)`, so the help compares against how
  the actual codebase solves the same thing, cited `path:start-end` like
  `explore`. Same graceful fallback as `look` when nothing is ingested — the
  attempt alone is still judgeable evidence, just without comparisons.
- **The Step 1 judge finally lands its second use:** new
  `judge.judge_attempt(goal, attempt, hits)` reuses the SAME `Verdict` schema
  and `VERDICT_SCHEMA_NOTE` — no fork — with a new rubric
  (`ATTEMPT_SYSTEM_PROMPT`) that judges the MECHANISM, not the polish: a
  practice attempt legitimately simplifies (different names, hardcoded values,
  no error handling), and none of that lowers the verdict when the core
  mechanism is right. Unlike `judge_relevance`, empty hits don't short-circuit.
- **Levels reused exactly:** the saved level from `learning_state.json` pitches
  the assistance (`assist_with_attempt` composes `CHECK_ASSIST_SYSTEM_PROMPT` +
  the same `_level_block`), overridable with `--level` like everywhere else.
- **Wrong files never crash:** missing path, directory, unreadable/binary, and
  empty files each get a plain human message (`_read_attempt`); an empty file
  gets a friendly "write your attempt, then run `check` again", not an error
  trace. Oversized files are truncated with a note (`MAX_ATTEMPT_CHARS`).

Verified live against the ingested Swerve repo: a simplified collectible-spawner
attempt (correct probabilistic gate, missing the dot-trail loop and safe-lane
placement) got assistance that opened with what the attempt got right, closed
the two real gaps with `js/collectibles.js:170-190` / `main.js:145-152`
citations, explicitly declined to demand `getSafeLane` in a practice file, and
never used grade language.

**Noted for later (out of v1 scope, per the step spec):**

- **Screenshot-based intent** could return as an enhancement — capture to see
  *which* file/editor the learner is working in — but naming the file replaced
  it cleanly for v1.
- **Practice-file inference** (finding the attempt without being told) is a
  possible later convenience.
- **Memory writing is still the Step 5 thread:** `check` reads the level and
  the latest thread but records nothing — routing shaky verdicts into
  `find_revisit`/refresher machinery and the `mastery` field is next.

## 2026-06-19 — Make `look` interactive: follow-up Q&A on the highlighted selection

`look` was one-shot — capture the highlight, teach it, stop. Now the lesson opens
into a **back-and-forth**, the way `explore`'s in-lesson follow-ups already work,
so the learner can question the selection until it genuinely clicks. This lands
**before** Step 4 on purpose: `check` (write code from a thread, then verify it)
only means something if the learner understood the thread well enough to act on
it, and a single explanation often isn't deep enough. So this is a dependency of
Step 4, not a detour.

The design — **Option A, drill into one highlight:**

- Highlight once. `look` captures and teaches it exactly as before (screenshot
  for the exact selection + repo chunks for surrounding context).
- Then a follow-up loop (`_run_screen_followups`): ask questions about that
  selection and the tutor answers them in the same context, like `explore`'s
  follow-ups but anchored to a screen selection instead of a text query.
- **Every follow-up is answered against the SAME evidence already in hand** — the
  original screenshot of the selection AND the repo chunks pulled at capture time.
  **No re-capture per question.** Highlight once, converse about that selection
  with the context already loaded; want a new selection, run `look` again.
- `level <tier>` re-teaches the same selection from the same screenshot at a new
  altitude (reuses `teach_from_screen`); blank line / `done` / EOF / Ctrl-C all
  exit cleanly.

The one structural correctness point: **the loop runs INSIDE the
`with capture.captured_frame(...)` block.** `capture.image_block` re-reads the PNG
from disk on every call and `captured_frame` deletes it on exit, so to keep
answering against the same frame with no re-capture, the file has to stay alive
until the learner is done. `look` still calls `captured_frame` exactly once.

Reuse, don't rebuild: the new `answer_screen_followup` is the screen twin of
`answer_followup` — it composes the same image-block + repo `hits` +
`lesson`-so-far message and reuses `_ask_model`; the new
`SCREEN_FOLLOWUP_SYSTEM_PROMPT` mirrors `FOLLOWUP_SYSTEM_PROMPT` but keeps the
answer scoped to the highlighted selection (deeper on it or its repo
connections, never a tour of the file).

Verified offline (model calls stubbed): follow-ups route at the current level, a
`level` switch re-teaches once and subsequent follow-ups adopt the new level, a
bad level name is rejected without re-teaching, and blank / `done` / EOF / Ctrl-C
each exit gracefully. Then verified live on Swerve — highlighted a function, got
the lesson, asked 2–3 follow-ups that stayed on the selection and pulled in
off-screen repo context, and exited cleanly with no second capture.

Still out of scope and untouched, as specified: the judge / verdict schema and
any verification (Step 4), and memory writing — `look` still records nothing to
`learning_state.json` (it only reads the level); recording remains the logged
Step 5 thread.

## 2026-06-15 — Phase 3, Step 3.5: make `look` repo-aware (fuse screen + retrieval)

`look` no longer teaches from the screen alone. After it captures the highlighted
selection, it reads that selection back as text, uses it as a **retrieval query
into the ingested store**, and teaches the highlight grounded in **both** the
screenshot (the exact selection, visually) and the retrieved off-screen chunks
(the surrounding repo context the screen can't show) — citing `path:start-end`
the way `explore` does.

Verified live on Swerve: ingested Swerve, highlighted the `boostGeo` IIFE block
at the top of `collectibles.js`, and the tutor taught that block while correctly
citing its off-screen usage in `createBoost` (≈ lines 91–130) — pulled from
retrieval, not visible on screen. That off-screen reference is the tell that 3.5
is actually wired in and not just re-running Step 3.

How it works:

- **Selection → query.** A focused vision read (`transcribe_selection`)
  transcribes ONLY the highlighted code and returns it as plain text; that text is
  the retrieval query. No highlight → it returns the `NO_SELECTION` sentinel, so
  retrieval is skipped and the teaching prompt asks the learner to highlight
  something (unchanged Step 3 behavior).
- **Reuse `explore`'s retrieval, don't rebuild.** The query goes straight through
  the existing `answer.retrieve(...)` against the ingested ChromaDB store.
- **Teach from both sources.** `teach_from_screen` now also takes the retrieved
  `hits`: the screenshot is sent as the image (exact selection), the chunks are
  appended as labeled `path:start-end` context, and the prompt is told to reach
  into them for off-screen detail (callers/callees, where types/geometry live) and
  cite the keys — while staying scoped to the selection, not touring the file.
- **Screen for the exact selection, ingested repo for the surrounding truth.**

Graceful fallback (the requirement): `look` now depends on an ingested repo, but
**never crashes without one**. If the store is missing or empty,
`_related_chunks` catches it, teaching falls back to screen-only (Step 3
behavior), and the learner sees a clear note to run `ingest <path>` for
full-project context. A status line (`Pulled N related chunks…`) shows when
retrieval did land. New `--db` / `-k` flags on the `look` command mirror
`explore`/`ask`.

Still out of scope and untouched: the judge / verdict schema and any
verification — that remains Step 4.

**Carried-forward design question (flagged, not solved):** there is one ingested
repo at a time (the last one ingested), so `look` trusts that the on-screen file
belongs to it — "ingest the repo you're studying, then `look` at it." That
contract is fine for now, but `look` should eventually detect which repo the
on-screen file actually belongs to and pull from the matching store, so the
screen (exact selection) and the knowledge (retrieved context) can't silently
mismatch — e.g. highlighting in repo A while the store holds repo B would teach
with confidently wrong off-screen citations. Worth revisiting once multi-repo
stores exist.


## 2026-06-15 — Phase 3, Step 3: screen-as-context teaching, scoped by highlight

The tutor gains **sight**. New opt-in `look` command: it takes ONE announced
screenshot of the active editor window and teaches the code you've
**highlighted**, using the rest of the visible file as context — at your saved
level, in the same Socratic voice. Verified live against a real file open in
Cursor: highlight a function, run `look`, and it teaches that selection (using
file context) and not the whole file.

**Reframe from the original Phase 3 doc:** Step 3 is NOT verification/quizzing.
It's the tutor gaining sight so it can teach the specific part the learner points
at, grounded in on-screen context — "a patient expert looking over my shoulder at
the page I'm pointing to," never a proctor. The checking/verification flavor
(and the judge schema) stays reserved for Step 4's `check` command; this step
does not touch it.

What it does:

- **Opt-in, announced, one-shot.** Nothing is captured unless you run `look`; it
  announces the single frame before taking it. Default text-only `explore` is
  completely unchanged.
- **Reuses the Step 2 capture module**, doesn't rebuild it: active editor window
  by default (last-non-tutor-window rule, so the tutor's own terminal is skipped
  and the editor behind it is grabbed), `--screen` whole-screen fallback,
  `--window <id>` to pin, `--exclude APP` for edge cases.
- **Teaches the highlighted selection specifically.** The screenshot shows the
  whole visible file AND the colored selection background (Step 2 proved Opus
  reads the screen faithfully, so the highlight is simply visible in the image).
  The prompt scopes the lesson to the selection and uses the rest of the file as
  context — what the selection calls, what calls it, where it sits — and
  explicitly does NOT tour the whole file. If nothing is clearly highlighted it
  asks the learner to highlight the part they want rather than dumping the file.
- **Same tutor, same calibration.** Pitched at the learner's saved level
  (`_level_block`) and handed the prior-threads brief for cross-references, like
  the text path. Reads the level from learning state but writes nothing — a
  screenshot lesson has no chunk citations to record as a thread.

Plumbing:

- `capture.image_block(frame)` factored out of `read_frame` so the tutor composes
  its own vision message around a frame without duplicating the base64 encoding;
  `capture.describe_frame` made public for the capture announcement.
- `tutor._ask_model` generalized to accept an image-content list (not just text),
  so the existing truncation/continuation handling carries over to vision calls.
- New `tutor.SCREEN_TEACH_SYSTEM_PROMPT` + `teach_from_screen(frame, level,
  prior)` and the `look(...)` orchestrator (announce → capture → teach), wired to
  a thin `look` subcommand in `cli.py`.


## 2026-06-15 — Phase 3, Step 2: capture plumbing (de-risking spike)

Proved the **screenshot → vision** pipeline in isolation before any
learner-facing verification rests on it. New `repo_whisperer/capture.py`,
standalone and untouching `explore`/`tutor.py`/the judge schema. Step 3
(comprehension verification) will reuse this plumbing, feeding a captured frame
to the existing `Verdict` judge with image evidence.

What it does:

- **On-demand single frame**, never continuous or periodic — one explicit
  `screencapture` call (macOS), absolute path `/usr/sbin/screencapture` so a
  tampered `PATH` can't shadow it; `-x` silent, `-t png`.
- **Active editor window by default**, not the whole desktop. Windows are
  enumerated front-to-back via CoreGraphics (`CGWindowListCopyWindowInfo`,
  layer-0 + size filter), and the target is grabbed non-interactively by id
  (`-l<id> -o`). `--screen` is the whole-screen fallback; `--window <id>` pins
  an exact window; `--list` prints capturable windows with ids.
- **Alt-tab edge case** ("don't capture the tutor looking at itself"): the
  "last non-tutor window" rule walks front-to-back and skips dedicated terminal
  emulators (`TERMINAL_OWNERS`), so when you tab to the tutor's terminal to
  trigger a capture it grabs the editor behind it. Editor apps (VS Code, Cursor,
  Windsurf) are deliberately NOT excluded by default — they commonly host an
  integrated terminal AND show the code we want — so `--exclude APP` is offered
  for the cases that need it.
- **Permission gate, human message not stack trace:** `CGPreflightScreenCapture
  Access` preflights; if absent we fire the one-time `CGRequestScreenCapture
  Access` prompt, then re-check, and on a hard no raise `PermissionDeniedError`
  carrying `PERMISSION_MESSAGE` (System Settings → Privacy & Security → Screen
  Recording, then reopen the app). A silently-blocked grab is also caught by an
  empty/too-small file check and re-routed to the same message.
- **No artifacts left behind:** frames go to a `tempfile` PNG; `captured_frame()`
  is a context manager that unlinks it on exit (`--keep`/`--no-vision` retain it
  for inspection), and a failed capture cleans up its temp file too.

**The de-risking test** (the point of the step): the standalone runner
`python -m repo_whisperer.capture` captures one frame and sends it to Opus 4.8
as an image block (`read_frame`), asking it to transcribe the code/text it can
read — confirming the model reads code legibly off real pixels before
verification is built on top. Vision model reuses the Phase 1 `ANSWER_MODEL` and
the shared `answer._client`.

New dependency (macOS only): `pyobjc-framework-Quartz` for window enumeration +
the permission preflight.


## 2026-06-13 — Design thread (carried forward): a "practice space"

Captured mid-Phase-3, not scoped — recording the idea so it isn't lost. Right
now the tutor and learner go back and forth in plain English about code chunks
(explain, ask, refresh), which works well. The idea is to add a place where the
learner can *do* something with what they just learned, not only talk about it.
Strictly opt-in — offered, never pushed, the same pull principle as everything
else.

**Two distinct flavors — keep them separate; they are not the same feature:**

1. **Check my work** — the learner goes off, edits or writes real code based on a
   lesson, then asks the tutor to verify it. This is already scoped as Phase 3's
   `check` command (application verification: screen-for-intent / disk-for-truth →
   judge correctness against the taught thread).
2. **Give me practice** — the tutor *generates* a small exercise in a separate
   sandbox that didn't exist before, so the learner can apply the concept without
   needing a real task in the repo. This is the new, unscoped idea. It's the
   sibling of `check`: one *verifies* practice the learner brought, the other
   *offers* practice the tutor created.

**Why they pair:** both lean on the same honest-judge primitive (evidence + rubric
→ verdict). A generated exercise is just another thing the judge can later assess,
so "offer practice" and "check practice" could share a spine the way
relevance-judging and verification already do.

**Open questions for when this gets picked up:**

- Where does the sandbox live? A separate file / scratch space vs. an in-terminal
  prompt-and-respond drill.
- Does the tutor generate the exercise from the taught thread *plus the actual
  repo code*, so practice is grounded in this codebase rather than generic
  textbook problems?
- Does a completed/checked exercise feed `learning_state.json` as a stronger
  signal than "was taught" — i.e. "has practiced" (overlaps with the planned
  mastery field)?
- Ship alongside `check`, or as its own thing after verification lands?

**Status:** captured idea, not scoped. Decide its shape after Phase 3's core
verification (Steps 2–5) is built.

## 2026-06-13 — Phase 3, Step 1: the honest-judge primitive (model-judged relevance)

Starting Phase 3 (screen-aware verification). Design was settled in a session up
front: verify both comprehension (A) and application (B), staged in one phase,
sharing one judge; on-demand announced snapshots (active-window, whole-screen
fallback, capturing the last *non-tutor* window so an alt-tab to the tutor
doesn't grab the wrong thing); application verification lives in a new top-level
`check` command that defaults to the most-recently-taught thread. And: build the
honest-judge primitive **text-first** and verify it standalone before any vision
work — that's this step.

**New module `repo_whisperer/judge.py`.** An evidence-agnostic judge that returns
a fixed `Verdict` schema, deliberately stable so Phase 3 verification can reuse it
unchanged with image evidence instead of text:

```
verdict:          answers | partial | doesnt_answer | cant_tell
confidence:       high | medium | low
reason:           1-2 sentences grounded in the evidence
missing:          what would move it up a tier, or null if "answers"
evidence_quality: sufficient | thin | unreadable
```

- `Verdict` dataclass (+ `answered` convenience) and the allowed-value tuples.
- `VERDICT_SCHEMA_NOTE` — the JSON contract defined in ONE place, so the future
  verification rubric reuses the identical schema rather than drifting.
- `_parse_verdict` — forgiving: strips markdown fences, extracts the outermost
  `{...}`, coerces out-of-range field values to safe defaults, and degrades a
  totally unparseable reply to an honest `cant_tell` rather than raising. The
  judge failing must never take down the lesson around it (same philosophy as
  `learning.py`'s loader).
- `_run_judge(system, content, model)` — the core call, kept evidence-agnostic:
  `content` is a plain string today but can be a content list with an image block
  later, with no change to the schema or parsing.
- `judge_relevance(question, hits)` — this step's concrete use. Empty hits
  short-circuit to a confident `doesnt_answer` with no API spend.
- A standalone runner: `python -m repo_whisperer.judge "<question>"` retrieves and
  prints the verdict over the current store — so Step 1 is verifiable on its own,
  before any capture code exists.

**Why a model and not a threshold.** This is the intended replacement logged when
the old `WEAK_MATCH_DISTANCE` heads-up was removed: retrieval always returns its
top-k, and the closest-hit distance for a genuinely-absent feature (~0.68) and a
real feature worded differently (~0.65) sit in the same ~0.025 band, so one cosine
threshold can't separate them. A model reading the actual code can. The relevance
prompt is told explicitly that retrieval always returns *something*, so being from
the same repo or sharing keywords is not the same as answering.

**Hooked into `explore` at the `quick_answer` beat (`tutor.py`).** Right before
the short answer, `judge_relevance` runs and `_relevance_note` prints an honest
heads-up only when the verdict isn't `answers` — stating the retrieved code may
not answer the question, giving the judge's reason, and naming the closest file as
a best guess. A clean match stays silent (no clutter). One judge call per explore,
paired with the one existing short-answer call.

Verified offline: schema parsing across clean/fenced/malformed/out-of-range
inputs, the `"null"`/`"none"`→None normalization, the unparseable→`cant_tell`
degrade, the empty-hits short-circuit, and `_relevance_note` (silent on `answers`,
names the closest file on `doesnt_answer`). Both CLIs register. **Live
verification against Swerve still pending** (the audio query should pass; the orbs
query — absent feature — should fire the heads-up) before commit.

## 2026-06-09 — Explore polish: answer up front, revisit refreshers, drop the weak-match warning

Post-Phase-2 refinements to the `explore` flow, surfaced by live use (all in
`tutor.py` unless noted; not yet committed at time of writing).

- **Answer the question up front.** After showing the code, `explore` now prints
  a short "Short answer" — a direct 2-3 sentence grounded, cited reply to the
  question itself (new `quick_answer`) — *before* offering the deeper lesson. A
  "how does X work?" ask is always answered, never gated behind accepting a
  walkthrough. Pitched at the learner's level (vocabulary only; facts unchanged).

- **Re-asked topics become refreshers.** `LearningState.find_revisit` (in
  `learning.py`) recognizes when a query re-treads an already-taught thread by
  citation overlap (≥2 shared chunks, or a short thread fully re-hit). When it
  does, the prompt becomes "↩ You've explored this before — want a refresher?"
  instead of a fresh offer, and the re-teach is handed a `_refresher_note` so it
  acknowledges the prior visit and builds on it (including what it was connected
  to last time). Applies on the decline→redirect path too. No special command —
  re-asking a covered topic just works as review.

- **Removed the weak-match heads-up.** The distance-threshold warning (the old
  `WEAK_MATCH_DISTANCE` / `_weak_match_note`) is gone. It fired on good matches:
  e.g. "how does the audio work" correctly returned `js/audio.js` with a correct
  short answer but still printed "Nothing in this repo closely matches." The
  closest-hit distances for a genuinely-absent feature (~0.68) and a real feature
  worded differently (~0.65) sit in a ~0.025 band, so a single global threshold
  can't reliably tell "feature doesn't exist" from "feature exists, worded
  differently" — it did more harm than good. `_show_hits` is now a thin wrapper
  that just renders the chunks. **Intended future replacement: model-judged
  relevance** — let the model decide whether the retrieved chunks actually answer
  the question (and say so honestly when they don't), rather than thresholding a
  cosine distance. The short-answer call is already a natural place to hang this.

## 2026-06-09 — Phase 2, Step 5: "what's next" nudges (Phase 2 complete)

The last step of Phase 2. After a lesson, the tutor stops dropping the learner
back to the shell and instead suggests related threads they haven't explored
yet — pull, with gentle nudges, the learner always deciding.

**Nudges (`tutor.py`).** Once a lesson, its follow-ups, and the optional
comprehension invitation are done, `_run_nudges` runs a loop:

- `_next_candidates` re-queries the store around the just-taught topic with a
  wider net (`NUDGE_CANDIDATE_K = 12`), then drops every chunk already shown
  *and* every chunk cited by any covered thread in the learning memory — so what
  remains is genuinely related, genuinely unexplored code.
- `suggest_next_threads` makes one grounded model call over those candidates
  (plus the covered-threads brief) for up to `MAX_NUDGES = 3` one-line
  invitations naming real identifiers, steered toward the lesson and away from
  covered ground. If nothing is worth suggesting it returns the `NONE` sentinel
  and the session wraps up gracefully rather than inventing a nudge.
- The learner picks a suggestion by number, types their own topic, or presses
  Enter to stop. An accepted nudge runs the full existing lesson flow (teach →
  follow-ups → comprehension), is recorded to the learning memory, and then
  fresh nudges follow from the new lesson — the exclusion set growing each round
  so covered threads are never re-suggested. `ExploreResult.level` now reports
  the level in effect at the very end (a nudge lesson can re-pitch it).

**Two input fixes folded in as polish**, both surfaced by a live run where an
"orb" query pulled `js/player.js` and an accepted offer silently dead-ended:

- **Wider accept set.** `_prompt_decision` accepted only `""`/`y`/`yes`, so a
  natural "yeah"/"sure"/"ok" fell through to the decline→redirect path with no
  hint why. It now matches a `_AFFIRMATIVES` set of common affirmatives
  (case-insensitive, whitespace-stripped); Enter still takes the default, clear
  negatives still route to the redirect.
- **Weak-match heads-up.** Retrieval always returns top-k no matter how weak the
  match. When *every* hit is above `WEAK_MATCH_DISTANCE = 0.6` (strong hits sit
  ~0.3), `_weak_match_note` now says plainly that nothing closely matches the
  wording and names the closest file as a best guess, instead of passing off
  loosely related code as a real answer. Routed through a shared `_show_hits`
  wrapper so it fires wherever the learner types a query (initial, redirect, and
  nudge-chosen). One strong hit suppresses the note.

Verified live against Swerve end to end: the `"how do the orbs work"` query
(Swerve has no orbs — the system is `js/collectibles.js`) now shows the
weak-match warning naming `js/player.js`; `yeah` opens the lesson; and the nudge
loop ran two nudges in a row, each teaching a real lesson, with the second round
correctly omitting already-covered threads. An offline harness (model + retrieval
stubbed) also covers the accept set, the weak-match note, and the full
accept → nudge → second-lesson → record path.

**Phase 2 is complete** — all five steps (show-then-offer, Socratic teaching,
learner calibration, learning memory, and now "what's next" nudges) are built
and verified.

## 2026-06-09 — Fix: long lessons no longer truncate mid-sentence

A live lesson got cut off mid-sentence (stopped at "Are you lined up with the—")
and dropped back to the shell: the teaching call was hitting its `max_tokens`
ceiling on a long lesson and truncating. Two fixes in `tutor.py`:

- **More room.** `MAX_TEACH_TOKENS` raised 1536 → 4096 so a typical long lesson
  finishes its thought in a single call.
- **A safety net for the rare overrun.** `_ask_model` gained an opt-in
  `max_continuations` parameter: when a reply stops with `stop_reason ==
  "max_tokens"`, it makes up to that many follow-up calls to finish, keeping the
  partial reply in the conversation and asking the model to continue from where it
  left off without repeating. `teach_thread` uses it (`MAX_TEACH_CONTINUATIONS =
  2`); the intentionally short calls (offers, challenges) stay bounded at the
  default of 0. Note: this model rejects assistant-message prefill ("the
  conversation must end with a user message"), so the continuation is driven by a
  fresh user "keep going" turn rather than prefilling the assistant reply.

Verified against Swerve: the collision/hoop lesson that previously truncated at
1536 (`stop_reason == max_tokens`, ending "…So the game dec") now completes
cleanly under the new path, ending on a full summary sentence; a forced 250-token
ceiling confirmed the continuation stitches multiple chunks together.

## 2026-06-08 — Phase 2, Step 4: learning memory + explore-flow refinements

Step 4 gives the tutor a memory across sessions, plus a round of UX fixes to the
`explore` flow that testing surfaced.

**Learning memory (`repo_whisperer/learning.py`, new).** A single JSON sidecar
(`learning_state.json`, gitignored) keyed to the last-ingested repo, holding the
learner's chosen level and the threads they've covered. Plain dataclasses + `json`
(no SQLite), atomic writes, and a forgiving loader — a missing or corrupt file
starts fresh rather than crashing a lesson. `ingest` binds the state to the repo
and clears stale threads on a repo switch while keeping the level; `explore`
resolves the level as explicit `--level` → saved level → `beginner`, hands each
lesson a brief of past threads for cross-references, and records the covered
thread + final level on the way out. A thread's topic now comes from the
learner's query (not the offer — see below).

**Explore-flow refinements (`tutor.py`, `cli.py`), from a corrected, smaller
scope:**

- **Post-ingest guidance.** "Ready." now shows BOTH paths — `explore` (learn step
  by step) and `ask` (quick question) — instead of advertising only `ask`, which
  had been steering learners away from the tutoring loop.
- **Plain by default, specific only when memory earns it.** After showing the
  code, the invitation is a plain "Want to learn this? [Y/n]" (default yes) that
  teaches the thread the learner searched for. The auto-picked suggested lesson is
  KEPT (`generate_offer` retained, now memory-aware) but only fires as a SPECIFIC
  offer when the learning memory holds a genuine connection to a past lesson
  (e.g. "Want to see how this ties into your earlier lesson on `initRails`?"); the
  model returns a `NONE` sentinel when there's no real tie, and we fall back to
  the plain prompt. First/unconnected lessons skip the offer call entirely (one
  fewer round-trip).
- **Decline is no longer a dead end.** On `n`, the tutor asks what the learner
  would rather learn and teaches THAT instead — re-retrieving so the new lesson is
  grounded in its own code — then runs the same follow-up/comprehension/record
  flow. A blank reply exits gracefully. The teach→follow-ups→comprehension steps
  were factored into `_deliver_lesson`, shared by the accept and redirect paths.

**Verified live against Swerve:** empty memory → plain prompt → taught the
searched thread at beginner level, staying honestly grounded on a follow-up; state
persisted with a query-derived topic; a second run resumed, the real model
produced a genuine connection offer tying a track-segments query back to the
rails lesson, and declining redirected into a fresh grounded lesson that was
recorded. Step 5 ("what's next" nudges) is next.

## 2026-06-07 — Phase 2, Step 3: learner calibration (altitude, follow-ups, comprehension)

The task spec was revised (`PHASE_2_TASK_MODIFIED.md`) around one insight from
testing: Step 2 taught *correct* content but pitched it at a mid-level dev's
vocabulary, and the target learner (a curious learner, not a senior dev) couldn't
follow it. The content was right; the altitude was wrong. Step 3 fixes that with
three pieces, all in `tutor.py`, all reusing the existing teaching call:

- **(a) Depth dial.** New `--level` flag and `level=` param with three tiers —
  `beginner` (default; the target user — non-basic jargon must be defined in
  plain English with an everyday analogy the first time it appears),
  `intermediate`, `advanced`. Implemented as `LEVEL_GUIDANCE` blocks appended to
  each system prompt via `_level_block`, with a `GROUNDING_CONTRACT_NOTE` bolted
  on so calibration changes only assumed vocabulary — never the facts, citations,
  or grounding. `teach_thread` now takes `level`.
- **(b) In-lesson follow-ups.** After the lesson, `_run_followups` runs a read-loop:
  plain-English questions are answered by `answer_followup` (grounded in the same
  chunks + the lesson, at the chosen level). Bonus: typing `level <tier>`
  re-teaches the same thread at a new altitude on the fly, so the learner can
  dial it in until it lands. Blank line / `done` exits; EOF is handled.
- **(c) Light comprehension invitation.** `_run_comprehension` then offers an
  optional predict/modify/explain-back prompt (`comprehension_challenge`); if the
  learner attempts it, `evaluate_response` replies supportively. Skippable — an
  invitation, never a quiz.

Refactored the repeated Anthropic single-turn call into `_ask_model`. `ExploreResult`
gained a `level` field (the level in effect at the end, since it can change
mid-lesson). `--level` wired through the `explore` CLI command too.

Verified offline: imports, level validation (bad level → ValueError), the
beginner block carries the plain-English-analogy instruction, the grounding note
survives in every level, and `--level {beginner,intermediate,advanced}` shows in
the CLI.

**Verified live by the user:** re-ran the orb lesson at `beginner` and actually
followed it this time — even asked a follow-up. Notably, the original altitude
problem was surfaced by the user admitting they didn't understand the earlier
(Step 2) version; that honest "I don't get this" is what exposed the gap this
step closes.

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
