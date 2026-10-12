# chipsocket, for coding agents

Read this before adding a model call to a function, or before touching a
function that already has one.

## When to reach for it

A socket fits when one function decides one thing, some inputs can be decided
by code, and the rest get asked of a model. Signs you are looking at one:

- a heuristic or cache followed by an LLM call, with a threshold someone
  guessed;
- a "fallback" branch when the model fails that quietly changes the answer;
- a `# TODO: measure how often the model is actually needed` comment;
- two projects carrying the same judge with slightly different cuts.

A socket does not fit for pipelines, loops, or routing between different
contracts. It selects among implementations of one contract and records the
choice. If you need a graph, that is a different tool.

## The smallest correct change

1. Leave the function's answers alone. Decorate it with `@contract(...)` and
   have the code path return `Abstain("reason", score=s)` where it used to
   call the model. Give the socket a `sink` (a path or a `DailyJsonlSink`).
2. Seat the model behind it: `fn.member(model(gateway, prompt=..., result=...,
   profile=...))`. With somm, the gateway is `SommGateway(project, workload)`.
3. If the input carries anything that must not be recorded, pass `clean=` and
   make it return only what a receipt may hold. Do this before the first run.
4. Run the thing. Then `chipsocket report <receipts>`.

Nothing is promoted, demoted, or thresholded by the runner. It records.

## Phase 0, when the function is already in production

Do not re-run the incumbent. Seat its recorded verdict as primary with
`recorded(read, id=..., digest=<treatment hash>, kind="model")`, shadow the
candidate code member at `sample=1.0` (free), and shadow a gold model at a
small sample with `budget_usd_daily=`. Set `noise=True` on one model shadow so
the floor is measured before any agreement number is read.

## Learning a band

Once labels exist for some receipts, `chipsocket cuts <receipts> --truth
labels.json --key input.id --scorer <member>` returns `lo`, `hi`, how much
they close, and an evidence digest. `cut_member(score, cuts, negative=,
positive=)` is the member on those cuts. Seat it as a shadow first; promote it
when a second labelled day's cuts agree. Do not type cuts in as constants
without the `Cuts` record beside them.

## Rules the receipts depend on

- A member returns an `Answer`, an `Abstain`, or raises `SchemaFailure` /
  `TransportFailure`. Anything else is recorded as `error`, not propagated.
- A shadow never reaches the caller and can never fail the call.
- A `recorded` or model-derived member is `kind="model"` even though no call
  is made; counting it as code corrupts the closed-by-code share.
- Member ids are stable identifiers; the digest is what changes.
- `score`, `band`, `by_band`, `cuts` take `compare=` when `==` is the wrong
  equality for the output type.

## Commands

```
uv run pytest -q                      # 50 tests, no network, somm/typesafe stubbed
uv run ruff check src tests examples
uv run mypy src
uv run python examples/claim_admissibility.py && uv run chipsocket report /tmp/claims-receipts.jsonl
```

## Where things are

`socket.py` runner · `members.py` members and wrappers · `sinks.py` sinks and
readers · `report.py` live and held-out measures · `learn.py` cuts ·
`cli.py` the command · `somm.py`, `typesafe.py` gateways. The design is
`chip/docs/socket.md`; the contract library is `chipspec` (import `chip`).

## Do not

- import fab, or anything from a host. This package is the second host.
- add a scheduler, a policy gate, a state store, or an authority model here.
- make the runner decide promotion. It records; people and `chip.placement`
  decide.
