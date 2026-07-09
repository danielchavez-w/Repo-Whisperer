"""Phase 3 foundation — the honest-judge primitive.

A small, **evidence-agnostic** "judge": given a question (or claim) and the
evidence gathered for it, return a structured, honestly-calibrated verdict —
does the evidence actually answer the question, only partly, not at all, or is
it too unclear to tell?

The verdict schema is deliberately generic so it stays **stable** as the
evidence changes per use. Only the rubric framing changes; the `Verdict` shape
does not. Two uses so far, both text evidence: `judge_relevance` (did retrieval
actually answer the learner's question — Step 1) and `judge_attempt` (does the
learner's practice-file code, read from disk, demonstrate the thing they were
practicing — Step 4's `check`). The evidence-agnostic core (`_run_judge` takes
message content, which could carry an image block) keeps a future screenshot
use possible without touching the schema.

This step's concrete use is **model-judged relevance**. Retrieval always returns
its top-k, even when the repo has nothing on the topic, so a cosine distance
alone can't separate "this feature is absent" (~0.68) from "this feature exists
but is worded differently" (~0.65) — the two sit in the same ~0.025 band, which
is why the old distance-threshold weak-match warning was removed. A model reading
the actual code can tell them apart; that judgment is what `judge_relevance`
provides.

Run standalone (judge whether the store actually answers a question):

    python -m repo_whisperer.judge "<question>" [--db DIR] [-k N]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass

from repo_whisperer.answer import ANSWER_MODEL, Hit, _client, build_context, retrieve

# Allowed values for each verdict field. The parser coerces anything outside
# these sets to the safe default, so a malformed reply never crashes a lesson.
VERDICTS: tuple[str, ...] = ("answers", "partial", "doesnt_answer", "cant_tell")
CONFIDENCES: tuple[str, ...] = ("high", "medium", "low")
EVIDENCE_QUALITIES: tuple[str, ...] = ("sufficient", "thin", "unreadable")

# The judge reuses the Phase 1 answering model.
JUDGE_MODEL: str = ANSWER_MODEL

# A verdict is a tiny JSON object — this is plenty of room.
MAX_JUDGE_TOKENS: int = 400


@dataclass
class Verdict:
    """A structured, honestly-calibrated judgment about evidence.

    The shape is fixed across uses (relevance now, screen verification later):

    - `verdict` — one of `VERDICTS`: does the evidence answer the question?
    - `confidence` — how sure the judge is of that verdict (`CONFIDENCES`).
    - `reason` — 1-2 sentences grounded in the evidence.
    - `missing` — what would move the verdict up a tier, or None when it fully
      `answers`.
    - `evidence_quality` — whether the evidence itself was good enough to judge
      on (`EVIDENCE_QUALITIES`); `unreadable` matters mainly for the image case.
    """

    verdict: str
    confidence: str
    reason: str
    missing: str | None = None
    evidence_quality: str = "sufficient"

    @property
    def answered(self) -> bool:
        """True only when the evidence directly answers the question."""
        return self.verdict == "answers"


# The exact JSON contract every judge prompt asks for. Shared so the schema is
# defined in ONE place and stays identical when the verification rubric reuses it.
VERDICT_SCHEMA_NOTE = (
    "Respond with ONLY a single JSON object — no prose, no markdown fences — "
    "with exactly these keys:\n"
    "{\n"
    '  "verdict": "answers | partial | doesnt_answer | cant_tell",\n'
    '  "confidence": "high | medium | low",\n'
    '  "reason": "1-2 sentences grounded in the evidence",\n'
    '  "missing": "what would move it up a tier, or null if verdict is answers",\n'
    '  "evidence_quality": "sufficient | thin | unreadable"\n'
    "}\n\n"
    "Verdict meanings:\n"
    "- answers: the evidence directly and substantially addresses the question.\n"
    "- partial: the evidence addresses some of the question but leaves a "
    "material gap.\n"
    "- doesnt_answer: the evidence does not address the question, even if it "
    "comes from the same general area of the codebase.\n"
    "- cant_tell: the evidence is too unclear or insufficient to judge either "
    "way.\n\n"
    "Be honestly calibrated: do not say 'answers' just to be agreeable, and do "
    "not invent a gap that isn't really there. Set 'evidence_quality' to "
    "'unreadable' only when you genuinely cannot make out the evidence at all."
)

RELEVANCE_SYSTEM_PROMPT = (
    "You are a fair relevance judge for a code-exploration tutor. A learner asked "
    "a question, and a retrieval system returned the code excerpts it considered "
    "most relevant. That system ALWAYS returns its top matches even when the "
    "repository contains nothing on the topic — so your job is to judge honestly "
    "whether these excerpts actually do the thing the learner is asking about.\n\n"
    "For this judgment the QUESTION is the learner's question and the EVIDENCE "
    "is the retrieved code excerpts, each labeled with its `path:start-end` "
    "key.\n\n"
    "Judge by MEANING, not vocabulary:\n"
    "- The learner's wording will often differ from the code's naming. Do NOT "
    "require the learner's exact term to appear in the code. Map the learner's "
    "concept to what the code actually implements.\n"
    "- If the retrieved code implements the thing being asked about under a "
    "different name, that is 'answers' (or 'partial' if it's only partially "
    "covered) — NOT 'doesnt_answer'. Name the mapping in your reason, e.g. "
    "\"what you're calling 'orbs' is the collectibles system\".\n"
    "- A learner exploring a marble game who asks about 'orbs' almost certainly "
    "means the glowing collectibles you roll into for points — same concept, "
    "different label. Recognizing that mapping is your JOB, not a disqualifier. "
    "Same-concept-different-name pairs to honor in spirit (the principle "
    "generalizes — reason about meaning every time, don't pattern-match this "
    "list): 'orbs' = 'collectibles', 'save system' = 'persistence layer', "
    "'enemy AI' = 'behavior tree', 'health bar' = 'HP / damage state'.\n"
    "- Reserve 'doesnt_answer' for when the repo genuinely does not implement "
    "the thing at all, under ANY name — not for when you simply can't find a "
    "literal word match.\n"
    "- Reserve 'cant_tell' for when the evidence is too thin or unreadable to "
    "judge either way — not as a hedge when you can actually reason it out.\n\n"
    f"{VERDICT_SCHEMA_NOTE}"
)


ATTEMPT_SYSTEM_PROMPT = (
    "You are a fair judge for a coding tutor. A learner was taught a thread of a "
    "real codebase and then wrote their own PRACTICE ATTEMPT of the idea in a "
    "scratch file. Judge honestly whether the attempt demonstrates a working "
    "grasp of the thing they were practicing.\n\n"
    "For this judgment the QUESTION is 'does the attempt demonstrate the thing "
    "they were practicing?' and the EVIDENCE is the attempt code itself, read "
    "straight from disk — plus excerpts from the real codebase for comparison, "
    "when provided.\n\n"
    "Judge the MECHANISM, not the polish:\n"
    "- A practice attempt legitimately simplifies: different names, fewer "
    "features, hardcoded values, no error handling. None of that lowers the "
    "verdict when the core mechanism is right.\n"
    "- 'answers' = the core mechanism of the practiced thing is present and "
    "would work.\n"
    "- 'partial' = the attempt is clearly going for the right thing and gets "
    "some of it, but a material piece of the mechanism is missing or wrong — "
    "name that piece in 'missing'.\n"
    "- 'doesnt_answer' = the attempt does not do the practiced thing, under any "
    "naming or simplification.\n"
    "- 'cant_tell' = the attempt is too fragmentary or unclear to judge either "
    "way — not a hedge when you can actually reason it out.\n"
    "- The real-codebase excerpts show what the mechanism actually requires; "
    "compare against them, but never demand the attempt mirror the real code "
    "line-for-line.\n\n"
    f"{VERDICT_SCHEMA_NOTE}"
)


def _coerce(value: object, allowed: tuple[str, ...], default: str) -> str:
    """Return `value` if it's an allowed string, else the safe `default`."""
    return value if isinstance(value, str) and value in allowed else default


def _parse_verdict(raw: str) -> Verdict:
    """Parse the model's JSON reply into a Verdict, forgivingly.

    Strips any markdown fence, extracts the outermost `{...}` object, and coerces
    out-of-range field values to safe defaults. A reply that can't be parsed at
    all becomes an honest `cant_tell` rather than raising — the judge degrading
    to "not sure" must never take down the lesson around it.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]

    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("verdict was not a JSON object")
    except (ValueError, TypeError):
        return Verdict(
            verdict="cant_tell",
            confidence="low",
            reason="The relevance check did not return a readable verdict.",
            missing=None,
            evidence_quality="thin",
        )

    missing = data.get("missing")
    if missing is not None and not isinstance(missing, str):
        missing = str(missing)
    if isinstance(missing, str) and missing.strip().lower() in {"", "null", "none"}:
        missing = None

    return Verdict(
        verdict=_coerce(data.get("verdict"), VERDICTS, "cant_tell"),
        confidence=_coerce(data.get("confidence"), CONFIDENCES, "low"),
        reason=str(data.get("reason", "")).strip() or "(no reason given)",
        missing=missing,
        evidence_quality=_coerce(
            data.get("evidence_quality"), EVIDENCE_QUALITIES, "thin"
        ),
    )


def _run_judge(system: str, content, model: str) -> Verdict:
    """Make one judging call and parse the reply into a Verdict.

    `content` is the user-message content. Today it's the plain text of the
    question + evidence; Phase 3 verification will pass a content list that also
    carries an image block — the schema and parsing stay identical, which is
    exactly why this core is kept evidence-agnostic. Raises ValueError (via
    `_client`) if the API key is missing.
    """
    client = _client()
    response = client.messages.create(
        model=model,
        max_tokens=MAX_JUDGE_TOKENS,
        system=system,
        messages=[{"role": "user", "content": content}],
    )
    raw = "".join(block.text for block in response.content if block.type == "text")
    return _parse_verdict(raw)


def judge_relevance(
    question: str, hits: list[Hit], model: str = JUDGE_MODEL
) -> Verdict:
    """Judge whether the retrieved `hits` actually answer `question`.

    The text-evidence use of the shared judge. Returns a `Verdict`; an empty
    `hits` list is an immediate, confident `doesnt_answer` (nothing came back to
    answer with) without spending an API call. Phase 3 verification reuses the
    same `Verdict` schema with image evidence. Raises ValueError if the API key
    is missing.
    """
    if not hits:
        return Verdict(
            verdict="doesnt_answer",
            confidence="high",
            reason="Nothing was retrieved for this question.",
            missing="code in this repo that addresses the question",
            evidence_quality="thin",
        )
    evidence = build_context(hits)
    content = (
        f'The learner asked: "{question}"\n\n'
        f"The retrieved code excerpts (the EVIDENCE):\n\n{evidence}\n\n"
        f"Judge honestly whether these excerpts actually answer the question."
    )
    return _run_judge(RELEVANCE_SYSTEM_PROMPT, content, model)


def judge_attempt(
    goal: str,
    attempt: str,
    hits: list[Hit],
    filename: str = "",
    model: str = JUDGE_MODEL,
) -> Verdict:
    """Judge whether a learner's practice `attempt` demonstrates `goal`.

    The application-verification use of the shared judge (Phase 3, Step 4).
    `goal` describes what the learner was practicing (the most recently taught
    thread, or a note to infer it from the code when no lesson is recorded);
    `attempt` is their practice-file code read from disk — the disk-for-truth
    rule, never a screenshot. `hits`, when non-empty, are excerpts from the real
    codebase so the judge can see what the mechanism actually requires. Unlike
    `judge_relevance`, empty `hits` do NOT short-circuit — the attempt itself is
    the evidence and can be judged without comparison excerpts. The verdict is
    a PRIVATE signal for the tutor to translate into assistance; it is never
    shown to the learner as a grade. Raises ValueError if the API key is missing.
    """
    comparison = (
        f"Excerpts from the real codebase, for comparison:\n\n{build_context(hits)}"
        if hits
        else "(No real-codebase excerpts available — judge from the attempt alone.)"
    )
    file_note = f" (file `{filename}`)" if filename else ""
    content = (
        f"The learner was practicing: {goal}\n\n"
        f"Their practice attempt, read from disk{file_note}:\n\n{attempt}\n\n"
        f"{comparison}\n\n"
        f"Judge honestly whether the attempt demonstrates a working grasp of "
        f"what they were practicing."
    )
    return _run_judge(ATTEMPT_SYSTEM_PROMPT, content, model)


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m repo_whisperer.judge",
        description=(
            "Judge whether the ingested repo's retrieved code actually answers "
            "a question (model-judged relevance)."
        ),
    )
    parser.add_argument("question", help="the question to test, in quotes")
    parser.add_argument(
        "--db", default="chroma_db", metavar="DIR",
        help="ChromaDB storage directory (default: chroma_db)",
    )
    parser.add_argument(
        "-k", "--top-k", type=int, default=6, dest="top_k",
        help="number of chunks to retrieve and judge (default: 6)",
    )
    args = parser.parse_args(argv[1:])

    if not args.question.strip():
        print("error: question is empty", file=sys.stderr)
        return 1

    try:
        hits = retrieve(args.question, k=args.top_k, db_dir=args.db)
        verdict = judge_relevance(args.question, hits)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(
        f"Verdict: {verdict.verdict}  "
        f"(confidence: {verdict.confidence}, evidence: {verdict.evidence_quality})"
    )
    print(f"Reason:  {verdict.reason}")
    if verdict.missing:
        print(f"Missing: {verdict.missing}")
    print("\n" + "-" * 60)
    print(f"Judged over {len(hits)} retrieved chunks (cosine distance, lower = closer):")
    for h in hits:
        print(f"  {h.distance:.3f}  {h.id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
