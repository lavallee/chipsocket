# chipsocket

**One contract, several live implementations.** The socket runner for
[chip](https://github.com/lavallee/chip).

A socket is a typed function contract with a portfolio of members seated
behind it: deterministic code, a model at some tier, a model at a higher tier,
a raw-model baseline. A strategy picks among them per call. Every call writes
a receipt saying which member answered, why, and what any shadow said. Over
enough calls the receipts are the evidence for where the code-vs-model
boundary belongs, and they are produced on production traffic without leaving
the program the call lives in.

The design is in
[`chip/docs/socket.md`](https://github.com/lavallee/chip/blob/main/docs/socket.md).
This package is the runner it describes, and it is also chip's minimal second
host: it depends on `chipspec` and nothing else, and it never imports fab.

## Install

```bash
pip install chipsocket            # the runner
pip install "chipsocket[somm]"    # + somm as the gateway and the meter
```

Requires Python 3.12+.

## Two-minute version

```python
from chipsocket import Abstain, contract, model
from chipsocket.somm import SommGateway

@contract(input=str, output=Verdict, invariants=[reason_is_short],
          sink="receipts.jsonl", harvest="harvest.jsonl")
def admissible(claim: str) -> Verdict | Abstain:
    ...                                   # your existing code, unchanged;
                                          # return Abstain("band") where it cannot decide

gateway = SommGateway(project="myproj", workload="claims")
admissible.member(model(gateway, prompt=render, result=Verdict, profile="claims-mid"))
admissible.shadow(model(gateway, prompt=render, result=Verdict, profile="claims-gold"),
                  sample=0.03, budget_usd_daily=2.0, noise=True)

verdict = admissible(claim)               # code first; model on abstain; gold shadows 3%
```

Then:

```bash
chipsocket report receipts.jsonl
```

gives the closed-by-code share, the escalation triggers split into boundary
causes (`abstained`, `invariant`) and operations causes (`schema`,
`transport`, `error`, `budget`), and each shadow's agreement with the
answering member net of that member's own noise floor.

## What a member may return

- a **value**, checked against the contract's output type and invariants;
- an **abstention** with a reason and an optional score, which is a first-class
  answer and the normal way a code member hands an input to a model;
- a **typed failure**: `SchemaFailure` or `TransportFailure`. Anything else a
  member raises is recorded as `error` and the chain moves on.

## Strategies

`first-success` runs the chain in order and escalates on abstention, invariant
failure, schema failure, transport failure, or error. `shadow` members run on a
sample with a daily budget, are recorded against the answer, and never reach
the caller. A `canary` member is placed first on a fraction of calls. The
`noise` flag on a shadow re-runs the answering member so a disagreement has a
floor to clear.

## somm

`chipsocket.somm.SommGateway` is a gateway for model members: every model call
is an ordinary somm call under the socket's workload, with the socket's run id
as the session id. `chipsocket.somm.bind` registers the socket as a somm
workload handler: its code members serve what they can and decline the rest,
so the provider chain answers under the same workload id and "should this
still be a model call" is a `GROUP BY provider`.

## Layout

| Path | What it is |
| --- | --- |
| `src/chipsocket/socket.py` | The runner: `Socket`, `contract`, strategies, receipts, harvest |
| `src/chipsocket/members.py` | `code`, `model`, `baseline` members; `Abstain`, `Answer`, typed failures |
| `src/chipsocket/report.py` | Receipts to placement measures; the `chipsocket report` CLI |
| `src/chipsocket/somm.py` | The somm gateway and workload handler |
| `examples/claim_admissibility.py` | The first socket, runnable offline |

## Status

v0.1, one runner, no host. The receipt schema is
`chip.socket-receipt/v0alpha1` and will change.

## License

MIT.
