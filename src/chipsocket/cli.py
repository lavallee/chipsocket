"""The ``chipsocket`` command: receipts in, measures out.

    chipsocket report  <receipts>                         live placement measures per socket
    chipsocket score   <receipts> --truth F --key P       every member against a labelled truth
    chipsocket band    <receipts> --truth F --key P --member M     truth by score where M abstained
    chipsocket cuts    <receipts> --truth F --key P --scorer M     the learned cut points

``<receipts>`` is a ``.jsonl``, a ``.jsonl.gz``, a directory of either, or a
glob. ``--truth`` is a JSON object ``{key: value}`` or a JSONL file of
``{"key": ..., "truth": ...}`` lines. ``--key`` is a dotted path into the
receipt, usually under ``input`` (``input.id``). Output is always JSON, so a
skill or a CI step can consume it without parsing prose. Exit 0 on success,
1 when a labelled command found no labelled receipts, 2 on usage.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from chipsocket import learn, report
from chipsocket.sinks import load_receipts


def load_truth(path: str | Path) -> dict[str, Any]:
    text = Path(path).read_text(encoding="utf-8")
    stripped = text.lstrip()
    if stripped.startswith("{"):
        try:
            data = json.loads(text)
            if isinstance(data, dict) and "key" not in data:
                return data
        except json.JSONDecodeError:
            pass
    out: dict[str, Any] = {}
    for line in text.splitlines():
        if line.strip():
            row = json.loads(line)
            out[str(row["key"])] = row["truth"]
    return out


def key_path(path: str):
    parts = path.split(".")

    def key(r: dict[str, Any]) -> str:
        cur: Any = r
        for p in parts:
            cur = cur[p] if isinstance(cur, dict) else getattr(cur, p)
        return str(cur)

    return key


def _positive(raw: str | None) -> Any:
    if raw is None:
        return True
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _emit(payload: Any) -> int:
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    return _emit(report.summarize(load_receipts(args.receipts)))


def _labelled(args: argparse.Namespace):
    return load_receipts(args.receipts), load_truth(args.truth), key_path(args.key)


def cmd_score(args: argparse.Namespace) -> int:
    rs, truth, key = _labelled(args)
    out = report.score(rs, truth, key, positive=_positive(args.positive))
    _emit(out)
    return 0 if out["labelled"] else 1


def cmd_band(args: argparse.Namespace) -> int:
    rs, truth, key = _labelled(args)
    rows = report.band(rs, truth, key, member=args.member, width=args.width, positive=_positive(args.positive))
    _emit(rows)
    return 0 if rows else 1


def cmd_cuts(args: argparse.Namespace) -> int:
    rs, truth, key = _labelled(args)
    c = learn.cuts(rs, truth, key, scorer=args.scorer, positive=_positive(args.positive),
                   precision=args.precision, width=args.width, min_n=args.min_n, min_bucket=args.min_bucket)
    _emit({**c.to_dict(), "closes": c.closes})
    return 0 if c.scored else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chipsocket", description="Receipts in, placement measures out.")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, help_: str, func, labelled: bool = False) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("receipts", help="receipts: .jsonl, .jsonl.gz, a directory of them, or a glob")
        if labelled:
            sp.add_argument("--truth", required=True, help="JSON {key: value} or JSONL {key, truth} lines")
            sp.add_argument("--key", required=True, help="dotted path into the receipt, e.g. input.id")
            sp.add_argument("--positive", help="the positive value as JSON (default: true)")
        sp.set_defaults(func=func)
        return sp

    add("report", "live placement measures per socket", cmd_report)
    add("score", "score every member against a labelled truth", cmd_score, labelled=True)
    p = add("band", "truth by score bucket where a member abstained", cmd_band, labelled=True)
    p.add_argument("--member", required=True, help="the abstaining, scoring member id")
    p.add_argument("--width", type=float, default=0.05)
    p = add("cuts", "learn the cut points where a constant beats asking", cmd_cuts, labelled=True)
    p.add_argument("--scorer", required=True, help="the scoring member id")
    p.add_argument("--precision", type=float, default=0.95)
    p.add_argument("--width", type=float, default=0.05)
    p.add_argument("--min-n", type=int, default=30, dest="min_n", help="inputs a cut needs on its side")
    p.add_argument("--min-bucket", type=int, default=10, dest="min_bucket", help="inputs a bucket needs to be judged")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0) if exc.code != 2 else 2
    try:
        return int(args.func(args))
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    except BrokenPipeError:
        return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
