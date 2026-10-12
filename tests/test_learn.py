"""Learning the band from receipts and labels, and receipting what was learned."""

from __future__ import annotations

import random

import pytest

from chipsocket import Abstain, Answer, Cuts, Socket, code, cut_member, cuts

# A score that separates: below 0.3 almost always negative, above 0.7 almost
# always positive, mixed between. 50 inputs per band.


def _population():
    rng = random.Random(1)
    rows = []
    for i in range(50):
        rows.append((f"lo{i}", rng.uniform(0.0, 0.3), rng.random() < 0.02))
    for i in range(50):
        rows.append((f"mid{i}", rng.uniform(0.3, 0.7), rng.random() < 0.5))
    for i in range(50):
        rows.append((f"hi{i}", rng.uniform(0.7, 1.0), rng.random() < 0.98))
    return rows


def _receipts(rows):
    receipts = []

    def scorer(x):
        return Abstain("band", score=x["s"])

    s = Socket("s", output=bool, sink=receipts.append).member(code(scorer, id="lex")).member(code(lambda x: True, id="yes"))
    for k, sc, _ in rows:
        s({"id": k, "s": sc})
    return receipts


def test_cuts_find_the_bands_where_a_constant_meets_the_bar():
    rows = _population()
    truth = {k: y for k, _, y in rows}
    c = cuts(_receipts(rows), truth, key=lambda r: r["input"]["id"], scorer="lex", precision=0.95, width=0.1, min_n=20)
    assert isinstance(c, Cuts)
    assert c.labelled == 150 and c.scored == 150
    assert c.lo is not None and 0.2 <= c.lo <= 0.4
    assert c.hi is not None and 0.6 <= c.hi <= 0.8
    assert c.below["accuracy"] >= 0.95 and c.above["accuracy"] >= 0.95
    assert c.below["n"] >= 20 and c.above["n"] >= 20
    assert c.evidence.startswith("sha256:")
    assert c.closes is not None and 0.4 <= c.closes <= 0.8
    d = c.to_dict()
    assert d["lo"] == c.lo and d["minN"] == 20


def test_cuts_are_a_function_of_the_evidence():
    rows = _population()
    truth = {k: y for k, _, y in rows}
    key = lambda r: r["input"]["id"]  # noqa: E731
    a = cuts(_receipts(rows), truth, key, "lex", width=0.1, min_n=20)
    b = cuts(_receipts(rows), truth, key, "lex", width=0.1, min_n=20)
    assert a.evidence == b.evidence
    flipped = dict(truth)
    flipped["lo0"] = not flipped["lo0"]
    assert cuts(_receipts(rows), flipped, key, "lex", width=0.1, min_n=20).evidence != a.evidence


def test_cuts_abstain_when_no_band_meets_the_bar():
    rows = [(f"k{i}", i / 100, i % 2 == 0) for i in range(100)]  # score says nothing about truth
    truth = {k: y for k, _, y in rows}
    c = cuts(_receipts(rows), truth, key=lambda r: r["input"]["id"], scorer="lex", precision=0.95, width=0.1, min_n=10)
    assert c.lo is None and c.hi is None and c.below == {} and c.above == {}
    assert c.closes == 0.0
    empty = cuts([], truth, key=lambda r: r["input"]["id"], scorer="lex")
    assert empty.scored == 0 and empty.closes is None
    with pytest.raises(ValueError):
        cuts([], truth, key=lambda r: "", scorer="lex", precision=0)


def test_cut_member_answers_outside_and_abstains_inside():
    rows = _population()
    truth = {k: y for k, _, y in rows}
    c = cuts(_receipts(rows), truth, key=lambda r: r["input"]["id"], scorer="lex", width=0.1, min_n=20)
    m = cut_member(lambda x: x["s"], c, negative=False, positive=True, id="lex-cuts")
    assert m.kind == "code" and m.digest.startswith("sha256:")
    lo, hi = c.lo, c.hi
    assert m.call({"s": lo - 0.01}, {}) == Answer(value=False, score=lo - 0.01)
    assert m.call({"s": hi}, {}) == Answer(value=True, score=hi)
    mid = m.call({"s": (lo + hi) / 2}, {})
    assert isinstance(mid, Abstain) and mid.reason == "band"
    assert m.call({"s": None}, {}).reason == "unscored"
    # the member is versioned by what it learned from
    other = cuts(_receipts(rows), {**truth, "lo0": not truth["lo0"]}, key=lambda r: r["input"]["id"], scorer="lex",
                 width=0.1, min_n=20)
    assert cut_member(lambda x: x["s"], other, negative=False, positive=True).digest != cut_member(
        lambda x: x["s"], c, negative=False, positive=True).digest
    # seated in a socket, it closes the tails and defers the band
    receipts = []
    s = Socket("s", output=bool, sink=receipts.append).member(m).member(code(lambda x: True, id="model"))
    for k, sc, _ in rows:
        s({"id": k, "s": sc})
    by = {}
    for r in receipts:
        by[r["answeredBy"].split(":")[1].split("@")[0]] = by.get(r["answeredBy"].split(":")[1].split("@")[0], 0) + 1
    assert by["lex-cuts"] >= 60 and by["model"] >= 30
