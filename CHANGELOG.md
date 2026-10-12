# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Changed
- `GatewayResult` carries the meter's receipt fields beside `data` — `call_id`,
  `provider`, `tokens_in` / `tokens_out`, `cache_tokens_in`, `reasoning_tokens`,
  `latency_ms`, `cost_basis` — and a model member copies them into the answer's
  `meta`, so a socket receipt joins to the ledger instead of re-deriving cost
  and identity. `SommGateway` fills them from somm's `SommResult`.
- Shadow and noise runs tell the gateway what they are: the member context
  carries `role="shadow"` and the primary's `source_call_id`, which
  `SommGateway` passes to somm (>= 0.17) as `observation_role` /
  `source_call_id`. Shadow calls stay out of somm's rollups and budgets and
  link to the call they shadow. The `somm` extra now requires somm >= 0.17.

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
