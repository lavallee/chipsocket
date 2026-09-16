"""From receipts to placement measures.

A socket's receipts are its placement data. This module folds them into the
four numbers ``chip/docs/socket.md`` says a socket should be able to state
about itself, plus the trigger histogram split into boundary and operations
causes. It is a fold over records; it gathers nothing.

Agreement "net of noise" is the shadow's agreement rate with the answering
member minus that member's own self-disagreement rate on the same sample. A
shadow that disagrees less often than the incumbent disagrees with itself has
told you nothing.
"""

from __future__ import annotations

import json
import math
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from chipsocket.socket import BOUNDARY_TRIGGERS, OPERATIONS_TRIGGERS, _jsonable


def load_receipts(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def _member_id(ref: str | None) -> str | None:
    """``kind:id@digest`` -> ``id``."""
    if not ref:
        return None
    body = ref.split(":", 1)[1] if ":" in ref else ref
    return body.split("@", 1)[0]


def _kind(ref: str | None) -> str | None:
    return ref.split(":", 1)[0] if ref and ":" in ref else None


def summarize(receipts: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Fold receipts into per-socket placement measures."""
    by_socket: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in receipts:
        by_socket[r.get("socket", "?")].append(r)
    return {sid: _summarize_one(rs) for sid, rs in sorted(by_socket.items())}


def _summarize_one(rs: list[dict[str, Any]]) -> dict[str, Any]:
    calls = len(rs)
    answered = Counter()
    members: dict[str, dict[str, Any]] = {}
    triggers = Counter()
    cost = 0.0
    exhausted = 0
    for r in rs:
        cost += float(r.get("cost", {}).get("usd", 0.0))
        if r.get("answeredBy") is None:
            exhausted += 1
        else:
            answered[_member_id(r["answeredBy"])] += 1
        for a in r.get("attempts", []):
            mid = _member_id(a["member"])
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
    shadows = _shadow_measures(rs)
    boundary = sum(n for t, n in triggers.items() if t in BOUNDARY_TRIGGERS)
    operations = sum(n for t, n in triggers.items() if t in OPERATIONS_TRIGGERS)
    return {
        "calls": calls,
        "exhausted": exhausted,
        "costUsd": round(cost, 6),
        "closedByCodeShare": round(code_answered / calls, 4) if calls else None,
        "answeredBy": dict(answered),
        "members": {mid: {**m, "outcomes": dict(m["outcomes"]), "triggers": dict(m["triggers"]),
                          "costUsd": round(m["costUsd"], 6)} for mid, m in members.items()},
        "escalations": {
            "byTrigger": dict(triggers),
            "boundary": boundary,
            "operations": operations,
            "dominant": "boundary" if boundary > operations else ("operations" if operations > boundary else None),
        },
        "shadows": shadows,
    }


def _shadow_measures(rs: list[dict[str, Any]]) -> dict[str, Any]:
    """Per shadow member: agreement with the answering member, and the noise floor.

    Agreement is broken down by which *kind* of member answered, because a
    shadow agreeing with the code member on the inputs it closed is a different
    fact from agreeing with the model on the inputs it did not.
    """
    per: dict[str, dict[str, Any]] = {}
    noise_by_member: dict[str, Counter] = defaultdict(Counter)
    for r in rs:
        n = r.get("noise")
        if n:
            c = noise_by_member[_member_id(n["member"])]
            c["sampled"] += 1
            if n.get("outcome") == "value":
                c["compared"] += 1
                c["agreed"] += 1 if n.get("agrees") else 0
        answered_kind = _kind(r.get("answeredBy"))
        answered_id = _member_id(r.get("answeredBy"))
        for s in r.get("shadows", []):
            mid = _member_id(s["member"])
            m = per.setdefault(mid, {"kind": _kind(s["member"]), "sampled": 0, "skippedForBudget": 0,
                                     "compared": 0, "agreed": 0, "costUsd": 0.0,
                                     "byAnsweringKind": defaultdict(lambda: Counter()),
                                     "answeringMembers": Counter()})
            m["sampled"] += 1
            m["costUsd"] += float(s.get("costUsd", 0.0))
            if s.get("outcome") == "skipped":
                m["skippedForBudget"] += 1
                continue
            if "agrees" in s:
                m["compared"] += 1
                m["agreed"] += 1 if s["agrees"] else 0
                m["byAnsweringKind"][answered_kind]["compared"] += 1
                m["byAnsweringKind"][answered_kind]["agreed"] += 1 if s["agrees"] else 0
                if answered_id:
                    m["answeringMembers"][answered_id] += 1
    out: dict[str, Any] = {}
    for mid, m in per.items():
        agreement = m["agreed"] / m["compared"] if m["compared"] else None
        # Noise floor: the answering members' self-disagreement on the sample. If
        # several members answered, take the one that answered most on this
        # shadow's compared calls; report all of them beside it.
        floors = {}
        for amid, c in noise_by_member.items():
            floors[amid] = round(1 - c["agreed"] / c["compared"], 4) if c["compared"] else None
        top = m["answeringMembers"].most_common(1)
        floor = floors.get(top[0][0]) if top else None
        net = round(agreement - floor, 4) if agreement is not None and floor is not None else None
        out[mid] = {
            "kind": m["kind"],
            "sampled": m["sampled"],
            "skippedForBudget": m["skippedForBudget"],
            "compared": m["compared"],
            "agreement": round(agreement, 4) if agreement is not None else None,
            "noiseFloor": floor,
            "noiseFloors": floors,
            "agreementNetOfNoise": net,
            "byAnsweringKind": {k: {"compared": c["compared"], "agreed": c["agreed"],
                                    "agreement": round(c["agreed"] / c["compared"], 4) if c["compared"] else None}
                                for k, c in m["byAnsweringKind"].items()},
            "costUsd": round(m["costUsd"], 6),
        }
    return out


# ── held-out scoring ────────────────────────────────────────────────────────


def _member_values(r: dict[str, Any]) -> dict[str, tuple[str, Any, float | None]]:
    """member id -> (outcome, value, score) for every member that ran on this call."""
    out: dict[str, tuple[str, Any, float | None]] = {}
    for a in r.get("attempts", []) + r.get("shadows", []):
        out[_member_id(a["member"])] = (a["outcome"], a.get("value"), a.get("score"))
    return out


def score(
    receipts: Iterable[dict[str, Any]],
    truth: dict[str, Any],
    key: Callable[[dict[str, Any]], str],
    *,
    compare: Callable[[Any, Any], bool] | None = None,
    positive: Any = True,
) -> dict[str, Any]:
    """Score every member against a held-out truth, from receipts alone.

    ``truth`` maps an input id to its expected value and ``key`` derives that id
    from a receipt (usually from ``receipt["input"]``). Receipts whose id is not
    in ``truth`` are ignored, so a live receipt stream and a labelled subset
    can be scored with the same call.

    Per member: how many labelled calls it *answered* (a value, whether or not
    it was the one the socket returned), its accuracy on those, its coverage
    (answered / labelled calls it saw), and — when ``truth`` is binary —
    precision, recall and F1 for ``positive``. Abstentions are counted, not
    scored: a member that abstains on the hard cases and is right on the rest
    is exactly what a code member should be, and the coverage number is what
    says how much of the work it took.
    """
    # Receipt values are record-shaped (dataclasses became dicts on the way
    # in), so the truth is put through the same transform before comparing.
    eq = compare or (lambda a, b: a == b)
    pos = _jsonable(positive)
    per: dict[str, dict[str, Any]] = {}
    n_labelled = 0
    for r in receipts:
        k = key(r)
        if k not in truth:
            continue
        n_labelled += 1
        expected = _jsonable(truth[k])
        for mid, (outcome, value, _score) in _member_values(r).items():
            m = per.setdefault(mid, {"saw": 0, "answered": 0, "correct": 0, "abstained": 0, "failed": 0,
                                     "tp": 0, "fp": 0, "fn": 0, "tn": 0})
            m["saw"] += 1
            if outcome == "value":
                m["answered"] += 1
                right = eq(value, expected)
                m["correct"] += 1 if right else 0
                is_pos_truth = eq(expected, pos)
                is_pos_val = eq(value, pos)
                if is_pos_val and is_pos_truth:
                    m["tp"] += 1
                elif is_pos_val:
                    m["fp"] += 1
                elif is_pos_truth:
                    m["fn"] += 1
                else:
                    m["tn"] += 1
            elif outcome == "abstained":
                m["abstained"] += 1
            else:
                m["failed"] += 1
    out: dict[str, Any] = {"labelled": n_labelled, "members": {}}
    for mid, m in per.items():
        p = m["tp"] / (m["tp"] + m["fp"]) if (m["tp"] + m["fp"]) else None
        rc = m["tp"] / (m["tp"] + m["fn"]) if (m["tp"] + m["fn"]) else None
        f1 = 2 * p * rc / (p + rc) if p and rc else None
        out["members"][mid] = {
            "saw": m["saw"], "answered": m["answered"], "abstained": m["abstained"], "failed": m["failed"],
            "coverage": round(m["answered"] / m["saw"], 4) if m["saw"] else None,
            "accuracy": round(m["correct"] / m["answered"], 4) if m["answered"] else None,
            "precision": round(p, 4) if p is not None else None,
            "recall": round(rc, 4) if rc is not None else None,
            "f1": round(f1, 4) if f1 is not None else None,
            "confusion": {"tp": m["tp"], "fp": m["fp"], "fn": m["fn"], "tn": m["tn"]},
        }
    return out


def _bucket(sc: float, width: float) -> float:
    return round(math.floor(sc / width + 1e-9) * width, 6)


def by_band(
    receipts: Iterable[dict[str, Any]],
    truth: dict[str, Any],
    key: Callable[[dict[str, Any]], str],
    scorer: str,
    *,
    width: float = 0.05,
    constants: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Every member's accuracy inside each bucket of one member's score.

    ``scorer`` is a member that returns a score on its answers or abstentions
    (a lexical similarity, a classifier margin). For each bucket of that score
    this reports how often every member that answered was right, beside the
    accuracy of each constant answer in ``constants`` (``{"always-no": False}``).
    A bucket where a constant beats the model is a bucket the model should not
    be asked about; that is the placement decision, read off labels.
    """
    consts = constants or {}
    acc: dict[float, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for r in receipts:
        k = key(r)
        if k not in truth:
            continue
        expected = _jsonable(truth[k])
        vals = _member_values(r)
        if scorer not in vals or vals[scorer][2] is None:
            continue
        lo = _bucket(vals[scorer][2], width)
        for name, c in consts.items():
            cell = acc[lo][name]
            cell[1] += 1
            cell[0] += 1 if _jsonable(c) == expected else 0
        for mid, (outcome, value, _sc) in vals.items():
            if outcome == "value":
                cell = acc[lo][mid]
                cell[1] += 1
                cell[0] += 1 if value == expected else 0
    out = []
    for lo in sorted(acc):
        row: dict[str, Any] = {"lo": lo, "hi": round(lo + width, 6), "n": max(v[1] for v in acc[lo].values())}
        row["accuracy"] = {name: {"n": v[1], "accuracy": round(v[0] / v[1], 4)} for name, v in acc[lo].items() if v[1]}
        out.append(row)
    return out


def band(
    receipts: Iterable[dict[str, Any]],
    truth: dict[str, Any],
    key: Callable[[dict[str, Any]], str],
    member: str,
    *,
    width: float = 0.05,
    positive: Any = True,
) -> list[dict[str, Any]]:
    """Where a scoring member abstained, what was the truth, by score bucket?

    This is the escalation band made visible: the lowest bucket at which the
    positive rate clears the precision you want is where a code member could
    stop abstaining and answer. Buckets are ``[lo, lo + width)`` on the
    abstention's ``score``; a member whose abstentions carry no score
    contributes nothing here.
    """
    buckets: dict[float, list[int]] = defaultdict(lambda: [0, 0])
    pos = _jsonable(positive)
    for r in receipts:
        k = key(r)
        if k not in truth:
            continue
        for mid, (outcome, _value, sc) in _member_values(r).items():
            if mid == member and outcome == "abstained" and sc is not None:
                lo = _bucket(sc, width)
                b = buckets[lo]
                b[0] += 1
                b[1] += 1 if _jsonable(truth[k]) == pos else 0
    return [{"lo": lo, "hi": round(lo + width, 6), "n": n, "positiveRate": round(pos / n, 4)}
            for lo, (n, pos) in sorted(buckets.items())]


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print("usage: chipsocket report <receipts.jsonl>", file=sys.stderr)
        return 2
    if args[0] == "report":
        args = args[1:]
    if not args:
        print("usage: chipsocket report <receipts.jsonl>", file=sys.stderr)
        return 2
    print(json.dumps(summarize(load_receipts(args[0])), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
