# chipsocket

**One contract, several live implementations.** The socket runner for
[chip](https://github.com/lavallee/chip), and chip's minimal second host.

A socket is a typed function contract with a portfolio of members seated
behind it: deterministic code, a model at some tier, a model at a higher tier,
a raw-model baseline. A strategy picks among them per call. Every call writes
a receipt saying which member answered, why, and what any shadow said. Over
enough calls the receipts are the evidence for where the code-vs-model
boundary belongs, and they are produced on production traffic without leaving
the program the call lives in.

The design is
[`chip/docs/socket.md`](https://github.com/lavallee/chip/blob/main/docs/socket.md);
the ladder it serves is
[`chip/docs/skill-to-chip.md`](https://github.com/lavallee/chip/blob/main/docs/skill-to-chip.md).
This package depends on `chipspec` and nothing else, and it never imports fab.

## Install

```bash
pip install chipsocket            # the runner (PyPI publication pending; until then:)
pip install git+https://github.com/lavallee/chip git+https://github.com/lavallee/chipsocket
pip install "chipsocket[somm]"    # + somm as the gateway and the meter
pip install "chipsocket[typesafe]"  # + TypeSafe Jev as a gateway
```

Requires Python 3.12+.

## Two-minute version

You have a function that decides something, and somewhere in it a model gets
asked. Declare the contract on the function. It keeps answering exactly as it
did; what changes is that every call is receipted, and other members can be
seated behind it.

```python
from chipsocket import Abstain, contract, model
from chipsocket.somm import SommGateway

@contract(input=str, output=Verdict, invariants=[reason_is_short],
          sink="receipts.jsonl", harvest="harvest.jsonl")
def admissible(claim: str) -> Verdict | Abstain:
    ...                                   # your existing code, unchanged;
                                          # return Abstain("band", score=s) where it cannot decide

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

## Three shapes that recur

These came out of the first two production sockets and are now part of the
package rather than copied between projects.

**Phase 0: observe, change nothing.** The incumbent's verdict is read off the
row it already wrote and seated as primary at zero extra cost; candidates
shadow it.

```python
from chipsocket import Socket, code, model, recorded

s = Socket("lane.verdict", output=Verdict, sink="receipts.jsonl", clean=redact)
s.member(recorded(lambda row: row.incumbent, id="incumbent", digest=TREATMENT_SHA, kind="model"))
s.shadow(code(rules), sample=1.0)                                     # free, every row
s.shadow(model(gold_gateway, prompt=render, result=Verdict, profile="gold"),
         sample=0.03, budget_usd_daily=0.5)
```

**Keep a paid member inside the band.** A member that asks about inputs a
cheaper member already closes is spending on calls nothing will route to it.

```python
from chipsocket import banded

s.shadow(banded(jev_member, score=lambda p: p.lexical, lo=0.15, hi=0.75), sample=1.0,
         budget_usd_daily=0.5)
```

**Learn the band, then receipt what was learned.** Given labelled receipts,
`cuts` finds the widest band at each end where a constant answer meets a
precision bar, and `cut_member` turns that into a `code` member whose digest
includes the evidence it was learned from. Seat it as a shadow first.

```python
from chipsocket import cut_member, cuts, load_receipts

c = cuts(load_receipts("receipts/"), truth, key=lambda r: r["input"]["id"],
         scorer="prefilter", positive=True, precision=0.95, min_n=200)
print(c.lo, c.hi, c.closes, c.evidence)   # e.g. 0.15 0.75 0.49 sha256:…
s.shadow(cut_member(lambda p: p.lexical, c, negative=False, positive=True, id="lexical-cuts"))
```

```bash
chipsocket cuts receipts/ --truth labels.json --key input.id --scorer prefilter --min-n 200
```

A receipt for every call adds up. `DailyJsonlSink(directory, prefix)` writes
one file per UTC day and gzips finished days; `load_receipts` reads a file, a
directory, or a glob, compressed or not.

## What a member may return

- a **value**, checked against the contract's output type and invariants;
- an **abstention** with a reason and an optional score, which is a first-class
  answer and the normal way a code member hands an input to a model;
- a **typed failure**: `SchemaFailure` or `TransportFailure`. Anything else a
  member raises is recorded as `error` and the chain moves on.

A `model` member's parser may also decide, not just decode: it can return an
`Abstain` when the distribution is flat, or an `Answer` carrying a score.

## Strategies

`first-success` runs the chain in order and escalates on abstention, invariant
failure, schema failure, transport failure, or error. `shadow` members run on a
sample with a daily budget, are recorded against the answer, and never reach
the caller. A `canary` member is placed first on a fraction of calls. The
`noise` flag on a shadow re-runs the answering member so a disagreement has a
floor to clear.

## Gateways

`chipsocket.somm.SommGateway` makes every model call an ordinary somm call
under the socket's workload, with the socket's run id as the session id and
shadow runs marked as observations. `chipsocket.somm.bind` registers the
socket as a somm workload handler: its code members serve what they can and
decline the rest. `chipsocket.typesafe.TypeSafeGateway` asks Jev typed
questions about a state and returns probabilities a parser can abstain on.
Any `gateway(request) -> GatewayResult | dict` works.

## CLI

```
chipsocket report <receipts>                                   live measures per socket
chipsocket score  <receipts> --truth F --key P [--positive J]  every member vs. a labelled truth
chipsocket band   <receipts> --truth F --key P --member M      truth by score where M abstained
chipsocket cuts   <receipts> --truth F --key P --scorer M      the learned cut points
```

`<receipts>` is a `.jsonl`, a `.jsonl.gz`, a directory, or a glob. `--truth`
is a JSON object `{key: value}` or JSONL `{"key": …, "truth": …}` lines.
`--key` is a dotted path into the receipt. Output is JSON; exit 0/1/2.

## Layout

| Path | What it is |
| --- | --- |
| `src/chipsocket/socket.py` | The runner: `Socket`, `contract`, strategies, receipts, harvest |
| `src/chipsocket/members.py` | `code`, `model`, `baseline`; `banded`, `recorded`; `Abstain`, `Answer`, typed failures |
| `src/chipsocket/sinks.py` | `JsonlSink`, `DailyJsonlSink`, `load_receipts` |
| `src/chipsocket/report.py` | Receipts to placement measures; `score`, `band`, `by_band` |
| `src/chipsocket/learn.py` | `cuts`, `cut_member`: the learned band and the member that carries it |
| `src/chipsocket/cli.py` | The `chipsocket` command |
| `src/chipsocket/somm.py` | The somm gateway and workload handler |
| `src/chipsocket/typesafe.py` | The TypeSafe Jev gateway |
| `examples/claim_admissibility.py` | The first socket, runnable offline |
| `AGENTS.md` | What a coding agent should know before reaching for this |

## Status

v0.2, one runner, no host. Two production sockets feed it: a same-event judge
at ~18,000 receipts a day on a local 7B model, and a phase-0 claim
admissibility pilot. The receipt schema is `chip.socket-receipt/v0alpha1` and
will change.

## License

MIT.
