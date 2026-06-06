"""Phase 2, Step 1 — Show-then-offer-to-teach.

The first tutoring beat, built on top of the Phase 1 engine. The flow has two
parts:

1. **Show me.** The learner asks where something is. We reuse Phase 1 retrieval
   (`answer.retrieve`) to pull the most relevant chunks and SHOW them — the
   actual code, with `path:start-end` citations.
2. **Offer the doorway.** Having seen the code, the learner may get curious about
   how it works. So we generate ONE specific, tempting invitation to be taught —
   derived from the actual retrieved code, naming a real function/concept (e.g.
   "Want me to walk you through how `initRails` builds the two rail meshes?").
   The learner accepts or declines; we capture the choice.

This step deliberately stops at the offer: it shows, offers, and records the
accept/decline. The actual Socratic teaching of an accepted thread is Step 2.

Run standalone:

    python -m repo_whisperer.tutor "<where-is-x query>" [--db DIR] [-k N]
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

# The model that writes the offer. Reuse the Phase 1 answering model.
OFFER_MODEL: str = ANSWER_MODEL

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


@dataclass
class ExploreResult:
    """The outcome of one show-then-offer-to-teach interaction.

    Captures everything Step 2 will need to pick up an accepted thread: the
    learner's query, the chunks they were shown, the offer we made, and whether
    they accepted. `accepted` is None when the decision wasn't collected (e.g.
    `decide=False` for a non-interactive caller).
    """

    query: str
    hits: list[Hit]
    offer: str
    accepted: bool | None = None


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
    client = _client()
    response = client.messages.create(
        model=model,
        max_tokens=MAX_OFFER_TOKENS,
        system=OFFER_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    return "".join(
        block.text for block in response.content if block.type == "text"
    ).strip()


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


def explore(
    query: str,
    k: int = DEFAULT_TOP_K,
    db_dir: str = "chroma_db",
    model: str = OFFER_MODEL,
    decide: bool = True,
    input_fn=input,
) -> ExploreResult:
    """Run the show-then-offer-to-teach beat for `query`.

    Retrieves and prints the relevant chunks, generates and prints a specific
    teaching offer, and (when `decide`) captures the learner's accept/decline.
    Returns an `ExploreResult` so callers — and Step 2 — can act on the choice.
    """
    hits = retrieve(query, k=k, db_dir=db_dir)
    print(format_hits(query, hits))

    offer = generate_offer(query, hits, model=model)

    if not decide:
        print("\n" + "=" * 60)
        print(f"💡 {offer}")
        return ExploreResult(query=query, hits=hits, offer=offer, accepted=None)

    accepted = _prompt_decision(offer, input_fn=input_fn)
    if accepted:
        print("\nGreat — I'll teach this thread.")
        print("(Walking through it lands in Step 2; for now your choice is saved.)")
    else:
        print("\nNo problem — keep exploring.")
        print('Run `explore` again with whatever catches your eye.')

    return ExploreResult(query=query, hits=hits, offer=offer, accepted=accepted)


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
    args = parser.parse_args(argv[1:])

    if not args.query.strip():
        print("error: query is empty", file=sys.stderr)
        return 1

    try:
        explore(args.query, k=args.top_k, db_dir=args.db)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
