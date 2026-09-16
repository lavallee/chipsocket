# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

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
