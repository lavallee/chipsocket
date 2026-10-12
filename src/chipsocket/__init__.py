"""chipsocket — one contract, several live implementations.

The socket runner for chip (``chip/docs/socket.md``): a contract declared on
an ordinary function, a portfolio of members seated behind it, a strategy that
picks among them per call, and one receipt per call saying which member
answered, why, and what any shadow said.

    from chipsocket import contract, model, Abstain

    @contract(input=Claim, output=Verdict, sink="receipts.jsonl", harvest="harvest.jsonl")
    def admissible(claim: Claim) -> Verdict | Abstain:
        ...                                   # existing code, unchanged

    admissible.member(model(gateway, prompt=render, result=Verdict, profile="claims-mid"))
    admissible.shadow(model(gateway, prompt=render, result=Verdict, profile="claims-gold"),
                      sample=0.03, budget_usd_daily=2.0, noise=True)

    verdict = admissible(claim)               # receipted, escalated, harvested
"""

from chipsocket.learn import Cuts, cut_member, cuts
from chipsocket.members import (
    KINDS,
    Abstain,
    Answer,
    GatewayResult,
    Member,
    MemberFailure,
    SchemaFailure,
    TransportFailure,
    banded,
    baseline,
    code,
    model,
    recorded,
)
from chipsocket.report import band, by_band, score, summarize
from chipsocket.sinks import DailyJsonlSink, JsonlSink, iter_receipts, load_receipts
from chipsocket.socket import (
    BOUNDARY_TRIGGERS,
    OPERATIONS_TRIGGERS,
    OUTCOMES,
    RECEIPT_SCHEMA,
    STRATEGIES,
    TERMINAL_REASONS,
    TRIGGERS,
    Run,
    Socket,
    SocketExhausted,
    contract,
)

__version__ = "0.2.1"

__all__ = [
    "BOUNDARY_TRIGGERS",
    "KINDS",
    "OPERATIONS_TRIGGERS",
    "OUTCOMES",
    "RECEIPT_SCHEMA",
    "STRATEGIES",
    "TERMINAL_REASONS",
    "TRIGGERS",
    "Abstain",
    "Answer",
    "Cuts",
    "DailyJsonlSink",
    "GatewayResult",
    "JsonlSink",
    "Member",
    "MemberFailure",
    "Run",
    "SchemaFailure",
    "Socket",
    "SocketExhausted",
    "TransportFailure",
    "band",
    "banded",
    "baseline",
    "by_band",
    "code",
    "contract",
    "cut_member",
    "cuts",
    "iter_receipts",
    "load_receipts",
    "model",
    "recorded",
    "score",
    "summarize",
]
