"""Step 5 — Retrieval + grounded answer.

Take a natural-language question, embed it with the same local model used for
indexing, retrieve the most similar chunks from ChromaDB, and ask Claude to
answer using only those excerpts — citing the exact `path:start-end` locations
it relied on. This is the payoff of steps 2–4: answers grounded in the actual
code, not the model's prior.

Run standalone:

    python -m repo_whisperer.answer "<question>" [--db DIR] [-k N]
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass

from repo_whisperer.store import COLLECTION_NAME, embed_texts, get_client

# Answering model (see PHASE_1_TASK.md). Verified reachable with this exact id.
ANSWER_MODEL: str = "claude-opus-4-8"

# How many chunks to retrieve and hand to the model as grounding context.
DEFAULT_TOP_K: int = 6

# Cap the answer length; grounded code explanations rarely need more.
MAX_TOKENS: int = 1024

SYSTEM_PROMPT = (
    "You are a coding tutor answering questions about one specific codebase. "
    "You are given excerpts retrieved from that codebase, each labeled with a "
    "citation key of the form `path:start-end` (the file path and its 1-indexed, "
    "inclusive line range).\n\n"
    "Rules:\n"
    "- Answer using ONLY the provided excerpts. Do not invent code, files, or "
    "behavior that isn't shown.\n"
    "- Cite the specific excerpts you used inline, with their `path:start-end` "
    "keys, so the reader can verify every claim.\n"
    "- If the excerpts don't contain enough to answer, say so plainly and point "
    "to whichever excerpt is closest, rather than guessing.\n"
    "- Be concise and concrete; refer to real identifiers from the code."
)


@dataclass
class Hit:
    """One retrieved chunk and its similarity to the query."""

    id: str          # citation key "<path>:<start>-<end>"
    path: str
    start_line: int
    end_line: int
    text: str
    distance: float  # cosine distance (lower = more similar)


def retrieve(question: str, k: int = DEFAULT_TOP_K, db_dir: str = "chroma_db") -> list[Hit]:
    """Embed `question` and return the top-`k` most similar stored chunks.

    Raises ValueError with a helpful message if the collection hasn't been
    built yet (i.e. nothing has been ingested).
    """
    client = get_client(db_dir)
    try:
        collection = client.get_collection(COLLECTION_NAME)
    except Exception as exc:  # chromadb raises if the collection is missing
        raise ValueError(
            f"No ingested repo found in '{db_dir}'. Run "
            f"`python -m repo_whisperer.store <path-to-repo>` first."
        ) from exc

    count = collection.count()
    if count == 0:
        raise ValueError(
            f"The collection in '{db_dir}' is empty. Re-ingest a repo first."
        )

    res = collection.query(
        query_embeddings=embed_texts([question]),
        n_results=min(k, count),
    )
    hits: list[Hit] = []
    for cid, doc, meta, dist in zip(
        res["ids"][0], res["documents"][0], res["metadatas"][0], res["distances"][0]
    ):
        hits.append(
            Hit(
                id=cid,
                path=meta["path"],
                start_line=meta["start_line"],
                end_line=meta["end_line"],
                text=doc,
                distance=dist,
            )
        )
    return hits


def build_context(hits: list[Hit]) -> str:
    """Render retrieved chunks into a labeled context block for the prompt."""
    blocks = []
    for h in hits:
        blocks.append(f"[{h.id}]\n{h.text}")
    return "\n\n".join(blocks)


def _client():
    """Create the Anthropic client, loading the API key from .env if present."""
    from dotenv import load_dotenv

    load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise ValueError(
            "ANTHROPIC_API_KEY is not set. Add it to a .env file "
            "(see .env.example) or export it in your shell."
        )
    import anthropic

    return anthropic.Anthropic()


def answer_question(
    question: str,
    k: int = DEFAULT_TOP_K,
    db_dir: str = "chroma_db",
    model: str = ANSWER_MODEL,
) -> tuple[str, list[Hit]]:
    """Retrieve context for `question`, ask Claude, and return (answer, hits)."""
    hits = retrieve(question, k=k, db_dir=db_dir)
    context = build_context(hits)
    user_message = (
        f"Question: {question}\n\n"
        f"Retrieved excerpts from the codebase:\n\n{context}\n\n"
        f"Answer the question using only these excerpts, citing the "
        f"`path:start-end` keys you rely on."
    )

    client = _client()
    response = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    answer = "".join(
        block.text for block in response.content if block.type == "text"
    ).strip()
    return answer, hits


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m repo_whisperer.answer",
        description="Ask a question about the ingested repo and get a cited answer.",
    )
    parser.add_argument("question", help="the question to ask, in quotes")
    parser.add_argument(
        "--db", default="chroma_db", metavar="DIR",
        help="ChromaDB storage directory (default: chroma_db)",
    )
    parser.add_argument(
        "-k", "--top-k", type=int, default=DEFAULT_TOP_K, dest="top_k",
        help=f"number of chunks to retrieve (default: {DEFAULT_TOP_K})",
    )
    args = parser.parse_args(argv[1:])

    if not args.question.strip():
        print("error: question is empty", file=sys.stderr)
        return 1

    try:
        answer, hits = answer_question(args.question, k=args.top_k, db_dir=args.db)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(answer)
    print("\n" + "-" * 60)
    print(f"Retrieved {len(hits)} chunks (cosine distance, lower = closer):")
    for h in hits:
        print(f"  {h.distance:.3f}  {h.id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
