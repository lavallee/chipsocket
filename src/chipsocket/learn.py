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

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from chipsocket._record import jsonable, sha
from chipsocket.members import Abstain, Answer, Member
from chipsocket.report import Compare, Key, _equal, labelled, member_values
from chipsocket.report import bucket as _bucket


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
    #: Support behind each cut: ``n``, ``accuracy``, ``constant``, and ``thin``
    #: when the support fell under ``min_n`` and the cut was withheld.
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
        n = (self.below.get("n", 0) if self.lo is not None else 0) + (
            self.above.get("n", 0) if self.hi is not None else 0)
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
    min_bucket: int = 10,
    compare: Compare | None = None,
) -> Cuts:
    """Find the cut points where a constant beats asking, from labelled receipts.

    Reads ``scorer``'s score off every labelled receipt it ran on, answered or
    abstained, and buckets it by ``width``. Walking up from the bottom, ``lo``
    extends over each bucket while the negative constant is right on at least
    ``precision`` of that bucket; walking down from the top, ``hi`` extends
    over each bucket while the positive constant is right on at least
    ``precision`` of it. A bucket with fewer than ``min_bucket`` inputs is
    skipped rather than judged. The cut is kept only if at least ``min_n``
    inputs lie on its side; otherwise it is ``None`` and ``below`` / ``above``
    say how thin the support was.

    The criterion is per bucket, not cumulative, on purpose. A cumulative
    precision floor lets a mass of easy inputs at the far end carry a cut over
    a band where a model is more accurate than the constant: on one labelled
    day the band [0.15, 0.30) had the constant right 87.8% of the time and the
    model 91.1%, and a cumulative floor of 95% still put the cut at 0.30
    because the 4,087 inputs below 0.15 were 99% negative. The cut is a
    promise about each input it closes, so each bucket has to earn it.

    If the two bands would overlap, both are dropped: a score that meets the
    bar from both ends does not separate anything.
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

    buckets: dict[float, list[int]] = {}
    for _, s, p in rows:
        b = buckets.setdefault(_bucket(s, width), [0, 0])
        b[0] += 1
        b[1] += 1 if p else 0
    keys = sorted(buckets)

    def walk(order: list[float], want_positive: bool) -> float | None:
        """The boundary reached while each judged bucket meets the bar."""
        edge: float | None = None
        for k in order:
            total, pos = buckets[k]
            if total < min_bucket:
                continue
            acc = (pos if want_positive else total - pos) / total
            if acc < precision:
                break
            edge = round(k + width, 6) if not want_positive else k
        return edge

    lo = walk(keys, False)
    hi = walk(list(reversed(keys)), True)

    def support(edge: float | None, side: str, constant: str) -> dict[str, Any]:
        if edge is None:
            return {}
        if side == "below":
            on = [p for _, s, p in rows if s < edge]
            acc = 1 - sum(on) / len(on) if on else 0.0
        else:
            on = [p for _, s, p in rows if s >= edge]
            acc = sum(on) / len(on) if on else 0.0
        return {"n": len(on), "accuracy": round(acc, 4), "constant": constant}

    below = support(lo, "below", "negative")
    above = support(hi, "above", "positive")
    if lo is not None and below.get("n", 0) < min_n:
        below["thin"] = True
        lo = None
    if hi is not None and above.get("n", 0) < min_n:
        above["thin"] = True
        hi = None
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
