"""Phase 2 — the tutoring layer, built on top of the Phase 1 engine.

The flow:

1. **Show me.** The learner asks where something is. We reuse Phase 1 retrieval
   (`answer.retrieve`) to pull the most relevant chunks and SHOW them — the
   actual code, with `path:start-end` citations.
2. **Offer the doorway.** Having seen the code, the learner may get curious about
   how it works. So we generate ONE specific, tempting invitation to be taught —
   derived from the actual retrieved code, naming a real function/concept (e.g.
   "Want me to walk you through how `initRails` builds the two rail meshes?").
   The learner accepts or declines; we capture the choice.
3. **Teach it — at the learner's altitude.** If the learner accepts, we teach
   that thread (`teach_thread`), pitched at their chosen `level`
   (beginner/intermediate/advanced). The SAME grounded, cited content is
   explained with more or less assumed vocabulary; at `beginner`, jargon is
   defined in plain English the first time it appears. Then the learner can ask
   in-lesson follow-ups (and re-pitch the level on the fly), and finally take an
   optional, light comprehension invitation.

Still to come: learning memory across threads (Step 4) and "what's next" nudges
(Step 5).

Run standalone:

    python -m repo_whisperer.tutor "<where-is-x query>" [--db DIR] [-k N] [--level L]
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

from repo_whisperer.answer import (
    ANSWER_MODEL,
    DEFAULT_TOP_K,
    Hit,
    _client,
    build_context,
    retrieve,
)

# The teaching offer is short by design — a single tempting sentence.
MAX_OFFER_TOKENS: int = 200

# A taught thread is a few focused paragraphs — longer than an offer or a flat
# answer, but still bounded; we're explaining one thread, not the whole repo.
MAX_TEACH_TOKENS: int = 1536

# Follow-ups, challenges, and evaluations are shorter, focused replies.
MAX_FOLLOWUP_TOKENS: int = 800
MAX_CHALLENGE_TOKENS: int = 300
MAX_EVAL_TOKENS: int = 600

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


def _ask_model(system: str, user: str, max_tokens: int, model: str) -> str:
    """Single-turn Anthropic call returning the concatenated text blocks."""
    client = _client()
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(
        block.text for block in response.content if block.type == "text"
    ).strip()


def generate_offer(query: str, hits: list[Hit], model: str = OFFER_MODEL) -> str:
    """Ask the model for one specific teaching invitation grounded in `hits`.

    The offer names a real identifier from the retrieved code so it tempts the
    learner toward a concrete mechanism rather than a generic "want to learn
    more?". Raises ValueError (via `_client`) if the API key is missing.
    """
    context = build_context(hits)
    user_message = (
        f'The learner asked: "{query}"\n\n'
        f"These are the code excerpts they were just shown:\n\n{context}\n\n"
        f"Offer one specific, tempting invitation to teach them how this works."
    )
    return _ask_model(OFFER_SYSTEM_PROMPT, user_message, MAX_OFFER_TOKENS, model)


def teach_thread(
    query: str,
    hits: list[Hit],
    offer: str,
    level: str = DEFAULT_LEVEL,
    model: str = TEACH_MODEL,
) -> str:
    """Teach the accepted thread, grounded in `hits`, cited, and pitched at `level`.

    Explains how the retrieved code works in a teaching voice, building
    understanding step by step and citing `path:start-end` keys. `offer` keeps
    the lesson on the specific thread the learner accepted; `level` controls only
    the assumed vocabulary, not the facts. Raises ValueError on an unknown level
    or a missing API key.
    """
    _check_level(level)
    context = build_context(hits)
    user_message = (
        f'The learner originally asked: "{query}"\n\n'
        f"They accepted this teaching offer:\n{offer}\n\n"
        f"These are the code excerpts they were shown:\n\n{context}\n\n"
        f"Now teach this thread: walk them through how it works, grounded in "
        f"these excerpts and citing the `path:start-end` keys as you go."
    )
    system = f"{TEACH_SYSTEM_PROMPT}\n\n{_level_block(level)}"
    return _ask_model(system, user_message, MAX_TEACH_TOKENS, model)


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


def _prompt_decision(offer: str, input_fn=input) -> bool:
    """Show the offer and capture the learner's accept/decline. Returns accepted."""
    print("\n" + "=" * 60)
    print(f"💡 {offer}")
    try:
        reply = input_fn("\nLearn this? [y/N] ").strip().lower()
    except EOFError:
        # No interactive input available — treat as "not now", don't crash.
        reply = ""
    return reply in {"y", "yes"}


def _run_followups(
    query: str,
    hits: list[Hit],
    offer: str,
    teaching: str,
    level: str,
    model: str,
    input_fn,
) -> tuple[str, str]:
    """Let the learner ask follow-ups and re-pitch the level until they're done.

    Returns the (possibly re-taught) lesson text and the level in effect at exit.
    Typing `level <tier>` re-teaches the same thread at a new altitude; a blank
    line or 'done' exits.
    """
    print("\n" + "-" * 60)
    print(f"You're learning at the '{level}' level.")
    print("Ask a follow-up in plain English (\"what does X mean?\", \"simpler?\"),")
    print(f"type 'level {'|'.join(LEVELS)}' to re-pitch it,")
    print("or press Enter / type 'done' to move on.")

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
                teaching = teach_thread(query, hits, offer, level=level, model=model)
                print(teaching)
            continue

        print("\n" + answer_followup(line, hits, teaching, level=level, model=model))

    return teaching, level


def _run_comprehension(
    query: str,
    hits: list[Hit],
    lesson: str,
    level: str,
    model: str,
    input_fn,
) -> None:
    """Offer an optional comprehension engagement and discuss the attempt."""
    print("\n" + "=" * 60)
    try:
        reply = input_fn("Want a quick way to make this stick? [y/N] ").strip().lower()
    except EOFError:
        reply = ""
    if reply not in {"y", "yes"}:
        print("\nNo worries — keep exploring.")
        return

    challenge = comprehension_challenge(query, hits, lesson, level=level, model=model)
    print("\n" + challenge)
    try:
        response = input_fn("\nYour take (Enter to skip): ").strip()
    except EOFError:
        response = ""
    if not response:
        print("\nAll good — no pressure.")
        return

    print(
        "\n"
        + evaluate_response(challenge, response, hits, lesson, level=level, model=model)
    )


def explore(
    query: str,
    k: int = DEFAULT_TOP_K,
    db_dir: str = "chroma_db",
    model: str = OFFER_MODEL,
    level: str = DEFAULT_LEVEL,
    decide: bool = True,
    input_fn=input,
) -> ExploreResult:
    """Run the show → offer → teach beat for `query`, calibrated to `level`.

    Retrieves and prints the relevant chunks, generates and prints a specific
    teaching offer, and (when `decide`) captures the learner's accept/decline. On
    accept it teaches the thread at `level`, runs an in-lesson follow-up loop
    (where the level can be re-pitched), and offers an optional comprehension
    invitation. Returns an `ExploreResult`.
    """
    _check_level(level)
    hits = retrieve(query, k=k, db_dir=db_dir)
    print(format_hits(query, hits))

    offer = generate_offer(query, hits, model=model)

    if not decide:
        print("\n" + "=" * 60)
        print(f"💡 {offer}")
        return ExploreResult(
            query=query, hits=hits, offer=offer, accepted=None, level=level,
        )

    accepted = _prompt_decision(offer, input_fn=input_fn)
    if not accepted:
        print("\nNo problem — keep exploring.")
        print("Run `explore` again with whatever catches your eye.")
        return ExploreResult(
            query=query, hits=hits, offer=offer, accepted=False, level=level,
        )

    print(f"\nGreat — let's walk through it (at the '{level}' level).")
    print("-" * 60)
    teaching = teach_thread(query, hits, offer, level=level, model=model)
    print(teaching)

    teaching, level = _run_followups(
        query, hits, offer, teaching, level, model, input_fn,
    )
    _run_comprehension(query, hits, teaching, level, model, input_fn)

    return ExploreResult(
        query=query,
        hits=hits,
        offer=offer,
        accepted=True,
        teaching=teaching,
        level=level,
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
        "--level", choices=LEVELS, default=DEFAULT_LEVEL,
        help=f"teaching altitude (default: {DEFAULT_LEVEL})",
    )
    args = parser.parse_args(argv[1:])

    if not args.query.strip():
        print("error: query is empty", file=sys.stderr)
        return 1

    try:
        explore(args.query, k=args.top_k, db_dir=args.db, level=args.level)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
