from __future__ import annotations

import random
from dataclasses import dataclass

import pytest

from chipsocket import (
    Abstain,
    Answer,
    GatewayResult,
    SchemaFailure,
    Socket,
    SocketExhausted,
    TransportFailure,
    baseline,
    code,
    contract,
    model,
)
from chipsocket.socket import TERMINAL_REASONS, TRIGGERS


@dataclass(frozen=True)
class Verdict:
    verdict: str
    reason: str


def parse_verdict(d):
    if isinstance(d, Verdict):
        return d
    if d["verdict"] not in ("verified", "dropped", "needs_review"):
        raise ValueError("bad verdict")
    return Verdict(d["verdict"], d["reason"])


def short_reason(v: Verdict) -> bool:
    return len(v.reason.split()) <= 25


def rules(claim: str):
    if any(ch.isdigit() for ch in claim):
        return Verdict("verified", "has a number")
    if claim.lower().startswith(("we should", "you must")):
        return Verdict("dropped", "opinion")
    return Abstain("band", score=0.4)


class FakeGateway:
    """Canned answers; records requests; can be told to fail."""

    def __init__(self, answer="verified", cost=0.001, fail=None):
        self.answer, self.cost, self.fail = answer, cost, fail
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        if self.fail == "transport":
            raise TimeoutError("provider timed out")
        if self.fail == "schema":
            return {"verdict": "maybe", "reason": "?"}
        if self.fail == "raise-typed":
            raise TransportFailure("429")
        return GatewayResult(data={"verdict": self.answer, "reason": "model said so"},
                             cost_usd=self.cost, tuple_key="fake/mid@profile", model="fake-mid")


def make(sink, harvest=None, **kw):
    s = Socket("test.admissible", input=str, output=Verdict, invariants=[short_reason],
               sink=sink, harvest=harvest, rng=random.Random(7), **kw)
    return s


class Collect(list):
    def __call__(self, doc):
        self.append(doc)


def test_code_member_answers_and_receipt_shape():
    sink = Collect()
    s = make(sink).member(code(rules))
    assert s("Budget rose 3% in 2025") == Verdict("verified", "has a number")
    r = sink[0]
    assert r["schema"] == "chip.socket-receipt/v0alpha1"
    assert r["socket"] == "test.admissible"
    assert r["runStatus"] == "completed"
    assert r["terminalReason"] == "answered-by-code"
    assert r["semanticOutcome"] == "answered"
    assert r["strategy"] == "first-success"
    assert r["canary"] is False
    assert r["answeredBy"].startswith("code:rules@sha256:")
    assert len(r["attempts"]) == 1
    a = r["attempts"][0]
    assert a["trigger"] == "primary" and a["outcome"] == "value"
    assert a["costUsd"] == 0.0
    assert r["digests"]["contract"].startswith("sha256:")
    assert "rules" in r["digests"]["members"]
    assert r["cost"]["usd"] == 0.0
    assert r["input"] == "Budget rose 3% in 2025"


def test_escalates_on_abstain_to_model():
    sink = Collect()
    gw = FakeGateway()
    s = make(sink).member(code(rules)).member(
        model(gw, prompt=lambda c: f"Claim: {c}", result=parse_verdict, profile="mid"))
    v = s("The agency changed course")
    assert v == Verdict("verified", "model said so")
    r = sink[0]
    assert r["terminalReason"] == "answered-by-model"
    assert [a["trigger"] for a in r["attempts"]] == ["primary", "abstained"]
    assert r["attempts"][0]["outcome"] == "abstained"
    assert r["attempts"][0]["reason"] == "band"
    assert r["attempts"][0]["score"] == 0.4
    assert r["attempts"][1]["tuple"] == "fake/mid@profile"
    assert r["attempts"][1]["meta"] == {"model": "fake-mid"}
    assert r["cost"]["usd"] == pytest.approx(0.001)
    # the model member saw the rendered prompt and the run id
    req = gw.requests[0]
    assert req["prompt"] == "Claim: The agency changed course"
    assert req["profile"] == "mid" and req["run"].startswith("srun_")


def test_invariant_failure_is_recorded_and_escalates():
    sink = Collect()

    def wordy(_c):
        return Verdict("verified", " ".join(["word"] * 30))

    gw = FakeGateway()
    s = make(sink).member(code(wordy)).member(
        model(gw, prompt=str, result=parse_verdict, profile="mid"))
    v = s("anything")
    assert v.reason == "model said so"
    r = sink[0]
    assert r["attempts"][0]["outcome"] == "invariant"
    assert r["attempts"][0]["reason"] == "short_reason"
    assert r["attempts"][0]["value"]["reason"].startswith("word")  # the repaired value stays on the receipt
    assert r["attempts"][1]["trigger"] == "invariant"


def test_output_type_is_an_invariant():
    sink = Collect()
    s = make(sink).member(code(lambda c: "not a verdict"))
    with pytest.raises(SocketExhausted):
        s("x")
    assert sink[0]["attempts"][0]["outcome"] == "invariant"
    assert "contract says Verdict" in sink[0]["attempts"][0]["reason"]


@pytest.mark.parametrize("fail,outcome", [("transport", "transport"), ("schema", "schema"), ("raise-typed", "transport")])
def test_typed_failures(fail, outcome):
    sink = Collect()
    gw_bad = FakeGateway(fail=fail)
    gw_ok = FakeGateway(answer="dropped")
    s = (make(sink)
         .member(model(gw_bad, prompt=str, result=parse_verdict, profile="mid"))
         .member(model(gw_ok, prompt=str, result=parse_verdict, profile="gold")))
    assert s("c").verdict == "dropped"
    r = sink[0]
    assert r["attempts"][0]["outcome"] == outcome
    assert r["attempts"][1]["trigger"] == outcome


def test_untyped_exception_is_error_not_a_crash():
    sink = Collect()

    def boom(_c):
        raise KeyError("oops")

    s = make(sink).member(code(boom)).member(code(rules))
    assert s("2 things").verdict == "verified"
    assert sink[0]["attempts"][0]["outcome"] == "error"
    assert "KeyError" in sink[0]["attempts"][0]["reason"]
    assert sink[0]["attempts"][1]["trigger"] == "error"


def test_exhausted_writes_failed_receipt_and_raises():
    sink = Collect()
    s = make(sink).member(code(lambda c: Abstain("no")))
    with pytest.raises(SocketExhausted) as ei:
        s("x")
    assert ei.value.receipt["runStatus"] == "failed"
    assert ei.value.receipt["terminalReason"] == "exhausted"
    assert ei.value.receipt["answeredBy"] is None
    assert sink[0] is ei.value.receipt


def test_shadow_never_answers_and_records_agreement():
    sink = Collect()
    gold = FakeGateway(answer="dropped", cost=0.01)
    s = make(sink).member(code(rules)).shadow(
        model(gold, prompt=str, result=parse_verdict, profile="gold"), sample=1.0)
    assert s("costs 5 dollars").verdict == "verified"  # code answered; gold said dropped; caller sees code
    r = sink[0]
    assert r["answeredBy"].startswith("code:")
    assert len(r["shadows"]) == 1
    sh = r["shadows"][0]
    assert sh["trigger"] == "sampled" and sh["outcome"] == "value" and sh["agrees"] is False
    assert r["cost"]["usd"] == pytest.approx(0.01)
    assert r["noise"] is None


def test_shadow_sampling_uses_rng():
    sink = Collect()
    gold = FakeGateway()
    s = make(sink).member(code(rules)).shadow(
        model(gold, prompt=str, result=parse_verdict, profile="gold"), sample=0.5)
    for _ in range(200):
        s("1")
    sampled = sum(1 for r in sink if r["shadows"])
    assert 60 < sampled < 140
    assert len(gold.requests) == sampled


def test_shadow_budget_skips_and_resets_daily():
    now = [1_700_000_000.0]
    sink = Collect()
    gold = FakeGateway(cost=0.6)
    s = make(sink, clock=lambda: now[0]).member(code(rules)).shadow(
        model(gold, prompt=str, result=parse_verdict, profile="gold"), sample=1.0, budget_usd_daily=1.0)
    for c in ("1", "2", "3"):
        s(c)
    outcomes = [r["shadows"][0]["outcome"] for r in sink]
    assert outcomes == ["value", "value", "skipped"]
    assert sink[2]["shadows"][0]["trigger"] == "budget"
    now[0] += 86_400
    s("4")
    assert sink[3]["shadows"][0]["outcome"] == "value"


def test_noise_reruns_the_answering_member():
    sink = Collect()
    calls = {"n": 0}

    def flaky(_c):
        calls["n"] += 1
        return Verdict("verified" if calls["n"] % 2 else "dropped", "r")

    gold = FakeGateway()
    s = make(sink).member(code(flaky)).shadow(
        model(gold, prompt=str, result=parse_verdict, profile="gold"), sample=1.0, noise=True)
    s("x")
    n = sink[0]["noise"]
    assert n["member"].startswith("code:flaky@")
    assert n["outcome"] == "value" and n["agrees"] is False


def test_shadow_failure_never_breaks_the_call():
    sink = Collect()
    s = make(sink).member(code(rules)).shadow(
        model(FakeGateway(fail="transport"), prompt=str, result=parse_verdict, profile="gold"), sample=1.0)
    assert s("2 things").verdict == "verified"
    assert sink[0]["shadows"][0]["outcome"] == "transport"


def test_canary_places_member_first_on_fraction():
    sink = Collect()
    s = make(sink).member(code(rules)).canary(code(lambda c: Verdict("dropped", "canary"), id="cand"), fraction=0.5)
    for _ in range(100):
        s("1")
    canaried = [r for r in sink if r["canary"]]
    assert 30 < len(canaried) < 70
    assert all(r["strategy"] == "canary" and r["answeredBy"].startswith("code:cand@") for r in canaried)
    assert all(r["strategy"] == "first-success" for r in sink if not r["canary"])


def test_harvest_weights_deferrals_and_disagreement():
    sink, harvest = Collect(), Collect()
    gw = FakeGateway(answer="needs_review")
    gold = FakeGateway(answer="dropped")
    s = (make(sink, harvest=harvest).member(code(rules))
         .member(model(gw, prompt=str, result=parse_verdict, profile="mid"))
         .shadow(model(gold, prompt=str, result=parse_verdict, profile="gold"), sample=1.0))
    s("in 2025 it rose")        # code closes, gold disagrees -> weight 1 + 0 + 2
    s("the agency moved")       # abstain -> model; gold disagrees -> 1 + 1 + 2
    assert [h["weight"] for h in harvest] == [3, 4]
    assert harvest[1]["triggers"] == ["primary", "abstained"]
    assert sink[0]["harvest"] == f"harvest:test.admissible#{sink[0]['run']}"


def test_clean_transforms_what_enters_the_record():
    sink = Collect()
    s = make(sink, clean=lambda v: "<redacted>").member(code(rules))
    s("secret 1")
    assert sink[0]["input"] == "<redacted>"
    assert sink[0]["attempts"][0]["value"] == "<redacted>"


def test_serve_runs_only_code_members_and_declines():
    sink = Collect()
    gw = FakeGateway()
    s = make(sink).member(code(rules)).member(model(gw, prompt=str, result=parse_verdict, profile="mid"))
    ans = s.serve("3 numbers")
    assert isinstance(ans, Answer) and ans.value.verdict == "verified"
    assert s.serve("no numbers here") is None
    assert gw.requests == []
    assert len(sink) == 2 and sink[1]["terminalReason"] == "exhausted"


def test_contract_decorator():
    sink = Collect()

    @contract(id="decorated", input=str, output=Verdict, sink=sink, rng=random.Random(1))
    def admissible(claim: str):
        """docstring survives"""
        return rules(claim)

    assert isinstance(admissible, Socket)
    assert admissible.__doc__ == "docstring survives"
    assert admissible("1").verdict == "verified"
    assert sink[0]["socket"] == "decorated"


def test_contract_decorator_to_paths(tmp_path):
    @contract(input=str, output=Verdict, sink=tmp_path / "r.jsonl", harvest=tmp_path / "h.jsonl")
    def admissible(claim: str):
        return rules(claim)

    admissible("1")
    assert (tmp_path / "r.jsonl").read_text().count("\n") == 1
    assert (tmp_path / "h.jsonl").read_text().count("\n") == 1


def test_duplicate_member_ids_rejected():
    s = make(Collect()).member(code(rules))
    with pytest.raises(ValueError):
        s.member(code(rules))
    with pytest.raises(ValueError):
        s.shadow(code(rules), sample=1.0)


def test_baseline_member_prompts_with_the_raw_input():
    sink = Collect()
    gw = FakeGateway()
    s = make(sink).member(baseline(gw, result=parse_verdict, profile="gold"))
    s("just the claim")
    assert gw.requests[0]["prompt"] == "just the claim"
    assert gw.requests[0]["system"] == ""
    assert sink[0]["terminalReason"] == "answered-by-baseline"
    assert sink[0]["answeredBy"].startswith("baseline:baseline:gold@")


def test_model_member_treats_already_validated_instance_as_parsed():
    calls = []

    def gateway(req):
        calls.append(req)
        return Verdict("verified", "instance")

    s = make(Collect()).member(model(gateway, prompt=str, result=Verdict, profile="mid"))
    assert s("x") == Verdict("verified", "instance")


def test_schema_failure_when_parse_rejects():
    def gateway(_req):
        return {"verdict": "nope", "reason": "x"}

    sink = Collect()
    s = make(sink).member(model(gateway, prompt=str, result=parse_verdict, profile="mid"))
    with pytest.raises(SocketExhausted):
        s("x")
    assert sink[0]["attempts"][0]["outcome"] == "schema"
    assert isinstance(SchemaFailure("x"), Exception)


def test_vocabularies_are_closed():
    assert set(TERMINAL_REASONS.values()) == {"completed", "failed"}
    assert "error" in TRIGGERS and "sampled" in TRIGGERS
