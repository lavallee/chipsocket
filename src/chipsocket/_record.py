"""What goes on a record: stable digests and record-shaped values.

Shared by members, the socket, the report, and the learner. Nothing here knows
what a socket is.
"""

from __future__ import annotations

import dataclasses
import hashlib
import inspect
from collections.abc import Callable
from typing import Any


def sha(*parts: Any) -> str:
    """``sha256:<16 hex>`` over the parts, NUL-separated. Stable across processes."""
    h = hashlib.sha256()
    for part in parts:
        h.update(str(part).encode("utf-8"))
        h.update(b"\0")
    return "sha256:" + h.hexdigest()[:16]


def source_digest(fn: Callable[..., Any]) -> str:
    """Digest a function by its source where possible, else by its qualified name.

    Source is the honest identity for a code member: two deployments with the
    same function name and different bodies must not share receipts.
    """
    try:
        return sha(inspect.getsource(fn))
    except (OSError, TypeError):
        return sha(getattr(fn, "__module__", ""), getattr(fn, "__qualname__", repr(fn)))


def jsonable(v: Any) -> Any:
    """Make a value record-shaped without asking the caller to: dataclasses and
    pydantic models become dicts; everything else is left for the sink."""
    if dataclasses.is_dataclass(v) and not isinstance(v, type):
        return dataclasses.asdict(v)
    dump = getattr(v, "model_dump", None)
    if callable(dump):
        try:
            return dump()
        except Exception:
            return v
    return v
