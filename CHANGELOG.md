# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

## [0.2.1] - 2026-10-12

### Changed
- `learn.cuts` judges each score bucket on its own instead of applying the
  precision bar cumulatively from the end. The second labelled day in gabe
  showed why: 4,087 inputs below 0.15 that were 99% negative carried a
  cumulative 95% floor over the band [0.15, 0.30), where the constant was
  right 87.8% of the time and the model 91.1%, and put the cut at 0.30. A cut
  is a promise about each input it closes, so each bucket has to earn it.
  New `min_bucket` (default 10) skips buckets too small to judge; `below` /
  `above` carry `thin: true` when a cut was withheld for having fewer than
  `min_n` inputs behind it. `Cuts.closes` counts only cuts that exist.
- `chipsocket cuts` takes `--min-bucket`.

## [0.2.0] - 2026-10-12

The three shapes two production sockets each hand-rolled are now in the
package, the band can be learned from receipts rather than typed in, and the
CLI grew the labelled commands. One dead parameter and one dead class are gone.

### Added
- `chipsocket.learn`: `cuts(receipts, truth, key, scorer, ...)` finds the
  widest band at each end of one member's score where a constant answer meets
  a precision bar, cumulatively from the end, and returns a `Cuts` record
  whose `evidence` digest covers every (key, score, truth) triple it read.
  `cut_member(score, cuts, negative=, positive=)` is the `code` member on
  those cuts; its digest includes the evidence, so a member re-learned from a
  different day is a different member on the receipts.
- `banded(inner, score, lo, hi)`: confine a member to a score band, abstaining
  outside it with the score. The digest moves with the band.
- `recorded(read, id=, digest=, kind=)`: seat an answer that was already
  produced elsewhere as a member (the phase-0 incumbent). `kind` is what the
  incumbent was, so the closed-by-code share stays honest.
- `DailyJsonlSink(directory, prefix)`: one file per UTC day, earlier days
  gzipped at rollover. `load_receipts` / `iter_receipts` read a file, a
  `.jsonl.gz`, a directory, or a glob.
- `Socket.describe()` and `Socket.seated()`.
- `chipsocket score`, `band`, and `cuts` CLI commands beside `report`, with
  `--truth` (JSON map or JSONL) and `--key` (dotted path into the receipt).
  All output is JSON; exit codes 0/1/2.
- `py.typed`; the package type-checks under mypy.
- `GatewayResult` carries the meter's receipt fields beside `data` — `call_id`,
  `provider`, `tokens_in` / `tokens_out`, `cache_tokens_in`, `reasoning_tokens`,
  `latency_ms`, `cost_basis` — and a model member copies them into the answer's
  `meta`, so a socket receipt joins to the ledger instead of re-deriving cost
  and identity. `SommGateway` fills them from somm's `SommResult`.
- Shadow and noise runs tell the gateway what they are: the member context
  carries `role="shadow"` and the primary's `source_call_id`, which
  `SommGateway` passes to somm (>= 0.17) as `observation_role` /
  `source_call_id`. Shadow calls stay out of somm's rollups and budgets and
  link to the call they shadow. The `somm` extra requires somm >= 0.17.

### Changed
- `score`, `band`, and `by_band` all take `compare=`; `by_band` previously
  compared with `==` regardless.
- `Socket.serve` shares the execution path with `run` instead of building a
  throwaway socket; its receipts are unchanged.
- The console script is `chipsocket.cli:main`; `chipsocket.report.main` is gone.
- The somm sibling checkout is no longer a uv path source; somm resolves from
  PyPI. chipspec remains a path source until it is published.

### Removed
- `contract(strategy=...)`: it was validated and then ignored. The strategy
  is decided per call by what is seated.
- `InvariantError`: defined, exported, never raised.

## [0.1.0] - 2026-09-16

### Added
- The socket runner: `contract` decorator, `Socket`, `code` / `model` / `baseline`
  members, `first-success` chain with typed escalation triggers, budgeted and
  sampled `shadow` members with an optional noise re-run, `canary` placement,
  one receipt per call (`chip.socket-receipt/v0alpha1`), and per-call harvest.
- `chipsocket.report`: fold receipts into placement measures (closed-by-code
  share, escalation triggers split into boundary vs operations, shadow agreement
  net of the noise floor). `chipsocket report <receipts.jsonl>` CLI.
- `chipsocket.somm`: `SommGateway` for model members and `SocketHandler` /
  `bind` so a socket's code members serve a somm workload and decline the rest.
- `examples/claim_admissibility.py`: the first socket, offline.
