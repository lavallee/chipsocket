from __future__ import annotations

import json
import random
from dataclasses import dataclass

from chipsocket import Abstain, GatewayResult, Socket, code, load_receipts, model, summarize
from chipsocket.report import main


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
