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

from chipsocket.members import (
    KINDS,
    Abstain,
    Answer,
    GatewayResult,
    Member,
    MemberFailure,
    SchemaFailure,
    TransportFailure,
    baseline,
    code,
    model,
)
from chipsocket.report import load_receipts, summarize
from chipsocket.socket import (
    BOUNDARY_TRIGGERS,
    OPERATIONS_TRIGGERS,
    OUTCOMES,
    RECEIPT_SCHEMA,
    STRATEGIES,
    TERMINAL_REASONS,
    TRIGGERS,
    InvariantError,
    JsonlSink,
    Run,
    Socket,
    SocketExhausted,
    contract,
)

__version__ = "0.1.0"

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
    "GatewayResult",
    "InvariantError",
    "JsonlSink",
    "Member",
    "MemberFailure",
    "Run",
    "SchemaFailure",
    "Socket",
    "SocketExhausted",
    "TransportFailure",
    "baseline",
    "code",
    "contract",
    "load_receipts",
    "model",
    "summarize",
]
