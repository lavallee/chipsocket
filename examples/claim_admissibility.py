"""The first socket: claim admissibility, runnable offline.

    uv run python examples/claim_admissibility.py
    uv run chipsocket report /tmp/claims-receipts.jsonl

A `code` member of rules answers what it can and abstains on the band; a
`model` member behind a canned gateway answers the rest; a gold member shadows
at a sample with a budget and a noise re-run. Swap `canned` for
``chipsocket.somm.SommGateway(project, workload)`` and nothing else changes.
"""

from __future__ import annotations

import random
import re
import sys
from dataclasses import dataclass

from chipsocket import Abstain, GatewayResult, contract, model, summarize
from chipsocket.report import load_receipts

VERDICTS = ("verified", "dropped", "needs_review")


@dataclass(frozen=True)
class Verdict:
    verdict: str
    reason: str


def parse_verdict(d) -> Verdict:
    if isinstance(d, Verdict):
        return d
    if d.get("verdict") not in VERDICTS:
        raise ValueError(f"verdict {d.get('verdict')!r} not in {VERDICTS}")
    return Verdict(d["verdict"], str(d.get("reason", "")))


def reason_is_short(v: Verdict) -> bool:
    return 0 < len(v.reason.split()) <= 25


_OPINION = re.compile(r"^(we|you|they|it)\s+(should|must|need to|ought to)\b", re.I)
_YEAR = re.compile(r"\b(19|20)\d{2}\b")
_NUMBER = re.compile(r"\d")
_PROPER = re.compile(r"\b[A-Z][a-z]+\s+[A-Z][a-z]+\b")


RECEIPTS = "/tmp/claims-receipts.jsonl"
HARVEST = "/tmp/claims-harvest.jsonl"


@contract(id="claims.admissible", input=str, output=Verdict, invariants=[reason_is_short],
          sink=RECEIPTS, harvest=HARVEST, rng=random.Random(42))
def admissible(claim: str) -> Verdict | Abstain:
    """Rules the lane already half-implements: concrete entity or number, opinion filter, band."""
    if _OPINION.search(claim):
        return Verdict("dropped", "opinion, not a claim")
    concrete = bool(_YEAR.search(claim) or _NUMBER.search(claim) or _PROPER.search(claim))
    if concrete and len(claim.split()) >= 6:
        return Verdict("verified", "concrete entity or number with an action")
    if len(claim.split()) < 4:
        return Verdict("dropped", "fragment")
    return Abstain("band", score=0.5 if concrete else 0.2)


def render(claim: str) -> dict:
    return {"prompt": f"Claim: {claim}\nIs this a verifiable, on-beat claim?",
            "system": "Reply as JSON: {verdict: verified|dropped|needs_review, reason: <=25 words}."}


def canned(profile: str, rng: random.Random):
    """A stand-in gateway. Gold is a little stricter than mid, and both are a little noisy."""

    def gateway(request: dict) -> GatewayResult:
        text = request["prompt"]
        strict = profile == "gold"
        drop = ("rumor" in text.lower()) or (strict and "may" in text.lower()) or rng.random() < 0.05
        v = "dropped" if drop else "verified"
        return GatewayResult(data={"verdict": v, "reason": f"{profile} judged it"},
                             cost_usd=0.003 if strict else 0.0001, tuple_key=f"canned/{profile}")

    return gateway


rng = random.Random(7)
admissible.member(model(canned("mid", rng), prompt=render, result=parse_verdict, profile="claims-mid"))
admissible.shadow(model(canned("gold", rng), prompt=render, result=parse_verdict, profile="claims-gold"),
                  sample=0.5, budget_usd_daily=0.05, noise=True)

CLAIMS = [
    "The Associated Press cut 30 newsroom jobs in 2024 after adopting AI summaries.",
    "We should all use AI more.",
    "Reuters may expand its automated earnings coverage this year.",
    "A rumor says the Times will drop its paywall.",
    "Gannett deployed an AI tool across 200 local titles in March 2025.",
    "Local outlets are experimenting with new tools.",
    "AI",
    "The Washington Post launched Haystacker to search video archives.",
]

if __name__ == "__main__":
    for c in CLAIMS * 5:
        try:
            v = admissible(c)
        except Exception as exc:  # SocketExhausted carries the receipt
            print("EXHAUSTED", c, exc, file=sys.stderr)
            continue
        print(f"{v.verdict:13} {v.reason:45} | {c[:50]}")
    print()
    import json

    print(json.dumps(summarize(load_receipts(RECEIPTS)), indent=2)[:2500])
