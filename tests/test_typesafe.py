"""The TypeSafe gateway, against a stub client."""

from __future__ import annotations

import random
from types import SimpleNamespace

import pytest

from chipsocket import (
    Abstain,
    Answer,
    SchemaFailure,
    Socket,
    SocketExhausted,
    TransportFailure,
    model,
)
from chipsocket.typesafe import TypeSafeGateway, answers_to_dict, load_key


class _Choice:
    def __init__(self, choice, probs, conf):
        self.choice, self.probabilities, self.confidence = choice, probs, conf


class _Noul:
    def __init__(self, p):
        self.noul = p


class _Client:
    def __init__(self, choice="SAME", p=0.9):
        self.calls = []
        self.choice, self.p = choice, p

    def system_one(self, *, state, questions, model=None, timeout=None):
        self.calls.append((state, sorted(questions), model, timeout))
        return SimpleNamespace(
            model="jev-1.13.0",
            usage=SimpleNamespace(input_tokens=500, output_tokens=40),
            answers={"same_event": _Choice(self.choice, {"SAME": self.p, "DIFFERENT": 1 - self.p}, abs(2 * self.p - 1)),
                     "roundup": _Noul(0.05)},
        )


def test_gateway_returns_record_shaped_answers_and_real_cost():
    c = _Client()
    gw = TypeSafeGateway({"same_event": {"type": "choice"}, "roundup": {"type": "noul"}}, client=c)
    out = gw({"state": {"a": "x", "b": "y"}})
    assert out.data["same_event"] == {"type": "choice", "choice": "SAME", "confidence": pytest.approx(0.8),
                                      "probabilities": {"SAME": 0.9, "DIFFERENT": pytest.approx(0.1)}}
    assert out.data["roundup"] == {"type": "noul", "noul": 0.05}
    assert out.cost_usd == pytest.approx(500 / 1e6 * 0.042)
    assert out.tuple_key == "typesafe/jev-1.13.0" and out.model == "jev-1.13.0"
    assert c.calls[0][0] == {"a": "x", "b": "y"} and c.calls[0][1] == ["roundup", "same_event"]


def test_gateway_as_a_scored_member_that_abstains_when_flat():
    def render(pair):
        return {"state": {"a": pair["a"], "b": pair["b"]}}

    def parse(d):
        ans = d["same_event"]
        p = ans["probabilities"]["SAME"]
        if 0.35 < p < 0.65:
            return Abstain("flat", score=p)
        return Answer(value=ans["choice"] == "SAME", score=p)

    receipts = []
    confident = TypeSafeGateway({"same_event": {"type": "choice"}}, client=_Client("SAME", 0.9))
    flat = TypeSafeGateway({"same_event": {"type": "choice"}}, client=_Client("SAME", 0.55))
    s = (Socket("s", output=bool, sink=receipts.append, rng=random.Random(0))
         .member(model(confident, prompt=render, result=parse, profile="jev")))
    assert s({"a": "x", "b": "y"}) is True
    assert receipts[0]["attempts"][0]["score"] == 0.9
    s2 = Socket("s2", output=bool, sink=receipts.append).member(model(flat, prompt=render, result=parse, profile="jev"))
    with pytest.raises(SocketExhausted):
        s2({"a": "x", "b": "y"})
    assert receipts[1]["attempts"][0]["outcome"] == "abstained" and receipts[1]["attempts"][0]["score"] == 0.55
    # the flat answer was still a paid call, and the receipt says so
    assert receipts[1]["attempts"][0]["costUsd"] == pytest.approx(500 / 1e6 * 0.042)
    assert receipts[1]["cost"]["usd"] == pytest.approx(500 / 1e6 * 0.042)


def test_errors_are_typed_and_key_lookup(tmp_path, monkeypatch):
    class Down:
        def system_one(self, **kw):
            raise ConnectionError("no route")

    gw = TypeSafeGateway({"q": {"type": "noul"}}, client=Down())
    with pytest.raises(TransportFailure):
        gw({"state": "x"})
    with pytest.raises(SchemaFailure):
        gw({})
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert load_key(tmp_path / "missing.env") is None
    (tmp_path / ".env").write_text("OTHER=1\nTYPESAFE_API_KEY='abc'\n")
    assert load_key(tmp_path / ".env") == "abc"
    with pytest.raises(ValueError):
        TypeSafeGateway({})
    assert answers_to_dict(SimpleNamespace(answers={"x": object()}))["x"]["type"] == "object"
