"""The member wrappers: banded and recorded."""

from __future__ import annotations

import pytest

from chipsocket import Abstain, Answer, Socket, SocketExhausted, banded, code, model, recorded


class Collect(list):
    def __call__(self, doc):
        self.append(doc)


def test_banded_runs_inner_only_inside_the_band():
    asked = []

    def gateway(req):
        asked.append(req["prompt"])
        return {"v": True}

    inner = model(gateway, prompt=lambda x: x["text"], result=lambda d: d["v"], profile="mid", id="mid")
    m = banded(inner, lambda x: x["s"], 0.2, 0.8)
    assert m.id == "mid" and m.kind == "model" and m.digest != inner.digest
    out = m.call({"text": "hi", "s": 0.1}, {})
    assert isinstance(out, Abstain) and out.reason == "outside-band" and out.score == 0.1
    assert m.call({"text": "hi", "s": 0.8}, {}).reason == "outside-band"
    assert isinstance(m.call({"text": "hi", "s": 0.5}, {}), Answer)
    assert asked == ["hi"]
    unscored = m.call({"text": "x", "s": None}, {})
    assert isinstance(unscored, Abstain) and unscored.score is None
    with pytest.raises(ValueError):
        banded(inner, lambda x: 0.0, 0.8, 0.2)


def test_banded_digest_moves_with_the_cuts():
    inner = code(lambda x: x)
    a = banded(inner, lambda x: x, 0.1, 0.9)
    b = banded(inner, lambda x: x, 0.2, 0.9)
    assert a.digest != b.digest
    assert banded(inner, lambda x: x, 0.1, 0.9).digest == a.digest


def test_recorded_seats_an_existing_verdict_as_primary():
    rows = {"a": True, "b": None, "c": Answer(value=False, tuple_key="minimax/M3@treat")}
    m = recorded(lambda x: rows[x], id="incumbent", digest="treatment-hash", kind="model")
    assert m.kind == "model" and m.digest.startswith("sha256:")
    assert recorded(lambda x: None, id="i", digest="sha256:abcd").digest == "sha256:abcd"
    sink = Collect()
    s = Socket("s", output=bool, sink=sink).member(m)
    assert s("a") is True and s("c") is False
    assert sink[0]["terminalReason"] == "answered-by-model"
    assert sink[1]["attempts"][0]["tuple"] == "minimax/M3@treat"
    with pytest.raises(SocketExhausted):
        s("b")
    assert sink[2]["attempts"][0]["outcome"] == "abstained" and sink[2]["attempts"][0]["reason"] == "unrecorded"
