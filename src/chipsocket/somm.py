"""somm as the meter: a gateway for model members and a workload handler for code members.

Two directions, both inside somm's existing surfaces:

- :class:`SommGateway` is a gateway callable for :func:`chipsocket.members.model`.
  Every model member call is an ordinary somm call under the socket's workload
  id, so cost, latency, outcome, and provenance land where they already land.
  The socket's run id is passed as somm's ``session_id`` so a receipt joins to
  its calls.
- :class:`SocketHandler` is a somm workload handler. ``serve`` runs the socket's
  code members and declines otherwise, so the provider chain answers what the
  code could not, under the same workload id, and "should this still be a
  model call" is a ``GROUP BY provider``.

``somm`` is imported lazily; the runner does not depend on it.

Shadow and noise calls carry ``observation_role="shadow"`` and the primary's
``source_call_id`` on their somm rows when the request says so
(``request["role"]`` / ``request["source_call_id"]``, somm >= 0.17), so they
stay out of somm's rollups and budgets and join to the call they shadow. They
also share the run id as ``session_id``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from chipsocket.members import GatewayResult, SchemaFailure, TransportFailure
from chipsocket.socket import Socket


class SommGateway:
    """``gateway(request) -> GatewayResult`` backed by ``somm.llm(...).generate_structured``."""

    def __init__(
        self,
        project: str,
        workload: str,
        *,
        model: str | None = None,
        provider: str | None = None,
        max_tokens: int = 512,
        temperature: float = 0.1,
        retries: int = 1,
        allow_fallback: bool | None = None,
        client: Any = None,
    ):
        self.project = project
        self.workload = workload
        self.model = model
        self.provider = provider
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.retries = retries
        self.allow_fallback = allow_fallback
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            import somm

            self._client = somm.llm(project=self.project)
        return self._client

    def __call__(self, request: dict[str, Any]) -> GatewayResult:
        schema = request.get("schema")
        if schema is None:
            raise SchemaFailure("model member has no result contract to hand somm")
        try:
            from somm.client import SommStructuredError
        except Exception:  # pragma: no cover - somm absent; the error path below still types
            SommStructuredError = ()
        try:
            obj, res = self.client.generate_structured(
                request.get("prompt", ""),
                schema=schema,
                system=request.get("system", ""),
                workload=self.workload,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                model=self.model,
                provider=self.provider,
                retries=self.retries,
                allow_fallback=self.allow_fallback,
                session_id=request.get("run"),
                observation_role=request.get("role") or "production",
                source_call_id=request.get("source_call_id"),
            )
        except SommStructuredError as exc:
            raise SchemaFailure(str(exc)) from exc
        except Exception as exc:
            raise TransportFailure(f"{type(exc).__name__}: {exc}") from exc
        provider = getattr(res, "provider", None) or self.provider or ""
        served = getattr(res, "model", None) or self.model or ""
        # Everything the meter knows about the call travels with the result;
        # the receipt is then a join to somm's ledger, not a re-derivation.
        return GatewayResult(
            data=obj,
            cost_usd=float(getattr(res, "cost_usd", 0.0) or 0.0),
            tuple_key=f"{provider}/{served}@{request.get('profile', '')}",
            model=served,
            call_id=getattr(res, "call_id", None),
            provider=provider or None,
            tokens_in=getattr(res, "tokens_in", None),
            tokens_out=getattr(res, "tokens_out", None),
            cache_tokens_in=getattr(res, "cache_tokens_in", None),
            reasoning_tokens=getattr(res, "reasoning_tokens", None),
            latency_ms=getattr(res, "latency_ms", None),
            cost_basis="reported" if getattr(res, "cost_usd", None) is not None else None,
        )


class SocketHandler:
    """A somm ``WorkloadHandler`` whose ``serve`` is the socket's deterministic path.

    ``decode`` turns somm's ``WorkloadRequest`` into the contract's input;
    ``encode`` turns a value into the text somm records. Both are the caller's,
    because only the caller knows how its prompt carries the input.
    """

    def __init__(
        self,
        socket: Socket,
        *,
        decode: Callable[[Any], Any],
        encode: Callable[[Any], str],
        kind: str = "socket",
    ):
        self.socket = socket
        self.decode = decode
        self.encode = encode
        self.kind = kind

    def serve(self, request: Any) -> Any:
        from somm.workloads import WorkloadResult

        x = self.decode(request)
        answer = self.socket.serve(x)
        if answer is None:
            return None
        return WorkloadResult(text=self.encode(answer.value), model=answer.tuple_key or "", cost_usd=0.0)


def bind(socket: Socket, workload: str, *, decode: Callable[[Any], Any], encode: Callable[[Any], str],
         kind: str = "socket", replace: bool = False) -> SocketHandler:
    """Register a handler for ``socket`` and bind ``workload`` to it in somm."""
    from somm import workloads

    handler = SocketHandler(socket, decode=decode, encode=encode, kind=kind)
    workloads.register(handler, replace=replace)
    workloads.bind(workload, kind, socket=socket.id)
    return handler
