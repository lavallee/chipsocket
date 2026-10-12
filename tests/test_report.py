from __future__ import annotations

import json
import random
from dataclasses import dataclass

import pytest

from chipsocket import Abstain, GatewayResult, Socket, code, load_receipts, model, summarize
from chipsocket.cli import main


@dataclass(frozen=True)
class V:
    verdict: str


def parse(d):
    return d if isinstance(d, V) else V(d["verdict"])


def rules(c: str):
    return V("verified") if any(ch.isdigit() for ch in c) else Abstain("band")


class Gateway:
    def __init__(self, answers):
        self.answers = list(answers)

    def __call__(self, _req):
        a = self.answers.pop(0)
        return GatewayResult(data={"verdict": a}, cost_usd=0.01, tuple_key="t")


def test_summarize_measures():
    receipts = []
    mid = Gateway(["verified", "dropped", "verified", "dropped", "verified", "dropped"])
    # gold shadow: agrees with code on the two code-closed calls, disagrees once on model calls
    gold = Gateway(["verified", "verified", "verified", "dropped", "dropped"])
    s = (Socket("s", output=V, sink=receipts.append, rng=random.Random(0))
         .member(code(rules))
         .member(model(mid, prompt=str, result=parse, profile="mid"))
         .shadow(model(gold, prompt=str, result=parse, profile="gold"), sample=1.0, noise=True))
    # noise re-runs the answering member; feed extra answers for the model's re-runs
    mid.answers += ["verified", "dropped", "verified"]
    for c in ["1", "2", "a", "b", "c"]:
        s(c)
    rep = summarize(receipts)["s"]
    assert rep["calls"] == 5
    assert rep["exhausted"] == 0
    assert rep["closedByCodeShare"] == 0.4
    assert rep["answeredBy"]["rules"] == 2 and rep["answeredBy"]["mid"] == 3
    assert rep["escalations"]["byTrigger"] == {"abstained": 3}
    assert rep["escalations"]["boundary"] == 3 and rep["escalations"]["operations"] == 0
    assert rep["escalations"]["dominant"] == "boundary"
    gold_m = rep["shadows"]["gold"]
    assert gold_m["sampled"] == 5 and gold_m["compared"] == 5
    assert gold_m["byAnsweringKind"]["code"]["compared"] == 2
    assert gold_m["byAnsweringKind"]["model"]["compared"] == 3
    assert gold_m["noiseFloor"] is not None and gold_m["agreementNetOfNoise"] is not None
    assert rep["members"]["mid"]["costUsd"] > 0
    assert rep["costUsd"] > rep["members"]["mid"]["costUsd"]


def test_load_and_cli(tmp_path, capsys):
    p = tmp_path / "r.jsonl"
    p.write_text(json.dumps({"socket": "x", "answeredBy": "code:f@sha256:1", "attempts": [
        {"member": "code:f@sha256:1", "trigger": "primary", "outcome": "value", "costUsd": 0}], "cost": {"usd": 0}}) + "\n")
    assert len(load_receipts(p)) == 1
    assert load_receipts(tmp_path / "missing.jsonl") == []
    assert main(["report", str(p)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["x"]["closedByCodeShare"] == 1.0
    assert main([]) == 2


def test_score_and_band_against_truth():
    receipts = []
    lex = Gateway([])  # unused

    def prefilter(x):
        s = x["lex"]
        return V("no") if s < 0.1 else Abstain("band", score=s)

    mid = Gateway(["yes", "no", "yes", "yes"])
    s = (Socket("s", output=V, sink=receipts.append, rng=random.Random(0))
         .member(code(prefilter, id="pre"))
         .member(model(mid, prompt=str, result=parse, profile="mid")))
    for c in [{"id": "a", "lex": 0.05}, {"id": "b", "lex": 0.2}, {"id": "c", "lex": 0.3}, {"id": "d", "lex": 0.6}, {"id": "e", "lex": 0.62}]:
        s(c)
    truth = {"a": V("no"), "b": V("yes"), "c": V("yes"), "d": V("no"), "e": V("yes")}
    from chipsocket import band, score
    sc = score(receipts, truth, key=lambda r: r["input"]["id"], positive=V("yes"))
    assert sc["labelled"] == 5
    pre = sc["members"]["pre"]
    assert pre["answered"] == 1 and pre["abstained"] == 4 and pre["coverage"] == 0.2 and pre["accuracy"] == 1.0
    m = sc["members"]["mid"]
    assert m["saw"] == 4 and m["answered"] == 4
    assert m["confusion"] == {"tp": 2, "fp": 1, "fn": 1, "tn": 0}
    assert m["precision"] == pytest.approx(0.6667, abs=1e-4) and m["recall"] == pytest.approx(0.6667, abs=1e-4)
    b = band(receipts, truth, key=lambda r: r["input"]["id"], member="pre", width=0.2, positive=V("yes"))
    assert [(x["lo"], x["n"], x["positiveRate"]) for x in b] == [(0.2, 2, 1.0), (0.6, 2, 0.5)]
    assert lex.answers == []


def test_by_band_compares_members_with_constants():
    receipts = []

    def prefilter(x):
        return Abstain("band", score=x["lex"])

    mid = Gateway(["yes", "no", "yes", "yes"])
    s = (Socket("s", output=V, sink=receipts.append, rng=random.Random(0))
         .member(code(prefilter, id="pre"))
         .member(model(mid, prompt=str, result=parse, profile="mid")))
    for c in [{"id": "a", "lex": 0.1}, {"id": "b", "lex": 0.12}, {"id": "c", "lex": 0.8}, {"id": "d", "lex": 0.85}]:
        s(c)
    truth = {"a": V("no"), "b": V("no"), "c": V("yes"), "d": V("yes")}
    from chipsocket import by_band
    rows = by_band(receipts, truth, key=lambda r: r["input"]["id"], scorer="pre", width=0.5,
                   constants={"always-no": V("no"), "always-yes": V("yes")})
    assert [r["lo"] for r in rows] == [0.0, 0.5]
    low, high = rows
    assert low["accuracy"]["always-no"] == {"n": 2, "accuracy": 1.0}
    assert low["accuracy"]["mid"] == {"n": 2, "accuracy": 0.5}      # said yes, no -> one right
    assert high["accuracy"]["always-yes"]["accuracy"] == 1.0 and high["accuracy"]["mid"]["accuracy"] == 1.0
