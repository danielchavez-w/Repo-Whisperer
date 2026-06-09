"""Phase 2, Step 4 — learning memory / state.

The tutor remembers across sessions. Two payoffs, both small but real:

1. **The level persists.** The learner sets their altitude once (Step 3's
   `--level`); later sessions open at that same level without re-passing it.
2. **Cross-referencing.** When teaching a new thread, the tutor is handed a brief
   of what was already covered, so it can say "this uses the same pattern you saw
   in `initRails` earlier" — turning a pile of one-off lessons into a connected
   understanding instead of isolated explanations.

State is a single JSON file (default `learning_state.json`), keyed to the repo
that was last ingested. The store holds one repo at a time (Phase 1 behavior), so
ingesting a *different* repo resets the covered threads but keeps the learner's
chosen level.

Kept deliberately simple per the spec: plain dataclasses + `json`, no SQLite and
no migration machinery beyond a version stamp and a forgiving loader — a missing
or corrupt file just starts fresh rather than crashing a lesson.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_STATE_PATH: str = "learning_state.json"
STATE_VERSION: int = 1

# Mirrors tutor.DEFAULT_LEVEL. Duplicated here (rather than imported) to avoid a
# circular import — tutor imports this module. Kept in sync intentionally.
_FALLBACK_LEVEL: str = "beginner"

# How many prior threads to surface to the teaching prompt — recent ones, so the
# cross-reference note stays short and relevant rather than a full history dump.
_PRIOR_LIMIT: int = 8


def _now() -> str:
    """An ISO-8601 UTC timestamp, second precision — stable and sortable."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def topic_from_query(query: str) -> str:
    """Derive a short label for a thread from the learner's query.

    The label comes from the query (not the offer) because an offer now either is
    plain or names a *past* lesson it ties into — neither reliably names the new
    thread being taught, whereas the query always describes it.
    """
    trimmed = query.strip()
    return trimmed if len(trimmed) <= 60 else trimmed[:57] + "..."


@dataclass
class Thread:
    """One thread the learner has been taught."""

    query: str
    offer: str
    topic: str
    citations: list[str]
    level: str
    engaged: bool
    timestamp: str


@dataclass
class LearningState:
    """Everything the tutor remembers between sessions."""

    level: str = _FALLBACK_LEVEL
    repo: str | None = None
    threads: list[Thread] = field(default_factory=list)
    version: int = STATE_VERSION

    def record_thread(
        self,
        *,
        query: str,
        offer: str,
        citations: list[str],
        level: str,
        engaged: bool,
    ) -> Thread:
        """Add (or refresh) a covered thread, deduped by topic + citations.

        Re-teaching the same thread updates the existing record in place rather
        than piling up duplicates, so the memory reflects threads, not visits.
        """
        topic = topic_from_query(query)
        thread = Thread(
            query=query,
            offer=offer,
            topic=topic,
            citations=list(citations),
            level=level,
            engaged=engaged,
            timestamp=_now(),
        )
        key = (topic, tuple(citations))
        for i, existing in enumerate(self.threads):
            if (existing.topic, tuple(existing.citations)) == key:
                self.threads[i] = thread
                return thread
        self.threads.append(thread)
        return thread

    def find_revisit(self, hit_ids: list[str], *, min_overlap: int = 2) -> "Thread | None":
        """Return the covered thread this retrieval re-treads, or None.

        A revisit is recognized when freshly retrieved chunks substantially
        overlap a thread already taught — at least `min_overlap` shared
        citations, or every citation of that thread (so a short, fully re-hit
        thread still counts). The best-overlapping thread wins. This is how a
        re-asked topic is recognized as review rather than treated as new
        ground, with no special command needed from the learner.
        """
        current = set(hit_ids)
        best: Thread | None = None
        best_overlap = 0
        for t in self.threads:
            cites = set(t.citations)
            overlap = len(current & cites)
            if overlap == 0:
                continue
            strong = overlap >= min_overlap or overlap == len(cites)
            if strong and overlap > best_overlap:
                best, best_overlap = t, overlap
        return best

    def prior_brief(
        self, *, exclude_citations: list[str] | None = None, limit: int = _PRIOR_LIMIT
    ) -> str:
        """A compact bulleted summary of covered threads for the teaching prompt.

        Skips the thread currently being taught (matched by citations) so the
        tutor never cross-references a lesson against itself. Returns "" when
        there is nothing prior to mention.
        """
        exclude = tuple(exclude_citations) if exclude_citations else None
        lines: list[str] = []
        for t in self.threads:
            if exclude is not None and tuple(t.citations) == exclude:
                continue
            shown = ", ".join(t.citations[:3])
            more = "…" if len(t.citations) > 3 else ""
            lines.append(f'- {t.topic} (from "{t.query}"; cited {shown}{more})')
        return "\n".join(lines[-limit:])


def load_state(path: str | None = DEFAULT_STATE_PATH) -> LearningState:
    """Load state from `path`, or return a fresh default if absent/unreadable.

    Forgiving by design: a missing file, bad JSON, or a malformed thread never
    raises — it just yields a clean state — because losing a lesson to a corrupt
    sidecar file would be a worse failure than starting over.
    """
    if not path:
        return LearningState()
    p = Path(path)
    if not p.exists():
        return LearningState()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return LearningState()
    if not isinstance(data, dict):
        return LearningState()

    threads: list[Thread] = []
    for raw in data.get("threads", []):
        try:
            threads.append(Thread(**raw))
        except TypeError:
            continue  # skip a thread whose shape we don't recognize

    return LearningState(
        level=data.get("level", _FALLBACK_LEVEL),
        repo=data.get("repo"),
        threads=threads,
        version=data.get("version", STATE_VERSION),
    )


def save_state(state: LearningState, path: str | None = DEFAULT_STATE_PATH) -> None:
    """Persist `state` to `path` as JSON (atomic write). No-op when path is None."""
    if not path:
        return
    p = Path(path)
    if p.parent and not p.parent.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(asdict(state), indent=2, ensure_ascii=False)
    # Write to a temp file in the same dir, then replace — so a crash mid-write
    # can't leave a half-written (and then unreadable) state file behind.
    fd, tmp = tempfile.mkstemp(dir=str(p.parent or "."), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp, p)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def note_repo_ingested(
    repo_path: str, path: str | None = DEFAULT_STATE_PATH
) -> LearningState:
    """Record which repo was just ingested, resetting threads on a repo switch.

    The level survives a switch (it's a learner preference, not a repo fact); the
    covered threads do not, since they cite code that's no longer in the store.
    Returns the saved state and whether a reset happened via `state.threads`.
    """
    state = load_state(path)
    normalized = os.path.abspath(repo_path)
    if state.repo is not None and state.repo != normalized:
        state.threads = []
    state.repo = normalized
    save_state(state, path)
    return state
