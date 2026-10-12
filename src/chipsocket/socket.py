"""The socket: one contract, several live members, a strategy, a receipt per call.

See ``chip/docs/socket.md`` for the design. This module is the runner: it
holds the members, applies the strategy, checks the contract's invariants on
every value, writes one receipt per call, and harvests every input.

The runner is deliberately small and deliberately not a chip host. It has no
scheduler, no policy gate, no state store, and no authority model. A chip host
that wants sockets embeds this behind its own envelope.
"""

from __future__ import annotations

import random
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from chipsocket._record import jsonable, sha
from chipsocket.members import Abstain, Answer, Member, MemberFailure, code
from chipsocket.sinks import JsonlSink

RECEIPT_SCHEMA = "chip.socket-receipt/v0alpha1"

#: Closed vocabulary of strategies.
STRATEGIES: tuple[str, ...] = ("first-success", "shadow", "canary")

#: Why a member ran. Closed.
TRIGGERS: tuple[str, ...] = (
    "primary",  # the strategy's first choice for this call
    "abstained",  # the prior member returned an abstention
    "invariant",  # the prior member's value failed the contract's invariants
    "schema",  # the prior member's result did not parse or validate
    "transport",  # the prior member's gateway call failed
    "error",  # the prior member raised something untyped
    "budget",  # a member was skipped because its budget was spent
    "sampled",  # a shadow, canary, or noise run selected by the sample
)

#: What a member's attempt produced. Closed.
OUTCOMES: tuple[str, ...] = (
    "value", "abstained", "invariant", "schema", "transport", "error", "skipped",
)

#: The triggers that say something about *placement*, as opposed to operations.
BOUNDARY_TRIGGERS: frozenset[str] = frozenset({"abstained", "invariant"})
OPERATIONS_TRIGGERS: frozenset[str] = frozenset({"schema", "transport", "error", "budget"})

#: Terminal reasons for a socket call. Closed, like the spec's §13.2.
TERMINAL_REASONS: dict[str, str] = {
    "answered-by-code": "completed",
    "answered-by-model": "completed",
    "answered-by-baseline": "completed",
    "exhausted": "failed",
}

Sink = Callable[[dict[str, Any]], None]
Invariant = Callable[[Any], "bool | None"]


class SocketExhausted(Exception):
    """Every member abstained or failed. The receipt is attached."""

    def __init__(self, receipt: dict[str, Any]):
        super().__init__(f"socket {receipt.get('socket')!r} exhausted: no member answered")
        self.receipt = receipt


@dataclass(frozen=True, slots=True)
class ShadowSpec:
    member: Member
    sample: float
    budget_usd_daily: float | None = None
    noise: bool = False


@dataclass(frozen=True, slots=True)
class CanarySpec:
    member: Member
    fraction: float


@dataclass
class Attempt:
    member: Member
    trigger: str
    outcome: str
    started: float
    ended: float
    value: Any = None
    score: float | None = None
    reason: str | None = None
    cost_usd: float = 0.0
    tuple_key: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, clean: Callable[[Any], Any]) -> dict[str, Any]:
        out: dict[str, Any] = {
            "member": self.member.ref,
            "trigger": self.trigger,
            "outcome": self.outcome,
            "latencyMs": round((self.ended - self.started) * 1000, 3),
            "costUsd": self.cost_usd,
        }
        if self.outcome in ("value", "invariant"):
            # A value that failed the invariants and was replaced stays on the
            # receipt: a repair that hides what it repaired is not a record.
            out["value"] = jsonable(clean(self.value))
        if self.score is not None:
            out["score"] = self.score
        if self.reason:
            out["reason"] = self.reason
        if self.tuple_key:
            out["tuple"] = self.tuple_key
        if self.meta:
            out["meta"] = dict(self.meta)
        return out


@dataclass(frozen=True, slots=True)
class Run:
    """What one call produced: the value and the receipt that explains it."""

    value: Any
    receipt: dict[str, Any]
    answered_by: Member


def _identity(x: Any) -> Any:
    return x


def _equal(a: Any, b: Any) -> bool:
    return bool(a == b)


def _as_sink(s: Sink | str | Path | None) -> Sink | None:
    return JsonlSink(s) if isinstance(s, (str, Path)) else s


class Socket:
    """One contract with a portfolio of members.

    Build one with :func:`contract` (the decorator) or directly. Members are
    added in the order the ``first-success`` chain should try them. Shadows
    and a canary are added separately and never change the chain's answer,
    except that a canary member is placed first on the fraction of calls it is
    sampled for.
    """

    def __init__(
        self,
        id: str,
        *,
        input: Any = None,
        output: Any = None,
        invariants: Iterable[Invariant] = (),
        sink: Sink | str | Path | None = None,
        harvest: Sink | str | Path | None = None,
        clean: Callable[[Any], Any] | None = None,
        compare: Callable[[Any, Any], bool] | None = None,
        rng: random.Random | None = None,
        clock: Callable[[], float] | None = None,
    ):
        self.id = id
        self.input = input
        self.output = output
        self.invariants = tuple(invariants)
        self.sink = _as_sink(sink)
        self.harvest = _as_sink(harvest)
        self.clean = clean or _identity
        self.compare = compare or _equal
        self.rng = rng or random.Random()
        self.clock = clock or time.time
        self.members: list[Member] = []
        self.shadows: list[ShadowSpec] = []
        self.canary_spec: CanarySpec | None = None
        self._spent: dict[str, tuple[str, float]] = {}

    # -- composition ----------------------------------------------------------

    def member(self, m: Member) -> Socket:
        """Append a member to the first-success chain."""
        self._check_new(m)
        self.members.append(m)
        return self

    def shadow(self, m: Member, *, sample: float = 1.0, budget_usd_daily: float | None = None,
               noise: bool = False) -> Socket:
        """Run ``m`` on a sampled fraction of calls; record, never answer.

        ``budget_usd_daily`` caps what the shadow may spend per UTC day; past
        it the attempt is recorded as ``skipped`` with trigger ``budget``.
        ``noise`` also re-runs the member that answered, on the same sample, and
        records whether it agreed with itself — the floor any shadow
        disagreement must clear before it means anything.
        """
        if not 0.0 <= sample <= 1.0:
            raise ValueError("sample must be in [0, 1]")
        self._check_new(m)
        self.shadows.append(ShadowSpec(member=m, sample=sample, budget_usd_daily=budget_usd_daily, noise=noise))
        return self

    def canary(self, m: Member, *, fraction: float) -> Socket:
        """Place ``m`` first in the chain on ``fraction`` of calls. A policy, never a default."""
        if not 0.0 < fraction <= 1.0:
            raise ValueError("fraction must be in (0, 1]")
        self._check_new(m)
        self.canary_spec = CanarySpec(member=m, fraction=fraction)
        return self

    def seated(self) -> list[Member]:
        """Every member on this socket: chain, then shadows, then the canary."""
        out = list(self.members) + [s.member for s in self.shadows]
        if self.canary_spec is not None:
            out.append(self.canary_spec.member)
        return out

    def _check_new(self, m: Member) -> None:
        if m.id in {x.id for x in self.seated()}:
            raise ValueError(f"member id {m.id!r} is already seated in socket {self.id!r}")

    @property
    def contract_digest(self) -> str:
        names = [getattr(self.input, "__qualname__", repr(self.input)),
                 getattr(self.output, "__qualname__", repr(self.output))]
        names += [getattr(f, "__qualname__", repr(f)) for f in self.invariants]
        return sha(self.id, *names)

    def describe(self) -> dict[str, Any]:
        """The socket as a record: contract digest and every seated member."""
        return {
            "socket": self.id,
            "contract": self.contract_digest,
            "members": [m.ref for m in self.members],
            "shadows": [{"member": s.member.ref, "sample": s.sample, "budgetUsdDaily": s.budget_usd_daily,
                         "noise": s.noise} for s in self.shadows],
            "canary": ({"member": self.canary_spec.member.ref, "fraction": self.canary_spec.fraction}
                       if self.canary_spec else None),
        }

    # -- the call ---------------------------------------------------------------

    def __call__(self, x: Any) -> Any:
        return self.run(x).value

    def run(self, x: Any) -> Run:
        """Apply the strategy to one input and write one receipt."""
        if not self.members and self.canary_spec is None:
            raise ValueError(f"socket {self.id!r} has no members")
        chain = list(self.members)
        strategy, is_canary = "first-success", False
        if self.canary_spec is not None and self.rng.random() < self.canary_spec.fraction:
            chain = [self.canary_spec.member, *chain]
            strategy, is_canary = "canary", True
        return self._execute(x, chain, strategy, is_canary, with_shadows=True)

    def serve(self, x: Any) -> Answer | None:
        """The deterministic-only path: run the code members, decline otherwise.

        This is what a somm workload handler calls. It writes a receipt like any
        call, with strategy ``first-success`` over the code members only and no
        shadows, and returns ``None`` where the chain would have escalated to a
        model — so the provider chain answers under the same workload id.
        """
        codes = [m for m in self.members if m.kind == "code"]
        if not codes:
            return None
        try:
            r = self._execute(x, codes, "first-success", False, with_shadows=False)
        except SocketExhausted:
            return None
        return Answer(value=r.value, tuple_key=r.answered_by.ref)

    # -- internals ---------------------------------------------------------------

    def _execute(self, x: Any, chain: list[Member], strategy: str, is_canary: bool, *,
                 with_shadows: bool) -> Run:
        run_id = f"srun_{uuid.uuid4().hex[:20]}"
        ctx = {"run": run_id, "socket": self.id}
        started = self.clock()
        attempts: list[Attempt] = []
        answered: Attempt | None = None
        trigger = "primary"
        for m in chain:
            attempt = self._attempt(m, x, ctx, trigger)
            attempts.append(attempt)
            if attempt.outcome == "value":
                answered = attempt
                break
            trigger = attempt.outcome

        shadows: list[dict[str, Any]] = []
        noise: dict[str, Any] | None = None
        if with_shadows and answered is not None and self.shadows:
            shadows, noise = self._run_shadows(x, ctx, answered)

        ended = self.clock()
        receipt = self._receipt(run_id, x, strategy, is_canary, attempts, answered, shadows, noise, started, ended)
        if self.sink is not None:
            self.sink(receipt)
        if self.harvest is not None:
            self.harvest(self._harvest_record(run_id, x, attempts, answered, shadows))
        if answered is None:
            raise SocketExhausted(receipt)
        return Run(value=answered.value, receipt=receipt, answered_by=answered.member)

    def _attempt(self, m: Member, x: Any, ctx: dict[str, Any], trigger: str) -> Attempt:
        t0 = self.clock()
        try:
            out = m.call(x, ctx)
        except MemberFailure as exc:
            return Attempt(m, trigger, exc.outcome, t0, self.clock(), reason=exc.detail)
        except Exception as exc:
            return Attempt(m, trigger, "error", t0, self.clock(), reason=f"{type(exc).__name__}: {exc}")
        t1 = self.clock()
        if isinstance(out, Abstain):
            return Attempt(m, trigger, "abstained", t0, t1, reason=out.reason, score=out.score,
                           cost_usd=out.cost_usd)
        if not isinstance(out, Answer):
            return Attempt(m, trigger, "error", t0, t1, reason=f"member returned {type(out).__name__}")
        violated = self._violation(out.value)
        outcome = "invariant" if violated else "value"
        return Attempt(m, trigger, outcome, t0, t1, value=out.value, score=out.score, reason=violated,
                       cost_usd=out.cost_usd, tuple_key=out.tuple_key, meta=out.meta)

    def _violation(self, value: Any) -> str | None:
        if self.output is not None and isinstance(self.output, type) and not isinstance(value, self.output):
            return f"value is {type(value).__name__}, contract says {self.output.__name__}"
        for inv in self.invariants:
            name = getattr(inv, "__name__", repr(inv))
            try:
                ok = inv(value)
            except Exception as exc:
                return f"{name}: {type(exc).__name__}: {exc}"
            if ok is False:
                return name
        return None

    def _run_shadows(self, x: Any, ctx: dict[str, Any], answered: Attempt) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        shadows: list[dict[str, Any]] = []
        noise: dict[str, Any] | None = None
        for spec in self.shadows:
            if self.rng.random() >= spec.sample:
                continue
            if spec.budget_usd_daily is not None and self._spent_today(spec.member.id) >= spec.budget_usd_daily:
                shadows.append({"member": spec.member.ref, "trigger": "budget", "outcome": "skipped", "costUsd": 0.0})
                continue
            shadow_ctx = {**ctx, "role": "shadow", "source_call_id": answered.meta.get("call_id")}
            attempt = self._attempt(spec.member, x, shadow_ctx, "sampled")
            self._spend(spec.member.id, attempt.cost_usd)
            rec = attempt.to_dict(self.clean)
            if attempt.outcome == "value":
                rec["agrees"] = self._agrees(attempt.value, answered.value)
            shadows.append(rec)
            if spec.noise and noise is None:
                again = self._attempt(answered.member, x, shadow_ctx, "sampled")
                self._spend(answered.member.id, again.cost_usd)
                noise = {"member": answered.member.ref, "outcome": again.outcome, "costUsd": again.cost_usd}
                if again.outcome == "value":
                    noise["agrees"] = self._agrees(again.value, answered.value)
        return shadows, noise

    def _agrees(self, a: Any, b: Any) -> bool:
        try:
            return bool(self.compare(a, b))
        except Exception:
            return False

    def _today(self) -> str:
        return datetime.fromtimestamp(self.clock(), tz=UTC).strftime("%Y-%m-%d")

    def _spent_today(self, member_id: str) -> float:
        day, spent = self._spent.get(member_id, (None, 0.0))
        return spent if day == self._today() else 0.0

    def _spend(self, member_id: str, usd: float) -> None:
        today = self._today()
        day, spent = self._spent.get(member_id, (today, 0.0))
        self._spent[member_id] = (today, (spent if day == today else 0.0) + usd)

    def _receipt(self, run_id: str, x: Any, strategy: str, is_canary: bool, attempts: list[Attempt],
                 answered: Attempt | None, shadows: list[dict[str, Any]], noise: dict[str, Any] | None,
                 started: float, ended: float) -> dict[str, Any]:
        reason = "exhausted" if answered is None else f"answered-by-{answered.member.kind}"
        cost = sum(a.cost_usd for a in attempts) + sum(float(s.get("costUsd", 0.0)) for s in shadows)
        if noise:
            cost += float(noise.get("costUsd", 0.0))
        receipt: dict[str, Any] = {
            "schema": RECEIPT_SCHEMA,
            "socket": self.id,
            "run": run_id,
            "runStatus": TERMINAL_REASONS[reason],
            "semanticOutcome": "answered" if answered is not None else "exhausted",
            "terminalReason": reason,
            "strategy": strategy,
            "canary": is_canary,
            "answeredBy": answered.member.ref if answered is not None else None,
            "attempts": [a.to_dict(self.clean) for a in attempts],
            "shadows": shadows,
            "noise": noise,
            "digests": {"contract": self.contract_digest, "members": {m.id: m.digest for m in self.seated()}},
            "timing": {
                "startedAt": datetime.fromtimestamp(started, tz=UTC).isoformat(),
                "endedAt": datetime.fromtimestamp(ended, tz=UTC).isoformat(),
                "totalMs": round((ended - started) * 1000, 3),
            },
            "cost": {"usd": round(cost, 8)},
            "input": jsonable(self.clean(x)),
        }
        if self.harvest is not None:
            receipt["harvest"] = f"harvest:{self.id}#{run_id}"
        return receipt

    def _harvest_record(self, run_id: str, x: Any, attempts: list[Attempt], answered: Attempt | None,
                        shadows: list[dict[str, Any]]) -> dict[str, Any]:
        triggers = [a.trigger for a in attempts]
        disagreed = any(s.get("agrees") is False for s in shadows)
        # Deferrals and disagreements are the inputs with the most to say about
        # where the boundary should move; the weight is what a curator sorts by.
        weight = 1 + sum(1 for t in triggers if t in BOUNDARY_TRIGGERS) + (2 if disagreed else 0)
        return {
            "socket": self.id,
            "run": run_id,
            "input": jsonable(self.clean(x)),
            "answeredBy": answered.member.ref if answered is not None else None,
            "triggers": triggers,
            "shadowDisagreed": disagreed,
            "weight": weight,
        }


def contract(
    *,
    id: str | None = None,
    input: Any = None,
    output: Any = None,
    invariants: Iterable[Invariant] = (),
    sink: Sink | str | Path | None = None,
    harvest: Sink | str | Path | None = None,
    clean: Callable[[Any], Any] | None = None,
    compare: Callable[[Any, Any], bool] | None = None,
    rng: random.Random | None = None,
    clock: Callable[[], float] | None = None,
) -> Callable[[Callable[[Any], Any]], Socket]:
    """Declare a contract on an existing function and seat it as the first ``code`` member.

    The decorated function is unchanged in meaning: calling the socket calls it
    first. What changes is that every call is receipted, and other members can
    be seated behind it::

        @contract(input=Claim, output=Verdict, sink="receipts.jsonl")
        def admissible(claim: Claim) -> Verdict | Abstain: ...

        admissible.member(model(gateway, prompt=render, result=Verdict, profile="mid"))
    """

    def decorate(fn: Callable[[Any], Any]) -> Socket:
        s = Socket(
            id or f"{fn.__module__}.{fn.__qualname__}",
            input=input, output=output, invariants=invariants, sink=sink, harvest=harvest,
            clean=clean, compare=compare, rng=rng, clock=clock,
        )
        s.member(code(fn))
        s.__doc__ = fn.__doc__
        return s

    return decorate
