"""Members: the implementations seated in a socket.

A member is a pure function of its input plus at most one gateway call, the
same discipline as a chip implementation. It returns exactly one of three
things: an :class:`Answer`, an :class:`Abstain`, or a typed
:class:`MemberFailure`. Nothing else leaves a member; an uncaught exception is
classified as ``error`` by the socket and recorded like any other outcome.

Three kinds:

- ``code``: deterministic, no gateway. Built from an ordinary function.
- ``model``: a prompt, a result contract, and a gateway at some profile.
- ``baseline``: the raw model with the result contract as its only
  instruction. Kept so that "does the model still need this envelope" has a
  standing comparator rather than a one-off.

The gateway is a callable ``gateway(request: dict) -> GatewayResult | dict``,
mirroring the host execution contract's ``gateway(request) -> dict``. The
runner never holds credentials or knows which provider answered; that is the
gateway's business, and ``chipsocket.somm`` is one gateway.
"""

from __future__ import annotations

import hashlib
import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

#: Closed vocabulary of member kinds.
KINDS: tuple[str, ...] = ("code", "model", "baseline")


@dataclass(frozen=True, slots=True)
class Abstain:
    """A first-class answer: the member could not decide, and says why.

    ``score`` is optional and is an ordering, not a probability. A code member
    that returns its lexical similarity beside ``Abstain("band")`` lets the
    escalation band be learned from harvested labels instead of guessed.
    """

    reason: str
    score: float | None = None
    #: What producing the abstention cost. A model member that asked and found
    #: the distribution flat still paid for the call; a budget must see it.
    cost_usd: float = 0.0


@dataclass(frozen=True, slots=True)
class Answer:
    """A member's value, with what it cost to produce."""

    value: Any
    score: float | None = None
    cost_usd: float = 0.0
    #: The evaluated tuple that produced a model answer (spec §21): provider,
    #: served model, profile. ``None`` for code members.
    tuple_key: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


class MemberFailure(Exception):
    """A typed failure. ``outcome`` is one of ``schema`` or ``transport``."""

    outcome: str = "error"

    def __init__(self, detail: str = ""):
        super().__init__(detail)
        self.detail = detail


class SchemaFailure(MemberFailure):
    """The result did not parse or validate against the result contract."""

    outcome = "schema"


class TransportFailure(MemberFailure):
    """The gateway call failed: timeout, rate limit, provider exhausted."""

    outcome = "transport"


@dataclass(frozen=True, slots=True)
class GatewayResult:
    """What a gateway returns. A plain dict is accepted and treated as ``data``."""

    data: Any
    cost_usd: float = 0.0
    tuple_key: str | None = None
    model: str | None = None


@dataclass(frozen=True, slots=True)
class Member:
    """One implementation of a contract, with a stable identity."""

    id: str
    kind: str
    digest: str
    call: Callable[[Any, dict[str, Any]], Answer | Abstain]
    profile: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"member kind {self.kind!r} is not one of {KINDS}")
        if not self.id:
            raise ValueError("a member needs a non-empty id")

    @property
    def ref(self) -> str:
        """``kind:id@digest`` — how the member appears on a receipt."""
        return f"{self.kind}:{self.id}@{self.digest}"


def _sha(*parts: str) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8"))
        h.update(b"\0")
    return "sha256:" + h.hexdigest()[:16]


def _source_digest(fn: Callable[..., Any]) -> str:
    """Digest a function by its source where possible, else by its qualified name.

    Source is the honest identity for a code member: two deployments with the
    same function name and different bodies must not share receipts.
    """
    try:
        return _sha(inspect.getsource(fn))
    except (OSError, TypeError):
        return _sha(getattr(fn, "__module__", ""), getattr(fn, "__qualname__", repr(fn)))


def code(fn: Callable[[Any], Any], *, id: str | None = None) -> Member:
    """Seat an ordinary function as a ``code`` member.

    ``fn`` may return a plain value (wrapped as an :class:`Answer`), an
    :class:`Answer`, or an :class:`Abstain`.
    """

    def call(x: Any, _ctx: dict[str, Any]) -> Answer | Abstain:
        out = fn(x)
        if isinstance(out, (Answer, Abstain)):
            return out
        return Answer(value=out)

    return Member(id=id or fn.__name__, kind="code", digest=_source_digest(fn), call=call)


def _to_gateway_result(raw: Any) -> GatewayResult:
    if isinstance(raw, GatewayResult):
        return raw
    return GatewayResult(data=raw)


def model(
    gateway: Callable[[dict[str, Any]], Any],
    *,
    prompt: Callable[[Any], str | dict[str, Any]],
    result: Any,
    profile: str,
    id: str | None = None,
    system: str = "",
    parse: Callable[[Any], Any] | None = None,
    kind: str = "model",
) -> Member:
    """Seat a prompt plus result contract plus gateway profile as a ``model`` member.

    ``prompt(x)`` renders the request for one input: a string, or a dict that is
    merged into the gateway request. ``result`` is the result contract handed
    to the gateway (a pydantic model, a JSON schema dict, or a callable), and is
    also what ``parse`` defaults to when it is callable. The gateway's
    exceptions become :class:`TransportFailure`; a parse failure becomes
    :class:`SchemaFailure`. The member never sees credentials.
    """
    parser = parse if parse is not None else (result if callable(result) else None)
    template_id = getattr(prompt, "__qualname__", repr(prompt))
    result_id = getattr(result, "__qualname__", None) or getattr(result, "__name__", None) or repr(result)
    digest = _sha(kind, profile, system, template_id, str(result_id), _source_digest(prompt))

    def call(x: Any, ctx: dict[str, Any]) -> Answer | Abstain:
        rendered = prompt(x)
        request: dict[str, Any] = {"profile": profile, "system": system, "schema": result, "run": ctx.get("run")}
        if isinstance(rendered, dict):
            request.update(rendered)
        else:
            request["prompt"] = rendered
        try:
            raw = gateway(request)
        except MemberFailure:
            raise
        except Exception as exc:  # the gateway is the only I/O a member does
            raise TransportFailure(f"{type(exc).__name__}: {exc}") from exc
        gr = _to_gateway_result(raw)
        data = gr.data
        if isinstance(data, Abstain):
            return Abstain(data.reason, data.score, cost_usd=data.cost_usd or gr.cost_usd)
        if parser is not None and not _already_parsed(data, result):
            try:
                data = parser(data)
            except Exception as exc:
                raise SchemaFailure(f"{type(exc).__name__}: {exc}") from exc
        meta = {"model": gr.model} if gr.model else {}
        # A parser may decide, not just decode: it can return an Abstain (the
        # distribution was flat) or an Answer carrying a score. The gateway's
        # cost and tuple still belong on the record either way.
        if isinstance(data, Abstain):
            return Abstain(data.reason, data.score, cost_usd=gr.cost_usd)
        if isinstance(data, Answer):
            return Answer(value=data.value, score=data.score, cost_usd=gr.cost_usd,
                          tuple_key=data.tuple_key or gr.tuple_key or profile, meta={**meta, **data.meta})
        return Answer(value=data, cost_usd=gr.cost_usd, tuple_key=gr.tuple_key or profile, meta=meta)

    return Member(id=id or profile, kind=kind, digest=digest, call=call, profile=profile)


def _already_parsed(data: Any, result: Any) -> bool:
    """A gateway that validates (somm's generate_structured does) returns an instance."""
    return inspect.isclass(result) and isinstance(data, result)


def baseline(
    gateway: Callable[[dict[str, Any]], Any],
    *,
    result: Any,
    profile: str,
    id: str | None = None,
    parse: Callable[[Any], Any] | None = None,
) -> Member:
    """The raw model: the input as the whole prompt, the result contract as the only instruction."""

    def prompt(x: Any) -> dict[str, Any]:
        return {"prompt": x if isinstance(x, str) else repr(x)}

    return model(gateway, prompt=prompt, result=result, profile=profile, id=id or f"baseline:{profile}",
                 parse=parse, kind="baseline")
