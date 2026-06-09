"""Phase 2 — the tutoring layer, built on top of the Phase 1 engine.

The flow:

1. **Show me.** The learner asks where something is. We reuse Phase 1 retrieval
   (`answer.retrieve`) to pull the most relevant chunks and SHOW them — the
   actual code, with `path:start-end` citations.
2. **Offer the doorway.** Having seen the code, the learner is asked if they want
   to learn it. By default this is a plain invitation ("Want to learn this?") that
   teaches the thread they searched for. ONLY when the learning memory holds a
   genuine connection to a past lesson do we make a SPECIFIC offer that names the
   tie (e.g. "Want to see how this ties into your earlier lesson on `initRails`?").
   The learner accepts or declines. On a decline we don't dead-end — we ask what
   they'd rather learn and teach that instead.
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

from repo_whisperer import learning
from repo_whisperer.answer import (
    ANSWER_MODEL,
    DEFAULT_TOP_K,
    Hit,
    _client,
    build_context,
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

# Follow-ups, challenges, and evaluations are shorter, focused replies.
MAX_FOLLOWUP_TOKENS: int = 800
MAX_CHALLENGE_TOKENS: int = 300
MAX_EVAL_TOKENS: int = 600

# "What's next" nudges are a handful of one-line invitations.
MAX_NUDGE_TOKENS: int = 400

# At most this many next-thread suggestions per nudge.
MAX_NUDGES: int = 3

# How many chunks to pull when hunting for unexplored neighbors of a lesson.
# Wider than a normal retrieval so something usually survives the filtering of
# already-shown and already-covered chunks.
NUDGE_CANDIDATE_K: int = 12

# Cosine distance above which a retrieved chunk is a weak match. Strong hits sit
# well below this (~0.3); when EVERY retrieved chunk is above it, the query
# wording probably doesn't appear in the repo, so we say so plainly instead of
# passing off loosely related code as a real answer.
WEAK_MATCH_DISTANCE: float = 0.6

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
    user: str,
    max_tokens: int,
    model: str,
    max_continuations: int = 0,
) -> str:
    """Anthropic call returning the concatenated text blocks.

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


def teach_thread(
    query: str,
    hits: list[Hit],
    offer: str = "",
    level: str = DEFAULT_LEVEL,
    prior: str = "",
    model: str = TEACH_MODEL,
) -> str:
    """Teach the accepted thread, grounded in `hits`, cited, and pitched at `level`.

    Explains how the retrieved code works in a teaching voice, building
    understanding step by step and citing `path:start-end` keys. The lesson stays
    on the thread the learner searched for (`query`); `offer`, when non-empty,
    carries a connection to a past lesson the lesson can lean into. `level`
    controls only the assumed vocabulary, not the facts. `prior` is an optional
    brief of threads already covered, so the lesson can cross-reference them.
    Raises ValueError on an unknown level or a missing API key.
    """
    _check_level(level)
    context = build_context(hits)
    offer_line = f"They accepted this teaching offer:\n{offer}\n\n" if offer else ""
    user_message = (
        f'The learner originally asked: "{query}"\n\n'
        f"{offer_line}"
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


def _weak_match_note(hits: list[Hit]) -> str:
    """A heads-up when nothing strongly matches the query, else "".

    Retrieval always returns the top-k chunks no matter how weak the match is.
    When every one of them is above WEAK_MATCH_DISTANCE, none is a confident
    hit — usually because the learner's wording doesn't appear in this repo — so
    we flag that and name the closest file as a best guess, rather than letting
    them assume the code shown is really what they asked about.
    """
    if not hits or any(h.distance <= WEAK_MATCH_DISTANCE for h in hits):
        return ""
    closest = min(hits, key=lambda h: h.distance)
    return (
        "\n⚠  Nothing in this repo closely matches your wording, so the code "
        f"above is only loosely related — the closest guess is `{closest.path}`. "
        "Try rephrasing with a term from the codebase if this isn't what you meant."
    )


def _show_hits(query: str, hits: list[Hit], lead: str = "") -> None:
    """Print the retrieved code for `query`, plus a weak-match heads-up if warranted."""
    print(lead + format_hits(query, hits))
    note = _weak_match_note(hits)
    if note:
        print(note)


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


def _prompt_decision(offer: str, input_fn=input) -> bool:
    """Ask whether to learn the thread, defaulting to yes. Returns accepted.

    When `offer` is non-empty (a genuine connection to a past lesson) it's shown
    as the invitation; otherwise the prompt is the plain "Want to learn this?".
    Accepts any natural affirmative (see `_AFFIRMATIVES`), not just "y"/"yes", so
    a casual "sure" or "yeah" doesn't silently get read as a decline.
    """
    print("\n" + "=" * 60)
    if offer:
        print(f"💡 {offer}")
        prompt = "\nLearn this? [Y/n] "
    else:
        prompt = "Want to learn this? [Y/n] "
    try:
        reply = input_fn(prompt).strip().lower()
    except EOFError:
        # No interactive input available — don't barrel into a lesson uninvited.
        return False
    return reply in _AFFIRMATIVES


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
                    query, hits, offer, level=level, prior=prior, model=model
                )
                print(teaching)
            continue

        asked = True
        print("\n" + answer_followup(line, hits, teaching, level=level, model=model))

    return teaching, level, asked


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
) -> tuple[str, str, bool]:
    """Teach `query` (grounded in `hits`), then run follow-ups and comprehension.

    Shared by the accept path and the on-decline redirect path. Returns the final
    lesson text, the level in effect at exit, and whether the learner engaged.
    """
    print(f"\nGreat — let's walk through it (at the '{level}' level).")
    print("-" * 60)
    teaching = teach_thread(query, hits, offer, level=level, prior=prior, model=model)
    print(teaching)

    teaching, level, asked = _run_followups(
        query, hits, offer, teaching, level, prior, model, input_fn,
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
    Retrieves and prints the relevant chunks, then asks whether to learn the
    thread. The invitation is plain by default; it becomes a specific offer only
    when the learning memory holds a genuine connection to a past lesson. On
    accept it teaches the thread the learner searched for; on decline it asks what
    they'd rather learn and teaches that instead (never a dead end). The lesson is
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

    # A specific offer is made ONLY when memory yields a genuine connection;
    # otherwise the prompt is plain and we skip the offer call entirely.
    prior = state.prior_brief(exclude_citations=[h.id for h in hits])
    offer = generate_offer(query, hits, prior=prior, model=model) if prior else ""

    if not decide:
        print("\n" + "=" * 60)
        print(f"💡 {offer}" if offer else "Want to learn this? [Y/n]")
        return ExploreResult(
            query=query, hits=hits, offer=offer, accepted=None, level=level,
        )

    if not _prompt_decision(offer, input_fn=input_fn):
        redirect = _prompt_redirect(input_fn=input_fn)
        if not redirect:
            print("\nNo problem — keep exploring.")
            print("Run `explore` again with whatever catches your eye.")
            return ExploreResult(
                query=query, hits=hits, offer=offer, accepted=False, level=level,
            )
        # Teach what they'd rather learn instead, grounded in its own code.
        query = redirect
        hits = retrieve(query, k=k, db_dir=db_dir)
        _show_hits(query, hits)
        offer = ""
        prior = state.prior_brief(exclude_citations=[h.id for h in hits])

    teaching, level, engaged = _deliver_lesson(
        query, hits, offer, level, prior, model, input_fn,
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
