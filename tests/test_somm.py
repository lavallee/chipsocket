"""The somm adapter, exercised against a stub `somm` so the suite needs no provider."""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass

import pytest

from chipsocket import Abstain, SchemaFailure, Socket, TransportFailure, code, model
from chipsocket.somm import SocketHandler, SommGateway, bind


@dataclass(frozen=True)
class V:
    verdict: str


@pytest.fixture
def stub_somm(monkeypatch):
    """A minimal `somm` package: workloads registry + a client with generate_structured."""
    somm = types.ModuleType("somm")
    workloads = types.ModuleType("somm.workloads")
    client = types.ModuleType("somm.client")

    @dataclass(frozen=True)
    class WorkloadResult:
        text: str
        model: str = ""
        cost_usd: float = 0.0

    registry: dict = {"handlers": {}, "bindings": {}}

    def register(handler, *, replace=False):
        if handler.kind in registry["handlers"] and not replace:
            raise ValueError("dup")
        registry["handlers"][handler.kind] = handler

    def bind_(workload, kind, **config):
        registry["bindings"][workload] = (kind, config)

    workloads.WorkloadResult = WorkloadResult
    workloads.register = register
    workloads.bind = bind_
    workloads.registry = registry

    class SommStructuredError(Exception):
        pass

    client.SommStructuredError = SommStructuredError

    @dataclass
    class Res:
        provider: str = "ollama"
        model: str = "qwen"
        cost_usd: float = 0.002

    class Client:
        def __init__(self):
            self.calls = []
            self.mode = "ok"

        def generate_structured(self, prompt, *, schema, **kw):
            self.calls.append((prompt, schema, kw))
            if self.mode == "bad":
                raise SommStructuredError("never valid")
            if self.mode == "down":
                raise ConnectionError("no route")
            return schema({"verdict": "verified"}), Res()

    somm.llm = lambda project: Client()
    somm.workloads = workloads
    somm.client = client
    monkeypatch.setitem(sys.modules, "somm", somm)
    monkeypatch.setitem(sys.modules, "somm.workloads", workloads)
    monkeypatch.setitem(sys.modules, "somm.client", client)
    return somm


def test_gateway_maps_result_and_errors(stub_somm):
    gw = SommGateway("proj", "claims", model="qwen", provider="ollama")
    out = gw({"prompt": "p", "schema": lambda d: V(**d), "system": "s", "profile": "mid", "run": "srun_1"})
    assert out.data == V("verified")
    assert out.cost_usd == 0.002
    assert out.tuple_key == "ollama/qwen@mid"
    prompt, schema, kw = gw.client.calls[0]
    assert kw["workload"] == "claims" and kw["session_id"] == "srun_1" and kw["system"] == "s"
    gw.client.mode = "bad"
    with pytest.raises(SchemaFailure):
        gw({"prompt": "p", "schema": V, "run": "r"})
    gw.client.mode = "down"
    with pytest.raises(TransportFailure):
        gw({"prompt": "p", "schema": V, "run": "r"})
    with pytest.raises(SchemaFailure):
        gw({"prompt": "p"})


def test_gateway_inside_a_socket(stub_somm):
    receipts = []
    s = Socket("s", output=V, sink=receipts.append).member(
        model(SommGateway("proj", "claims"), prompt=str, result=lambda d: d if isinstance(d, V) else V(**d), profile="mid"))
    assert s("claim") == V("verified")
    assert receipts[0]["attempts"][0]["tuple"] == "ollama/qwen@mid"
    assert receipts[0]["cost"]["usd"] == 0.002


def test_handler_serves_code_and_declines(stub_somm):
    receipts = []

    def rules(c: str):
        return V("verified") if "1" in c else Abstain("band")

    s = Socket("s", output=V, sink=receipts.append).member(code(rules))

    @dataclass
    class Req:
        prompt: str

    h = bind(s, "claims", decode=lambda r: r.prompt, encode=lambda v: v.verdict)
    assert stub_somm.workloads.registry["bindings"]["claims"] == ("socket", {"socket": "s"})
    assert isinstance(h, SocketHandler)
    served = h.serve(Req("claim 1"))
    assert served.text == "verified" and served.model.startswith("code:rules@")
    assert h.serve(Req("claim two")) is None
    assert [r["terminalReason"] for r in receipts] == ["answered-by-code", "exhausted"]
