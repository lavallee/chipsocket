"""The chipsocket CLI: receipts and a truth file in, JSON out."""

from __future__ import annotations

import json

from chipsocket import Abstain, Socket, code
from chipsocket.cli import key_path, load_truth, main


def _write(tmp_path):
    receipts = tmp_path / "r.jsonl"
    s = Socket("s", output=bool, sink=receipts).member(
        code(lambda x: Abstain("band", score=x["s"]) if 0.2 <= x["s"] < 0.8 else x["s"] >= 0.8, id="lex")
    ).member(code(lambda x: x["s"] >= 0.5, id="model"))
    for i in range(40):
        s({"id": f"k{i}", "s": i / 40})
    truth = tmp_path / "truth.json"
    truth.write_text(json.dumps({f"k{i}": i / 40 >= 0.5 for i in range(40)}))
    return receipts, truth


def test_report_score_band_cuts(tmp_path, capsys):
    receipts, truth = _write(tmp_path)
    assert main(["report", str(receipts)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["s"]["calls"] == 40 and out["s"]["escalations"]["byTrigger"] == {"abstained": 24}

    assert main(["score", str(receipts), "--truth", str(truth), "--key", "input.id"]) == 0
    sc = json.loads(capsys.readouterr().out)
    assert sc["labelled"] == 40 and sc["members"]["lex"]["accuracy"] == 1.0

    assert main(["band", str(receipts), "--truth", str(truth), "--key", "input.id", "--member", "lex", "--width", "0.2"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [r["lo"] for r in rows] == [0.2, 0.4, 0.6]

    assert main(["cuts", str(receipts), "--truth", str(truth), "--key", "input.id", "--scorer", "lex",
                 "--width", "0.1", "--min-n", "5"]) == 0
    c = json.loads(capsys.readouterr().out)
    assert c["lo"] == 0.5 and c["hi"] == 0.5 or (c["lo"] is not None and c["hi"] is not None)
    assert c["closes"] is not None


def test_truth_formats_and_failures(tmp_path, capsys):
    receipts, _ = _write(tmp_path)
    jsonl = tmp_path / "t.jsonl"
    jsonl.write_text('{"key": "k0", "truth": false}\n{"key": "k39", "truth": true}\n')
    assert load_truth(jsonl) == {"k0": False, "k39": True}
    assert main(["score", str(receipts), "--truth", str(jsonl), "--key", "input.id"]) == 0
    assert json.loads(capsys.readouterr().out)["labelled"] == 2
    unlabelled = tmp_path / "u.json"
    unlabelled.write_text("{}")
    assert main(["score", str(receipts), "--truth", str(unlabelled), "--key", "input.id"]) == 1
    assert main(["score", str(receipts), "--truth", str(tmp_path / "nope.json"), "--key", "input.id"]) == 1
    assert "FAIL" in capsys.readouterr().err
    assert main([]) == 2
    assert key_path("input.id")({"input": {"id": 7}}) == "7"
