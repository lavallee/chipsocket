"""Where receipts and harvest lines go, and how they are read back.

A sink is any callable that takes one record. Two are provided: one file, or
one file per UTC day with finished days compressed. ``load_receipts`` reads
either layout back, so a report never cares which one wrote it.
"""

from __future__ import annotations

import gzip
import json
import shutil
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _dump(document: dict[str, Any]) -> str:
    return json.dumps(document, separators=(",", ":"), default=str) + "\n"


class JsonlSink:
    """Append one JSON document per line to one file."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def __call__(self, document: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(_dump(document))


class DailyJsonlSink:
    """One ``<prefix>-YYYY-MM-DD.jsonl`` per UTC day; earlier days gzipped.

    A receipt for every call adds up: one estate build wrote ~40 MB of receipts
    six times a day and filled a volume. JSON receipts compress about twelve to
    one and a finished day is never appended to again, so at the first write of
    a new day every earlier day's file is compressed in place. ``load_receipts``
    reads both forms.
    """

    def __init__(
        self,
        directory: str | Path,
        prefix: str,
        *,
        compress_previous: bool = True,
        clock: Callable[[], float] | None = None,
    ):
        self.directory = Path(directory)
        self.prefix = prefix
        self.compress_previous = compress_previous
        self.clock = clock or time.time
        self._day: str | None = None

    def _today(self) -> str:
        return datetime.fromtimestamp(self.clock(), tz=UTC).strftime("%Y-%m-%d")

    def path_for(self, day: str) -> Path:
        return self.directory / f"{self.prefix}-{day}.jsonl"

    def rotate(self, today: str | None = None) -> list[Path]:
        """Compress every day but ``today``. Returns the files compressed."""
        today = today or self._today()
        done: list[Path] = []
        if not self.directory.exists():
            return done
        for f in sorted(self.directory.glob(f"{self.prefix}-*.jsonl")):
            if f == self.path_for(today):
                continue
            gz = f.with_suffix(".jsonl.gz")
            try:
                with f.open("rb") as src, gzip.open(gz, "wb", compresslevel=6) as dst:
                    shutil.copyfileobj(src, dst)
                f.unlink()
                done.append(gz)
            except OSError:
                gz.unlink(missing_ok=True)
        return done

    def __call__(self, document: dict[str, Any]) -> None:
        today = self._today()
        if today != self._day:
            self.directory.mkdir(parents=True, exist_ok=True)
            if self.compress_previous:
                self.rotate(today)
            self._day = today
        with self.path_for(today).open("a", encoding="utf-8") as fh:
            fh.write(_dump(document))


def _files(path: str | Path) -> list[Path]:
    p = Path(path)
    if p.is_file():
        return [p]
    if p.is_dir():
        return sorted(list(p.glob("*.jsonl")) + list(p.glob("*.jsonl.gz")), key=lambda f: f.name)
    # A glob, relative to its own parent.
    return sorted(p.parent.glob(p.name))


def iter_receipts(path: str | Path) -> Iterator[dict[str, Any]]:
    """Yield records from a ``.jsonl``, a ``.jsonl.gz``, a directory of them, or a glob."""
    for f in _files(path):
        opener = gzip.open if f.name.endswith(".gz") else open
        with opener(f, "rt", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    yield json.loads(line)


def load_receipts(path: str | Path) -> list[dict[str, Any]]:
    """``iter_receipts`` as a list. A missing path is an empty list, not an error."""
    return list(iter_receipts(path))
