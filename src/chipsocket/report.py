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
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from chipsocket.socket import BOUNDARY_TRIGGERS, OPERATIONS_TRIGGERS


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
