"""Sinks: one file, one file per day, and reading either back."""

from __future__ import annotations

import gzip
import json

from chipsocket import DailyJsonlSink, JsonlSink, Socket, code, iter_receipts, load_receipts


def test_jsonl_sink_appends(tmp_path):
    p = tmp_path / "nested" / "r.jsonl"
    sink = JsonlSink(p)
    sink({"a": 1})
    sink({"a": 2})
    assert [json.loads(line) for line in p.read_text().splitlines()] == [{"a": 1}, {"a": 2}]
    assert load_receipts(p) == [{"a": 1}, {"a": 2}]


def test_daily_sink_rotates_and_compresses_previous_days(tmp_path):
    now = [1_700_000_000.0]  # 2023-11-14 UTC
    sink = DailyJsonlSink(tmp_path, "same-event", clock=lambda: now[0])
    sink({"n": 1})
    sink({"n": 2})
    day1 = sink.path_for("2023-11-14")
    assert day1.exists() and day1.read_text().count("\n") == 2
    now[0] += 86_400
    sink({"n": 3})
    assert not day1.exists()
    gz = day1.with_suffix(".jsonl.gz")
    with gzip.open(gz, "rt") as fh:
        assert [json.loads(line)["n"] for line in fh] == [1, 2]
    assert sink.path_for("2023-11-15").read_text().strip() == '{"n":3}'
    # a directory reads back in day order, compressed days included
    assert [r["n"] for r in load_receipts(tmp_path)] == [1, 2, 3]
    assert [r["n"] for r in iter_receipts(tmp_path / "same-event-*.jsonl*")] == [1, 2, 3]
    assert load_receipts(tmp_path / "missing.jsonl") == []


def test_daily_sink_can_leave_previous_days_alone(tmp_path):
    now = [1_700_000_000.0]
    sink = DailyJsonlSink(tmp_path, "x", compress_previous=False, clock=lambda: now[0])
    sink({"n": 1})
    now[0] += 86_400
    sink({"n": 2})
    assert sorted(p.name for p in tmp_path.iterdir()) == ["x-2023-11-14.jsonl", "x-2023-11-15.jsonl"]
    assert len(sink.rotate("2023-11-15")) == 1


def test_socket_takes_a_daily_sink(tmp_path):
    s = Socket("s", sink=DailyJsonlSink(tmp_path, "s")).member(code(lambda x: x))
    s(1)
    assert load_receipts(tmp_path)[0]["socket"] == "s"
