"""Learn the escalation band from receipts and labels, and receipt what was learned.

The first learned code member in production (gabe's same-event lexical cuts)
was derived by a script and typed in as two constants. That works once. This
module makes the derivation a function of the evidence, and makes the member
it produces carry the digest of that evidence, so the receipts say not only
*which* code answered but *what it was learned from*.

:func:`cuts` reads one scoring member's scores off labelled receipts and
finds the widest band at each end where a constant answer meets a precision
bar: below ``lo`` the negative constant is right at least ``precision`` of
the time, at or above ``hi`` the positive one is. :func:`cut_member` turns
that into a ``code`` member that answers outside the band and abstains inside
it, carrying the score, which is exactly the shape a first-success chain
wants in front of a model.

A band learned from one labelled day is a hypothesis. Seat the member as a
shadow first; promote it when a second day's receipts agree.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from chipsocket._record import jsonable, sha
from chipsocket.members import Abstain, Answer, Member
from chipsocket.report import Compare, Key, _equal, labelled, member_values


@dataclass(frozen=True, slots=True)
class Cuts:
    """Two cut points on one member's score, with the evidence that set them.

    ``lo`` is ``None`` when no lower band met the bar; likewise ``hi``. The
    ``evidence`` digest covers every (key, score, truth) triple the learner
    read, so two learners over the same labelled receipts agree and a changed
    label changes the digest.
    """

    scorer: str
    lo: float | None
    hi: float | None
    precision: float
    width: float
    min_n: int
    labelled: int
    scored: int
    below: dict[str, Any] = field(default_factory=dict)
    above: dict[str, Any] = field(default_factory=dict)
    evidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "scorer": self.scorer, "lo": self.lo, "hi": self.hi,
            "precision": self.precision, "width": self.width, "minN": self.min_n,
            "labelled": self.labelled, "scored": self.scored,
            "below": dict(self.below), "above": dict(self.above), "evidence": self.evidence,
        }

    @property
    def closes(self) -> float | None:
        """Share of scored inputs a member on these cuts would answer."""
        n = self.below.get("n", 0) + self.above.get("n", 0)
        return round(n / self.scored, 4) if self.scored else None


def cuts(
    receipts: Iterable[dict[str, Any]],
    truth: dict[str, Any],
    key: Key,
    scorer: str,
    *,
    positive: Any = True,
    precision: float = 0.95,
    width: float = 0.05,
    min_n: int = 30,
    compare: Compare | None = None,
) -> Cuts:
    """Find the cut points where a constant beats asking, from labelled receipts.

    Reads ``scorer``'s score off every labelled receipt it ran on, answered or
    abstained. Candidate boundaries are multiples of ``width``. ``lo`` is the
    largest boundary such that at least ``min_n`` inputs score below it and
    the negative constant is right on at least ``precision`` of them; ``hi``
    is the smallest boundary such that at least ``min_n`` inputs score at or
    above it and the positive constant is right on at least ``precision``.
    Both are cumulative from the end, not per bucket: a cut is a promise about
    everything on its side of the line.

    If the two bands would overlap, both are dropped: a band that meets the
    bar from both ends is a score that does not separate anything.
    """
    if not 0 < precision <= 1:
        raise ValueError("precision must be in (0, 1]")
    if width <= 0:
        raise ValueError("width must be positive")
    eq = compare or _equal
    pos = jsonable(positive)
    rows: list[tuple[str, float, bool]] = []
    n_labelled = 0
    for r, expected in labelled(receipts, truth, key):
        n_labelled += 1
        vals = member_values(r)
        if scorer in vals and vals[scorer][2] is not None:
            rows.append((key(r), float(vals[scorer][2]), bool(eq(expected, pos))))  # type: ignore[arg-type]
    evidence = sha(scorer, *(f"{k}\t{s!r}\t{int(p)}" for k, s, p in sorted(rows)))
    base = Cuts(scorer=scorer, lo=None, hi=None, precision=precision, width=width, min_n=min_n,
                labelled=n_labelled, scored=len(rows), evidence=evidence)
    if not rows:
        return base

    scores = sorted(rows, key=lambda t: t[1])
    lo_b = math.floor(scores[0][1] / width) * width
    hi_b = math.ceil(scores[-1][1] / width) * width
    boundaries = [round(lo_b + i * width, 6) for i in range(int(round((hi_b - lo_b) / width)) + 2)]

    lo: float | None = None
    below: dict[str, Any] = {}
    for b in boundaries:
        under = [p for _, s, p in scores if s < b]
        if len(under) < min_n:
            continue
        acc = 1 - sum(under) / len(under)
        if acc >= precision:
            lo, below = b, {"n": len(under), "accuracy": round(acc, 4), "constant": "negative"}
    hi: float | None = None
    above: dict[str, Any] = {}
    for b in reversed(boundaries):
        over = [p for _, s, p in scores if s >= b]
        if len(over) < min_n:
            continue
        acc = sum(over) / len(over)
        if acc >= precision:
            hi, above = b, {"n": len(over), "accuracy": round(acc, 4), "constant": "positive"}
    if lo is not None and hi is not None and lo > hi:
        lo, hi, below, above = None, None, {}, {}
    return Cuts(scorer=scorer, lo=lo, hi=hi, precision=precision, width=width, min_n=min_n,
                labelled=n_labelled, scored=len(rows), below=below, above=above, evidence=evidence)


def cut_member(
    score: Callable[[Any], float | None],
    cuts: Cuts,
    *,
    negative: Any,
    positive: Any,
    id: str = "cuts",
    reason: str = "band",
) -> Member:
    """A ``code`` member that answers outside the learned band and abstains inside it.

    The digest covers the cuts and the evidence they were learned from, so a
    member re-learned from a different day is a different member on the
    receipts, and ``chipsocket report`` can compare them. An unscored input
    abstains with reason ``unscored``.
    """
    lo, hi = cuts.lo, cuts.hi
    digest = sha("cuts", cuts.evidence, repr(lo), repr(hi), repr(jsonable(negative)), repr(jsonable(positive)))

    def call(x: Any, _ctx: dict[str, Any]) -> Answer | Abstain:
        s = score(x)
        if s is None:
            return Abstain("unscored")
        if lo is not None and s < lo:
            return Answer(value=negative, score=s)
        if hi is not None and s >= hi:
            return Answer(value=positive, score=s)
        return Abstain(reason, score=s)

    return Member(id=id, kind="code", digest=digest, call=call)
