"""TypeSafe System One (Jev) as a gateway for model members.

Jev is not a text generator. It answers typed questions about a *state*:
``Noul`` (a probability that a statement is true), ``Score`` (an expected level
on an ordered rubric, with probabilities) and ``Choice`` (a label, with
probabilities and a confidence). Several questions ride in one request and are
evaluated in parallel. That shape fits a socket member unusually well: the
answer arrives with a score attached, so a member built on it can *abstain*
where the distribution is flat instead of guessing, and the escalation band
can be read off labels (``chipsocket.report.band``) rather than set by hand.

The gateway contract is the same as every other: ``gateway(request) -> GatewayResult``.
A member's ``prompt`` renders the *state* (a string, object, or array) and the
gateway holds the questions; ``GatewayResult.data`` is a plain dict of the
answers, one entry per question::

    {"same_event": {"type": "choice", "choice": "SAME", "confidence": 0.81,
                    "probabilities": {"SAME": 0.9, "DIFFERENT": 0.1}},
     "roundup":    {"type": "noul", "noul": 0.04}}

Cost is input tokens at the published rate (output tokens are free), so the
receipt's ``costUsd`` is real money. ``typesafe-sdk`` is imported lazily; the
runner does not depend on it.

Key lookup: the ``TYPESAFE_API_KEY`` environment variable, else a ``.env`` file
named by ``env_file``. The key never enters a receipt.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from chipsocket.members import GatewayResult, SchemaFailure, TransportFailure

#: Published input price, USD per million tokens (docs.typesafe.ai/models, 2026-09).
INPUT_USD_PER_MILLION = 0.042
DEFAULT_MODEL = "jev-latest"


def load_key(env_file: str | Path | None = None) -> str | None:
    key = os.environ.get("TYPESAFE_API_KEY")
    if key:
        return key
    if env_file and Path(env_file).exists():
        for line in Path(env_file).read_text(encoding="utf-8").splitlines():
            k, _, v = line.strip().partition("=")
            if k.strip() == "TYPESAFE_API_KEY" and v.strip():
                return v.strip().strip("'\"")
    return None


def answers_to_dict(response: Any) -> dict[str, Any]:
    """Flatten an SDK ``SystemOneResponse`` into plain, record-shaped answers."""
    out: dict[str, Any] = {}
    for name, a in getattr(response, "answers", {}).items():
        if hasattr(a, "choice"):
            out[name] = {"type": "choice", "choice": a.choice, "confidence": float(a.confidence),
                         "probabilities": {k: float(v) for k, v in a.probabilities.items()}}
        elif hasattr(a, "score"):
            out[name] = {"type": "score", "score": a.score, "confidence": float(a.confidence),
                         "probabilities": {str(k): float(v) for k, v in a.probabilities.items()}}
        elif hasattr(a, "noul"):
            out[name] = {"type": "noul", "noul": float(a.noul)}
        else:  # forward-compat: keep whatever it was
            out[name] = {"type": type(a).__name__}
    return out


class TypeSafeGateway:
    """``gateway(request)`` that asks Jev ``questions`` about ``request["state"]``.

    ``questions`` is a mapping of names to SDK question objects (or raw
    dictionaries). The member's ``prompt`` may return a string (used as the
    state) or a dict with a ``state`` key and optional ``questions`` to use for
    that call instead of the gateway's.
    """

    def __init__(
        self,
        questions: Mapping[str, Any],
        *,
        model: str = DEFAULT_MODEL,
        timeout: float = 20.0,
        env_file: str | Path | None = None,
        client: Any = None,
        input_usd_per_million: float = INPUT_USD_PER_MILLION,
    ):
        if not questions:
            raise ValueError("a TypeSafe gateway needs at least one question")
        self.questions = dict(questions)
        self.model = model
        self.timeout = timeout
        self.env_file = env_file
        self.price = input_usd_per_million
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            from typesafe_sdk import TypeSafeClient

            key = load_key(self.env_file)
            if not key:
                raise TransportFailure("TYPESAFE_API_KEY is not set")
            self._client = TypeSafeClient(api_key=key)
        return self._client

    def __call__(self, request: dict[str, Any]) -> GatewayResult:
        state = request.get("state", request.get("prompt"))
        if state is None:
            raise SchemaFailure("TypeSafe member rendered no state")
        questions = request.get("questions") or self.questions
        try:
            from typesafe_sdk import TypeSafeAPIError, TypeSafeError
        except Exception:  # pragma: no cover - sdk absent; the generic path below still types
            TypeSafeAPIError = TypeSafeError = ()
        try:
            resp = self.client.system_one(state=state, questions=questions, model=self.model, timeout=self.timeout)
        except TypeSafeAPIError as exc:  # 4xx/5xx after retries
            status = getattr(exc, "status_code", None)
            if status is not None and 400 <= int(status) < 500 and int(status) != 429:
                raise SchemaFailure(f"TypeSafe {status}: {exc}") from exc
            raise TransportFailure(f"TypeSafe {status or ''}: {exc}") from exc
        except TypeSafeError as exc:
            raise SchemaFailure(str(exc)) from exc
        except Exception as exc:
            raise TransportFailure(f"{type(exc).__name__}: {exc}") from exc
        usage = getattr(resp, "usage", None)
        tokens_in = int(getattr(usage, "input_tokens", 0) or 0)
        served = getattr(resp, "model", self.model)
        return GatewayResult(
            data=answers_to_dict(resp),
            cost_usd=tokens_in / 1_000_000 * self.price,
            tuple_key=f"typesafe/{served}",
            model=served,
        )
