"""From receipts to placement measures.

A socket's receipts are its placement data. This module folds them into the
four numbers ``chip/docs/socket.md`` says a socket should be able to state
about itself, plus the trigger histogram split into boundary and operations
causes. It is a fold over records; it gathers nothing.

Agreement "net of noise" is the shadow's agreement rate with the answering
member minus that member's own self-disagreement rate on the same sample. A
shadow that disagrees less often than the incumbent disagrees with itself has
told you nothing.

The held-out functions (:func:`score`, :func:`band`, :func:`by_band`) join
receipts to a labelled truth by a caller-supplied key, so a live receipt
stream and a labelled subset are scored with the same call.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from typing import Any

from chipsocket._record import jsonable
from chipsocket.sinks import iter_receipts, load_receipts
from chipsocket.socket import BOUNDARY_TRIGGERS, OPERATIONS_TRIGGERS

__all__ = ["band", "by_band", "iter_receipts", "load_receipts", "score", "summarize"]

Key = Callable[[dict[str, Any]], str]
Compare = Callable[[Any, Any], bool]


def _member_id(ref: str | None) -> str | None:
    """``kind:id@digest`` -> ``id``."""
    if not ref:
        return None
    body = ref.split(":", 1)[1] if ":" in ref else ref
    return body.split("@", 1)[0]


def _kind(ref: str | None) -> str | None:
    return ref.split(":", 1)[0] if ref and ":" in ref else None


def _equal(a: Any, b: Any) -> bool:
    return bool(a == b)


def _ratio(num: float, den: float, places: int = 4) -> float | None:
    return round(num / den, places) if den else None


# ── live measures ────────────────────────────────────────────────────────────


def summarize(receipts: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Fold receipts into per-socket placement measures."""
    by_socket: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in receipts:
        by_socket[r.get("socket", "?")].append(r)
    return {sid: _summarize_one(rs) for sid, rs in sorted(by_socket.items())}


def _summarize_one(rs: list[dict[str, Any]]) -> dict[str, Any]:
    calls = len(rs)
    answered: Counter[str] = Counter()
    members: dict[str, dict[str, Any]] = {}
    triggers: Counter[str] = Counter()
    cost = 0.0
    exhausted = 0
    for r in rs:
        cost += float(r.get("cost", {}).get("usd", 0.0))
        if r.get("answeredBy") is None:
            exhausted += 1
        else:
            answered[_member_id(r["answeredBy"]) or "?"] += 1
        for a in r.get("attempts", []):
            mid = _member_id(a["member"]) or "?"
            m = members.setdefault(mid, {"kind": _kind(a["member"]), "attempts": 0, "answered": 0,
                                         "outcomes": Counter(), "triggers": Counter(), "costUsd": 0.0})
            m["attempts"] += 1
            m["outcomes"][a["outcome"]] += 1
            m["triggers"][a["trigger"]] += 1
            m["costUsd"] += float(a.get("costUsd", 0.0))
            if a["outcome"] == "value" and r.get("answeredBy") == a["member"]:
                m["answered"] += 1
            if a["trigger"] != "primary":
                triggers[a["trigger"]] += 1

    code_answered = sum(n for mid, n in answered.items() if members.get(mid, {}).get("kind") == "code")
    boundary = sum(n for t, n in triggers.items() if t in BOUNDARY_TRIGGERS)
    operations = sum(n for t, n in triggers.items() if t in OPERATIONS_TRIGGERS)
    dominant = None
    if boundary != operations:
        dominant = "boundary" if boundary > operations else "operations"
    return {
        "calls": calls,
        "exhausted": exhausted,
        "costUsd": round(cost, 6),
        "closedByCodeShare": _ratio(code_answered, calls),
        "answeredBy": dict(answered),
        "members": {mid: {**m, "outcomes": dict(m["outcomes"]), "triggers": dict(m["triggers"]),
                          "costUsd": round(m["costUsd"], 6)} for mid, m in members.items()},
        "escalations": {
            "byTrigger": dict(triggers),
            "boundary": boundary,
            "operations": operations,
            "dominant": dominant,
        },
        "shadows": _shadow_measures(rs),
    }


def _shadow_measures(rs: list[dict[str, Any]]) -> dict[str, Any]:
    """Per shadow member: agreement with the answering member, and the noise floor.

    Agreement is broken down by which *kind* of member answered, because a
    shadow agreeing with the code member on the inputs it closed is a different
    fact from agreeing with the model on the inputs it did not.
    """
    per: dict[str, dict[str, Any]] = {}
    noise_by_member: dict[str, Counter[str]] = defaultdict(Counter)
    for r in rs:
        n = r.get("noise")
        if n:
            c = noise_by_member[_member_id(n["member"]) or "?"]
            c["sampled"] += 1
            if n.get("outcome") == "value":
                c["compared"] += 1
                c["agreed"] += 1 if n.get("agrees") else 0
        answered_kind = _kind(r.get("answeredBy"))
        answered_id = _member_id(r.get("answeredBy"))
        for s in r.get("shadows", []):
            mid = _member_id(s["member"]) or "?"
            m = per.setdefault(mid, {"kind": _kind(s["member"]), "sampled": 0, "skippedForBudget": 0,
                                     "compared": 0, "agreed": 0, "costUsd": 0.0,
                                     "byAnsweringKind": defaultdict(Counter),
                                     "answeringMembers": Counter()})
            m["sampled"] += 1
            m["costUsd"] += float(s.get("costUsd", 0.0))
            if s.get("outcome") == "skipped":
                m["skippedForBudget"] += 1
                continue
            if "agrees" in s:
                agreed = 1 if s["agrees"] else 0
                m["compared"] += 1
                m["agreed"] += agreed
                m["byAnsweringKind"][answered_kind]["compared"] += 1
                m["byAnsweringKind"][answered_kind]["agreed"] += agreed
                if answered_id:
                    m["answeringMembers"][answered_id] += 1
    floors = {amid: (round(1 - c["agreed"] / c["compared"], 4) if c["compared"] else None)
              for amid, c in noise_by_member.items()}
    out: dict[str, Any] = {}
    for mid, m in per.items():
        agreement = _ratio(m["agreed"], m["compared"])
        # Noise floor: the answering members' self-disagreement on the sample. If
        # several members answered, take the one that answered most on this
        # shadow's compared calls; report all of them beside it.
        top = m["answeringMembers"].most_common(1)
        floor = floors.get(top[0][0]) if top else None
        net = round(agreement - floor, 4) if agreement is not None and floor is not None else None
        out[mid] = {
            "kind": m["kind"],
            "sampled": m["sampled"],
            "skippedForBudget": m["skippedForBudget"],
            "compared": m["compared"],
            "agreement": agreement,
            "noiseFloor": floor,
            "noiseFloors": floors,
            "agreementNetOfNoise": net,
            "byAnsweringKind": {k: {"compared": c["compared"], "agreed": c["agreed"],
                                    "agreement": _ratio(c["agreed"], c["compared"])}
                                for k, c in m["byAnsweringKind"].items()},
            "costUsd": round(m["costUsd"], 6),
        }
    return out


# ── held-out scoring ────────────────────────────────────────────────────────


def member_values(r: dict[str, Any]) -> dict[str, tuple[str, Any, float | None]]:
    """member id -> (outcome, value, score) for every member that ran on this call."""
    out: dict[str, tuple[str, Any, float | None]] = {}
    for a in r.get("attempts", []) + r.get("shadows", []):
        out[_member_id(a["member"]) or "?"] = (a["outcome"], a.get("value"), a.get("score"))
    return out


def labelled(receipts: Iterable[dict[str, Any]], truth: dict[str, Any], key: Key) -> Iterable[tuple[dict[str, Any], Any]]:
    """Receipts that have a label, paired with the record-shaped truth."""
    for r in receipts:
        k = key(r)
        if k in truth:
            yield r, jsonable(truth[k])


def score(
    receipts: Iterable[dict[str, Any]],
    truth: dict[str, Any],
    key: Key,
    *,
    compare: Compare | None = None,
    positive: Any = True,
) -> dict[str, Any]:
    """Score every member against a held-out truth, from receipts alone.

    ``truth`` maps an input id to its expected value and ``key`` derives that id
    from a receipt (usually from ``receipt["input"]``). Receipts whose id is not
    in ``truth`` are ignored.

    Per member: how many labelled calls it *answered* (a value, whether or not
    it was the one the socket returned), its accuracy on those, its coverage
    (answered / labelled calls it saw), and — when ``truth`` is binary —
    precision, recall and F1 for ``positive``. Abstentions are counted, not
    scored: a member that abstains on the hard cases and is right on the rest
    is exactly what a code member should be, and the coverage number is what
    says how much of the work it took.
    """
    eq = compare or _equal
    pos = jsonable(positive)
    per: dict[str, dict[str, int]] = {}
    n_labelled = 0
    for r, expected in labelled(receipts, truth, key):
        n_labelled += 1
        for mid, (outcome, value, _score) in member_values(r).items():
            m = per.setdefault(mid, dict.fromkeys(("saw", "answered", "correct", "abstained", "failed",
                                                   "tp", "fp", "fn", "tn"), 0))
            m["saw"] += 1
            if outcome == "value":
                m["answered"] += 1
                m["correct"] += 1 if eq(value, expected) else 0
                is_pos_truth, is_pos_val = eq(expected, pos), eq(value, pos)
                cell = ("tp" if is_pos_truth else "fp") if is_pos_val else ("fn" if is_pos_truth else "tn")
                m[cell] += 1
            elif outcome == "abstained":
                m["abstained"] += 1
            else:
                m["failed"] += 1
    out: dict[str, Any] = {"labelled": n_labelled, "members": {}}
    for mid, m in per.items():
        p = _ratio(m["tp"], m["tp"] + m["fp"])
        rc = _ratio(m["tp"], m["tp"] + m["fn"])
        f1 = round(2 * p * rc / (p + rc), 4) if p and rc else None
        out["members"][mid] = {
            "saw": m["saw"], "answered": m["answered"], "abstained": m["abstained"], "failed": m["failed"],
            "coverage": _ratio(m["answered"], m["saw"]),
            "accuracy": _ratio(m["correct"], m["answered"]),
            "precision": p, "recall": rc, "f1": f1,
            "confusion": {k: m[k] for k in ("tp", "fp", "fn", "tn")},
        }
    return out


def bucket(sc: float, width: float) -> float:
    return round(math.floor(sc / width + 1e-9) * width, 6)


def by_band(
    receipts: Iterable[dict[str, Any]],
    truth: dict[str, Any],
    key: Key,
    scorer: str,
    *,
    width: float = 0.05,
    constants: dict[str, Any] | None = None,
    compare: Compare | None = None,
) -> list[dict[str, Any]]:
    """Every member's accuracy inside each bucket of one member's score.

    ``scorer`` is a member that returns a score on its answers or abstentions
    (a lexical similarity, a classifier margin). For each bucket of that score
    this reports how often every member that answered was right, beside the
    accuracy of each constant answer in ``constants`` (``{"always-no": False}``).
    A bucket where a constant beats the model is a bucket the model should not
    be asked about; that is the placement decision, read off labels.
    """
    eq = compare or _equal
    consts = {name: jsonable(c) for name, c in (constants or {}).items()}
    acc: dict[float, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for r, expected in labelled(receipts, truth, key):
        vals = member_values(r)
        if scorer not in vals or vals[scorer][2] is None:
            continue
        lo = bucket(vals[scorer][2], width)  # type: ignore[arg-type]
        for name, c in consts.items():
            cell = acc[lo][name]
            cell[1] += 1
            cell[0] += 1 if eq(c, expected) else 0
        for mid, (outcome, value, _sc) in vals.items():
            if outcome == "value":
                cell = acc[lo][mid]
                cell[1] += 1
                cell[0] += 1 if eq(value, expected) else 0
    out = []
    for lo in sorted(acc):
        row: dict[str, Any] = {"lo": lo, "hi": round(lo + width, 6), "n": max(v[1] for v in acc[lo].values())}
        row["accuracy"] = {name: {"n": v[1], "accuracy": round(v[0] / v[1], 4)} for name, v in acc[lo].items() if v[1]}
        out.append(row)
    return out


def band(
    receipts: Iterable[dict[str, Any]],
    truth: dict[str, Any],
    key: Key,
    member: str,
    *,
    width: float = 0.05,
    positive: Any = True,
    compare: Compare | None = None,
) -> list[dict[str, Any]]:
    """Where a scoring member abstained, what was the truth, by score bucket?

    This is the escalation band made visible: the lowest bucket at which the
    positive rate clears the precision you want is where a code member could
    stop abstaining and answer. Buckets are ``[lo, lo + width)`` on the
    abstention's ``score``; a member whose abstentions carry no score
    contributes nothing here. :func:`chipsocket.learn.cuts` turns this into a
    pair of cut points with the evidence attached.
    """
    eq = compare or _equal
    buckets: dict[float, list[int]] = defaultdict(lambda: [0, 0])
    pos = jsonable(positive)
    for r, expected in labelled(receipts, truth, key):
        for mid, (outcome, _value, sc) in member_values(r).items():
            if mid == member and outcome == "abstained" and sc is not None:
                b = buckets[bucket(sc, width)]
                b[0] += 1
                b[1] += 1 if eq(expected, pos) else 0
    return [{"lo": lo, "hi": round(lo + width, 6), "n": n, "positiveRate": round(p / n, 4)}
            for lo, (n, p) in sorted(buckets.items())]
