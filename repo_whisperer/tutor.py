"""Phase 2 — the tutoring layer, built on top of the Phase 1 engine.

The flow:

1. **Show me.** The learner asks where something is. We reuse Phase 1 retrieval
   (`answer.retrieve`) to pull the most relevant chunks and SHOW them — the
   actual code, with `path:start-end` citations.
2. **Answer, then offer the doorway.** Having seen the code, the learner first
   gets a short, direct answer to the question they asked (`quick_answer`) — so a
   "how does X work?" ask is always answered up front, never gated behind
   accepting a lesson. Then they're asked if they want to go deeper. By default
   this is a plain invitation that teaches the thread they searched for; it
   becomes a SPECIFIC offer naming a tie when the learning memory holds a genuine
   connection to a past lesson (e.g. "Want to see how this ties into your earlier
   lesson on `initRails`?"), or a "you've explored this before — want a
   refresher?" prompt when the topic was already covered. On a decline we don't
   dead-end — we ask what they'd rather learn and teach that instead.
3. **Teach it — at the learner's altitude.** If the learner accepts, we teach
   that thread (`teach_thread`), pitched at their chosen `level`
   (beginner/intermediate/advanced). The SAME grounded, cited content is
   explained with more or less assumed vocabulary; at `beginner`, jargon is
   defined in plain English the first time it appears. Then the learner can ask
   in-lesson follow-ups (and re-pitch the level on the fly), and finally take an
   optional, light comprehension invitation.
4. **Remember it across sessions.** State (the chosen level + covered threads)
   persists via `learning`. The level carries over between runs, and each new
   lesson is handed a brief of earlier threads so it can cross-reference them.
5. **Nudge what's next.** After a lesson, the tutor pulls related code the
   learner has NOT been taught yet (neighbors of the lesson in the store, minus
   everything already shown or covered) and suggests up to a few specific
   threads they might be curious about. Suggestions only — the learner picks
   one, types their own topic, or stops. Each accepted nudge runs the full
   lesson flow and is remembered, then fresh nudges follow from there.

Run standalone:

    python -m repo_whisperer.tutor "<where-is-x query>" [--db DIR] [-k N] [--level L]
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from repo_whisperer import capture, judge, learning
from repo_whisperer.answer import (
    ANSWER_MODEL,
    DEFAULT_TOP_K,
    Hit,
    _client,
    build_context,
    repo_map,
    retrieve,
)
from repo_whisperer.learning import DEFAULT_STATE_PATH

# The teaching offer is short by design — a single tempting sentence.
MAX_OFFER_TOKENS: int = 200

# A taught thread is a few focused paragraphs. Generous headroom so a long lesson
# finishes its thought rather than getting cut off mid-sentence at the ceiling.
MAX_TEACH_TOKENS: int = 4096

# Safety net for the rare lesson that still overruns MAX_TEACH_TOKENS: how many
# follow-up "keep going" calls _ask_model may make to finish a truncated reply.
MAX_TEACH_CONTINUATIONS: int = 2

# The upfront direct answer is brief by design — a couple of sentences that
# answer the question itself before any deeper lesson is offered.
MAX_QUICK_ANSWER_TOKENS: int = 256

# Follow-ups, challenges, and evaluations are shorter, focused replies.
MAX_FOLLOWUP_TOKENS: int = 800
MAX_CHALLENGE_TOKENS: int = 300
MAX_EVAL_TOKENS: int = 600

# "What's next" nudges are a handful of one-line invitations.
MAX_NUDGE_TOKENS: int = 400

# Guard on `check`: a practice file is a handful of functions, so anything past
# this is almost certainly the wrong file (a bundle, a log). Truncating keeps
# the prompt sane while still letting an honest attempt through with a note.
MAX_ATTEMPT_CHARS: int = 20_000

# A guide answer is orientation, not a lesson — a few short paragraphs.
MAX_GUIDE_TOKENS: int = 1024

# What `guide` answers when the learner doesn't type a question of their own.
DEFAULT_GUIDE_QUESTION: str = (
    "Where should I start in this repo, and which file should I learn first "
    "for my level?"
)

# Cap on the file map handed to the guide prompt; repos bigger than this get
# the largest files listed plus a count of the rest, keeping the prompt sane.
MAX_MAP_FILES: int = 200

# At most this many next-thread suggestions per nudge.
MAX_NUDGES: int = 3

# How many chunks to pull when hunting for unexplored neighbors of a lesson.
# Wider than a normal retrieval so something usually survives the filtering of
# already-shown and already-covered chunks.
NUDGE_CANDIDATE_K: int = 12

# Every tutoring call reuses the Phase 1 answering model.
OFFER_MODEL: str = ANSWER_MODEL
TEACH_MODEL: str = ANSWER_MODEL

# --- Learner calibration --------------------------------------------------
# The single most important Phase 2 fix: teach at the LEARNER'S altitude. The
# content, citations, and grounding stay identical across levels — only the
# assumed vocabulary changes. The target user is a curious learner, so the
# default is `beginner`.
DEFAULT_LEVEL: str = "beginner"

LEVEL_GUIDANCE: dict[str, str] = {
    "beginner": (
        "LEARNER LEVEL — beginner. They know basic coding concepts (functions, "
        "strings, values, conditions, floats, arrays) but do NOT fluently know "
        "intermediate jargon. The FIRST time you use any non-basic term (e.g. "
        "'factory function', 'buffer geometry', 'module-level constant', "
        "'descriptor object', 'closure', 'callback'), define it in plain English "
        "with a quick everyday analogy — e.g. 'a factory function is just a "
        "function whose job is to build something and hand it back, like a little "
        "assembly station'. Go gently, in small layers, one idea at a time."
    ),
    "intermediate": (
        "LEARNER LEVEL — intermediate. They are comfortable with common "
        "programming terms, so use standard jargon without stopping to define "
        "it, but still briefly explain non-obvious or domain-specific concepts. "
        "Keep a clear, explanatory voice."
    ),
    "advanced": (
        "LEARNER LEVEL — advanced. They are experienced and fluent in common and "
        "intermediate jargon. Go straight to the architecture and the "
        "interesting design decisions; be concise, with minimal hand-holding."
    ),
}

LEVELS: tuple[str, ...] = tuple(LEVEL_GUIDANCE)

# Bolted onto every level block so calibration never erodes the grounding.
GROUNDING_CONTRACT_NOTE = (
    "Adjusting the level changes ONLY the assumed vocabulary — never the facts, "
    "the citations, or the grounding contract. Stay grounded strictly in the "
    "provided excerpts and never invent code or behavior not shown. This is "
    "meeting the learner where they are, NOT dumbing the content down."
)


def _level_block(level: str) -> str:
    """Return the system-prompt fragment that pitches a reply at `level`."""
    return f"{LEVEL_GUIDANCE[level]}\n{GROUNDING_CONTRACT_NOTE}"


def _check_level(level: str) -> None:
    """Validate a level name, raising ValueError with the allowed options."""
    if level not in LEVEL_GUIDANCE:
        raise ValueError(
            f"unknown level '{level}'. Choose one of: {', '.join(LEVELS)}."
        )


OFFER_SYSTEM_PROMPT = (
    "You are a patient expert coding tutor sitting beside a learner who is "
    "exploring one specific codebase. The learner just asked to see where "
    "something is, and has been shown the retrieved code excerpts below. Your "
    "job is to offer the doorway to learning: ONE specific, tempting invitation "
    "to walk them through how this code actually works.\n\n"
    "Rules:\n"
    "- Base the offer ONLY on the provided excerpts. Name a REAL function, "
    "class, method, or concept that appears in them, using its exact "
    "identifier.\n"
    "- Make it specific and curiosity-piquing — point at the interesting "
    "mechanism, not 'this code' or 'this file'.\n"
    "- Respond with a SINGLE sentence phrased as an invitation, e.g. \"Want me "
    "to walk you through how `initRails` builds the two rail meshes?\"\n"
    "- Do NOT teach or explain anything yet. Just make the offer. No preamble, "
    "no lists, no code blocks."
)

# Returned by the connection-offer prompt when there's no genuine tie to a past
# lesson, signalling the caller to fall back to the plain invitation.
NO_CONNECTION_SENTINEL: str = "NONE"

CONNECTION_OFFER_SYSTEM_PROMPT = (
    "You are a patient expert coding tutor sitting beside a learner exploring one "
    "codebase. They just asked to see some code and have been shown it. You are "
    "also given a brief of threads you've ALREADY taught them in this repo. Your "
    "job is to decide whether this new code genuinely connects to one of those "
    "past lessons, and if so, offer to teach it through that connection.\n\n"
    "Rules:\n"
    f"- If there is NO real, substantive connection, respond with exactly "
    f"{NO_CONNECTION_SENTINEL} and nothing else. Do NOT manufacture a tie.\n"
    "- A real connection means a shared mechanism, pattern, data flow, or "
    "dependency — not merely 'both are code' or 'both are in this repo'.\n"
    "- When there IS one, respond with a SINGLE inviting sentence that names the "
    "past topic and the tie, e.g. \"Want to see how this reuses the same buffer "
    "setup you saw in `initRails`?\" Name real identifiers from the excerpts.\n"
    "- No preamble, no lists, no code blocks — just the sentence or the sentinel."
)

QUICK_ANSWER_SYSTEM_PROMPT = (
    "You are a patient expert coding tutor. The learner asked a question and has "
    "just been shown the relevant code excerpts. Give a SHORT, direct answer to "
    "what they actually asked — 2-3 sentences, no more.\n\n"
    "Rules:\n"
    "- Answer the question itself, plainly and up front. This is the immediate "
    "answer; a deeper guided walkthrough is offered separately, so do NOT teach "
    "in layers or pad it — just answer.\n"
    "- Ground it strictly in the provided excerpts and cite the relevant "
    "`path:start-end` key(s). If the excerpts don't actually contain the answer, "
    "say so plainly rather than inventing.\n"
    "- No preamble, no headers, no lists — just the couple of sentences."
)

TEACH_SYSTEM_PROMPT = (
    "You are a patient expert coding tutor walking a curious learner through one "
    "specific thread of a codebase. The learner has SEEN the code excerpts below "
    "and just accepted your offer to be taught how it works. Teach it.\n\n"
    "Rules:\n"
    "- Ground everything ONLY in the provided excerpts. Never invent code, "
    "files, or behavior that isn't shown; if a detail isn't in the excerpts, "
    "say so rather than guessing.\n"
    "- Cite the specific `path:start-end` keys as you go, so every claim is "
    "verifiable against the code in front of them.\n"
    "- Teach in a teaching voice — explain the WHY and the flow, don't just "
    "restate the lines. Build understanding step by step: start from the big "
    "picture (what this thread does and where it starts), then walk through the "
    "key mechanism, referring to real identifiers from the code.\n"
    "- Stay focused on the accepted thread; don't sprawl into the whole repo.\n"
    "- Do NOT quiz the learner or set exercises — just teach. (A light, optional "
    "comprehension check comes separately, later.)\n"
    "- Be warm and concrete, not a flat answer dump. Aim for a few focused "
    "paragraphs a curious adult can follow."
)

# Step 3 — teaching from sight. Same tutor, now looking over the learner's
# shoulder at the editor: the grounding is a SCREENSHOT (the whole visible file
# plus a highlighted selection). Step 3.5 fuses in retrieval — the lesson is
# scoped to the highlight but may also be handed RELATED CHUNKS pulled from the
# ingested repo (off-screen context). NOT verification — no quiz, grade, judge.
SCREEN_TEACH_SYSTEM_PROMPT = (
    "You are a patient expert coding tutor sitting beside a learner, looking over "
    "their shoulder at the code on their screen. You are given a SCREENSHOT of "
    "their editor: the whole visible file, with one part HIGHLIGHTED (shown by a "
    "colored selection background) — that highlight is the specific code they are "
    "pointing at and want explained. You may ALSO be given related code excerpts "
    "retrieved from elsewhere in the same repository, each labeled with a "
    "`path:start-end` citation key — these are off-screen context the screenshot "
    "can't show.\n\n"
    "Your job: teach the HIGHLIGHTED selection, specifically.\n\n"
    "Rules:\n"
    "- Teach ONLY the highlighted/selected code. Use the rest of the visible file "
    "as CONTEXT — to see what the selection calls, what calls it, and where it "
    "sits — but do NOT give a tour of the whole file. Keep the lesson scoped to "
    "the selection.\n"
    "- Read the screen faithfully and ground the lesson in the code actually "
    "visible, referring to the real identifiers you can see. If part of the "
    "selection is cut off or too small to read, say so rather than guessing what "
    "it says.\n"
    "- When the retrieved excerpts genuinely illuminate the selection, USE them to "
    "reach beyond the visible file — where the selection's data, types, geometry, "
    "or callers/callees live, what consumes its output — and CITE the relevant "
    "`path:start-end` key when you do (e.g. \"these get checked for collection "
    "over in `player.js:120-138`\"). Pull in real off-screen detail; don't invent "
    "connections the excerpts don't support, and stay anchored to the selection — "
    "the excerpts are supporting context, not new threads to tour.\n"
    "- If nothing is clearly highlighted (you cannot see a selection), do NOT "
    "default to teaching the whole file. Briefly say you can't tell which part "
    "they mean and ask them to highlight the specific code they want explained, "
    "then stop.\n"
    "- Teach in a real teaching voice: explain the WHY and the flow of the "
    "selected code, not just a restatement of the lines. Start from what this "
    "selection does and how it fits, then walk the key mechanism.\n"
    "- You are a patient expert looking at the page they're pointing to — never a "
    "proctor. Do NOT quiz them, grade them, or set exercises; just teach.\n"
    "- Be warm and concrete — a few focused paragraphs a curious adult can follow."
)

# Returned by the selection-transcriber when no highlight is visible, so the
# caller skips retrieval (no query to run) and lets the teaching prompt ask the
# learner to highlight something.
NO_SELECTION_SENTINEL: str = "NO_SELECTION"

SELECTION_READ_SYSTEM_PROMPT = (
    "You are reading a screenshot of a code editor to extract ONE thing: the code "
    "the user has HIGHLIGHTED (shown by a colored selection background). "
    "Transcribe ONLY the highlighted/selected code, as plain text, exactly as it "
    "appears — preserve identifiers and structure. Output just that code: no "
    "explanation, no commentary, no markdown fences. This text is used to search "
    "the rest of the codebase, so accuracy of the real identifiers matters more "
    "than perfect formatting. If no selection is visible (nothing is highlighted), "
    f"reply with exactly {NO_SELECTION_SENTINEL} and nothing else."
)

# The screen twin of FOLLOWUP_SYSTEM_PROMPT: the learner has been taught the
# highlighted selection (from the screenshot + retrieved repo chunks) and now
# wants to drill into it. Answered against the SAME evidence already in hand —
# never a new capture — so it stays anchored to that one selection.
SCREEN_FOLLOWUP_SYSTEM_PROMPT = (
    "You are a patient expert coding tutor sitting beside a learner, looking over "
    "their shoulder at their editor. You already taught them the HIGHLIGHTED "
    "selection in the screenshot (shown by a colored selection background), and "
    "now they have a follow-up question about it. Answer it in the same context — "
    "no new screenshot is taken, so reason from the selection you can see, the "
    "rest of the visible file, the lesson so far, and any related repo excerpts "
    "you were given.\n\n"
    "Rules:\n"
    "- Stay scoped to the highlighted selection. A follow-up may go DEEPER on the "
    "selection or on how it connects to the related excerpts, but do NOT drift "
    "into touring the whole file.\n"
    "- Ground every claim in what you can actually see in the screenshot and in "
    "the provided excerpts; cite the relevant `path:start-end` key(s) where they "
    "apply. If something isn't visible or in the excerpts, say so plainly rather "
    "than inventing it.\n"
    "- Answer just what they asked, then stop. Be warm and direct; don't re-teach "
    "the whole lesson, and never quiz or grade them."
)

# Step 4 — the `check` command's voice. The learner wrote their own practice
# attempt and asked the tutor to look at it. The judge's verdict runs underneath
# as a PRIVATE signal locating where their understanding is; what the learner
# sees is gap-closing assistance in the same patient voice as `explore`/`look` —
# never a grade, score, or report card.
CHECK_ASSIST_SYSTEM_PROMPT = (
    "You are a patient expert coding tutor. A learner was taught a thread of a "
    "real codebase and then tried writing their own version of the idea in a "
    "practice file. They've asked you to look at what they wrote and help them "
    "get it right. Your job is to ASSIST them in what they were trying to do — "
    "close the gap between their attempt and a working grasp — never to grade "
    "them.\n\n"
    "You are given: their practice-file code (read straight from disk — this is "
    "exactly what they wrote), what they were practicing, related excerpts from "
    "the REAL codebase labeled with `path:start-end` keys, and a PRIVATE honest "
    "assessment from a reviewing pass.\n\n"
    "Rules:\n"
    "- Assist, don't grade. NEVER show or mention the private assessment, a "
    "verdict, a score, pass/fail, or rubric words like 'partial' or "
    "'incorrect'. It exists only to tell you where their understanding is; "
    "translate it into help.\n"
    "- Start from what their attempt already gets right — be specific, naming "
    "the real identifiers they wrote.\n"
    "- Then close the gap: what's missing or off, and WHY it matters for the "
    "mechanism — explained as help toward what they were going for, not marked "
    "wrong.\n"
    "- Ground the help in how the real codebase does it: compare their attempt "
    "against the excerpts and cite the `path:start-end` keys (e.g. \"the real "
    "`createDot` also registers it for collision — see `dots.js:12-30`\"). "
    "Never invent code or behavior the excerpts don't show.\n"
    "- A practice attempt legitimately simplifies — different names, hardcoded "
    "values, fewer features are fine. Only surface a difference when it "
    "touches the core mechanism they were practicing.\n"
    "- If the attempt is genuinely solid, say so plainly and offer at most one "
    "real refinement. Do NOT manufacture a gap just to sound useful.\n"
    "- If you genuinely can't tell what they were going for (or the private "
    "assessment says it can't), say so warmly and ask them what they were "
    "trying to build, rather than bluffing.\n"
    "- Stay scoped to what they were practicing; don't sprawl into a "
    "whole-repo lesson.\n"
    "- Be warm and concrete — a few focused paragraphs — and end by pointing at "
    "the most useful next thing to try in their file."
)

# The side-question voice: whole-repo, learning-path questions ("where should I
# start?", "which file fits my level?") that no single retrieval thread can
# answer. The only prompt handed a MAP of every file in the ingested store —
# the bird's-eye view — alongside the usual level + covered-threads context.
GUIDE_SYSTEM_PROMPT = (
    "You are a patient expert coding tutor with a bird's-eye view of ONE "
    "ingested repository. The learner has a SIDE QUESTION — about the repo as "
    "a whole or about their own learning path ('where should I start?', "
    "'which file should I learn first for my level?', 'what is this project, "
    "big picture?') — rather than about one specific thread of code.\n\n"
    "You are given: a MAP of every file in the ingested repo (path, line "
    "count, chunk count), a few code excerpts retrieved for their question "
    "(each labeled `path:start-end`), the threads you've already taught them, "
    "and their level.\n\n"
    "Rules:\n"
    "- Answer their actual question, concretely: recommend by naming REAL "
    "files from the map, with their exact paths. Never mention a file that "
    "isn't in the map.\n"
    "- The map tells you what exists and how big it is — not what the code "
    "does. Ground claims about behavior in the excerpts (cite their "
    "`path:start-end` keys); you may reason from a filename (\"`player.js` "
    "most likely handles the player\") only if you flag it as a read of the "
    "name, not the code.\n"
    "- Fit the advice to WHO is asking: for a beginner, favor the small, "
    "self-contained, concrete entry point over the biggest or most central "
    "file; for advanced, go straight at the core. Factor in the threads "
    "already covered — build on them rather than re-recommending them.\n"
    "- Give a short WHY per recommendation — what makes it the right next "
    "step for them — and keep the whole answer to a few short paragraphs. "
    "One or two recommendations beat a ranked tour of the repo.\n"
    "- End with the concrete next move: a ready-to-run "
    "`explore \"<topic>\"` query for your top recommendation. (You may also "
    "mention `look`, but describe it correctly: it takes NO file argument — "
    "they open the file in their editor, highlight the code they're curious "
    "about, and run plain `look`.) Suggestions, never pressure."
)

FOLLOWUP_SYSTEM_PROMPT = (
    "You are a patient expert coding tutor. The learner is in the middle of a "
    "lesson about one thread of a codebase and has a follow-up question — e.g. "
    "'what does that term mean?', 'explain that simpler', or 'give me an "
    "example'. Answer it in the same context.\n\n"
    "Rules:\n"
    "- Stay grounded in the provided excerpts and the lesson so far; cite "
    "`path:start-end` keys where relevant. If the answer isn't in the excerpts, "
    "say so plainly rather than inventing.\n"
    "- Answer just what they asked, then stop. Be warm and direct; don't "
    "re-teach the whole thing."
)

CHALLENGE_SYSTEM_PROMPT = (
    "You are a patient expert coding tutor offering an OPTIONAL way for a curious "
    "adult learner to make a lesson stick — NOT a pop quiz. Based on the lesson "
    "and the excerpts, pose ONE small, friendly engagement: either a "
    "predict-the-behavior question, a tiny modify-the-code challenge, or an "
    "explain-it-back prompt. Pick whichever best fits this thread.\n\n"
    "Rules:\n"
    "- Keep it short and concrete, grounded in the code that was shown.\n"
    "- Output just the prompt, warmly phrased — no answer, no preamble, no "
    "multiple options. One inviting ask."
)

EVAL_SYSTEM_PROMPT = (
    "You are a patient expert coding tutor responding to a learner's attempt at "
    "an optional comprehension prompt. Be supportive and specific: affirm what "
    "they got right, gently correct or fill any gaps, and stay grounded in the "
    "excerpts (cite `path:start-end` where it helps). Never condescend or grade. "
    "Close by inviting them to keep going."
)

# Returned by the nudge prompt when none of the candidate code is genuinely
# worth suggesting, signalling the caller to end the session gracefully.
NO_NUDGE_SENTINEL: str = "NONE"

NUDGE_SYSTEM_PROMPT = (
    "You are a patient expert coding tutor. The learner just finished a lesson "
    "on one thread of a codebase. You are given that lesson, a list of threads "
    "they have ALREADY covered, and candidate code excerpts from the SAME repo "
    "that they have NOT been taught yet. Suggest what they might be curious "
    "about next.\n\n"
    "Rules:\n"
    f"- Suggest at most {MAX_NUDGES} threads, each grounded in the candidate "
    "excerpts — name a REAL function, class, or identifier that appears in "
    "them, using its exact name.\n"
    "- Prefer threads that genuinely relate to what was just learned (a caller "
    "or callee, shared data, the next step in the same flow) and steer AWAY "
    "from the threads already covered.\n"
    "- Phrase each as a short, tempting one-line invitation, one per line, "
    "numbered, e.g. \"1. Want to see how `spawnOrbs` decides where each orb "
    "appears?\"\n"
    "- These are suggestions, never pressure. No preamble, no explanations, no "
    "code blocks — just the numbered lines.\n"
    f"- If none of the candidates is genuinely worth suggesting, respond with "
    f"exactly {NO_NUDGE_SENTINEL} and nothing else."
)


@dataclass
class ExploreResult:
    """The outcome of one show → offer → teach interaction.

    Captures everything later steps need to pick up a thread: the learner's
    query, the chunks they were shown, the offer we made, whether they accepted,
    the teaching they received, and the level it was pitched at. `accepted` is
    None when the decision wasn't collected (e.g. `decide=False`); `teaching` is
    None unless the offer was accepted and taught. `level` is the level in effect
    at the end (the learner can re-pitch mid-lesson).
    """

    query: str
    hits: list[Hit]
    offer: str
    accepted: bool | None = None
    teaching: str | None = None
    level: str = DEFAULT_LEVEL


# Sent as a fresh user turn to finish a reply that was cut off at the token
# ceiling. The model doesn't support assistant prefill, so we keep the partial in
# the history and ask it to pick up where it stopped without repeating itself.
_CONTINUE_INSTRUCTION = (
    "Your previous message was cut off because it hit the length limit. Continue "
    "from exactly where you stopped — pick up mid-sentence or mid-word if that is "
    "where it ended. Do not repeat anything you already wrote and do not add any "
    "preamble; just continue the text."
)


def _ask_model(
    system: str,
    user: str | list[dict],
    max_tokens: int,
    model: str,
    max_continuations: int = 0,
) -> str:
    """Anthropic call returning the concatenated text blocks.

    `user` is either plain text or a content-block list (e.g. an image block plus
    a text instruction) — the latter is how the screen-aware tutor teaches from a
    screenshot. Either way the reply is text.

    When `max_continuations > 0` and a reply is truncated because it hit the token
    ceiling (`stop_reason == "max_tokens"`), this makes up to that many follow-up
    calls to finish it — keeping the partial reply in the conversation and asking
    the model to pick up where it left off — instead of returning a sentence cut
    off mid-word. Left at 0 (the default), it's a single bounded call.
    """
    client = _client()
    messages = [{"role": "user", "content": user}]
    accumulated = ""
    for _ in range(max_continuations + 1):
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
        )
        chunk = "".join(
            block.text for block in response.content if block.type == "text"
        )
        accumulated += chunk
        if response.stop_reason != "max_tokens" or not chunk:
            break
        messages.append({"role": "assistant", "content": chunk})
        messages.append({"role": "user", "content": _CONTINUE_INSTRUCTION})
    return accumulated.strip()


def generate_offer(
    query: str, hits: list[Hit], prior: str = "", model: str = OFFER_MODEL
) -> str:
    """Ask the model for a specific teaching invitation grounded in `hits`.

    With no `prior`, the offer names a real identifier from the retrieved code so
    it tempts the learner toward a concrete mechanism rather than a generic "want
    to learn more?".

    When `prior` (a brief of past lessons) is given, the offer is made ONLY if the
    new code genuinely connects to one of those lessons — phrased to name the tie
    (e.g. "Want to see how this ties into your earlier lesson on `X`?"). If there
    is no real connection the model returns the sentinel and we return "" so the
    caller can fall back to a plain invitation.

    Raises ValueError (via `_client`) if the API key is missing.
    """
    context = build_context(hits)
    if prior:
        user_message = (
            f'The learner asked: "{query}"\n\n'
            f"These are the code excerpts they were just shown:\n\n{context}\n\n"
            f"Threads you've already taught them in this repo:\n{prior}\n\n"
            f"If this new code genuinely connects to one of those past lessons, "
            f"offer to teach it through that tie; otherwise reply with exactly "
            f"{NO_CONNECTION_SENTINEL}."
        )
        offer = _ask_model(
            CONNECTION_OFFER_SYSTEM_PROMPT, user_message, MAX_OFFER_TOKENS, model
        )
        return "" if offer.strip().upper().startswith(NO_CONNECTION_SENTINEL) else offer
    user_message = (
        f'The learner asked: "{query}"\n\n'
        f"These are the code excerpts they were just shown:\n\n{context}\n\n"
        f"Offer one specific, tempting invitation to teach them how this works."
    )
    return _ask_model(OFFER_SYSTEM_PROMPT, user_message, MAX_OFFER_TOKENS, model)


def _prior_note(prior: str) -> str:
    """Build the optional 'previously covered' instruction for the teaching prompt.

    Returns "" when there's nothing prior, so a first lesson reads exactly as it
    did before this step. The note invites cross-referencing but only when it
    genuinely aids understanding — never a forced callback.
    """
    if not prior:
        return ""
    return (
        f"\n\nThe learner has already been taught these threads in this repo:\n"
        f"{prior}\n"
        f"If — and only if — it genuinely helps understanding, briefly connect "
        f"this lesson to one of them (e.g. \"this uses the same pattern you saw in "
        f"X earlier\"). Never force a connection or list them; skip it when there "
        f"isn't a real link."
    )


def quick_answer(
    query: str,
    hits: list[Hit],
    level: str = DEFAULT_LEVEL,
    model: str = TEACH_MODEL,
) -> str:
    """Answer the learner's question directly in 2-3 sentences, grounded in `hits`.

    This is the upfront answer shown right after the code, before any deeper
    lesson is offered — so a "how does X work?" question is always answered, not
    gated behind accepting a walkthrough. Pitched at `level` (vocabulary only);
    the facts and citations are unchanged. Raises ValueError on an unknown level
    or a missing API key.
    """
    _check_level(level)
    context = build_context(hits)
    user_message = (
        f'The learner asked: "{query}"\n\n'
        f"The code excerpts they were just shown:\n\n{context}\n\n"
        f"Answer their question directly in 2-3 sentences, grounded in these "
        f"excerpts and citing the `path:start-end` key(s)."
    )
    system = f"{QUICK_ANSWER_SYSTEM_PROMPT}\n\n{_level_block(level)}"
    return _ask_model(system, user_message, MAX_QUICK_ANSWER_TOKENS, model)


def teach_thread(
    query: str,
    hits: list[Hit],
    offer: str = "",
    level: str = DEFAULT_LEVEL,
    prior: str = "",
    model: str = TEACH_MODEL,
    note: str = "",
) -> str:
    """Teach the accepted thread, grounded in `hits`, cited, and pitched at `level`.

    Explains how the retrieved code works in a teaching voice, building
    understanding step by step and citing `path:start-end` keys. The lesson stays
    on the thread the learner searched for (`query`); `offer`, when non-empty,
    carries a connection to a past lesson the lesson can lean into. `level`
    controls only the assumed vocabulary, not the facts. `prior` is an optional
    brief of threads already covered, so the lesson can cross-reference them.
    `note`, when non-empty, is extra pitching guidance — used to flag a refresher
    of a thread the learner already explored so the lesson builds on it rather
    than starting cold. Raises ValueError on an unknown level or a missing API key.
    """
    _check_level(level)
    context = build_context(hits)
    offer_line = f"They accepted this teaching offer:\n{offer}\n\n" if offer else ""
    note_line = f"{note}\n\n" if note else ""
    user_message = (
        f'The learner originally asked: "{query}"\n\n'
        f"{offer_line}"
        f"{note_line}"
        f"These are the code excerpts they were shown:\n\n{context}\n\n"
        f"Now teach this thread: walk them through how it works, grounded in "
        f"these excerpts and citing the `path:start-end` keys as you go."
        f"{_prior_note(prior)}"
    )
    system = f"{TEACH_SYSTEM_PROMPT}\n\n{_level_block(level)}"
    return _ask_model(
        system, user_message, MAX_TEACH_TOKENS, model,
        max_continuations=MAX_TEACH_CONTINUATIONS,
    )


def transcribe_selection(
    frame: "capture.Frame", model: str = TEACH_MODEL
) -> str:
    """Read just the HIGHLIGHTED code out of `frame`, for use as a retrieval query.

    A focused vision read (Step 3.5): returns the selected code as plain text so it
    can be embedded and run against the ingested store to pull related off-screen
    chunks. Returns "" when no selection is visible (the model replies with
    `NO_SELECTION_SENTINEL`), so the caller knows to skip retrieval. Raises
    ValueError (via `_client`) if the API key is missing.
    """
    instruction = (
        "Transcribe only the highlighted/selected code in this editor screenshot, "
        "exactly as shown. If nothing is highlighted, reply with exactly "
        f"{NO_SELECTION_SENTINEL}."
    )
    content = [capture.image_block(frame), {"type": "text", "text": instruction}]
    text = _ask_model(
        SELECTION_READ_SYSTEM_PROMPT, content, MAX_QUICK_ANSWER_TOKENS, model
    )
    return "" if text.strip().upper().startswith(NO_SELECTION_SENTINEL) else text


def teach_from_screen(
    frame: "capture.Frame",
    hits: list[Hit] | None = None,
    level: str = DEFAULT_LEVEL,
    prior: str = "",
    model: str = TEACH_MODEL,
) -> str:
    """Teach the highlighted selection in `frame`, grounded in screen + repo.

    The same tutor as `teach_thread`, but its evidence is a SCREENSHOT (the exact
    highlighted selection, visually) optionally fused with `hits` — related chunks
    retrieved from the ingested repo that the screen can't show (Step 3.5). The
    model teaches the highlighted code specifically (scoped to the selection, not a
    tour of the file), reaching into the retrieved excerpts for off-screen context
    and citing their `path:start-end` keys. With `hits` empty it degrades to pure
    screen-only teaching (Step 3 behavior). Pitched at `level` (vocabulary only);
    `prior`, when non-empty, lets it cross-reference threads already taught. This
    is purely sight-plus-context teaching — no verification, quiz, or judge. Raises
    ValueError on an unknown level or a missing API key.
    """
    _check_level(level)
    hits = hits or []
    context_note = ""
    if hits:
        context_note = (
            "\n\nRelated code retrieved from elsewhere in the SAME repository — "
            "off-screen context you can use and cite by its `path:start-end` "
            f"key:\n\n{build_context(hits)}"
        )
    instruction = (
        "This is a screenshot of my editor. Teach me the HIGHLIGHTED selection "
        "specifically, using the rest of the visible file as immediate context. "
        "If nothing is clearly highlighted, ask me to highlight the part I want "
        "explained." + context_note
    )
    content = [capture.image_block(frame), {"type": "text", "text": instruction}]
    system = f"{SCREEN_TEACH_SYSTEM_PROMPT}\n\n{_level_block(level)}{_prior_note(prior)}"
    return _ask_model(
        system, content, MAX_TEACH_TOKENS, model,
        max_continuations=MAX_TEACH_CONTINUATIONS,
    )


def answer_screen_followup(
    question: str,
    frame: "capture.Frame",
    hits: list[Hit],
    lesson: str,
    level: str = DEFAULT_LEVEL,
    model: str = TEACH_MODEL,
) -> str:
    """Answer a follow-up about the highlighted selection, at `level`.

    The screen-aware twin of `answer_followup`: the evidence is the SAME
    screenshot (the highlighted selection, visually), the repo `hits` pulled at
    capture time, and the `lesson` so far — never a fresh capture. This is what
    makes `look` a back-and-forth: highlight once, then keep questioning that one
    selection with the context already loaded. Stays scoped to the selection.
    Raises ValueError on an unknown level or a missing API key.
    """
    _check_level(level)
    context_note = ""
    if hits:
        context_note = (
            "\n\nThe related repo excerpts already in hand (off-screen context, "
            f"cite by `path:start-end`):\n\n{build_context(hits)}"
        )
    instruction = (
        "This is the screenshot of my editor from the lesson — the HIGHLIGHTED "
        "selection is what we're discussing.\n\n"
        f"The lesson so far:\n{lesson}\n\n"
        f'My follow-up question: "{question}"\n\n'
        "Answer it grounded in the highlighted selection and the context already "
        "in hand, at my level, staying scoped to the selection." + context_note
    )
    content = [capture.image_block(frame), {"type": "text", "text": instruction}]
    system = f"{SCREEN_FOLLOWUP_SYSTEM_PROMPT}\n\n{_level_block(level)}"
    return _ask_model(system, content, MAX_FOLLOWUP_TOKENS, model)


def answer_followup(
    question: str,
    hits: list[Hit],
    lesson: str,
    level: str = DEFAULT_LEVEL,
    model: str = TEACH_MODEL,
) -> str:
    """Answer an in-lesson follow-up at `level`, grounded in `hits` and `lesson`."""
    _check_level(level)
    context = build_context(hits)
    user_message = (
        f"The lesson so far:\n{lesson}\n\n"
        f"The code excerpts in front of the learner:\n\n{context}\n\n"
        f'The learner\'s follow-up question: "{question}"\n\n'
        f"Answer it in this context, at their level."
    )
    system = f"{FOLLOWUP_SYSTEM_PROMPT}\n\n{_level_block(level)}"
    return _ask_model(system, user_message, MAX_FOLLOWUP_TOKENS, model)


def comprehension_challenge(
    query: str,
    hits: list[Hit],
    lesson: str,
    level: str = DEFAULT_LEVEL,
    model: str = TEACH_MODEL,
) -> str:
    """Generate one optional, level-appropriate way to engage with the lesson."""
    _check_level(level)
    context = build_context(hits)
    user_message = (
        f"The lesson just taught:\n{lesson}\n\n"
        f"The code excerpts:\n\n{context}\n\n"
        f"Pose one small, optional engagement to help this stick."
    )
    system = f"{CHALLENGE_SYSTEM_PROMPT}\n\n{_level_block(level)}"
    return _ask_model(system, user_message, MAX_CHALLENGE_TOKENS, model)


def evaluate_response(
    challenge: str,
    learner_response: str,
    hits: list[Hit],
    lesson: str,
    level: str = DEFAULT_LEVEL,
    model: str = TEACH_MODEL,
) -> str:
    """Respond supportively to the learner's attempt at the comprehension prompt."""
    _check_level(level)
    context = build_context(hits)
    user_message = (
        f"The lesson:\n{lesson}\n\n"
        f"The code excerpts:\n\n{context}\n\n"
        f"The comprehension prompt you posed:\n{challenge}\n\n"
        f'The learner\'s response:\n"{learner_response}"\n\n'
        f"Respond supportively at their level."
    )
    system = f"{EVAL_SYSTEM_PROMPT}\n\n{_level_block(level)}"
    return _ask_model(system, user_message, MAX_EVAL_TOKENS, model)


def _next_candidates(
    query: str,
    db_dir: str,
    exclude: set[str],
    k: int = NUDGE_CANDIDATE_K,
) -> list[Hit]:
    """Pull chunks related to `query` that the learner hasn't met yet.

    Retrieves wider than a normal lesson, then drops every chunk id in
    `exclude` (chunks just shown plus everything cited by covered threads), so
    what remains is genuinely unexplored neighboring code. May return [] —
    e.g. in a small repo where the learner has seen most of it.
    """
    neighbors = retrieve(query, k=k, db_dir=db_dir)
    return [h for h in neighbors if h.id not in exclude]


def suggest_next_threads(
    query: str,
    lesson: str,
    covered: str,
    candidates: list[Hit],
    model: str = TEACH_MODEL,
) -> list[str]:
    """Suggest up to MAX_NUDGES unexplored threads to be curious about next.

    Each suggestion is a one-line invitation grounded in the `candidates`
    excerpts (code the learner has NOT been taught), steered away from the
    `covered` brief. Returns [] when the model judges nothing genuinely worth
    suggesting — no manufactured nudges.
    """
    context = build_context(candidates)
    user_message = (
        f'The learner was just taught a lesson on: "{query}"\n\n'
        f"The lesson:\n{lesson}\n\n"
        f"Threads already covered:\n{covered or '(only this one so far)'}\n\n"
        f"Candidate UNEXPLORED code excerpts from the same repo:\n\n{context}\n\n"
        f"Suggest up to {MAX_NUDGES} next threads, or reply "
        f"{NO_NUDGE_SENTINEL} if none are genuinely worth suggesting."
    )
    raw = _ask_model(NUDGE_SYSTEM_PROMPT, user_message, MAX_NUDGE_TOKENS, model)
    if raw.strip().upper().startswith(NO_NUDGE_SENTINEL):
        return []
    suggestions: list[str] = []
    for line in raw.splitlines():
        text = re.sub(r"^\s*(?:\d+[.)]|[-*•])\s*", "", line).strip()
        if text:
            suggestions.append(text)
    return suggestions[:MAX_NUDGES]


def format_hits(query: str, hits: list[Hit]) -> str:
    """Render the retrieved chunks as a readable 'here is the code' view."""
    lines = [
        f'Showing the most relevant code for: "{query}"',
        "=" * 60,
    ]
    for h in hits:
        lines.append("")
        lines.append(f"[{h.id}]   (distance {h.distance:.3f})")
        lines.append("-" * 60)
        lines.append(h.text.rstrip("\n"))
    return "\n".join(lines)


def _show_hits(query: str, hits: list[Hit], lead: str = "") -> None:
    """Print the retrieved code for `query`.

    A thin wrapper so every learner-query display site (initial, redirect, and
    nudge-chosen) renders the same way; `lead` prepends a separator (e.g. "\\n")
    where the surrounding output needs it.
    """
    print(lead + format_hits(query, hits))


def _relevance_note(verdict: "judge.Verdict", hits: list[Hit]) -> str:
    """An honest heads-up when the judge says retrieval didn't really answer.

    Returns "" when the evidence answers the question, so a good match stays
    uncluttered. Otherwise it states plainly that the retrieved code may not
    answer the question, gives the judge's reason, and names the closest file as
    a best guess. This is the model-judged replacement for the old
    distance-threshold weak-match warning, which couldn't tell "feature absent"
    from "feature worded differently" — a model reading the code can.
    """
    if verdict.answered:
        return ""
    heads = {
        "partial": "⚠  Heads-up: the retrieved code only partly covers your question.",
        "doesnt_answer": "⚠  Heads-up: the retrieved code doesn't look like it actually answers that.",
        "cant_tell": "⚠  Heads-up: I'm not sure the retrieved code answers that.",
    }
    lines = [heads.get(verdict.verdict, heads["cant_tell"]), f"   {verdict.reason}"]
    if verdict.verdict != "partial" and hits:
        lines.append(f"   Closest file retrieved: {hits[0].path}")
    return "\n".join(lines)


# Natural affirmatives that count as "yes" at a [Y/n] prompt. A learner shouldn't
# have to know the one magic letter — "yeah", "sure", "ok" all plainly mean yes,
# and Enter (the empty string) takes the capital-Y default. Anything not in here
# is treated as a decline, which routes to the "what would you rather learn?"
# redirect rather than a dead end.
_AFFIRMATIVES: frozenset[str] = frozenset({
    "", "y", "yes", "yeah", "yea", "yep", "yup", "ya", "yah", "sure", "ok",
    "okay", "k", "yes please", "go", "go on", "go ahead", "please", "do it",
    "let's go", "lets go", "absolutely", "definitely",
})


def _read_yes(prompt: str, input_fn=input) -> bool:
    """Read a [Y/n] reply, counting any natural affirmative (see `_AFFIRMATIVES`).

    Returns False on EOF so a non-interactive stream never barrels into a lesson
    uninvited.
    """
    try:
        return input_fn(prompt).strip().lower() in _AFFIRMATIVES
    except EOFError:
        return False


def _refresher_note(thread: "learning.Thread") -> str:
    """Pitching guidance for re-teaching a thread the learner already explored.

    Tells the teaching prompt to acknowledge the prior visit and build on it, and
    — when last time's offer named a connection — what they tied it to before.
    """
    conn = ""
    if thread.offer and thread.offer.strip().upper() != NO_CONNECTION_SENTINEL:
        conn = f' Last time it was framed as: "{thread.offer.strip()}".'
    return (
        f'This is a REFRESHER — the learner already explored this thread (as '
        f'"{thread.topic}", at the \'{thread.level}\' level).{conn} Briefly '
        f"acknowledge they've seen it before and build on it rather than teaching "
        f"it cold from scratch."
    )


def _prompt_decision(offer: str, input_fn=input, revisit: "learning.Thread | None" = None) -> bool:
    """Ask whether to learn the thread, defaulting to yes. Returns accepted.

    When `revisit` is a previously-covered thread, the learner is told they've
    seen this before and offered a refresher. Otherwise, when `offer` is
    non-empty (a genuine connection to a past lesson) it's shown as the
    invitation; failing both, the prompt is the plain offer of a full
    walkthrough. Accepts any natural affirmative (see `_AFFIRMATIVES`), not just
    "y"/"yes", so a casual "sure" or "yeah" doesn't silently read as a decline.
    """
    print("\n" + "=" * 60)
    if revisit is not None:
        print(
            f"↩  You've explored this before — taught as \"{revisit.topic}\" "
            f"at the '{revisit.level}' level."
        )
        prompt = "Want a refresher? [Y/n] "
    elif offer:
        print(f"💡 {offer}")
        prompt = "\nLearn this? [Y/n] "
    else:
        prompt = "Want me to walk through how it works in depth? [Y/n] "
    return _read_yes(prompt, input_fn)


def _prompt_redirect(input_fn=input) -> str:
    """On a decline, ask what the learner would rather learn. Returns the new
    topic, or "" to stop — so declining never dead-ends."""
    print("\nNo problem — what would you like to learn instead?")
    try:
        return input_fn("(type a topic, or press Enter to stop) > ").strip()
    except EOFError:
        return ""


def _run_followups(
    query: str,
    hits: list[Hit],
    offer: str,
    teaching: str,
    level: str,
    prior: str,
    model: str,
    input_fn,
    note: str = "",
) -> tuple[str, str, bool]:
    """Let the learner ask follow-ups and re-pitch the level until they're done.

    Returns the (possibly re-taught) lesson text, the level in effect at exit, and
    whether the learner actually asked at least one follow-up (an engagement
    signal for the learning memory; re-pitching the level alone doesn't count).
    Typing `level <tier>` re-teaches the same thread at a new altitude; a blank
    line or 'done' exits.
    """
    print("\n" + "-" * 60)
    print(f"You're learning at the '{level}' level.")
    print("Ask a follow-up in plain English (\"what does X mean?\", \"simpler?\"),")
    print(f"type 'level {'|'.join(LEVELS)}' to re-pitch it,")
    print("or press Enter / type 'done' to move on.")

    asked = False
    while True:
        try:
            line = input_fn("\n> ").strip()
        except EOFError:
            break
        if line == "" or line.lower() in {"done", "exit", "quit"}:
            break

        if line.lower().startswith("level"):
            parts = line.split()
            new_level = parts[1].lower() if len(parts) > 1 else ""
            if new_level not in LEVEL_GUIDANCE:
                print(f"Pick a level: {', '.join(LEVELS)}.")
            elif new_level == level:
                print(f"(Already learning at the '{level}' level.)")
            else:
                level = new_level
                print(f"\nRe-teaching at the '{level}' level.")
                print("-" * 60)
                teaching = teach_thread(
                    query, hits, offer, level=level, prior=prior, model=model,
                    note=note,
                )
                print(teaching)
            continue

        asked = True
        print("\n" + answer_followup(line, hits, teaching, level=level, model=model))

    return teaching, level, asked


def _run_screen_followups(
    frame: "capture.Frame",
    hits: list[Hit],
    lesson: str,
    level: str,
    prior: str,
    model: str,
    input_fn,
) -> None:
    """Let the learner question the highlighted selection until they're done.

    The screen twin of `_run_followups`: every follow-up is answered against the
    SAME `frame` + repo `hits` already in hand (`answer_screen_followup`) — the
    selection is never re-captured. Typing `level <tier>` re-teaches the same
    selection from the same screenshot at a new altitude; a blank line, 'done',
    EOF, or Ctrl-C exits cleanly. `look` records nothing, so there's no
    engagement signal or final level to return — the loop just runs and ends.
    """
    print("\n" + "-" * 60)
    print(f"You're learning at the '{level}' level.")
    print('Ask a follow-up about the highlighted code ("what does X do?", "why this way?"),')
    print(f"type 'level {'|'.join(LEVELS)}' to re-pitch it,")
    print("or press Enter / type 'done' to move on.")

    while True:
        try:
            line = input_fn("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()  # land the cursor on a fresh line after ^C / EOF
            break
        if line == "" or line.lower() in {"done", "exit", "quit"}:
            break

        if line.lower().startswith("level"):
            parts = line.split()
            new_level = parts[1].lower() if len(parts) > 1 else ""
            if new_level not in LEVEL_GUIDANCE:
                print(f"Pick a level: {', '.join(LEVELS)}.")
            elif new_level == level:
                print(f"(Already learning at the '{level}' level.)")
            else:
                level = new_level
                print(f"\nRe-teaching the selection at the '{level}' level.")
                print("-" * 60)
                lesson = teach_from_screen(
                    frame, hits=hits, level=level, prior=prior, model=model,
                )
                print(lesson)
            continue

        print(
            "\n"
            + answer_screen_followup(line, frame, hits, lesson, level=level, model=model)
        )


def _run_comprehension(
    query: str,
    hits: list[Hit],
    lesson: str,
    level: str,
    model: str,
    input_fn,
) -> bool:
    """Offer an optional comprehension engagement and discuss the attempt.

    Returns whether the learner actually attempted it (an engagement signal for
    the learning memory); declining or skipping returns False.
    """
    print("\n" + "=" * 60)
    try:
        reply = input_fn("Want a quick way to make this stick? [y/N] ").strip().lower()
    except EOFError:
        reply = ""
    if reply not in {"y", "yes"}:
        print("\nNo worries — keep exploring.")
        return False

    challenge = comprehension_challenge(query, hits, lesson, level=level, model=model)
    print("\n" + challenge)
    try:
        response = input_fn("\nYour take (Enter to skip): ").strip()
    except EOFError:
        response = ""
    if not response:
        print("\nAll good — no pressure.")
        return False

    print(
        "\n"
        + evaluate_response(challenge, response, hits, lesson, level=level, model=model)
    )
    return True


def _deliver_lesson(
    query: str,
    hits: list[Hit],
    offer: str,
    level: str,
    prior: str,
    model: str,
    input_fn,
    note: str = "",
) -> tuple[str, str, bool]:
    """Teach `query` (grounded in `hits`), then run follow-ups and comprehension.

    Shared by the accept, on-decline redirect, and nudge paths. `note` is extra
    pitching guidance (e.g. a refresher acknowledgment) passed through to the
    teaching call. Returns the final lesson text, the level in effect at exit,
    and whether the learner engaged.
    """
    opener = "let's revisit it" if note else "let's walk through it"
    print(f"\nGreat — {opener} (at the '{level}' level).")
    print("-" * 60)
    teaching = teach_thread(
        query, hits, offer, level=level, prior=prior, model=model, note=note,
    )
    print(teaching)

    teaching, level, asked = _run_followups(
        query, hits, offer, teaching, level, prior, model, input_fn, note=note,
    )
    attempted = _run_comprehension(query, hits, teaching, level, model, input_fn)
    return teaching, level, asked or attempted


def _run_nudges(
    query: str,
    hits: list[Hit],
    lesson: str,
    level: str,
    state: learning.LearningState,
    state_path: str | None,
    k: int,
    db_dir: str,
    model: str,
    input_fn,
) -> str:
    """After a lesson, suggest related unexplored threads until the learner stops.

    Each round retrieves neighbors of the last lesson, filters out everything
    already shown or covered, and asks for up to MAX_NUDGES grounded
    suggestions. The learner picks one by number, types their own topic, or
    presses Enter to stop — always their call. An accepted nudge runs the full
    lesson flow (teach, follow-ups, comprehension), is recorded to the learning
    memory, and then fresh nudges follow from the new lesson. Returns the level
    in effect at exit.
    """
    while True:
        seen = {h.id for h in hits}
        seen.update(c for t in state.threads for c in t.citations)
        candidates = _next_candidates(query, db_dir=db_dir, exclude=seen)
        suggestions = (
            suggest_next_threads(
                query, lesson, state.prior_brief(), candidates, model=model
            )
            if candidates
            else []
        )
        if not suggestions:
            print("\nThat thread's all wrapped up — run `explore` again whenever")
            print("something else catches your eye.")
            return level

        print("\n" + "=" * 60)
        print("Curious where to go next? You might like:")
        for i, suggestion in enumerate(suggestions, 1):
            print(f"  {i}. {suggestion}")
        try:
            line = input_fn(
                "\n(type a number, your own topic, or press Enter to stop) > "
            ).strip()
        except EOFError:
            return level
        if not line:
            print("\nHappy exploring — run `explore` again any time.")
            return level

        query = (
            suggestions[int(line) - 1]
            if line.isdigit() and 1 <= int(line) <= len(suggestions)
            else line
        )
        hits = retrieve(query, k=k, db_dir=db_dir)
        _show_hits(query, hits, lead="\n")
        prior = state.prior_brief(exclude_citations=[h.id for h in hits])
        lesson, level, engaged = _deliver_lesson(
            query, hits, "", level, prior, model, input_fn,
        )
        state.record_thread(
            query=query,
            offer="",
            citations=[h.id for h in hits],
            level=level,
            engaged=engaged,
        )
        state.level = level
        learning.save_state(state, state_path)


def explore(
    query: str,
    k: int = DEFAULT_TOP_K,
    db_dir: str = "chroma_db",
    model: str = OFFER_MODEL,
    level: str | None = None,
    decide: bool = True,
    input_fn=input,
    state_path: str | None = DEFAULT_STATE_PATH,
) -> ExploreResult:
    """Run the show → offer → teach beat for `query`, calibrated and remembered.

    Loads learning state from `state_path` (set None to disable persistence) and
    resolves the level as: explicit `level` → the saved level → `beginner`.
    Retrieves and prints the relevant chunks, gives a short direct answer to the
    question up front, then asks whether to go deeper. The invitation is plain by
    default; it becomes a specific offer only when the learning memory holds a
    genuine connection to a past lesson, or a refresher prompt when this topic was
    already explored (recognized via `find_revisit`). On accept it teaches the
    thread the learner searched for — building on the prior visit when it's a
    refresher; on decline it asks what they'd rather learn and teaches that
    instead (never a dead end). The lesson is
    handed a brief of previously covered threads for cross-references, runs the
    in-lesson follow-up loop, and offers an optional comprehension invitation. The
    covered thread and final level are then saved. After the lesson, related
    unexplored threads are suggested (`_run_nudges`) until the learner stops;
    each accepted nudge is taught and remembered too. Returns an `ExploreResult`
    describing the original thread.
    """
    state = learning.load_state(state_path)
    level = level or state.level or DEFAULT_LEVEL
    _check_level(level)

    if state.threads:
        print(
            f"↩  Resuming — {len(state.threads)} thread(s) explored so far, "
            f"learning at the '{level}' level."
        )

    hits = retrieve(query, k=k, db_dir=db_dir)
    _show_hits(query, hits)

    # Recognize a re-asked topic as review, not new ground (feature 2): if this
    # retrieval re-treads a thread already taught, we offer a refresher.
    revisit = state.find_revisit([h.id for h in hits])
    prior = state.prior_brief(exclude_citations=[h.id for h in hits])
    # A specific connection offer is made ONLY for new ground that ties to a past
    # lesson; a revisit gets the refresher prompt instead, so skip the offer call.
    offer = (
        ""
        if revisit is not None
        else (generate_offer(query, hits, prior=prior, model=model) if prior else "")
    )

    if not decide:
        print("\n" + "=" * 60)
        if revisit is not None:
            print("↩  You've explored this before — want a refresher? [Y/n]")
        else:
            print(f"💡 {offer}" if offer else "Want to learn this? [Y/n]")
        return ExploreResult(
            query=query, hits=hits, offer=offer, accepted=None, level=level,
        )

    # Model-judged relevance — hung on the same beat as the short answer. Let the
    # model decide whether the retrieved code actually answers the question and
    # say so honestly when it doesn't, replacing the old distance-threshold
    # heads-up that couldn't tell "feature absent" from "worded differently".
    rel_note = _relevance_note(judge.judge_relevance(query, hits, model=model), hits)
    if rel_note:
        print("\n" + rel_note)

    # Answer the question itself up front (feature 1), before offering the deeper
    # lesson — so a "how does X work?" ask is always answered, not gated behind
    # accepting a walkthrough.
    print("\nShort answer")
    print("-" * 60)
    print(quick_answer(query, hits, level=level, model=model))

    note = _refresher_note(revisit) if revisit is not None else ""

    if not _prompt_decision(offer, input_fn=input_fn, revisit=revisit):
        redirect = _prompt_redirect(input_fn=input_fn)
        if not redirect:
            print("\nNo problem — keep exploring.")
            print("Run `explore` again with whatever catches your eye.")
            return ExploreResult(
                query=query, hits=hits, offer=offer, accepted=False, level=level,
            )
        # Teach what they'd rather learn instead, grounded in its own code —
        # which may itself be a topic they've covered, so re-check for a revisit.
        query = redirect
        hits = retrieve(query, k=k, db_dir=db_dir)
        _show_hits(query, hits)
        offer = ""
        revisit = state.find_revisit([h.id for h in hits])
        note = _refresher_note(revisit) if revisit is not None else ""
        prior = state.prior_brief(exclude_citations=[h.id for h in hits])

    teaching, level, engaged = _deliver_lesson(
        query, hits, offer, level, prior, model, input_fn, note=note,
    )

    state.record_thread(
        query=query,
        offer=offer,
        citations=[h.id for h in hits],
        level=level,
        engaged=engaged,
    )
    state.level = level
    learning.save_state(state, state_path)

    # Step 5 — suggest what to explore next, until the learner says stop.
    final_level = _run_nudges(
        query, hits, teaching, level, state, state_path, k, db_dir, model, input_fn,
    )
    if final_level != state.level:
        state.level = final_level
        learning.save_state(state, state_path)

    return ExploreResult(
        query=query,
        hits=hits,
        offer=offer,
        accepted=True,
        teaching=teaching,
        level=final_level,
    )


def _related_chunks(
    frame: "capture.Frame", k: int, db_dir: str, model: str
) -> tuple[list[Hit], str]:
    """Pull repo chunks related to the highlighted selection in `frame`.

    Step 3.5's fusion: transcribe just the highlighted code, then use it as a
    retrieval query into the ingested store so the lesson can reach off-screen
    context. Returns the retrieved hits plus a one-line status note for the
    learner. Degrades cleanly — an empty/missing store (or no visible selection)
    yields `[]` and a note explaining the lesson is screen-only — so `look` never
    crashes just because nothing has been ingested.
    """
    selection = transcribe_selection(frame, model=model)
    if not selection.strip():
        # No highlight detected — nothing to query; teaching will ask for one.
        return [], ""
    try:
        hits = retrieve(selection, k=k, db_dir=db_dir)
    except ValueError:
        return [], (
            "No ingested repo found, so this lesson is screen-only (just what's "
            "visible). For full-project context, run "
            "`python -m repo_whisperer ingest <path-to-repo>` first."
        )
    if not hits:
        return [], "The ingested repo had no related code, so this lesson is screen-only."
    plural = "chunk" if len(hits) == 1 else "chunks"
    return hits, f"Pulled {len(hits)} related {plural} from the ingested repo for context."


def look(
    mode: str = "window",
    window_id: int | None = None,
    exclude: frozenset[str] | set[str] | tuple = (),
    level: str | None = None,
    k: int = DEFAULT_TOP_K,
    db_dir: str = "chroma_db",
    model: str = TEACH_MODEL,
    state_path: str | None = DEFAULT_STATE_PATH,
    keep: bool = False,
    input_fn=input,
) -> str | None:
    """Look at the code on the learner's screen and teach the highlighted part.

    The opt-in, announced way to give the tutor sight (Step 3), now repo-aware
    (Step 3.5). It runs only when invoked and takes exactly ONE screenshot,
    announced before it happens — never continuous, never in the background. By
    default it captures the active editor window (reusing the Step 2 capture
    module, last-non-tutor-window rule); `mode="screen"` grabs the whole display
    and `window_id` pins an exact window.

    It then reads the highlighted selection, uses it to retrieve related chunks
    from the ingested repo (`_related_chunks`), and teaches the selection grounded
    in BOTH the screenshot and that off-screen context, citing `path:start-end`
    keys (`teach_from_screen`). With no repo ingested it falls back to screen-only
    teaching with a clear note. After the first lesson it opens a follow-up loop
    (`_run_screen_followups`) so the learner can question that same selection in a
    back-and-forth — every follow-up reuses the one screenshot + the chunks pulled
    at capture time, never a fresh capture — until they exit. Returns the initial
    lesson text — or None if the capture failed (permission or no window), having
    printed a human message. The level is read from `state_path` for consistency
    with `explore`; nothing is written (a screenshot lesson has no chunk citations
    to record as a thread).
    """
    state = learning.load_state(state_path)
    level = level or state.level or DEFAULT_LEVEL
    _check_level(level)

    where = (
        "your whole screen"
        if mode == "screen"
        else (f"window {window_id}" if window_id is not None else "your active editor window")
    )
    print(f"📸 Taking one screenshot of {where} to look at the code with you.")
    print("   (Nothing else is captured — this is the only frame, taken right now.)")

    try:
        with capture.captured_frame(
            mode=mode, exclude=set(exclude), window_id=window_id, keep=keep,
        ) as frame:
            print(f"\nCaptured {capture.describe_frame(frame)}.")
            print("Reading the highlighted selection…")
            hits, note = _related_chunks(frame, k=k, db_dir=db_dir, model=model)
            if note:
                print(note)
            print("Teaching the highlighted part…")
            prior = state.prior_brief()
            lesson = teach_from_screen(
                frame, hits=hits, level=level, prior=prior, model=model,
            )
            print("\n" + "-" * 60)
            print(lesson)

            # The selection is captured ONCE; now turn the lesson into a
            # back-and-forth. Every follow-up reuses this same frame + hits with
            # no re-capture — which is exactly why the loop runs INSIDE
            # `captured_frame`: `image_block` re-reads the PNG on each question,
            # so the file must stay on disk until the learner is done.
            _run_screen_followups(frame, hits, lesson, level, prior, model, input_fn)
    except capture.PermissionDeniedError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return None
    except capture.CaptureError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return None

    return lesson


def assist_with_attempt(
    goal: str,
    attempt: str,
    verdict: "judge.Verdict",
    hits: list[Hit],
    filename: str = "",
    level: str = DEFAULT_LEVEL,
    model: str = TEACH_MODEL,
) -> str:
    """Help the learner close the gap in their practice `attempt`, at `level`.

    The visible half of `check`: the judge's `verdict` is passed in as a PRIVATE
    signal — it locates where the learner's understanding is, and this call
    translates it into patient, gap-closing assistance grounded in the real
    codebase (`hits`, cited by `path:start-end`). The learner never sees the
    verdict itself. Raises ValueError on an unknown level or a missing API key.
    """
    _check_level(level)
    comparison = (
        f"Related excerpts from the REAL codebase (cite by `path:start-end`):"
        f"\n\n{build_context(hits)}"
        if hits
        else "(No real-codebase excerpts available for comparison.)"
    )
    private = (
        f"PRIVATE assessment of the attempt — for your eyes only, never to be "
        f"shown or mentioned:\n"
        f"- verdict: {verdict.verdict} (confidence: {verdict.confidence})\n"
        f"- reason: {verdict.reason}\n"
        f"- missing: {verdict.missing or '(nothing — the attempt demonstrates it)'}"
    )
    file_note = f" (their file `{filename}`)" if filename else ""
    user_message = (
        f"The learner was practicing: {goal}\n\n"
        f"Their practice attempt, read from disk{file_note}:\n\n{attempt}\n\n"
        f"{comparison}\n\n"
        f"{private}\n\n"
        f"Now assist them: help them close the gap between this attempt and "
        f"what they were going for, at their level."
    )
    system = f"{CHECK_ASSIST_SYSTEM_PROMPT}\n\n{_level_block(level)}"
    return _ask_model(
        system, user_message, MAX_TEACH_TOKENS, model,
        max_continuations=MAX_TEACH_CONTINUATIONS,
    )


def _read_attempt(file: str) -> str | None:
    """Read the learner's practice file from disk, or None with a human message.

    The disk-for-truth rule: what gets judged and assisted is exactly what's in
    the file, never a screenshot of it. Every wrong-file case (missing, a
    directory, binary, empty) gets a plain explanation instead of a traceback —
    naming the wrong file must never feel like a crash.
    """
    path = Path(file)
    if path.is_dir():
        print(f"error: {file} is a directory — point `check` at the practice "
              f"file you wrote.", file=sys.stderr)
        return None
    if not path.exists():
        print(f"error: {file} doesn't exist. Point `check` at the practice "
              f"file you wrote (the path is relative to where you run this).",
              file=sys.stderr)
        return None
    try:
        attempt = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        print(f"error: {file} doesn't look like a text file, so there's no "
              f"code to read in it.", file=sys.stderr)
        return None
    except OSError as exc:
        print(f"error: couldn't read {file}: {exc}", file=sys.stderr)
        return None
    if not attempt.strip():
        print(f"{file} is empty so far — write your attempt in it, then run "
              f"`check` again and I'll take a look.")
        return None
    if len(attempt) > MAX_ATTEMPT_CHARS:
        print(f"(That file is big for a practice attempt — reading just the "
              f"first {MAX_ATTEMPT_CHARS:,} characters.)")
        attempt = attempt[:MAX_ATTEMPT_CHARS] + "\n… (truncated)"
    return attempt


def check(
    file: str,
    level: str | None = None,
    k: int = DEFAULT_TOP_K,
    db_dir: str = "chroma_db",
    model: str = TEACH_MODEL,
    state_path: str | None = DEFAULT_STATE_PATH,
) -> str | None:
    """Look at code the learner wrote and help them get it right (Step 4).

    The verification path that completes teach → verify — pointed assist-first:
    the learner names their practice file, `check` re-reads it straight from
    disk (never a screenshot), works out what they were going for, and helps
    them close the gap at their level. Underneath, `judge.judge_attempt` runs
    the attempt through the shared verdict schema to locate where their
    understanding is; that verdict stays PRIVATE — what prints is patient
    assistance, never a grade.

    What they were practicing defaults to the most recently taught thread in
    the learning state, announced as an assumption; with no lessons recorded it
    is inferred from the attempt itself. The attempt's own text is the retrieval
    query into the ingested store, so the help can cite how the real code does
    it (`path:start-end`), with the same graceful screen-only-style fallback as
    `look` when nothing is ingested. Reads the saved level; writes nothing —
    recording results into the learning memory is Step 5. Returns the assistance
    text, or None when the file couldn't be read (having printed why).
    """
    state = learning.load_state(state_path)
    level = level or state.level or DEFAULT_LEVEL
    _check_level(level)

    attempt = _read_attempt(file)
    if attempt is None:
        return None

    # The reference for "what were they going for": the most recently taught
    # thread (by timestamp — re-teaching refreshes a record in place, so list
    # order alone isn't recency). Announced, since it's an assumption.
    thread = max(state.threads, key=lambda t: t.timestamp) if state.threads else None
    if thread is not None:
        goal = (
            f'the thread they most recently learned: "{thread.topic}" '
            f'(their original question: "{thread.query}")'
        )
        print(f'🔎 Checking {file} against your latest lesson: "{thread.topic}"')
        print("   (assuming that's what you're practicing — reading the file "
              "fresh from disk)")
    else:
        goal = (
            "no lesson is recorded, so infer from the attempt itself what "
            "pattern they appear to be practicing"
        )
        print(f"🔎 Checking {file} — no lessons recorded yet, so I'll work out "
              f"what you're going for from the code itself.")

    # The attempt is its own retrieval query: pull how the real repo does the
    # same thing, so the help can compare and cite. Degrades cleanly when
    # nothing is ingested — the attempt alone is still judgeable evidence.
    try:
        hits = retrieve(attempt, k=k, db_dir=db_dir)
    except ValueError:
        hits = []
        print("No ingested repo found, so I can't compare against the real "
              "code — run `python -m repo_whisperer ingest <path-to-repo>` "
              "for grounded, cited help.")
    if hits:
        plural = "chunk" if len(hits) == 1 else "chunks"
        print(f"Pulled {len(hits)} related {plural} from the real repo to "
              f"compare against.")

    print("Looking at what you were going for…")
    verdict = judge.judge_attempt(goal, attempt, hits, filename=file, model=model)
    assistance = assist_with_attempt(
        goal, attempt, verdict, hits, filename=file, level=level, model=model,
    )
    print("\n" + "-" * 60)
    print(assistance)
    print("\n" + "-" * 60)
    print(f"Tweak the file and run `check {file}` again whenever you're ready.")
    return assistance


def _format_repo_map(files: list[tuple[str, int, int]]) -> str:
    """Render the repo map for the guide prompt, one file per line.

    Repos beyond `MAX_MAP_FILES` keep their largest files (the likelier
    landmarks) plus a count of what was elided, so the prompt stays bounded.
    """
    elided = ""
    if len(files) > MAX_MAP_FILES:
        kept = sorted(files, key=lambda f: f[1], reverse=True)[:MAX_MAP_FILES]
        elided = f"\n… and {len(files) - MAX_MAP_FILES} more, smaller files."
        files = sorted(kept)
    lines = [
        f"{path} — {line_count} lines ({chunks} chunk{'s' if chunks != 1 else ''})"
        for path, line_count, chunks in files
    ]
    return "\n".join(lines) + elided


def answer_side_question(
    question: str,
    files: list[tuple[str, int, int]],
    hits: list[Hit],
    prior: str = "",
    level: str = DEFAULT_LEVEL,
    model: str = TEACH_MODEL,
) -> str:
    """Answer a whole-repo / learning-path question, at `level`.

    The guide voice: `files` is the map of everything in the ingested store
    (the bird's-eye view no single retrieval can give), `hits` are excerpts
    retrieved for the question so behavior claims stay grounded and citable,
    and `prior` keeps the advice building on what's already covered. Raises
    ValueError on an unknown level or a missing API key.
    """
    _check_level(level)
    excerpts = (
        f"Code excerpts retrieved for their question (cite by `path:start-end`):"
        f"\n\n{build_context(hits)}"
        if hits
        else "(No excerpts retrieved.)"
    )
    user_message = (
        f'The learner\'s side question: "{question}"\n\n'
        f"The MAP of every file in the ingested repo:\n\n{_format_repo_map(files)}\n\n"
        f"{excerpts}\n\n"
        f"Threads you've already taught them:\n{prior or '(none yet)'}\n\n"
        f"Answer their question with concrete, level-fitted guidance."
    )
    system = f"{GUIDE_SYSTEM_PROMPT}\n\n{_level_block(level)}"
    return _ask_model(system, user_message, MAX_GUIDE_TOKENS, model)


def guide(
    question: str = "",
    level: str | None = None,
    k: int = DEFAULT_TOP_K,
    db_dir: str = "chroma_db",
    model: str = TEACH_MODEL,
    state_path: str | None = DEFAULT_STATE_PATH,
) -> str:
    """Answer a side question about the whole repo or the learning path.

    The learner's escape hatch from thread-scoped tutoring: `explore`/`look`/
    `check` all work one thread at a time, but "which file should I learn
    first for my level?" needs the whole repo in view. `guide` hands the tutor
    a map of every file in the ingested store plus excerpts retrieved for the
    question, the saved level, and the covered threads — and answers with
    concrete, named-file recommendations that end in a ready-to-run `explore`
    query. An empty `question` asks the default where-should-I-start question.
    Reads the learning state; writes nothing. Raises ValueError when nothing
    has been ingested (a whole-repo question needs a repo).
    """
    state = learning.load_state(state_path)
    level = level or state.level or DEFAULT_LEVEL
    _check_level(level)
    question = question.strip() or DEFAULT_GUIDE_QUESTION

    files = repo_map(db_dir)
    hits = retrieve(question, k=k, db_dir=db_dir)
    plural = "file" if len(files) == 1 else "files"
    print(f"🧭 Looking across the whole ingested repo ({len(files)} {plural}) …")

    reply = answer_side_question(
        question, files, hits,
        prior=state.prior_brief(), level=level, model=model,
    )
    print("\n" + "-" * 60)
    print(reply)
    return reply


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m repo_whisperer.tutor",
        description="Show relevant code, then offer to teach how it works.",
    )
    parser.add_argument("query", help="what you want to see, in quotes")
    parser.add_argument(
        "--db", default="chroma_db", metavar="DIR",
        help="ChromaDB storage directory (default: chroma_db)",
    )
    parser.add_argument(
        "-k", "--top-k", type=int, default=DEFAULT_TOP_K, dest="top_k",
        help=f"number of chunks to retrieve (default: {DEFAULT_TOP_K})",
    )
    parser.add_argument(
        "--level", choices=LEVELS, default=None,
        help=f"teaching altitude (default: your saved level, else {DEFAULT_LEVEL})",
    )
    parser.add_argument(
        "--state", default=DEFAULT_STATE_PATH, metavar="FILE",
        help=f"learning-state file (default: {DEFAULT_STATE_PATH})",
    )
    args = parser.parse_args(argv[1:])

    if not args.query.strip():
        print("error: query is empty", file=sys.stderr)
        return 1

    try:
        explore(
            args.query, k=args.top_k, db_dir=args.db,
            level=args.level, state_path=args.state,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
